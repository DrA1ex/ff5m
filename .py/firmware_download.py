#!/usr/bin/env python3

## Shared download and archive helpers for firmware images.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

import hashlib
import json
import tarfile
import urllib.request


RESERVE_BYTES = 16 * 1024 * 1024
USER_AGENT = "Forge-X-firmware-updater"


def emit(line):
    print(line, flush=True)


def emit_progress(percent, message):
    emit("@@PROGRESS|{}|{}".format(percent, message))


def request_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download(url, destination, expected_size, expected_sha256=None):
    """Download in bounded chunks, verifying size and optional SHA-256 inline."""
    if expected_size <= 0:
        raise RuntimeError("Invalid firmware image size.")

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256() if expected_sha256 else None
    downloaded = 0
    next_progress = 5

    with urllib.request.urlopen(request, timeout=60) as response, \
            open(destination, "wb") as output:
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if digest is not None:
                digest.update(chunk)
            percent = min(100, downloaded * 100 // expected_size)
            if percent >= next_progress:
                emit_progress(percent, "Downloading {:.1f}/{:.1f} MiB".format(
                    downloaded / 1048576.0, expected_size / 1048576.0))
                next_progress = (percent // 5 + 1) * 5

    if downloaded != expected_size:
        raise RuntimeError(
            "Downloaded firmware size is incorrect: {} of {} bytes."
            .format(downloaded, expected_size))
    if digest is not None and digest.hexdigest().lower() != expected_sha256.lower():
        raise RuntimeError("Downloaded firmware SHA-256 mismatch.")


def archive_names(path, mode="r:*"):
    """Read just archive entries, rejecting paths and links outside the archive."""
    names = set()
    with tarfile.open(path, mode=mode) as archive:
        for member in archive:
            name = member.name
            if name.startswith("/") or ".." in name.split("/"):
                raise RuntimeError("Firmware archive contains an unsafe path.")
            if member.issym() or member.islnk():
                target = member.linkname
                if target.startswith("/") or ".." in target.split("/"):
                    raise RuntimeError("Firmware archive contains an unsafe link.")
            names.add(name.lstrip("./"))
    return names
