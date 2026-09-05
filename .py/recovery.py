#!/usr/bin/env python3

## Touch recovery menu for the early Forge-X boot path.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import argparse
import base64
import heapq
import http.server
import json
import os
import re
import select
import signal
import shutil
import socket
import socketserver
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.parse


TYPER = "/opt/config/mod/.bin/exec/typer"
CURL = os.environ.get("RECOVERY_CURL", "")
CURL_CACERT = os.environ.get("RECOVERY_CACERT", "")
XZ = "/usr/bin/xz"
SHA256SUM = "/usr/bin/sha256sum"
DRAW_PIPE = "/tmp/forge-x-recovery-draw"
EVENT_PIPE = "/tmp/forge-x-recovery-events"
TOUCH_DEVICE = "/dev/input/guppy"
DOWNLOAD_DIR = "/data/forge-x-recovery"
VERIFICATION_LOG = "/data/logFiles/verification.log"
ZBACKUP = "/opt/config/mod/.shell/commands/zbackup.sh"
FSCK = "/sbin/fsck"
RECOVERY_HTTP_PORT = 80
CLEANUP_LIMIT = 40
GITHUB_RELEASES_API = (
    "https://api.github.com/repos/DrA1ex/ff5m/releases?per_page=100")
USER_AGENT = "Forge-X-early-recovery"
RESERVE_BYTES = 16 * 1024 * 1024
MAX_FRAME_BYTES = 3584
VERIFICATION_RENDER_INTERVAL = 1.0

UI_WIDTH = 800
UI_HEIGHT = 480
UI_HEADER_HEIGHT = 64
UI_BACKGROUND = "030607"
UI_HEADER_BACKGROUND = "091820"
UI_ACCENT = "35d9e6"
UI_MUTED = "8ea6ad"
UI_TEXT = "ffffff"
UI_DANGER = "ff6b6b"
UI_LONG = "f6c344"
UI_BUTTON_BACKGROUND = "0d222b"
UI_BUTTON_DANGER_BACKGROUND = "2b1014"
UI_BUTTON_LONG_BACKGROUND = "2b2410"
UI_BUTTON_PRESSED_BACKGROUND = "243b46"
UI_HIGHLIGHT = "b47aff"
UI_FONT_TITLE = "JetBrainsMono 16pt"
UI_FONT_TITLE_BOLD = "JetBrainsMono Bold 16pt"
UI_FONT_TITLE_SMALL = "JetBrainsMono Bold 12pt"
UI_FONT_BODY = "JetBrainsMono 12pt"
UI_FONT_SMALL = "JetBrainsMono 8pt"
UI_FONT_BUTTON = "JetBrainsMono Bold 8pt"

# Shared screen content geometry. Interactive screen content uses one common
# horizontal inset so navigation does not shift between menu/list depths.
# Dialogs and modal action rows intentionally keep their own geometry.
UI_CONTENT_X = 30
UI_CONTENT_WIDTH = UI_WIDTH - UI_CONTENT_X * 2
UI_MAIN_COLUMN_GAP = 30
UI_MAIN_COLUMN_WIDTH = (UI_CONTENT_WIDTH - UI_MAIN_COLUMN_GAP) // 2
UI_MAIN_COLUMN_STEP = UI_MAIN_COLUMN_WIDTH + UI_MAIN_COLUMN_GAP

# Shared component vertical geometry. Screens using the same component type
# must not choose their own row positions or spacing.
UI_MENU_TOP = 78
UI_MENU_ROW_HEIGHT = 60
UI_MENU_ROW_STEP = 76
UI_LIST_TOP = 78
UI_LIST_ROW_HEIGHT = 62
UI_LIST_ROW_STEP = 78
UI_PAGER_Y = 405
UI_PAGER_BUTTON_WIDTH = 220
UI_PAGER_GAP = (UI_CONTENT_WIDTH - UI_PAGER_BUTTON_WIDTH * 3) // 2
UI_PAGER_STEP = UI_PAGER_BUTTON_WIDTH + UI_PAGER_GAP
VERIFICATION_PROGRESS = re.compile(
    r"^// Processed (\d+) / (\d+); errors: (\d+)\.$")

FORGE_X_ASSET = re.compile(
    r"^Adventurer5M(?:Pro)?-ForgeX-[A-Za-z0-9._-]+\.(?:tgz|tar\.xz)$")

GITHUB_RELEASE_ASSET_URL = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)$")


FACTORY_IMAGES = (
    {
        "version": "2.7.8 Factory",
        "name": "Adventurer5M-2.7.8-2.2.3-20241213-Factory.tgz",
        "url": "https://github.com/DrA1ex/ff5m/releases/download/1.2.0/"
               "Adventurer5M-2.7.8-2.2.3-20241213-Factory.tgz",
    },
    {
        "version": "3.1.3 Factory",
        "name": "Adventurer5M-3.1.3-2.2.3-20250107-Factory.tgz",
        "url": "https://github.com/DrA1ex/ff5m/releases/download/1.2.0/"
               "Adventurer5M-3.1.3-2.2.3-20250107-Factory.tgz",
    },
)

RECOVERY_DRY_IMAGE = {
    "version": "Recovery dry run",
    "name": "Adventurer5M-3.x.x-2.2.3-recovery-dry.tgz",
    "url": "https://github.com/DrA1ex/ff5m/releases/download/1.2.0/"
           "Adventurer5M-3.x.x-2.2.3-recovery-dry.tgz",
    "shared": True,
}

FULL_RECOVERY_IMAGE = {
    "version": "Full file recovery",
    "name": "Adventurer5M-3.x.x-2.2.3-recovery-full.tgz",
    "url": "https://github.com/DrA1ex/ff5m/releases/download/1.2.0/"
           "Adventurer5M-3.x.x-2.2.3-recovery-full.tgz",
    "shared": True,
}

RECOVERY_IMAGES = (RECOVERY_DRY_IMAGE, FULL_RECOVERY_IMAGE)

# Cleanup browser policy. These lists are the only place to adjust where the
# recovery cleanup feature may scan and what it must never touch.
CLEANUP_SCAN_ROOTS = ("/data", "/opt/config")
# Subtrees that are never scanned, so nothing inside them is ever offered.
CLEANUP_SEARCH_EXCLUDED = ("/opt/config/mod", "/data/.mod/.forge-x")
# Paths that are never deletion targets themselves. Scan roots are protected
# separately: only entries strictly inside a root are ever offered.
CLEANUP_DELETE_PROTECTED = ("/opt", "/root", "/data", "/data/.mod/.forge-x")


def quote(value):
    value = str(value).replace("\r", " ").replace("\n", " ")
    return '"{}"'.format(value.replace("\\", "\\\\").replace('"', '\\"'))


def model_name(machine, generic_name):
    if machine == "Adventurer5MPro":
        if generic_name.startswith("Adventurer5MPro-"):
            return generic_name
        if generic_name.startswith("Adventurer5M-"):
            return "Adventurer5MPro-" + generic_name[len("Adventurer5M-"):]

    if machine == "Adventurer5M" and generic_name.startswith("Adventurer5MPro-"):
        return "Adventurer5M-" + generic_name[len("Adventurer5MPro-"):]

    return generic_name


def static_catalog(machine, entries):
    result = []
    for source in entries:
        entry = dict(source)
        source_name = entry["name"]
        destination_name = model_name(machine, source_name)
        if machine == "Adventurer5MPro" and not entry.get("shared"):
            entry["url"] = entry["url"].replace(source_name, destination_name)

        entry.pop("shared", None)
        entry["name"] = destination_name
        result.append(entry)

    return result


def request_json(url):
    command = curl_command(
        "--header", "Accept: application/vnd.github+json", url)
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError(curl_error(result.stderr))

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("Server returned invalid JSON.") from error


def curl_command(*arguments):
    if not CURL or not os.path.isfile(CURL) or not os.access(CURL, os.X_OK):
        raise RuntimeError("Vendor HTTPS client is unavailable.")
    if not CURL_CACERT or not os.path.isfile(CURL_CACERT):
        raise RuntimeError("HTTPS certificate bundle is unavailable.")

    return [
        CURL,
        "--cacert", CURL_CACERT,
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--connect-timeout", "30",
        "--speed-limit", "1024",
        "--speed-time", "60",
        "--user-agent", USER_AGENT,
    ] + list(arguments)


