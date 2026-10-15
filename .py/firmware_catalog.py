#!/usr/bin/env python3

## Pure catalog helpers shared by Forge-X and early stock Recovery.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

import re


FORGEX_API = "https://api.github.com/repos/DrA1ex/ff5m/releases"
DOWNLOAD_DIR = "/data/forge-x-recovery"
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-._][A-Za-z0-9]+)*$")
FORGEX_RE = re.compile(r"^Adventurer5M(?:Pro)?-ForgeX-[A-Za-z0-9._-]+\.(?:tgz|tar\.xz)$")
STOCK_RE = re.compile(
    r"^Adventurer5M(?:Pro)?-([0-9]+(?:\.[0-9]+){1,3})-[A-Za-z0-9._-]+\.tgz$")


def model_name(machine, generic_name):
    if machine == "Adventurer5MPro":
        if generic_name.startswith("Adventurer5MPro-"):
            return generic_name
        if generic_name.startswith("Adventurer5M-"):
            return "Adventurer5MPro-" + generic_name[len("Adventurer5M-"):]
    if machine == "Adventurer5M" and generic_name.startswith("Adventurer5MPro-"):
        return "Adventurer5M-" + generic_name[len("Adventurer5MPro-"):]
    return generic_name


def asset_sha256(asset):
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest[len("sha256:"):].lower()
    return None


def forgex_asset(release, machine, allow_xz=False):
    """Return (asset, model-specific filename) for a tagged Forge-X image."""
    if release.get("draft"):
        return None
    tag = str(release.get("tag_name") or "")
    if not VERSION_RE.fullmatch(tag):
        return None
    suffixes = ["-ForgeX-" + tag + ".tgz"]
    if allow_xz:
        suffixes.append("-ForgeX-" + tag + ".tar.xz")
    candidates = [
        asset for asset in release.get("assets", ())
        if FORGEX_RE.fullmatch(str(asset.get("name") or ""))
        and str(asset["name"]).endswith(tuple(suffixes))
    ]
    selected = next(
        (asset for asset in candidates if asset["name"].startswith(machine + "-")),
        None)
    if selected is None and machine == "Adventurer5MPro":
        selected = next(
            (asset for asset in candidates
             if asset["name"].startswith("Adventurer5M-")), None)
    if selected is None:
        return None
    return selected, model_name(machine, selected["name"])


def stock_assets(releases, machine):
    """Yield factory firmware assets published in Forge-X releases."""
    for release in releases:
        if release.get("draft"):
            continue
        for asset in release.get("assets", ()):
            name = str(asset.get("name") or "")
            match = STOCK_RE.fullmatch(name)
            if not match or not name.startswith(machine + "-"):
                continue
            if "5M2PRO" in name or "PRO25M" in name:
                continue
            yield match.group(1), asset, name
