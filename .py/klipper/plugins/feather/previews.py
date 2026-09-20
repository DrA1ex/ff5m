## G-code preview decoding, coloring, and bounded runtime caching for Feather.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from collections import OrderedDict
import math
import struct
import subprocess
import sys
import time


class PreviewCancelled(Exception):
    """The caller no longer needs this preview."""


DEFAULT_PREVIEW_CACHE_KB = 256
MIN_PREVIEW_CACHE_KB = 64
PREVIEW_EXECUTABLE = "/opt/config/mod/.bin/exec/preview"
PREVIEW_TIMEOUT = 15.0
# File tiles and the print page use one mask size and validation contract.
PREVIEW_MASK_SIZE = (186, 177)


class PreviewCache:
    """Reactor-owned LRU cache bounded by approximate retained memory."""

    _ENTRY_OVERHEAD = 96

    def __init__(self, budget_bytes):
        self.budget_bytes = max(0, int(budget_bytes))
        self.size_bytes = 0
        self._items = OrderedDict()

    def lookup(self, key):
        try:
            value, size = self._items.pop(key)
        except KeyError:
            return False, None
        self._items[key] = (value, size)
        return True, value

    def contains(self, key):
        return key in self._items

    @classmethod
    def _retained_size(cls, key, value):
        seen = set()

        def measure(item):
            identity = id(item)
            if identity in seen:
                return 0
            seen.add(identity)
            size = sys.getsizeof(item)
            if isinstance(item, dict):
                size += sum(measure(k) + measure(v)
                            for k, v in item.items())
            elif isinstance(item, (tuple, list, set, frozenset)):
                size += sum(measure(part) for part in item)
            return size

        return cls._ENTRY_OVERHEAD + measure(key) + measure(value)

    def store(self, key, value):
        size = self._retained_size(key, value)
        previous = self._items.pop(key, None)
        if previous is not None:
            self.size_bytes -= previous[1]

        # A path has only one useful file version. Remove older metadata as
        # soon as a replacement is published.
        path = key[0]
        for old_key in tuple(self._items):
            if old_key[0] == path:
                _old_value, old_size = self._items.pop(old_key)
                self.size_bytes -= old_size

        if size > self.budget_bytes:
            return False
        while self._items and self.size_bytes + size > self.budget_bytes:
            _old_key, (_old_value, old_size) = self._items.popitem(last=False)
            self.size_bytes -= old_size
        self._items[key] = (value, size)
        self.size_bytes += size
        return True

    def __len__(self):
        return len(self._items)


def _decode_packbits(payload, expected_size):
    output = bytearray()
    index = 0

    while index < len(payload):
        header = struct.unpack_from("<b", payload, index)[0]
        index += 1

        if header >= 0:
            count = header + 1
            end = index + count

            if end > len(payload):
                raise RuntimeError(
                    "preview helper returned truncated PackBits data")

            output.extend(payload[index:end])
            index = end
        elif header != -128:
            count = 1 - header

            if index >= len(payload):
                raise RuntimeError(
                    "preview helper returned truncated PackBits repeat")

            output.extend(payload[index:index + 1] * count)
            index += 1

        if len(output) > expected_size:
            raise RuntimeError(
                "preview helper returned oversized PackBits output")

    if len(output) != expected_size:
        raise RuntimeError(
            "preview helper returned invalid PackBits output size")

    return bytes(output)


def decode_fxi1(blob):
    if len(blob) < 20 or blob[:4] != b"FXI1":
        raise RuntimeError("preview helper returned invalid FXI1 header")

    version, compression, palette_size, bpp = struct.unpack_from(
        "<BBBB", blob, 4)
    width, height, unpacked_size, payload_size = struct.unpack_from(
        "<HHII", blob, 8)

    if version != 1:
        raise RuntimeError("preview helper returned unsupported FXI1 version")

    if palette_size < 1 or palette_size > 8:
        raise RuntimeError("preview helper returned invalid FXI1 palette size")

    expected_bpp = 1 if palette_size <= 2 else 2 if palette_size <= 4 else 4

    if bpp != expected_bpp:
        raise RuntimeError(
            "preview helper returned invalid FXI1 bits-per-pixel")

    expected_unpacked_size = (width * height * bpp + 7) // 8

    if unpacked_size != expected_unpacked_size:
        raise RuntimeError("preview helper returned invalid FXI1 unpacked size")

    palette_offset = 20
    palette_end = palette_offset + palette_size * 4
    payload_end = palette_end + payload_size

    if len(blob) != payload_end:
        raise RuntimeError("preview helper returned truncated FXI1 payload")

    palette = struct.unpack_from("<%dI" % palette_size, blob, palette_offset)
    payload = blob[palette_end:payload_end]

    if compression == 0:
        packed = payload
    elif compression == 1:
        packed = _decode_packbits(payload, unpacked_size)
    else:
        raise RuntimeError("preview helper returned unknown FXI1 compression")

    if len(packed) != unpacked_size:
        raise RuntimeError("preview helper returned malformed FXI1 payload")

    return {
        "blob": blob,
        "width": width,
        "height": height,
        "palette": palette,
        "packed": packed,
        "bpp": bpp,
    }