def curl_error(stderr):
    detail = stderr.decode("utf-8", "replace").strip()
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("curl:"):
            return explained_curl_error(line[:240])

    if lines:
        return explained_curl_error(lines[0][:240])

    return "HTTPS request failed."


def explained_curl_error(line):
    # The printer has no RTC: right after flashing, or with a stale saved
    # clock, HTTPS fails its certificate date check before the background
    # NTP sync completes. Name that cause instead of a bare transport error.
    if "certificate is not yet valid" in line:
        return (
            "The system clock is not synchronized yet, so HTTPS certificate "
            "checks fail.\n"
            "Wait about a minute for the background time sync and retry, "
            "or reboot to the normal system to sync time first.\n"
            + line)

    return line


def asset_sha256(asset):
    # GitHub publishes release asset integrity as "sha256:<hex>"; assets
    # uploaded before that feature have no digest and skip the check.
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest[len("sha256:"):].lower()
    return None


def forge_x_catalog(machine, releases):
    result = []
    seen = set()
    for release in releases:
        if release.get("draft"):
            continue

        tag = str(release.get("tag_name") or "untagged")
        candidates = [
            asset for asset in release.get("assets", [])
            if FORGE_X_ASSET.fullmatch(str(asset.get("name") or ""))
        ]
        if not candidates:
            continue

        exact_prefix = machine + "-"
        asset = next(
            (candidate for candidate in candidates
             if candidate["name"].startswith(exact_prefix)),
            candidates[0],
        )
        destination_name = model_name(machine, asset["name"])
        if destination_name in seen:
            continue

        seen.add(destination_name)
        result.append({
            "version": tag + (" beta" if release.get("prerelease") else ""),
            "name": destination_name,
            "url": asset.get("browser_download_url"),
            "size": int(asset.get("size") or 0),
            "sha256": asset_sha256(asset),
        })

    return result


def release_asset_metadata(url):
    # Live server-side checksum source: the GitHub releases API publishes
    # each release asset's current size and SHA-256 digest. Deliberately
    # best-effort — every failure means "no published checksum", never a
    # blocked cache check or download.
    match = GITHUB_RELEASE_ASSET_URL.match(str(url or ""))
    if not match:
        return None

    owner, repository, tag, name = match.groups()
    try:
        release = request_json(
            "https://api.github.com/repos/{}/{}/releases/tags/{}"
            .format(owner, repository, urllib.parse.quote(tag)))
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None

    if not isinstance(release, dict):
        return None

    for asset in release.get("assets") or []:
        if str(asset.get("name") or "") == name:
            return {
                "size": int(asset.get("size") or 0),
                "sha256": asset_sha256(asset),
            }

    return None


def attach_published_checksums(entry):
    if entry.get("size") or entry.get("sha256"):
        return

    metadata = release_asset_metadata(entry.get("url"))
    if not metadata:
        return

    if metadata["size"]:
        entry["size"] = metadata["size"]
    if metadata["sha256"]:
        entry["sha256"] = metadata["sha256"]


