## Explicit host-side runner for semantic checks of saved UI screenshots.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Explicit host-side runner for semantic checks of saved UI screenshots."""

import argparse
import hashlib
import json
import mimetypes
import os
import pathlib
import struct
import sys
import zlib

from . import openai_compatible as vision


SUPPORTED_SUFFIXES = frozenset((".bmp", ".png", ".jpg", ".jpeg", ".webp"))
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 32 * 1024 * 1024


def _mime_type(path):
    suffix = path.suffix.lower()
    if suffix == ".bmp":
        return "image/bmp"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    value = mimetypes.guess_type(str(path))[0]
    if not value or not value.startswith("image/"):
        raise ValueError("unsupported image type: %s" % path)
    return value


def _png_chunk(kind, payload):
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)
    )


def _bmp_to_png(data):
    """Convert the uncompressed 24/32-bit BMP emitted by Feather to PNG."""
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("invalid BMP header")
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40 or len(data) < 14 + dib_size:
        raise ValueError("unsupported BMP information header")
    width, signed_height = struct.unpack_from("<ii", data, 18)
    planes, bits_per_pixel = struct.unpack_from("<HH", data, 26)
    compression = struct.unpack_from("<I", data, 30)[0]
    height = abs(signed_height)
    if (
        width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS
        or planes != 1 or bits_per_pixel not in (24, 32)
        or compression != 0
    ):
        raise ValueError(
            "only bounded uncompressed 24/32-bit BMP images are supported")
    bytes_per_pixel = bits_per_pixel // 8
    stride = ((width * bits_per_pixel + 31) // 32) * 4
    if pixel_offset < 14 + dib_size or pixel_offset + stride * height > len(data):
        raise ValueError("truncated BMP pixel data")

    rows = bytearray()
    source_rows = (
        range(height) if signed_height < 0 else range(height - 1, -1, -1))
    for source_row in source_rows:
        start = pixel_offset + source_row * stride
        row = data[start:start + width * bytes_per_pixel]
        rows.append(0)  # PNG filter: None
        for offset in range(0, len(row), bytes_per_pixel):
            blue, green, red = row[offset:offset + 3]
            rows.extend((red, green, blue))
            if bytes_per_pixel == 4:
                rows.append(row[offset + 3])

    color_type = 6 if bytes_per_pixel == 4 else 2
    header = struct.pack(
        ">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows)))
        + _png_chunk(b"IEND", b"")
    )


def _request_image(data, mime_type):
    if mime_type == "image/bmp":
        return _bmp_to_png(data), "image/png"
    return data, mime_type


def _safe_artifact_file(directory, name):
    root = directory.resolve()
    candidate = (directory / name).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("manifest image escapes its artifact directory")
    return candidate


def discover_images(inputs):
    images = []
    seen = set()
    for raw in inputs:
        path = pathlib.Path(raw)
        if path.is_dir():
            manifest_path = path / "manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8"))
                if not isinstance(manifest, list):
                    raise ValueError("manifest.json must contain an array")
                candidates = []
                for item in manifest:
                    if not isinstance(item, dict) or not item.get("file"):
                        continue
                    candidate = _safe_artifact_file(
                        path, str(item["file"]))
                    candidates.append((candidate, {
                        "number": item.get("number"),
                        "label": item.get("label") or candidate.stem,
                        "page": item.get("page"),
                        "case_id": item.get("case_id"),
                        "semantic_page_id": item.get("semantic_page_id"),
                        "source": item.get("source"),
                    }))
            else:
                candidates = [
                    (candidate, {
                        "number": None, "label": candidate.stem, "page": None,
                    })
                    for candidate in sorted(path.iterdir())
                    if candidate.is_file()
                    and candidate.suffix.lower() in SUPPORTED_SUFFIXES
                ]
        else:
            candidates = [(path, {
                "number": None, "label": path.stem, "page": None,
            })]
        for candidate, context in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            if not candidate.is_file():
                raise ValueError("image does not exist: %s" % candidate)
            if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ValueError("unsupported image type: %s" % candidate)
            seen.add(resolved)
            images.append({
                "path": candidate,
                "context": context,
                "mime_type": _mime_type(candidate),
            })
    if not images:
        raise ValueError("no screenshot images found")
    return images


