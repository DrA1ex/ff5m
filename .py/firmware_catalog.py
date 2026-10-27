#!/usr/bin/env python3

## Pure catalog helpers shared by Forge-X and early stock Recovery.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
## This file may be distributed under the terms of the GNU GPLv3 license.

import re
import urllib.parse


FORGEX_API = "https://api.github.com/repos/DrA1ex/ff5m/releases"
DOWNLOAD_DIR = "/data/forge-x-recovery"
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-._][A-Za-z0-9]+)*$")
FORGEX_RE = re.compile(r"^Adventurer5M(?:Pro)?-ForgeX-[A-Za-z0-9._-]+\.(?:tgz|tar\.xz)$")
GITHUB_RELEASE_ASSET_URL = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/]+)$")

# Stock and Factory images are the assets of these archive releases. Their
# names carry the model and version, so publishing an asset lists it.
ARCHIVE_RELEASES = {"STOCK": "Stock", "FACTORY": "factory"}
ARCHIVE_RE = re.compile(r"^(Adventurer5M(?:Pro)?)-([0-9]+(?:\.[0-9]+){1,3})(-Factory)?\.tgz$")

# Recovery images are legacy assets of release 1.2.0. "id" is the stable
# VERSION accepted by FLASH_FIRMWARE; "version" is the displayed label.
RECOVERY_DRY_IMAGE = {
    "id": "dry",
    "version": "Recovery dry run",
    "name": "Adventurer5M-3.x.x-2.2.3-recovery-dry.tgz",
    "url": "https://github.com/DrA1ex/ff5m/releases/download/1.2.0/"
           "Adventurer5M-3.x.x-2.2.3-recovery-dry.tgz",
}

FULL_RECOVERY_IMAGE = {
    "id": "full",
    "version": "Full file recovery",
    "name": "Adventurer5M-3.x.x-2.2.3-recovery-full.tgz",
    "url": "https://github.com/DrA1ex/ff5m/releases/download/1.2.0/"
           "Adventurer5M-3.x.x-2.2.3-recovery-full.tgz",
}

RECOVERY_IMAGES = (RECOVERY_DRY_IMAGE, FULL_RECOVERY_IMAGE)


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
    # Older release assets have no published digest; size and archive
    # validation still apply to those images.
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest[len("sha256:"):].lower()
    return None


def archive_api(kind):
    return FORGEX_API + "/tags/" + ARCHIVE_RELEASES[kind]


def archive_catalog(machine, *releases):
    """This model's images in Stock/Factory archive releases, newest first."""
    result = []
    for asset in (asset for release in releases for asset in release.get("assets") or []):
        match = ARCHIVE_RE.fullmatch(str(asset.get("name") or ""))
        if not match or match.group(1) != machine:
            continue
        version = match.group(2)
        result.append({
            "id": version,
            "version": version + (" Factory" if match.group(3) else ""),
            "name": asset["name"],
            "url": asset.get("browser_download_url"),
            "size": int(asset.get("size") or 0),
            "sha256": asset_sha256(asset),
        })
    # The sort is stable: an equal version keeps the order of the releases.
    result.sort(key=lambda entry: [int(part) for part in entry["id"].split(".")], reverse=True)
    return result


def static_catalog(machine, entries):
    # Recovery images share one asset; the saved name still identifies the model.
    return [dict(entry, name=model_name(machine, entry["name"])) for entry in entries]


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


def forgex_catalog(machine, releases, allow_xz=False):
    result = []
    seen = set()
    for release in releases:
        selected = forgex_asset(release, machine, allow_xz)
        if selected is None:
            continue

        asset, destination_name = selected
        if destination_name in seen:
            continue

        seen.add(destination_name)
        tag = str(release["tag_name"])
        result.append({
            "id": tag,
            "version": tag + (" beta" if release.get("prerelease") else ""),
            "name": destination_name,
            "url": asset.get("browser_download_url"),
            "size": int(asset.get("size") or 0),
            "sha256": asset_sha256(asset),
        })

    return result


def release_asset_location(url):
    """Return (release API URL, asset name) for a GitHub release download URL."""
    match = GITHUB_RELEASE_ASSET_URL.match(str(url or ""))
    if not match:
        return None
    owner, repository, tag, name = match.groups()
    return ("https://api.github.com/repos/{}/{}/releases/tags/{}"
            .format(owner, repository, urllib.parse.quote(tag)), name)


def published_metadata(release, name):
    """Return the published size and SHA-256 of one release asset."""
    if not isinstance(release, dict):
        return None
    for asset in release.get("assets") or []:
        if str(asset.get("name") or "") == name:
            return {"size": int(asset.get("size") or 0), "sha256": asset_sha256(asset)}
    return None