def archive_members(path):
    if not str(path).endswith((".tar.xz", ".tar.xz.part")):
        with tarfile.open(path, mode="r:*") as archive:
            for member in archive:
                yield member
        return

    process = subprocess.Popen(
        [XZ, "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                yield member
    finally:
        process.stdout.close()
        stderr = process.stderr.read()
        status = process.wait()
        process.stderr.close()

    if status != 0:
        raise RuntimeError(
            stderr.decode("utf-8", "replace").strip()
            or "Unable to decompress firmware archive.")


def validate_archive(path):
    try:
        names = []
        for member in archive_members(path):
            name = member.name.lstrip("./")
            parts = [part for part in name.split("/") if part]
            if member.name.startswith("/") or ".." in parts:
                raise RuntimeError("Firmware archive contains an unsafe path.")

            names.append(name)
    except (OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        raise RuntimeError("Downloaded file is not a valid firmware archive.") from error

    entrypoints = {"forge-x-init", "forge-x-init.sh", "flashforge_init.sh"}
    if not entrypoints.intersection(names):
        raise RuntimeError("Firmware archive has no supported installer.")


def file_sha256(path):
    # The stock Python runtime is minimal (it lacks _lzma, for example), so
    # SHA-256 is computed by the printer's sha256sum binary.
    try:
        result = subprocess.run(
            [SHA256SUM, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Unable to compute SHA-256.") from error

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(
            "sha256sum failed: {}".format(detail[:200] or "unknown error"))

    fields = result.stdout.decode("utf-8", "replace").split()
    if not fields or len(fields[0]) != 64:
        raise RuntimeError("sha256sum returned an unexpected result.")
    return fields[0].lower()


def cached_image_matches(path, expected_sha256):
    # A saved image is reused only while it still validates as a firmware
    # archive and matches the checksum currently published for it; with no
    # published checksum the file is trusted by name alone.
    try:
        validate_archive(path)
    except (OSError, RuntimeError):
        return False

    if expected_sha256 and file_sha256(path) != expected_sha256.lower():
        return False

    return True


def format_bytes(value):
    value = int(value)
    if value >= 1024 ** 3:
        return "{:.1f} GB".format(value / float(1024 ** 3))
    if value >= 1024 ** 2:
        return "{:.1f} MB".format(value / float(1024 ** 2))
    if value >= 1024:
        return "{:.1f} KB".format(value / 1024.0)
    return "{} B".format(value)


def _regular_file_stat(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Unsupported non-regular file: {}".format(path))
    return info


def _remove_if_exists(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _terminate_process(process):
    if process is None or process.poll() is not None:
        return

    try:
        process.terminate()
        process.wait(timeout=1.0)
        return
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        pass

    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _terminate_process_group(process):
    # Archive creation may have tar/gzip children. Terminate the whole session so
    # a Recovery-side timeout cannot leave an orphan still writing a .part file.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.terminate()
        except OSError:
            return

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except OSError:
            pass

    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _run_recovery_archive(option, destination, timeout):
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    partial = destination + ".part"
    _remove_if_exists(partial)

    process = subprocess.Popen(
        ["/bin/bash", ZBACKUP, option, destination],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        try:
            process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        _remove_if_exists(partial)
        raise RuntimeError("Archive creation timed out.")

    if process.returncode != 0:
        _remove_if_exists(partial)
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        raise RuntimeError(lines[-1] if lines else "Archive creation failed.")

    _remove_if_exists(partial)
    _regular_file_stat(destination)
    return destination


def create_config_backup(destination_dir=None):
    # The backup contents and archive format are owned by zbackup.sh. Recovery
    # only selects an explicit output location and shares the resulting archive.
    destination_dir = destination_dir or DOWNLOAD_DIR
    destination = os.path.join(destination_dir, "backup.tar.gz")
    return _run_recovery_archive("--tar-backup-to", destination, 90)


def disk_usage():
    result = []
    for label, path in (("Root filesystem", "/"), ("Data", "/data")):
        values = os.statvfs(path)
        total = values.f_blocks * values.f_frsize
        free = values.f_bavail * values.f_frsize
        used = max(0, total - free)
        percent = int(round((used * 100.0 / total))) if total else 0
        result.append({
            "label": label, "path": path, "used": used,
            "total": total, "free": free, "percent": percent,
        })
    return result


def _decode_mount_field(value):
    return (value.replace("\\040", " ").replace("\\011", "\t")
            .replace("\\012", "\n").replace("\\134", "\\"))


def read_mounts(path="/proc/mounts"):
    mounts = []
    with open(path, "r", encoding="utf-8", errors="replace") as source:
        for line in source:
            fields = line.split()
            if len(fields) < 4:
                continue
            mounts.append({
                "source": _decode_mount_field(fields[0]),
                "target": _decode_mount_field(fields[1]),
                "type": fields[2],
                "options": fields[3].split(","),
            })
    return mounts


def filesystem_check_command(device, fsck=None):
    tool = fsck or (FSCK if os.path.isfile(FSCK) else shutil.which("fsck"))
    if not tool:
        raise RuntimeError("Filesystem checker is unavailable.")
    return [tool, "-n", device]


def filesystem_check_device(target, source):
    if (target == "/" and source == "/dev/root"
            and os.path.exists("/dev/mmcblk0p6")):
        return "/dev/mmcblk0p6"
    return source


def check_filesystems(mounts_path="/proc/mounts"):
    mounts = read_mounts(mounts_path)
    by_target = {item["target"]: item for item in mounts}
    results = []
    for label, target in (("Root filesystem", "/"), ("Data", "/data")):
        mounted = by_target.get(target)
        if mounted is None:
            results.append({
                "label": label, "status": "unavailable",
                "detail": "Mount information is unavailable.",
            })
            continue
        device = filesystem_check_device(target, mounted["source"])
        if not device.startswith("/dev/"):
            results.append({
                "label": label, "status": "unavailable",
                "detail": "{} ({}) has no block-device checker."
                          .format(device, mounted["type"]),
            })
            continue
        try:
            command = filesystem_check_command(device)
            completed = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=45, check=False)
            lines = [line.strip() for line in completed.stdout.splitlines()
                     if line.strip()]
            detail = " / ".join(lines[:3]) or "No checker output."
            results.append({
                "label": label,
                "status": "ok" if completed.returncode == 0 else "problems",
                "code": completed.returncode,
                "detail": detail,
                "mounted": True,
                "device": device,
            })
        except subprocess.TimeoutExpired:
            results.append({
                "label": label, "status": "timeout", "device": device,
                "mounted": True, "detail": "Read-only check timed out.",
            })
        except Exception as error:
            results.append({
                "label": label, "status": "error", "device": device,
                "mounted": True, "detail": str(error),
            })
    return results


def _cleanup_at_or_within(path, root):
    path = os.path.normpath(path)
    root = os.path.normpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _cleanup_scan_pruned(path):
    # Lexical check only: the walk builds paths by joining the scan root and
    # symlinked directories are pruned, so nothing needs to be resolved here.
    return any(
        _cleanup_at_or_within(path, excluded)
        for excluded in CLEANUP_SEARCH_EXCLUDED)


def _cleanup_delete_allowed(path):
    # Fail closed: the resolved target must sit strictly inside a scan root,
    # stay outside every excluded subtree, and not be a protected path.
    path = os.path.realpath(path)
    if any(_cleanup_at_or_within(path, os.path.realpath(excluded))
           for excluded in CLEANUP_SEARCH_EXCLUDED):
        return False
    if any(path == os.path.realpath(protected)
           for protected in CLEANUP_DELETE_PROTECTED):
        return False
    return any(
        _cleanup_at_or_within(path, os.path.realpath(root))
        and path != os.path.realpath(root)
        for root in CLEANUP_SCAN_ROOTS)


def scan_cleanup_files(limit=CLEANUP_LIMIT):
    if limit <= 0:
        return []

    # Keep only the current top N candidates. File count may be unexpectedly
    # large on /data, so the scan must not grow memory with the filesystem.
    largest = []

    def offer(size, sequence, entry):
        candidate = (size, sequence, entry)
        if len(largest) < limit:
            heapq.heappush(largest, candidate)
        elif size > largest[0][0]:
            heapq.heapreplace(largest, candidate)

    sequence = 0
    for root in CLEANUP_SCAN_ROOTS:
        root = os.path.normpath(root)
        if not os.path.isdir(root):
            continue

        # Folder candidates carry the eligible size of their whole subtree,
        # which is only final once the root has been walked completely.
        directory_sizes = {}
        for directory, directories, files in os.walk(root, followlinks=False):
            # Hidden entries, excluded subtrees, and symlinked directories are
            # never scanned. Pruning /data/.mod also keeps the walk out of the
            # recovery chroot and its kernel mounts.
            directories[:] = sorted(
                name for name in directories
                if not name.startswith(".")
                and not os.path.islink(os.path.join(directory, name))
                and not _cleanup_scan_pruned(os.path.join(directory, name)))

            for name in files:
                if name.startswith("."):
                    continue

                path = os.path.join(directory, name)
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue

                offer(info.st_size, sequence, {
                    "path": path,
                    "root": root,
                    "directory": False,
                    "size": info.st_size,
                    "dev": info.st_dev,
                    "ino": info.st_ino,
                })
                sequence += 1

                current = directory
                while current != root:
                    directory_sizes[current] = (
                        directory_sizes.get(current, 0) + info.st_size)
                    current = os.path.dirname(current)

        for path, size in directory_sizes.items():
            if size <= 0:
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISDIR(info.st_mode):
                continue
            if any(os.path.normpath(path) == os.path.normpath(protected)
                   for protected in CLEANUP_DELETE_PROTECTED):
                continue

            offer(size, sequence, {
                "path": path,
                "root": root,
                "directory": True,
                "size": size,
                "dev": info.st_dev,
                "ino": info.st_ino,
            })
            sequence += 1

    result = [item[2] for item in largest]
    result.sort(key=lambda item: (-item["size"], item["path"]))
    return result


def _cleanup_refuse_active_mounts(path):
    try:
        mounts = read_mounts()
    except OSError as error:
        raise RuntimeError(
            "Unable to verify mounts for the cleanup target.") from error

    target = os.path.realpath(path)
    for mount in mounts:
        if _cleanup_at_or_within(os.path.realpath(mount["target"]), target):
            raise RuntimeError(
                "Cleanup target contains an active mount: {}."
                .format(mount["target"]))


def delete_cleanup_entry(entry):
    if not _cleanup_delete_allowed(entry["path"]):
        raise RuntimeError("Cleanup target is not an allowed deletion target.")

    path = entry["path"]
    parent = os.path.dirname(path)
    name = os.path.basename(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(parent, flags)
    try:
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if entry["directory"]:
            if not stat.S_ISDIR(current.st_mode):
                raise RuntimeError("Cleanup target is no longer a directory.")
        elif not stat.S_ISREG(current.st_mode):
            raise RuntimeError("Cleanup target is no longer a regular file.")

        if current.st_dev != entry["dev"] or current.st_ino != entry["ino"]:
            raise RuntimeError("Cleanup target changed after it was scanned.")

        if entry["directory"]:
            # rmtree never follows symlinks; an active mount inside the tree
            # must not be crossed.
            _cleanup_refuse_active_mounts(path)
            shutil.rmtree(path)
        else:
            os.unlink(name, dir_fd=descriptor)
    finally:
        os.close(descriptor)


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.download_path = ""
        self.download_identity = None
        self.active_requests = set()
        self.active_requests_lock = threading.Lock()

    def process_request_thread(self, request, client_address):
        with self.active_requests_lock:
            self.active_requests.add(request)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.active_requests_lock:
                self.active_requests.discard(request)

    def close_active_requests(self):
        with self.active_requests_lock:
            requests = list(self.active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                request.close()
            except OSError:
                pass


class _RecoveryHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "Forge-X-Recovery/1"
    sys_version = ""

    def log_message(self, _format, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        expected = "/" + urllib.parse.quote(
            os.path.basename(self.server.download_path))
        if parsed.query or parsed.fragment or parsed.path != expected:
            self.send_error(404)
            return
        try:
            source = open(self.server.download_path, "rb")
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino)
                    != self.server.download_identity):
                source.close()
                self.send_error(404)
                return
        except OSError:
            self.send_error(404)
            return

        try:
            self.connection.settimeout(1.0)
            with source:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(info.st_size))
                self.send_header(
                    "Content-Disposition", "attachment; filename=\"{}\"".format(
                        os.path.basename(self.server.download_path).replace('\"', "")))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                shutil.copyfileobj(source, self.wfile, 64 * 1024)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            # Navigation/STOP SHARING closes active sockets deliberately.
            return


class RecoveryFileServer:
    def __init__(self, host="0.0.0.0", port=RECOVERY_HTTP_PORT):
        self.host = host
        self.requested_port = port
        self.server = None
        self.thread = None

    @property
    def port(self):
        if self.server is None:
            return self.requested_port
        return self.server.server_address[1]

    def start_download(self, path):
        info = _regular_file_stat(path)
        self.stop()
        server = _ThreadingHTTPServer(
            (self.host, self.requested_port), _RecoveryHTTPRequestHandler)
        server.download_path = path
        server.download_identity = (info.st_dev, info.st_ino)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        self.server = server
        self.thread = thread

    def stop(self):
        server = self.server
        thread = self.thread
        self.server = None
        self.thread = None
        if server is not None:
            # Close accepted sockets before waiting for serve_forever to stop.
            # A client that stopped reading must not block Recovery navigation.
            server.close_active_requests()
            server.shutdown()
            server.close_active_requests()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)


class TyperSession:
    def __init__(self, binary=TYPER, draw_pipe=DRAW_PIPE,
                 event_pipe=EVENT_PIPE, touch_device=TOUCH_DEVICE):
        self.binary = binary
        self.draw_pipe = draw_pipe
        self.event_pipe = event_pipe
        self.touch_device = touch_device
        self.process = None
        self.draw_fd = None
        self.event_fd = None
        self.partial = ""

    def start(self):
        for path in (self.draw_pipe, self.event_pipe):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

            os.mkfifo(path, 0o666)

        self.event_fd = os.open(self.event_pipe, os.O_RDWR | os.O_NONBLOCK)
        self.process = subprocess.Popen([
            self.binary,
            "--double-buffered",
            "--touch-device", self.touch_device,
            "--event-pipe", self.event_pipe,
            "batch", "--pipe", self.draw_pipe,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        self.draw_fd = os.open(self.draw_pipe, os.O_RDWR | os.O_NONBLOCK)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Typer stopped during recovery initialization.")
            for line in self._read_lines(0.1):
                if line == "touch-device connected":
                    return

                if line == "touch-device unavailable":
                    raise RuntimeError("Recovery touch input is unavailable.")

        raise RuntimeError("Recovery touch input did not initialize.")

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1.0)

            self.process = None

        for descriptor in (self.draw_fd, self.event_fd):
            if descriptor is not None:
                os.close(descriptor)

        self.draw_fd = None
        self.event_fd = None

        for path in (self.draw_pipe, self.event_pipe):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def send(self, commands):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Recovery display stopped.")

        suffix_size = len("--batch flush\n--end\n".encode("utf-8"))
        chunks = []
        current = []
        current_size = suffix_size
        for command in commands:
            encoded_size = len((command + "\n").encode("utf-8"))
            if current and current_size + encoded_size > MAX_FRAME_BYTES:
                chunks.append(current)
                current = []
                current_size = suffix_size

            if current_size + encoded_size > MAX_FRAME_BYTES:
                raise RuntimeError("Recovery display command is too large.")

            current.append(command)
            current_size += encoded_size

        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks):
            suffix = ["--end", ""]
            if index == len(chunks) - 1:
                suffix = ["--batch flush", "--end", ""]

            payload = "\n".join(chunk + suffix).encode("utf-8")
            view = memoryview(payload)

            while view:
                written = os.write(self.draw_fd, view)
                if written <= 0:
                    raise RuntimeError("Recovery display pipe closed.")

                view = view[written:]

    def next_tap(self):
        while True:
            if self.process.poll() is not None:
                raise RuntimeError("Recovery display stopped.")

            for line in self._read_lines(0.5):
                if line.startswith("tap "):
                    return line[4:].strip()

    def _read_lines(self, timeout):
        ready, _, _ = select.select([self.event_fd], [], [], timeout)
        if not ready:
            return []

        try:
            data = os.read(self.event_fd, 8192)
        except BlockingIOError:
            return []

        text = self.partial + data.decode("utf-8", "replace")
        lines = text.split("\n")
        self.partial = lines.pop()
        return [line.strip() for line in lines if line.strip()]


class RecoveryView:
    def __init__(self, session=None):
        self.session = session or TyperSession()
        self.generation = 0
        self.buttons = {}

    def start(self):
        self.session.start()

    def close(self):
        self.session.close()

    def send(self, commands):
        self.session.send(commands)

    def begin_page(self, title, subtitle="", subtitle_detail="",
                   title_font=UI_FONT_TITLE):
        self.generation += 1
        self.buttons = {}
        title_width = 360 if subtitle or subtitle_detail else 740
        commands = [
            "--batch clear-hitboxes --layer base",
            "--batch clear-hitboxes --layer overlay",
            "--batch fill -p 0 0 -s {width} {height} -c {color}".format(
                width=UI_WIDTH, height=UI_HEIGHT, color=UI_BACKGROUND),
            "--batch fill -p 0 0 -s {width} {height} -c {color}".format(
                width=UI_WIDTH, height=UI_HEADER_HEIGHT,
                color=UI_HEADER_BACKGROUND),
            self.text(30, 31, title, color=UI_ACCENT, font=title_font,
                      h_align="left", max_width=title_width),
        ]
        if subtitle:
            y = 18 if subtitle_detail else 31
            commands.append(self.text(
                770, y, subtitle,
                color=UI_ACCENT if subtitle_detail else UI_MUTED,
                font=UI_FONT_SMALL, h_align="right", max_width=370))
        if subtitle_detail:
            commands.append(self.text(
                770, 46, subtitle_detail, color=UI_MUTED,
                font=UI_FONT_SMALL, h_align="right", max_width=370))

        return commands

    @staticmethod
    def text(x, y, value, color=UI_TEXT, font=UI_FONT_BODY,
             h_align="center", v_align="middle", max_width=None,
             max_height=None, wrap=False, truncate=True):
        command = [
            "--batch text",
            "-p {} {}".format(x, y),
            "-c {}".format(color),
            "-f {}".format(quote(font)),
            "-ha {}".format(h_align),
            "-va {}".format(v_align),
        ]
        if max_width is not None:
            command.append("--max-width {}".format(max_width))
        if max_height is not None:
            command.append("--max-height {}".format(max_height))
        if wrap:
            command.append("--wrap")
        if truncate:
            command.append("--truncate")
        command.append("-t {}".format(quote(value)))
        return " ".join(command)

    def button(self, action, x, y, width, height, label, danger=False,
               long=False, interactive=True, pressed=False):
        # Danger always wins over long-running. A destructive operation must
        # stay red even when it can also take a long time.
        if pressed:
            border = UI_TEXT
            background = UI_BUTTON_PRESSED_BACKGROUND
        elif danger:
            border = UI_DANGER
            background = UI_BUTTON_DANGER_BACKGROUND
        elif long:
            border = UI_LONG
            background = UI_BUTTON_LONG_BACKGROUND
        else:
            border = UI_ACCENT
            background = UI_BUTTON_BACKGROUND
        wire = "{}:{}".format(self.generation, action)
        hitbox = " --id {}".format(wire) if interactive else ""
        if interactive:
            self.buttons[action] = (x, y, width, height, label, danger, long)

        return (
            "--batch button -p {x} {y} -s {width} {height} "
            "--background {background} --border {border} --text-color {text_color} "
            "-lw 2 -f {font} --max-width {max_width} "
            "--truncate -t {label}{hitbox} --layer base"
        ).format(
            x=x, y=y, width=width, height=height,
            background=background, border=border, text_color=UI_TEXT,
            font=quote(UI_FONT_BUTTON), max_width=max(1, width - 28),
            label=quote(label), hitbox=hitbox)

    def button_feedback(self, action, pressed):
        spec = self.buttons.get(action)
        if spec is None:
            return False

        self.send([self.button(
            action, *spec, interactive=False, pressed=pressed)])
        return True

    def wait_action(self):
        while True:
            wire_action = self.session.next_tap()
            prefix, separator, action = wire_action.partition(":")
            if separator and prefix == str(self.generation):
                if self.button_feedback(action, True):
                    time.sleep(0.08)
                    self.button_feedback(action, False)
                return action

    def action_row(self, actions, y=360, height=76):
        count = len(actions)
        layouts = {
            1: ((245, 310),),
            2: ((55, 315), (430, 315)),
            3: ((30, 230), (285, 230), (540, 230)),
        }
        if count not in layouts:
            raise ValueError("Recovery action rows support one to three buttons.")

        commands = []
        for (x, width), item in zip(layouts[count], actions):
            action, label = item[0], item[1]
            danger = bool(item[2]) if len(item) > 2 else False
            long = bool(item[3]) if len(item) > 3 else False
            commands.append(self.button(
                action, x, y, width, height, label, danger, long))
        return commands

    def dialog(self, title, detail, actions):
        commands = self.begin_page(title)
        commands.append(self.text(
            400, 185, detail, max_width=700, max_height=190,
            wrap=True))
        commands.extend(self.action_row(actions))
        self.send(commands)
        return self.wait_action()

    def confirm(self, title, detail, confirm_label):
        action = self.dialog(title, detail, (
            ("confirm.cancel", "CANCEL"),
            ("confirm.accept", confirm_label, True),
        ))
        return action == "confirm.accept"

    def message(self, title, detail, extra=None):
        actions = [("message.back", "BACK")]
        if extra is not None:
            action, label = extra[0], extra[1]
            danger = bool(extra[2]) if len(extra) > 2 else False
            long = bool(extra[3]) if len(extra) > 3 else False
            actions.append((action, label, danger, long))
        return self.dialog(title, detail, actions)

    def menu(self, title, items, subtitle="", subtitle_detail=""):
        commands = self.begin_page(title, subtitle, subtitle_detail)
        for index, item in enumerate(items):
            action, label = item[0], item[1]
            danger = bool(item[2]) if len(item) > 2 else False
            long = bool(item[3]) if len(item) > 3 else False
            commands.append(self.button(
                action, UI_CONTENT_X, UI_MENU_TOP + index * UI_MENU_ROW_STEP,
                UI_CONTENT_WIDTH, UI_MENU_ROW_HEIGHT, label, danger, long))
        self.send(commands)
        return self.wait_action()

    def text_result(self, title, lines, subtitle=""):
        commands = self.begin_page(title, subtitle)
        for index, line in enumerate(list(lines)[:8]):
            commands.append(self.text(
                35, 92 + index * 38, line, font=UI_FONT_SMALL,
                h_align="left", max_width=730))
        commands.extend(self.action_row(
            (("result.back", "BACK"),), y=405, height=50))
        self.send(commands)
        return self.wait_action()

    def progress(self, title, detail, percent=None):
        commands = self.begin_page(title)
        commands.append(self.text(
            400, 205, detail, max_width=700, max_height=130, wrap=True))
        if percent is not None:
            percent = max(0, min(100, int(percent)))
            commands += [
                "--batch stroke -p 100 315 -s 600 38 -c {} -lw 2 -sd inner"
                .format(UI_ACCENT),
                "--batch fill -p 108 323 -s {} 22 -c {}".format(
                    percent * 584 // 100, UI_HIGHLIGHT),
                self.text(400, 380, "{}%".format(percent),
                          color=UI_ACCENT, truncate=False),
            ]

        self.send(commands)

    def verification_progress(self, messages, checked, total, failures):
        commands = self.begin_page("CHECKING SYSTEM FILES")
        commands.append(self.text(
            400, 92, "CHECKED: {} / {}   ERRORS: {}".format(
                checked, total or "?", failures),
            color=UI_DANGER if failures else UI_ACCENT,
            font=UI_FONT_TITLE_SMALL, max_width=740))
        for index, (line, is_error) in enumerate(messages[-7:]):
            commands.append(self.text(
                30, 132 + index * 43, line,
                color=UI_DANGER if is_error else UI_TEXT,
                font=UI_FONT_SMALL, h_align="left", max_width=740))

        self.send(commands)

    def network_progress(self, label, status):
        stages = {
            "STARTING": "STARTING",
            "STARTUP": "STARTING",
            "PREPARING": "PREPARING",
            "FINDING_NETWORK": "FINDING NETWORK",
            "ASSOCIATING": "ASSOCIATING",
            "HANDSHAKE": "AUTHENTICATING",
            "RETRYING": "RETRYING",
            "DHCP_WAIT": "GETTING ADDRESS",
            "NO_CARRIER": "WAITING FOR CABLE",
            "ONLINE": "CONNECTED",
        }
        progress = status.get("progress") or "STARTING"
        stage = stages.get(progress, progress.replace("_", " "))
        state = status.get("state") or "CONNECTING"
        network = status.get("ssid") or label
        header_detail = " / ".join(
            value for value in (network, status.get("ip")) if value)
        commands = self.begin_page(
            "NETWORK CONNECTION", state, header_detail,
            title_font=UI_FONT_TITLE_SMALL)
        commands.append(self.text(
            400, 145, stage, color=UI_ACCENT, font=UI_FONT_TITLE_BOLD,
            max_width=700))
        commands.append(self.text(
            400, 220, "NETWORK: " + network, max_width=700))
        attempt = status.get("attempt")
        if attempt:
            commands.append(self.text(
                400, 275, "ATTEMPT: " + attempt.replace("/", " / "),
                color=UI_HIGHLIGHT, max_width=700))
        self.send(commands)

    def paged_list_page(self, title, entries, label, page, prefix,
                        extra_action=None, items_long=False):
        # An extra action occupies the fourth list row. Row geometry itself is
        # identical for every paged list.
        page_size = 3 if extra_action is not None else 4
        page_count = max(1, (len(entries) + page_size - 1) // page_size)
        page = min(max(0, page), page_count - 1)
        commands = self.begin_page(
            title, "Page {} of {}".format(page + 1, page_count))
        start = page * page_size

        for row, entry in enumerate(entries[start:start + page_size]):
            commands.append(self.button(
                "{}.{}".format(prefix, start + row),
                UI_CONTENT_X, UI_LIST_TOP + row * UI_LIST_ROW_STEP,
                UI_CONTENT_WIDTH, UI_LIST_ROW_HEIGHT, label(entry),
                long=items_long))

        if extra_action is not None:
            action, action_label = extra_action[0], extra_action[1]
            danger = bool(extra_action[2]) if len(extra_action) > 2 else False
            long = bool(extra_action[3]) if len(extra_action) > 3 else False
            commands.append(self.button(
                action, UI_CONTENT_X, UI_LIST_TOP + 3 * UI_LIST_ROW_STEP,
                UI_CONTENT_WIDTH, UI_LIST_ROW_HEIGHT, action_label, danger, long))

        # Pager positions are stable even when PREVIOUS or NEXT is absent.
        commands.append(self.button(
            "{}.back".format(prefix), UI_CONTENT_X, UI_PAGER_Y,
            UI_PAGER_BUTTON_WIDTH, 50, "BACK"))
        if page > 0:
            commands.append(self.button(
                "{}.prev".format(prefix), UI_CONTENT_X + UI_PAGER_STEP,
                UI_PAGER_Y, UI_PAGER_BUTTON_WIDTH, 50, "PREVIOUS"))
        if page + 1 < page_count:
            commands.append(self.button(
                "{}.next".format(prefix), UI_CONTENT_X + UI_PAGER_STEP * 2,
                UI_PAGER_Y, UI_PAGER_BUTTON_WIDTH, 50, "NEXT"))

        self.send(commands)
        return self.wait_action(), page

    def choose(self, title, entries, label, items_long=False):
        page = 0
        while True:
            action, page = self.paged_list_page(
                title, entries, label, page, "choose", items_long=items_long)
            if action == "choose.back":
                return None
            if action == "choose.prev":
                page -= 1
                continue
            if action == "choose.next":
                page += 1
                continue
            if action.startswith("choose."):
                try:
                    return entries[int(action.split(".", 1)[1])]
                except (IndexError, ValueError):
                    continue


class RecoveryUI:
    def __init__(self, machine, action_file, session=None):
        self.machine = machine
        self.action_file = action_file
        self.view = RecoveryView(session)
        self.file_server = RecoveryFileServer()
        self.ssh_active = os.environ.get("RECOVERY_SSH_ACTIVE") == "1"
        self.notice = os.environ.get("RECOVERY_NOTICE", "").strip()

    def close(self):
        self.file_server.stop()
        self.view.close()

    def write_action(self, action):
        temporary = self.action_file + ".{}.tmp".format(os.getpid())
        with open(temporary, "w", encoding="utf-8") as output:
            output.write(action + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.action_file)

    def run(self):
        self.view.start()
        self.render_main()

        if self.notice:
            self.view.message("RECOVERY NOTICE", self.notice)
            self.render_main()

        while True:
            action = self.view.wait_action()
            if action == "main.boot":
                if self.boot_options_menu():
                    return

            elif action == "main.system":
                self.system_diagnostics_menu()

            elif action == "main.firmware":
                self.firmware_menu()

            elif action == "main.backup":
                self.backup_reset_menu()

            elif action == "main.network":
                self.network_menu()

            elif action == "main.reboot":
                if self.view.confirm(
                        "REBOOT PRINTER", "No firmware will be written.", "REBOOT"):
                    self.write_action("reboot")
                    return

            self.render_main()

    def render_main(self):
        commands = self.view.begin_page("FORGE-X RECOVERY", self.machine)
        items = (
            ("main.boot", "BOOT OPTIONS", False),
            ("main.system", "SYSTEM / DIAGNOSTICS", False),
            ("main.firmware", "FIRMWARE / RESTORE", False),
            ("main.backup", "BACKUP / RESET", False),
            ("main.network", "NETWORK / SSH", False),
            ("main.reboot", "REBOOT", True),
        )
        for index, (action, label, danger) in enumerate(items):
            x = UI_CONTENT_X + (index % 2) * UI_MAIN_COLUMN_STEP
            y = 88 + (index // 2) * 118
            commands.append(self.view.button(
                action, x, y, UI_MAIN_COLUMN_WIDTH, 88, label, danger))

        self.view.send(commands)

    def boot_options_menu(self):
        while True:
            action = self.view.menu("BOOT OPTIONS", (
                ("boot.soft", "STOCK + SSH"),
                ("boot.hard", "STOCK ONLY", True),
                ("boot.back", "BACK"),
            ))
            if action == "boot.back":
                return False
            if action == "boot.soft":
                if self.view.confirm(
                        "STOCK + SSH",
                        "Start stock firmware with Forge-X services off. "
                        "Recovery SSH remains available.",
                        "START STOCK + SSH"):
                    self.write_action("stock-soft")
                    return True
            elif action == "boot.hard":
                if self.view.confirm(
                        "STOCK ONLY",
                        "Start stock firmware with every Forge-X service, "
                        "including recovery SSH, disabled.",
                        "START STOCK ONLY"):
                    self.write_action("stock-hard")
                    return True

    def system_diagnostics_menu(self):
        while True:
            action = self.view.menu("SYSTEM / DIAGNOSTICS", (
                ("system.verify", "CHECK SYSTEM FILES", False, True),
                ("system.storage", "STORAGE"),
                ("system.diagnostics", "CREATE DIAGNOSTICS", False, True),
                ("system.back", "BACK"),
            ))
            if action == "system.back":
                return
            if action == "system.verify":
                self.verify_system()
            elif action == "system.storage":
                self.storage_menu()
            elif action == "system.diagnostics":
                self.create_diagnostics()

    def storage_menu(self):
        while True:
            action = self.view.menu("STORAGE", (
                ("storage.usage", "DISK USAGE"),
                ("storage.check", "CHECK FILESYSTEM", False, True),
                ("storage.cleanup", "LARGE FILES / CLEANUP", False, True),
                ("storage.back", "BACK"),
            ))
            if action == "storage.back":
                return
            if action == "storage.usage":
                self.show_disk_usage()
            elif action == "storage.check":
                self.show_filesystem_check()
            elif action == "storage.cleanup":
                self.large_files_cleanup()

    def share_download(self, path, title):
        status = self.network_status()
        ip = status.get("ip") or ""
        if not ip:
            self.view.message(
                "NETWORK REQUIRED",
                "Connect Ethernet or Wi-Fi before sharing this file. "
                "The generated file was kept in recovery storage.")
            return
        try:
            self.file_server.start_download(path)
            origin = "http://{}".format(ip)
            if self.file_server.port != 80:
                origin += ":{}".format(self.file_server.port)
            url = "{}/{}".format(
                origin, urllib.parse.quote(os.path.basename(path)))
            commands = self.view.begin_page(title)
            commands.append(self.view.text(
                400, 132, "Open on another device:", max_width=700))
            commands.append(self.view.text(
                400, 205, url, color=UI_ACCENT, font=UI_FONT_SMALL,
                max_width=740))
            commands.extend(self.view.action_row((
                ("share.stop", "STOP SHARING"),
            )))
            self.view.send(commands)
            self.view.wait_action()
        except Exception as error:
            self.view.message("SHARING FAILED", str(error))
        finally:
            self.file_server.stop()

    def show_disk_usage(self):
        try:
            values = disk_usage()
        except Exception as error:
            self.view.message("DISK USAGE FAILED", str(error))
            return
        lines = []
        for item in values:
            lines.extend([
                item["label"],
                "{} / {}   {}% used".format(
                    format_bytes(item["used"]), format_bytes(item["total"]),
                    item["percent"]),
                "Free: {}".format(format_bytes(item["free"])),
            ])
        self.view.text_result("DISK USAGE", lines)

    def show_filesystem_check(self):
        self.view.progress(
            "CHECK FILESYSTEM",
            "Running read-only filesystem checks. Mounted results are advisory.")
        try:
            results = check_filesystems()
        except Exception as error:
            self.view.message("FILESYSTEM CHECK FAILED", str(error))
            return
        lines = ["No repair was attempted (fsck -n)."]
        for item in results:
            lines.append("{}: {}".format(item["label"], item["status"].upper()))
            if item.get("device"):
                lines.append("Device: {}".format(item["device"]))
            lines.append(item.get("detail", "")[:180])
        self.view.text_result("FILESYSTEM CHECK", lines)

    def large_files_cleanup(self):
        while True:
            self.view.progress(
                "LARGE FILES / CLEANUP", "Scanning /data and /opt/config...")
            try:
                entries = scan_cleanup_files()
            except Exception as error:
                self.view.message("CLEANUP SCAN FAILED", str(error))
                return
            if not entries:
                self.view.message(
                    "NO CLEANUP TARGETS",
                    "No large files or folders were found in /data or /opt/config.")
                return

            def entry_label(item):
                root = item["root"].rstrip(os.sep)
                relative = item["path"][len(root):].lstrip(os.sep)
                if item["directory"]:
                    relative += "/"
                return "{}  {}".format(format_bytes(item["size"]), relative)

            selected = self.view.choose("LARGE FILES / CLEANUP", entries, entry_label)
            if selected is None:
                return
            noun = "folder" if selected["directory"] else "file"
            if not self.view.confirm(
                    "DELETE " + noun.upper(),
                    "Delete {} {} ({})?\n\nThis cannot be undone."
                    .format(noun, selected["path"], format_bytes(selected["size"])),
                    "DELETE"):
                continue
            try:
                delete_cleanup_entry(selected)
            except Exception as error:
                self.view.message("DELETE FAILED", str(error))
                continue
            self.view.message("DELETED", "Deleted {}.".format(selected["path"]))

    def firmware_menu(self):
        while True:
            action = self.view.menu("FIRMWARE / RESTORE", (
                ("firmware.forgex", "FORGE-X RELEASES"),
                ("firmware.factory", "FACTORY IMAGES"),
                ("firmware.recovery", "RECOVERY IMAGES"),
                ("firmware.downloaded", "DOWNLOADED IMAGES"),
                ("firmware.back", "BACK"),
            ))

            if action == "firmware.back":
                return

            if action == "firmware.forgex":
                self.choose_forgex_release()
            elif action == "firmware.factory":
                self.choose_static_image(FACTORY_IMAGES, "Factory images")
            elif action == "firmware.recovery":
                self.choose_static_image(RECOVERY_IMAGES, "Recovery images")
            elif action == "firmware.downloaded":
                self.downloaded_images_menu()

    def choose_forgex_release(self):
        self.view.progress("FORGE-X RELEASES", "Loading GitHub releases...")
        try:
            releases = request_json(GITHUB_RELEASES_API)
            entries = forge_x_catalog(self.machine, releases)
            if not entries:
                raise RuntimeError("No compatible Forge-X release images were found.")
        except Exception as error:
            self.view.message("RELEASE LIST FAILED", str(error))
            return

        entry = self.view.choose(
            "FORGE-X RELEASES", entries, lambda item: item["version"],
            items_long=True)
        if entry is not None:
            self.download_and_offer(entry)

    def choose_static_image(self, source, title):
        entries = static_catalog(self.machine, source)
        entry = self.view.choose(
            title.upper(), entries, lambda item: item["version"],
            items_long=True)
        if entry is not None:
            self.download_and_offer(entry)

    def downloaded_files(self):
        try:
            with os.scandir(DOWNLOAD_DIR) as entries:
                return sorted((
                    entry.path for entry in entries
                    if entry.is_file(follow_symlinks=False)
                    and entry.name.startswith("Adventurer5M")
                    and entry.name.endswith((
                        ".tgz", ".tar.xz", ".tgz.part", ".tar.xz.part"))),
                    reverse=True)
        except FileNotFoundError:
            return []

    def clear_downloaded_files(self):
        try:
            files = self.downloaded_files()
        except OSError as error:
            self.view.message("CLEANUP FAILED", str(error))
            return

        total = len(files)
        if not total:
            self.view.message(
                "STORAGE IS EMPTY",
                "There are no downloaded files to delete.")
            return

        noun = "file" if total == 1 else "files"
        if not self.view.confirm(
                "CLEAR DOWNLOADS",
                "Delete all {} downloaded firmware {}?".format(total, noun),
                "CLEAR DOWNLOADS"):
            return

        removed = 0
        failures = 0
        for path in files:
            try:
                os.unlink(path)
                removed += 1
            except FileNotFoundError:
                removed += 1
            except OSError:
                failures += 1

        if failures:
            failure_noun = "file" if failures == 1 else "files"
            self.view.message(
                "CLEANUP INCOMPLETE",
                "Deleted {} of {} files. {} {} could not be removed."
                .format(removed, total, failures, failure_noun))
            return

        self.view.message(
            "DOWNLOADS CLEARED",
            "Deleted {} downloaded {}.".format(total, noun))

    def downloaded_images_menu(self):
        page = 0
        while True:
            files = self.downloaded_files()
            prefix = self.machine + "-"
            images = [
                path for path in files
                if os.path.basename(path).startswith(prefix)
                and not path.endswith(".part")
            ]
            action, page = self.view.paged_list_page(
                "DOWNLOADED IMAGES", images, os.path.basename, page,
                "downloaded",
                ("downloaded.clear", "CLEAR DOWNLOADS", True))
            if action == "downloaded.back":
                return
            if action == "downloaded.prev":
                page -= 1
                continue
            if action == "downloaded.next":
                page += 1
                continue
            if action == "downloaded.clear":
                self.clear_downloaded_files()
                continue
            if action.startswith("downloaded."):
                try:
                    selected = images[int(action.split(".", 1)[1])]
                except (IndexError, ValueError):
                    continue
                self.offer_flash(selected)

    def download_and_offer(self, entry):
        self.view.progress("PREPARING IMAGE", "Checking local image and download...")
        try:
            path = self.download(entry)
        except Exception as error:
            self.view.message("DOWNLOAD FAILED", str(error))
            return

        self.offer_flash(path)

    def download(self, entry):
        if not entry.get("url"):
            raise RuntimeError("Release asset has no download URL.")

        attach_published_checksums(entry)
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        destination = os.path.join(DOWNLOAD_DIR, entry["name"])
        partial = destination + ".part"
        expected_size = int(entry.get("size") or 0)
        expected_sha256 = entry.get("sha256")

        if os.path.exists(destination):
            self.view.progress("VERIFYING IMAGE", "Checking the saved image...")
            if cached_image_matches(destination, expected_sha256):
                return destination

        downloaded = 0
        process = None
        try:
            free = shutil.disk_usage(DOWNLOAD_DIR).free
            if expected_size > 0 and free < expected_size + RESERVE_BYTES:
                raise RuntimeError(
                    "Not enough /data space: {:.1f} MiB required, {:.1f} MiB free."
                    .format((expected_size + RESERVE_BYTES) / 1048576.0,
                            free / 1048576.0))

            self.view.progress(
                "DOWNLOADING IMAGE", "Connecting to the download server...")
            process = subprocess.Popen(
                curl_command("--output", partial, entry["url"]),
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            last_reported = -5
            while process.poll() is None:
                try:
                    downloaded = os.path.getsize(partial)
                except FileNotFoundError:
                    downloaded = 0

                if downloaded + RESERVE_BYTES > free:
                    raise RuntimeError("Not enough /data space for this image.")

                if expected_size > 0:
                    percent = min(100, downloaded * 100 // expected_size)
                    if percent >= last_reported + 5:
                        self.view.progress(
                            "DOWNLOADING IMAGE",
                            "{:.1f} / {:.1f} MiB".format(
                                downloaded / 1048576.0,
                                expected_size / 1048576.0),
                            percent)
                        last_reported = percent
                elif downloaded > 0:
                    self.view.progress(
                        "DOWNLOADING IMAGE",
                        "{:.1f} MiB downloaded".format(downloaded / 1048576.0))

                time.sleep(0.25)

            stderr = process.stderr.read()
            process.stderr.close()
            if process.returncode != 0:
                raise RuntimeError(curl_error(stderr))

            downloaded = os.path.getsize(partial)
            if expected_size > 0 and downloaded != expected_size:
                raise RuntimeError(
                    "Downloaded size is incorrect: {} of {} bytes."
                    .format(downloaded, expected_size))

            if expected_sha256 and file_sha256(partial) != expected_sha256.lower():
                raise RuntimeError(
                    "Downloaded image SHA-256 does not match the published value.")

            self.view.progress("VERIFYING IMAGE", "Checking firmware archive...")
            validate_archive(partial)
            os.replace(partial, destination)

            if hasattr(os, "sync"):
                os.sync()

            return destination
        except Exception:
            _terminate_process(process)
            _remove_if_exists(partial)
            raise
        finally:
            if (process is not None and process.stderr is not None
                    and not process.stderr.closed):
                process.stderr.close()

    def offer_flash(self, path):
        name = os.path.basename(path)
        if not self.view.confirm(
                "FLASH FIRMWARE IMAGE",
                "{}\n\nThe printer must stay powered until the installer finishes."
                .format(name),
                "CONTINUE"):
            return

        if not self.view.confirm(
                "FINAL CONFIRMATION",
                "Start writing this image now? This operation cannot be cancelled safely.",
                "FLASH NOW"):
            return

        self.view.progress(
            "PREPARING FIRMWARE", "Stopping recovery services...")
        self.write_action("flash:" + path)
        raise SystemExit(0)

    def verify_system(self):
        command = ["/opt/config/mod/.shell/commands/zcheck.sh", "verify-plain"]
        os.makedirs(os.path.dirname(VERIFICATION_LOG), exist_ok=True)
        last_line = "Reading checksum list..."
        messages = [(last_line, False)]
        checked = 0
        total = 0
        failures = 0
        self.view.verification_progress(messages, checked, total, failures)
        last_rendered = (tuple(messages), checked, total, failures)
        last_render_at = time.monotonic()
        process = None
        try:
            with open(VERIFICATION_LOG, "w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue

                    log.write(line + "\n")
                    log.flush()
                    last_line = line.lstrip("@?!/ ")
                    progress = VERIFICATION_PROGRESS.fullmatch(line)
                    if progress:
                        checked, total, failures = map(int, progress.groups())
                    else:
                        messages.append((last_line, line.startswith("@@")))
                        messages = messages[-7:]

                    snapshot = (tuple(messages), checked, total, failures)
                    now = time.monotonic()
                    if (snapshot != last_rendered
                            and now - last_render_at
                            >= VERIFICATION_RENDER_INTERVAL):
                        self.view.verification_progress(
                            messages, checked, total, failures)
                        last_rendered = snapshot
                        last_render_at = now

                status = process.wait()
        finally:
            _terminate_process(process)
            if (process is not None and process.stdout is not None
                    and not process.stdout.closed):
                process.stdout.close()

        snapshot = (tuple(messages), checked, total, failures)
        if snapshot != last_rendered:
            remaining = VERIFICATION_RENDER_INTERVAL - (
                time.monotonic() - last_render_at)
            if remaining > 0:
                time.sleep(remaining)

            self.view.verification_progress(messages, checked, total, failures)

        if status == 0:
            self.view.message("SYSTEM FILES OK", last_line)
            return

        action = self.view.message(
            "SYSTEM FILE PROBLEMS", last_line,
            ("message.recovery", "DOWNLOAD RECOVERY", False, True))
        if action == "message.recovery":
            full = static_catalog(self.machine, (FULL_RECOVERY_IMAGE,))[0]
            self.download_and_offer(full)

    def create_diagnostics(self):
        self.view.progress("CREATE DIAGNOSTICS", "Collecting recovery diagnostics...")
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        destination = os.path.join(DOWNLOAD_DIR, "debug.tar.gz")
        try:
            _run_recovery_archive("--tar-debug-to", destination, 90)
        except Exception as error:
            self.view.message("DIAGNOSTICS FAILED", str(error))
            return
        self.share_download(destination, "DIAGNOSTICS READY")

    def backup_reset_menu(self):
        while True:
            action = self.view.menu("BACKUP / RESET", (
                ("backup.create", "CREATE CONFIG BACKUP", False, True),
                ("backup.reset", "RESET CONFIGURATION", True),
                ("backup.uninstall", "UNINSTALL FORGE-X", True),
                ("backup.back", "BACK"),
            ))
            if action == "backup.back":
                return
            if action == "backup.create":
                self.create_config_backup_ui()
            elif action == "backup.reset":
                if self.reset_configuration_confirmation():
                    return
            elif action == "backup.uninstall":
                self.uninstall_menu()

    def uninstall_menu(self):
        while True:
            action = self.view.menu("UNINSTALL FORGE-X", (
                ("uninstall.soft", "SOFT UNINSTALL", True),
                ("uninstall.hard", "HARD UNINSTALL", True),
                ("uninstall.back", "BACK"),
            ))
            if action == "uninstall.back":
                return
            if action == "uninstall.soft":
                if self.view.confirm(
                        "SOFT UNINSTALL",
                        "Remove Forge-X while keeping root / SSH access and "
                        "Forge-X data that can be reused later.",
                        "UNINSTALL"):
                    self.write_action("uninstall-soft")
                    raise SystemExit(0)
            elif action == "uninstall.hard":
                if self.view.confirm(
                        "HARD UNINSTALL",
                        "Remove Forge-X completely, including Forge-X data and "
                        "root / SSH access.",
                        "UNINSTALL"):
                    self.write_action("uninstall")
                    raise SystemExit(0)

    def reset_configuration_confirmation(self):
        detail = (
            "Restore all Forge-X settings and the stock printer "
            "configuration for {}. Create and download a backup first. "
            "Print files are not deleted.".format(self.machine))
        while True:
            action = self.view.dialog("RESET CONFIGURATION", detail, (
                ("reset.back", "BACK"),
                ("reset.backup", "CREATE BACKUP"),
                ("reset.accept", "RESET", True),
            ))
            if action == "reset.back":
                return False
            if action == "reset.backup":
                self.create_config_backup_ui()
                continue
            if action == "reset.accept":
                self.write_action("reset-config")
                raise SystemExit(0)

    def create_config_backup_ui(self):
        self.view.progress("CREATE CONFIG BACKUP", "Creating the normal Forge-X user backup...")
        try:
            path = create_config_backup()
        except Exception as error:
            self.view.message("BACKUP FAILED", str(error))
            return
        self.share_download(path, "BACKUP READY")

    def netd_request(self, command, timeout=10.0):
        deadline = time.monotonic() + timeout
        lines = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect("/run/netd.sock")
            client.sendall((command + "\n").encode("utf-8"))
            partial = ""
            while time.monotonic() < deadline:
                client.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    data = client.recv(8192)
                except socket.timeout:
                    break

                if not data:
                    break

                text = partial + data.decode("utf-8", "replace")
                complete = text.split("\n")
                partial = complete.pop()
                for line in complete:
                    line = line.strip()
                    if not line:
                        continue

                    lines.append(line)
                    if line == "OK" or line.startswith("OK "):
                        return lines

                    if line.startswith("ERR "):
                        raise RuntimeError(line[4:])

        return lines

    @staticmethod
    def netd_send(command, timeout=5.0):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect("/run/netd.sock")
            client.sendall((command + "\n").encode("utf-8"))

    @staticmethod
    def parse_status(lines):
        status = {}
        for line in lines:
            key, separator, value = line.partition("=")
            if not separator:
                continue

            key = key.lower()
            if key == "ssid":
                try:
                    value = base64.b64decode(value, validate=True).decode(
                        "utf-8", "replace")
                except Exception:
                    value = ""

            status[key] = value

        return status

    def network_status(self):
        try:
            return self.parse_status(self.netd_request("GET", 3.0))
        except Exception:
            return {"state": "UNAVAILABLE", "ip": "", "ssid": ""}

    def network_menu(self):
        while True:
            status = self.network_status()
            state = status.get("state", "UNKNOWN")
            detail = " / ".join(
                value for value in (status.get("ssid"), status.get("ip"))
                if value)
            action = self.view.menu("NETWORK / SSH", (
                ("network.ethernet", "USE ETHERNET"),
                ("network.wifi", "SAVED WI-FI"),
                ("network.ssh",
                 "SSH ACTIVE" if self.ssh_active else "START SSH SHELL"),
                ("network.back", "BACK"),
            ), state, detail)

            if action == "network.back":
                return

            if action == "network.ethernet":
                self.connect_network("USE_ETHERNET", "Ethernet")
            elif action == "network.wifi":
                self.choose_saved_wifi()
            elif action == "network.ssh":
                if self.ssh_active:
                    self.view.message(
                        "SSH RECOVERY ACTIVE",
                        "Connect as root on port 22. IP: {}"
                        .format(status.get("ip") or "not assigned"))
                else:
                    self.write_action("ssh")
                    raise SystemExit(0)

    def choose_saved_wifi(self):
        self.view.progress("SCANNING WI-FI", "Looking for saved networks...")
        try:
            lines = self.netd_request("SCAN", 30.0)
            networks = []
            for line in lines:
                if not line.startswith("FREQUENCY=") or " SAVED=1 " not in line:
                    continue

                encoded = line.split(" NETWORK=", 1)[-1]
                try:
                    ssid = base64.b64decode(encoded, validate=True).decode(
                        "utf-8", "replace")
                except Exception:
                    continue

                if ssid and ssid not in networks:
                    networks.append(ssid)

            if not networks:
                raise RuntimeError(
                    "No saved Wi-Fi is visible. Use Ethernet or configure Wi-Fi in stock firmware.")
        except Exception as error:
            self.view.message("WI-FI SCAN FAILED", str(error))
            return

        ssid = self.view.choose("SAVED WI-FI", networks, lambda item: item)
        if ssid is None:
            return

        encoded = base64.b64encode(ssid.encode("utf-8")).decode("ascii")
        self.connect_network("CONNECT_WIFI ssid=" + encoded, ssid)

    def connect_network(self, command, label):
        expected_mode = "ETHERNET" if command == "USE_ETHERNET" else "WIFI"
        self.view.network_progress(label, {
            "mode": expected_mode, "state": "CONNECTING",
            "progress": "STARTING", "ssid": label if expected_mode == "WIFI" else "",
        })
        try:
            self.netd_send(command, 5.0)
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                status = self.network_status()
                state = status.get("state", "CONNECTING")
                mode = status.get("mode")
                if mode != expected_mode:
                    status = {
                        "mode": expected_mode, "state": "CONNECTING",
                        "progress": "PREPARING",
                        "ssid": label if expected_mode == "WIFI" else "",
                    }
                    state = "CONNECTING"

                self.view.network_progress(label, status)
                if mode == expected_mode and state == "CONNECTED" and status.get("ip"):
                    self.view.message(
                        "NETWORK CONNECTED", "{} / {}".format(label, status["ip"]))
                    return

                if mode == expected_mode and state in (
                        "WRONG_KEY", "AUTH_FAILED", "FAILED"):
                    raise RuntimeError(status.get("reason") or state)

                time.sleep(1.0)

            raise RuntimeError("Network connection timed out.")
        except Exception as error:
            self.view.message("NETWORK FAILED", str(error))


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Forge-X early recovery UI")
    parser.add_argument("--machine", choices=("Adventurer5M", "Adventurer5MPro"), required=True)
    parser.add_argument("--action-file", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    ui = RecoveryUI(args.machine, args.action_file)
    try:
        ui.run()
    finally:
        ui.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Recovery failed: {}".format(error), file=sys.stderr, flush=True)
        sys.exit(1)
