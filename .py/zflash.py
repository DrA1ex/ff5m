#!/usr/bin/env python3

## Select, download and verify a firmware image for one-shot Recovery flashing.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

import fcntl
import hashlib
import os
import re
import shutil
import sys
import tarfile

from firmware_catalog import (
    ARCHIVE_RELEASES, DOWNLOAD_DIR, FORGEX_API, RECOVERY_IMAGES, VERSION_RE, archive_api,
    archive_catalog, forgex_catalog, published_metadata, release_asset_location, static_catalog,
)
from firmware_download import (
    RESERVE_BYTES, archive_names, download, emit, emit_progress, request_json,
)

LOCK = "/tmp/forge-x-flash-download.lock"
PAGE_SIZE = 6
IMAGE_RE = re.compile(r"^Adventurer5M(?:Pro)?-[A-Za-z0-9._-]+\.tgz$")
HEADINGS = {"STOCK": "STOCK FIRMWARE", "FACTORY": "FACTORY FIRMWARE",
            "FORGEX": "FORGE-X RELEASES", "RECOVERY": "RECOVERY IMAGES"}
MACHINES = ("Adventurer5M", "Adventurer5MPro")


def catalog(kind, machine):
    # Recovery's firmware menu lists exactly the same entries.
    if kind == "FORGEX":
        return forgex_catalog(machine, request_json(FORGEX_API + "?per_page=100"))
    if kind in ARCHIVE_RELEASES:
        return archive_catalog(machine, request_json(archive_api(kind)))
    if kind == "RECOVERY":
        return static_catalog(machine, RECOVERY_IMAGES)
    raise RuntimeError("Unknown firmware type.")


def selected_image(kind, version, machine):
    if kind == "FORGEX":
        # Selecting a known version does not need the complete releases list.
        if version != "latest" and not VERSION_RE.fullmatch(version):
            raise RuntimeError("Invalid firmware version.")
        endpoint = "/latest" if version == "latest" else "/tags/" + version
        entries = forgex_catalog(machine, [request_json(FORGEX_API + endpoint)])
    else:
        entries = [entry for entry in catalog(kind, machine) if entry["id"] == version]
        for entry in entries:
            if kind == "RECOVERY":
                # Fixed entries carry no size; GitHub publishes it with each asset.
                location = release_asset_location(entry["url"])
                entry.update(published_metadata(request_json(location[0]), location[1]) or {})
    if not entries:
        raise RuntimeError("{} image {} for {} was not found.".format(kind, version, machine))
    return entries[0]


def show_catalog(kind, machine, page):
    entries = catalog(kind, machine)
    if kind == "FORGEX":
        entries.insert(0, {"id": "latest", "version": "Latest stable"})
    if not entries:
        raise RuntimeError("No compatible {} images were found.".format(kind))
    count = (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE
    if not page.isdigit() or not 1 <= int(page) <= count:
        raise RuntimeError("Invalid firmware list page.")
    page = int(page)

    emit("// action:prompt_end")
    emit("// action:prompt_begin " + HEADINGS[kind])
    emit("// action:prompt_text Printer: {}. Select the image to install. Page {} / {}."
         .format(machine, page, count))
    current = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    for index in range(0, len(current), 2):
        emit("// action:prompt_button_group_start")
        for entry in current[index:index + 2]:
            emit("// action:prompt_button {}|FLASH_FIRMWARE TYPE={} VERSION={}|primary"
                 .format(entry["version"], kind, entry["id"]))
        emit("// action:prompt_button_group_end")

    emit("// action:prompt_footer_button BACK|FLASH_FIRMWARE|secondary")
    if page > 1:
        emit("// action:prompt_footer_button PREV|FLASH_FIRMWARE TYPE={} PAGE={}"
             .format(kind, page - 1))
    if page < count:
        emit("// action:prompt_footer_button NEXT|FLASH_FIRMWARE TYPE={} PAGE={}"
             .format(kind, page + 1))
    emit("// action:prompt_show")


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path):
    try:
        names = archive_names(path)
    except (OSError, tarfile.TarError) as error:
        raise RuntimeError("Downloaded file is not a valid firmware archive.") from error
    if not {"flashforge_init.sh", "forge-x-init.sh", "forge-x-init"}.intersection(names):
        raise RuntimeError("Firmware archive has no supported installer.")


def verify_file(path, size, digest):
    if not os.path.isfile(path) or os.path.islink(path):
        return False
    if os.stat(path).st_size != size:
        return False
    if digest and sha256(path) != digest:
        return False
    try:
        verify_archive(path)
        return True
    except RuntimeError:
        return False


def prepare_download(kind, version, machine):
    emit("// Looking up {} {} for {}...".format(kind, version, machine))
    entry = selected_image(kind, version, machine)
    name, url = entry["name"], entry.get("url") or ""
    size, digest = int(entry.get("size") or 0), entry.get("sha256")
    if size <= 0 or not url or not IMAGE_RE.fullmatch(name):
        raise RuntimeError("Invalid firmware image metadata.")
    if digest and not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise RuntimeError("Invalid firmware image checksum.")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    destination = os.path.join(DOWNLOAD_DIR, name)
    partial = destination + ".part"
    if os.path.islink(destination) or os.path.islink(partial):
        raise RuntimeError("Firmware destination is an unsafe symbolic link.")

    if verify_file(destination, size, digest):
        emit("// Using verified cached image: " + name)
    else:
        # A failed cache check cannot preserve an image that will be replaced.
        # Release its space before checking whether the new download fits.
        if os.path.exists(destination):
            os.unlink(destination)
        if os.path.exists(partial):
            os.unlink(partial)
        free = shutil.disk_usage(DOWNLOAD_DIR).free
        if free < size + RESERVE_BYTES:
            raise RuntimeError("Not enough free space on /data for firmware download.")
        try:
            emit_progress(0, "Downloading {}".format(name))
            download(url, partial, size, digest)
            emit("// Checking firmware archive...")
            verify_archive(partial)
            os.replace(partial, destination)
            os.sync()
        except Exception:
            if os.path.exists(partial):
                os.unlink(partial)
            raise

    emit("// Firmware ready: " + name)
    emit("@@IMAGE|" + name)


def main(argv):
    if len(argv) == 4 and argv[1] == "list":
        machine, _, kind, page = argv
    elif len(argv) == 3:
        machine, kind, version = argv
    else:
        raise RuntimeError("Usage: zflash.py MACHINE TYPE VERSION | MACHINE list TYPE PAGE")
    if machine not in MACHINES or kind not in HEADINGS:
        raise RuntimeError("Unsupported firmware type or printer model.")

    if len(argv) == 4:
        show_catalog(kind, machine, page)
        return

    # The kernel releases this lock even when the download is killed.
    with open(LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another firmware download is in progress.") from None
        prepare_download(kind, version, machine)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as error:
        emit("!! {}".format(str(error) or error.__class__.__name__))
        sys.exit(1)