def run_checks(settings, images, evaluator=None):
    evaluator = evaluator or vision.VisualCheckEvaluator(settings)
    records = []
    for image in images:
        path = image["path"]
        size = path.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise ValueError(
                "image exceeds the %d-byte limit: %s" %
                (MAX_IMAGE_BYTES, path))
        data = path.read_bytes()
        request_data, request_mime_type = _request_image(
            data, image["mime_type"])
        context = dict(image["context"])
        comparison_path = image.get("comparison_path")
        if comparison_path is not None:
            comparison_path = pathlib.Path(comparison_path)
            comparison_data = comparison_path.read_bytes()
            comparison_data, comparison_mime = _request_image(
                comparison_data, _mime_type(comparison_path))
            context["_comparison_image"] = (
                comparison_data, comparison_mime)
        result = evaluator.evaluate(
            request_data, request_mime_type, context)
        result["screenshot"] = {
            "number": image["context"].get("number"),
            "label": image["context"].get("label"),
            "page": image["context"].get("page"),
            "case_id": image["context"].get("case_id"),
            "semantic_page_id": image["context"].get("semantic_page_id"),
            "source": image["context"].get("source"),
            "file": path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "input_mime_type": image["mime_type"],
            "submitted_mime_type": request_mime_type,
            "expectation": image["context"].get("expectation"),
            "expectation_references": [
                "cases.%s.%s[%d]" % (
                    image["context"].get("case_id"), section, index)
                for section in ("required", "forbidden",
                                "allowed_variations")
                for index, _item in enumerate(
                    image["context"].get(
                        "expectation", {}).get(section, ()))
            ],
        }
        records.append(result)
    artifact = evaluator.artifact(records)
    artifact["summary"] = evaluator.summary(records)
    statuses = [item["status"] for item in records]
    artifact["status"] = (
        "disabled" if not settings.enabled else
        "failed" if any(item.get("strict_failure") for item in records) else
        "warning" if any(item != "passed" for item in statuses) else
        "passed")
    return artifact


def write_artifact(path, artifact):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    temporary.replace(path)


def _arguments(argv):
    parser = argparse.ArgumentParser(
        description=(
            "Run development-only semantic checks against saved UI images. "
            "No request is made unless --enable is supplied."))
    parser.add_argument(
        "inputs", nargs="+",
        help="saved UI-test artifact directories or explicit image files")
    parser.add_argument(
        "--enable", action="store_true",
        help="explicitly enable OpenAI-compatible requests")
    parser.add_argument(
        "--base-url", default=os.environ.get(
            "FF5M_VISUAL_BASE_URL", ""),
        help="OpenAI-compatible base URL (not written to artifacts)")
    parser.add_argument(
        "--model", default=os.environ.get("FF5M_VISUAL_MODEL", ""),
        help="one explicit model name")
    parser.add_argument(
        "--api-key-env", default="FF5M_VISUAL_API_KEY",
        help="environment variable containing the optional API key")
    parser.add_argument(
        "--timeout", type=float, default=os.environ.get(
            "FF5M_VISUAL_TIMEOUT", "30"))
    parser.add_argument(
        "--mode", choices=("advisory", "strict"), default=os.environ.get(
            "FF5M_VISUAL_MODE", "advisory"))
    parser.add_argument("--output")
    return parser.parse_args(argv)


def main(argv=None):
    args = _arguments(argv)
    api_key = os.environ.get(args.api_key_env, "")
    try:
        settings = vision.VisualCheckSettings(
            enabled=args.enable, base_url=args.base_url, model=args.model,
            api_key=api_key, timeout=args.timeout, mode=args.mode)
        images = discover_images(args.inputs)
        artifact = run_checks(settings, images)
        output = args.output
        if not output:
            first = pathlib.Path(args.inputs[0])
            output = (
                first / "visual-checks.json"
                if len(args.inputs) == 1 and first.is_dir()
                else pathlib.Path.cwd() / "visual-checks.json")
        write_artifact(output, artifact)
    except (OSError, ValueError) as exc:
        print("Visual check configuration/input error: %s" % exc,
              file=sys.stderr)
        return 2
    print("Visual checks: %s; model=%s; artifact=%s" % (
        artifact["status"], settings.model or "disabled", output))
    return 1 if artifact["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