def _encode_packbits(payload):
    output = bytearray()
    index = 0

    while index < len(payload):
        repeat = 1
        while (index + repeat < len(payload) and repeat < 128
               and payload[index + repeat] == payload[index]):
            repeat += 1

        if repeat >= 3:
            output.extend((257 - repeat, payload[index]))
            index += repeat
            continue

        literal_start = index
        literal_count = 0
        while index < len(payload) and literal_count < 128:
            repeat = 1
            while (index + repeat < len(payload) and repeat < 128
                   and payload[index + repeat] == payload[index]):
                repeat += 1
            if repeat >= 3:
                break
            take = min(repeat, 128 - literal_count)
            index += take
            literal_count += take

        output.append(literal_count - 1)
        output.extend(payload[literal_start:index])

    return bytes(output)


def _parse_argb(value):
    value = str(value).strip().lstrip("#")
    if not value or len(value) > 8:
        raise ValueError("invalid preview color: %s" % value)
    color = int(value, 16)
    return color if len(value) > 6 else 0xff000000 | color


def _mask_y_bounds(image):
    width = image["width"]
    packed = image["packed"]
    first = None
    last = None

    for y in range(image["height"]):
        row_start = y * width
        present = False
        for x in range(width):
            bit = row_start + x
            if packed[bit // 8] & (1 << (7 - bit % 8)):
                present = True
                break
        if present:
            if first is None:
                first = y
            last = y

    return None if first is None else (first, last)


def _fxi1_mask_blob(image, color, first_row=0):
    if image["bpp"] != 1:
        raise RuntimeError("preview helper returned a non-mask FXI1 image")

    width = image["width"]
    height = image["height"]
    packed = bytearray(image["packed"])
    first_bit = max(0, min(height, int(first_row))) * width
    full_bytes, remaining_bits = divmod(first_bit, 8)

    if full_bytes:
        packed[:full_bytes] = b"\x00" * full_bytes
    if remaining_bits and full_bytes < len(packed):
        packed[full_bytes] &= (1 << (8 - remaining_bits)) - 1

    compressed = _encode_packbits(packed)
    use_compression = len(compressed) < len(packed)
    payload = compressed if use_compression else bytes(packed)
    palette = struct.pack("<II", 0, _parse_argb(color))
    header = struct.pack(
        "<4sBBBBHHII", b"FXI1", 1, int(use_compression), 2, 1,
        width, height, len(packed), len(payload))
    return header + palette + payload


def layer_progress(stats):
    info = stats.get("info", {})
    current = info.get("current_layer")
    total = info.get("total_layer")
    try:
        current_value = float(current)
        total_value = float(total)
    except (TypeError, ValueError):
        return None
    if total_value <= 0:
        return None
    ratio = max(0.0, min(1.0, current_value / total_value))
    return current, total, ratio


def colorize_preview(image, layer_state, pending, printed):
    cache = image.setdefault("full_mask_blobs", {})

    def full_mask(color):
        blob = cache.get(color)
        if blob is None:
            blob = _fxi1_mask_blob(image, color)
            cache[color] = blob
        return blob

    bounds = image.get("bounds")
    if bounds is None or layer_state is None:
        return (full_mask(printed),)

    _current, _total, ratio = layer_state
    first_y, last_y = bounds
    row_count = last_y - first_y + 1
    printed_rows = min(row_count, max(0, int(math.ceil(row_count * ratio))))

    if printed_rows <= 0:
        return (full_mask(pending),)
    if printed_rows >= row_count:
        return (full_mask(printed),)

    cutoff = last_y + 1 - printed_rows
    return (
        full_mask(pending),
        _fxi1_mask_blob(image, printed, cutoff),
    )


def _decode_preview_mask(blob, width, height):
    image = decode_fxi1(blob)
    if image["width"] != width or image["height"] != height:
        raise RuntimeError("preview helper returned an unexpected FXI1 size")
    if image["bpp"] != 1 or len(image["palette"]) != 2:
        raise RuntimeError("preview helper returned a non-mask FXI1 image")
    image["bounds"] = _mask_y_bounds(image)
    return image


def load_preview(path, width, height, executable=PREVIEW_EXECUTABLE,
                 timeout=PREVIEW_TIMEOUT, cancel=None):
    if cancel is not None and cancel.is_set():
        raise PreviewCancelled()
    width = max(1, int(width))
    height = max(1, int(height))
    args = [
        "nice", "-n", "10",
        executable, path, "--size", str(width), str(height),
    ]

    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise PreviewCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("preview helper timed out")
            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, remaining) if cancel is not None else remaining)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        process.kill()
        process.communicate()
        raise

    if process.returncode == 2:
        return None

    if process.returncode != 0:
        detail = (stderr.decode("utf-8", "replace").strip()
                  or "no error output")[:400]
        raise RuntimeError(
            "preview helper failed (%d): %s" % (process.returncode, detail))

    return _decode_preview_mask(stdout, width, height)
