#!/usr/bin/env python3
## Check the Klipper replacement files against the public history repository.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Verify .py/klipper/patches/** against docs/klipper-ad5m-manifest.json.

Without options the script checks, offline, that every file in the overlay is
listed in the manifest and has the recorded SHA-256 (and that nothing else is
listed). With --fork it also clones the history repository, checks out the
recorded commit and compares every Python file byte for byte.

    python3 tests/verify_klipper_fork.py
    python3 tests/verify_klipper_fork.py --fork https://github.com/DrA1ex/klipper-ad5m
    python3 tests/verify_klipper_fork.py --update --commit <sha>
"""

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).parents[1]
PATCHES = ROOT / ".py" / "klipper" / "patches"
MANIFEST = ROOT / "docs" / "klipper-ad5m-manifest.json"
FORK_URL = "https://github.com/DrA1ex/klipper-ad5m"
BASE_COMMIT = "e02b725602067a2cd098a62be9a4bb10fc74a9bd"
STOCK_COMMIT = "182e96ab8394201923e095ede450f2468f293d16"
SKIPPED_PARTS = {"__pycache__"}


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def overlay_files():
    """Return {path relative to patches/: path} for the shipped overlay."""
    files = {}
    for path in sorted(PATCHES.rglob("*")):
        if not path.is_file() or SKIPPED_PARTS & set(path.parts):
            continue
        if path.suffix not in (".py", ".so"):
            continue
        files[path.relative_to(PATCHES).as_posix()] = path
    return files


def build_manifest(commit):
    sources, binaries = {}, {}
    for rel, path in overlay_files().items():
        entry = {"ff5m": path.relative_to(ROOT).as_posix(),
                 "sha256": sha256(path)}
        if path.suffix == ".py":
            sources["klippy/" + rel] = entry
        else:
            binaries["klippy/" + rel] = entry
    return {
        "fork": FORK_URL,
        "base_commit": BASE_COMMIT,
        "stock_commit": STOCK_COMMIT,
        "commit": commit,
        "sources": sources,
        "binaries": binaries,
    }


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def check_manifest(manifest):
    """Return a list of problems between the overlay and the manifest."""
    problems = []
    listed = {}
    listed.update(manifest["sources"])
    listed.update(manifest["binaries"])
    shipped = {"klippy/" + rel: path for rel, path in overlay_files().items()}
    for name in sorted(set(shipped) - set(listed)):
        problems.append("not in the manifest: " + name)
    for name in sorted(set(listed) - set(shipped)):
        problems.append("in the manifest but not shipped: " + name)
    for name in sorted(set(shipped) & set(listed)):
        if sha256(shipped[name]) != listed[name]["sha256"]:
            problems.append("SHA-256 differs from the manifest: " + name)
    return problems


def check_fork(manifest, fork):
    """Clone the fork at the recorded commit and compare the sources."""
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = pathlib.Path(tmp) / "fork"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "--depth", "1",
                        fork, manifest["commit"]], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "FETCH_HEAD"],
                       check=True)
        for name, entry in sorted(manifest["sources"].items()):
            forked = repo / name
            if not forked.is_file():
                problems.append("missing in the fork: " + name)
            elif sha256(forked) != entry["sha256"]:
                problems.append("differs from the fork: " + name)
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--fork", help="URL or path of the history repository")
    parser.add_argument("--update", action="store_true",
                        help="rewrite the manifest from the current overlay")
    parser.add_argument("--commit", help="fork commit to record with --update")
    args = parser.parse_args()

    if args.update:
        if not args.commit:
            parser.error("--update needs --commit")
        MANIFEST.write_text(
            json.dumps(build_manifest(args.commit), indent=2, sort_keys=True)
            + "\n", encoding="utf-8")
        print("wrote", MANIFEST.relative_to(ROOT))
        return 0

    manifest = load_manifest()
    problems = check_manifest(manifest)
    if args.fork:
        problems += check_fork(manifest, args.fork)
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print("OK: %d Python files and %d binaries match the manifest" % (
            len(manifest["sources"]), len(manifest["binaries"])))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
