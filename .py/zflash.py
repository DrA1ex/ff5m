#!/usr/bin/env python3

## Select, download and verify a firmware image for one-shot Recovery flashing.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

import hashlib
import os
import re
import shutil
import sys
import tarfile

from firmware_catalog import (
    DOWNLOAD_DIR, FORGEX_API, VERSION_RE, asset_sha256, forgex_asset, stock_assets,
)
from firmware_download import (
    RESERVE_BYTES, archive_names, download, emit, emit_progress, request_json,
)

LEGACY_API = FORGEX_API + "/tags/1.2.0"
PAGE_SIZE = 6
IMAGE_RE = re.compile(r"^Adventurer5M(?:Pro)?-[A-Za-z0-9._-]+\.tgz$")
KINDS = ("STOCK", "FORGEX", "RECOVERY")
MACHINES = ("Adventurer5M", "Adventurer5MPro")


def stock_catalog(machine):
    # Use only firmware images published in Forge-X releases.
    releases = request_json(FORGEX_API + "?per_page=100")
    entries = {}
    for version, asset, name in stock_assets(releases, machine):
        if version not in entries:
            entries[version] = (version, asset, name)
    return sorted(entries.values(),
                  key=lambda item: tuple(int(v) for v in item[0].split(".")),
                  reverse=True)


def forgex_catalog(machine):
    releases = request_json(FORGEX_API + "?per_page=100")
    entries = []
    for release in releases:
        version = str(release.get("tag_name") or "")
        if not VERSION_RE.fullmatch(version):
            continue
        result = forgex_asset(release, machine)
        if result:
            entries.append((version, result[0], result[1]))
    return entries


def recovery_catalog(machine):
    release = request_json(LEGACY_API)
    entries = []
    for version in ("full", "dry"):
        suffix = "-recovery-{}.tgz".format(version)
        for asset in release.get("assets", ()):
            name = str(asset.get("name") or "")
            if name.startswith("Adventurer5M-") and name.endswith(suffix):
                if machine == "Adventurer5MPro":
                    name = machine + name[len("Adventurer5M"):]
                entries.append((version, asset, name))
                break
    return entries


def catalog(kind, machine):
    if kind == "STOCK":
        return stock_catalog(machine)
    if kind == "FORGEX":
        return forgex_catalog(machine)
    if kind == "RECOVERY":
        return recovery_catalog(machine)
    raise RuntimeError("Unknown firmware type.")


def selected_image(kind, version, machine):
    if kind == "FORGEX":
        # Selecting a known version does not need the complete releases list.
        # The list view still uses the catalog for pagination.
        endpoint = ("/latest" if version == "latest"
                    else "/tags/" + version)
        release = request_json(FORGEX_API + endpoint)
        result = forgex_asset(release, machine)
        if result is None:
            raise RuntimeError("Forge-X release {} has no compatible image."
                               .format(version))
        return result

    # The version list and actual download share precisely the same resolution
    # rules, so the choice shown in the UI cannot select an unrelated archive.
    for item_version, asset, name in catalog(kind, machine):
        if item_version == version:
            return asset, name
    raise RuntimeError("{} image {} for {} was not found."
                       .format(kind, version, machine))


def show_catalog(kind, machine, page):
    entries = catalog(kind, machine)
    if kind == "FORGEX":
        entries.insert(0, ("latest", None, ""))
    if not entries:
        raise RuntimeError("No compatible {} images were found.".format(kind))
    count = (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE
    if page < 1 or page > count:
        raise RuntimeError("Invalid firmware list page.")

    heading = {"STOCK": "STOCK FIRMWARE", "FORGEX": "FORGE-X RELEASES",
               "RECOVERY": "RECOVERY IMAGES"}[kind]
    emit("// action:prompt_end")
    emit("// action:prompt_begin " + heading)
    emit("// action:prompt_text Printer: {}. Select the image to install. Page {} / {}."
         .format(machine, page, count))
    current = entries[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    for index in range(0, len(current), 2):
        emit("// action:prompt_button_group_start")
        for version, _, _ in current[index:index + 2]:
            label = ("LATEST STABLE" if version == "latest" else
                     "FULL RECOVERY" if version == "full" else
                     "DRY RUN" if version == "dry" else version)
            emit("// action:prompt_button {}|FLASH_FIRMWARE TYPE={} VERSION={}|primary"
                 .format(label, kind, version))
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
    if kind not in KINDS or machine not in MACHINES:
        raise RuntimeError("Unsupported firmware type or printer model.")
    if kind == "RECOVERY":
        if version not in ("dry", "full"):
            raise RuntimeError("Recovery VERSION must be dry or full.")
    elif version != "latest" or kind != "FORGEX":
        if not VERSION_RE.fullmatch(version):
            raise RuntimeError("Invalid firmware version.")

    emit("// Looking up {} {} for {}...".format(kind, version, machine))
    asset, name = selected_image(kind, version, machine)
    size = int(asset.get("size") or 0)
    url = asset.get("browser_download_url") or ""
    digest = asset_sha256(asset)
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
        free = shutil.disk_usage(DOWNLOAD_DIR).free
        if free < size + RESERVE_BYTES:
            raise RuntimeError("Not enough free space on /data for firmware download.")
        if os.path.exists(partial):
            os.unlink(partial)
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
    if len(argv) == 4 and argv[0] == "list":
        _, kind, machine, page = argv
        if kind not in KINDS or machine not in MACHINES:
            raise RuntimeError("Invalid firmware type or printer model.")
        if not page.isdigit():
            raise RuntimeError("Invalid list page.")
        show_catalog(kind, machine, int(page))
    elif len(argv) == 3:
        prepare_download(*argv)
    else:
        raise RuntimeError("Usage: zflash.py TYPE VERSION MACHINE | list TYPE MACHINE PAGE")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as error:
        emit("!! {}".format(str(error) or error.__class__.__name__))
        sys.exit(1)
