#!/bin/python3

## Download the latest full Forge-X firmware image to a mounted USB drive.
##
## Copyright (C) 2025, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


import glob
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request

API_URL = os.environ.get(
    "FORGE_X_FIRMWARE_API_URL",
    "https://api.github.com/repos/DrA1ex/ff5m/releases/latest",
)
ASSET_PATTERN = re.compile(r"Adventurer5M-ForgeX-[A-Za-z0-9._-]+\.tgz")
REQUIRED_FILES = {
    "flashforge_init.sh",
    "common.sh",
    "version.txt",
    "md5.list",
    "xz/data.tar.xz",
    "xz/buildroot.tar.xz",
    "xz/entware.tar.xz",
}
RESERVE_BYTES = 16 * 1024 * 1024


def emit(line):
    print(line, flush=True)


def emit_progress(percent, message):
    emit("@@PROGRESS|{}|{}".format(percent, message))


def prompt_ready(version, filename):
    emit("// action:prompt_end")
    emit("// action:prompt_begin Firmware update ready")
    emit("// action:prompt_text Forge-X {} was downloaded as {}.".format(version, filename))
    emit("// action:prompt_text Keep the USB drive inserted and reboot to start installation.")
    emit("// action:prompt_footer_button Later|RESPOND TYPE=command MSG=action:prompt_end|secondary")
    emit("// action:prompt_footer_button Reboot and install|REBOOT|primary")
    emit("// action:prompt_show")


def prompt_failed(message):
    emit("// action:prompt_end")
    emit("// action:prompt_begin Firmware update failed")
    emit("// action:prompt_text {}".format(message))
    emit("// action:prompt_footer_button Close|RESPOND TYPE=command MSG=action:prompt_end|error")
    emit("// action:prompt_show")


def request_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Forge-X-firmware-updater",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def select_asset(release):
    assets = [
        asset for asset in release.get("assets", [])
        if ASSET_PATTERN.fullmatch(asset.get("name", ""))
    ]
    if len(assets) != 1:
        raise RuntimeError(
            "The latest release does not contain exactly one Forge-X firmware image."
        )
    return assets[0]


def download(url, destination, expected_size):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Forge-X-firmware-updater"},
    )
    downloaded = 0
    progress_step = 5
    next_progress = progress_step

    with urllib.request.urlopen(request, timeout=60) as response, open(destination, "wb") as output:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)

            percent = min(100, downloaded * 100 // expected_size)
            if percent >= next_progress:
                emit_progress(percent, "Downloading {:.1f}/{:.1f} MiB".format(
                    downloaded / 1048576.0,
                    expected_size / 1048576.0,
                ))
                next_progress += progress_step

    if downloaded != expected_size:
        raise RuntimeError(
            "Downloaded firmware size is incorrect: {} of {} bytes.".format(
                downloaded, expected_size
            )
        )


def verify_posix_tar(path):
    # Release files intentionally use a .tgz name but contain an uncompressed
    # POSIX tar archive. Use r: so tarfile does not attempt gzip detection.
    try:
        with tarfile.open(path, mode="r:") as archive:
            names = {name.lstrip("./") for name in archive.getnames()}
    except tarfile.TarError as error:
        raise RuntimeError(
            "Downloaded file is not a valid POSIX tar firmware archive."
        ) from error

    missing = sorted(REQUIRED_FILES - names)
    if missing:
        raise RuntimeError(
            "Firmware archive is missing required files: {}".format(
                ", ".join(missing)
            )
        )


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("Adventurer5M", "Adventurer5MPro"):
        raise RuntimeError("Usage: zupdate.py Adventurer5M|Adventurer5MPro USB_PATH")

    model, mount_point = sys.argv[1:]
    emit("// Checking the latest Forge-X release...")

    existing = glob.glob(os.path.join(mount_point, "Adventurer5M*.tgz"))
    if existing:
        raise RuntimeError(
            "A firmware image already exists on the USB drive: {}".format(
                os.path.basename(existing[0])
            )
        )

    release = request_json(API_URL)
    asset = select_asset(release)
    version = str(release.get("tag_name") or "latest")
    asset_name = asset["name"]
    asset_size = int(asset.get("size") or 0)
    asset_url = asset.get("browser_download_url")
    if asset_size <= 0 or not asset_url:
        raise RuntimeError("GitHub returned invalid firmware metadata.")

    suffix = asset_name[len("Adventurer5M-"):]
    final_name = "{}-{}".format(model, suffix)
    final_path = os.path.join(mount_point, final_name)
    part_path = final_path + ".part"

    free_bytes = shutil.disk_usage(mount_point).free
    required_bytes = asset_size + RESERVE_BYTES
    if free_bytes < required_bytes:
        raise RuntimeError(
            "Not enough USB space: {:.1f} MiB required, {:.1f} MiB available.".format(
                required_bytes / 1048576.0,
                free_bytes / 1048576.0,
            )
        )

    try:
        if os.path.exists(part_path):
            os.remove(part_path)

        emit_progress(0, "Downloading Forge-X {}".format(version))
        emit("// Latest release: {}".format(version))
        download(asset_url, part_path, asset_size)
        emit("// Verifying POSIX tar firmware archive...")
        verify_posix_tar(part_path)
        os.replace(part_path, final_path)
        if hasattr(os, "sync"):
            os.sync()
    except Exception:
        if os.path.exists(part_path):
            os.remove(part_path)
        raise

    emit("// Firmware download completed: {}".format(final_name))
    prompt_ready(version, final_name)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) or error.__class__.__name__
        emit("!! {}".format(message))
        prompt_failed(message)
        sys.exit(1)
