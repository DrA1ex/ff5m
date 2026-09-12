#!/usr/bin/env python3
"""Generate Feather UI theme previews as a readable static HTML site.

Two modes are supported.

Single theme:
    python3 feather_theme_preview_batch.py themes/classic.json \
        --contract theme.py \
        --output-dir preview/classic

Batch directory:
    python3 feather_theme_preview_batch.py \
        --batch-dir themes \
        --contract theme.py \
        --output-dir preview

Batch output:
    preview/
      index.html
      themes/
        classic/
          index.html
          00-overview.png
          01-physical-color-map.png
          02-theme-color-palette.png
          03-control-token-gallery.png
          04-theme-role-fallback-audit.png
          05-base-token-contrast-matrix.png
          thumb-palette.png
          thumb-controls.png
          thumb-roles.png
        amber/
          ...

The root index contains three purpose-built thumbnails for every theme so the
palette/controls/roles can be compared before opening a detailed theme page.

The detailed page intentionally uses separate large images instead of one huge
poster. This keeps text readable in browsers and image viewers.
The text layout uses glyph-bbox centering, so labels are visually centered
rather than shifted downward by font ascent/descent.

Pillow is required.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil

from PIL import Image, ImageDraw, ImageFont


HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")

EMBEDDED_COLORS = (
    "background", "panel", "primary", "primary_dark", "secondary",
    "secondary_dark", "warning", "danger", "danger_background", "text",
    "bright", "dim", "border", "muted", "success", "pressed_background",
    "overlay",
)

EMBEDDED_ROLES = (
    "button_background", "button_border", "button_text",
    "button_selected_background", "button_selected_border",
    "button_selected_text", "accent_background", "accent_border",
    "accent_text", "header_background", "header_text",
    "header_border", "temperature_nozzle", "temperature_bed",
    "temperature_fan",
)

EMBEDDED_DEFAULTS = {
    "button_background": "panel",
    "button_border": "primary",
    "button_text": "primary",
    "button_selected_background": "panel",
    "button_selected_border": "secondary",
    "button_selected_text": "secondary",
    "accent_background": "primary_dark",
    "accent_border": "primary",
    "accent_text": "bright",
    "header_background": "panel",
    "header_text": "primary",
    "header_border": "border",
    "temperature_nozzle": "primary",
    "temperature_bed": "primary",
    "temperature_fan": "primary",
}

PALETTE_GROUPS = (
    ("SURFACES", (
        "background", "panel", "pressed_background", "overlay",
    )),
    ("ACCENTS", (
        "primary", "primary_dark", "secondary", "secondary_dark", "bright",
    )),
    ("TEXT / STRUCTURE", (
        "text", "dim", "muted", "border",
    )),
    ("STATUS", (
        "warning", "danger", "danger_background", "success",
    )),
)


def normalize_hex(value, label):
    value = str(value).strip().lower().lstrip("#")
    if HEX_RE.fullmatch(value) is None:
        raise ValueError("%s must be six-digit HEX, got %r" % (label, value))
    return value


def rgb(value):
    value = normalize_hex(value, "color")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def linear_channel(channel):
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def luminance(value):
    r, g, b = rgb(value)
    return (
        0.2126 * linear_channel(r)
        + 0.7152 * linear_channel(g)
        + 0.0722 * linear_channel(b)
    )


def contrast_ratio(foreground, background):
    first = luminance(foreground)
    second = luminance(background)
    light = max(first, second)
    dark = min(first, second)
    return (light + 0.05) / (dark + 0.05)


def best_text(background):
    black = contrast_ratio("000000", background)
    white = contrast_ratio("ffffff", background)
    return "000000" if black >= white else "ffffff"


def load_font(size, bold=False):
    candidates = (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/SFNSMono.ttf",
        )
        if bold else
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/SFNSMono.ttf",
        )
    )
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                pass
    return ImageFont.load_default()


class Fonts:
    def __init__(self, scale=1.0):
        self.hero = load_font(max(18, round(30 * scale)), True)
        self.section = load_font(max(16, round(22 * scale)), True)
        self.title = load_font(max(13, round(16 * scale)), True)
        self.body = load_font(max(11, round(13 * scale)))
        self.body_bold = load_font(max(11, round(13 * scale)), True)
        self.small = load_font(max(9, round(11 * scale)))
        self.small_bold = load_font(max(9, round(11 * scale)), True)
        self.tiny = load_font(max(8, round(9 * scale)))
        self.specimen = load_font(max(14, round(20 * scale)), True)
        self.temperature = load_font(max(12, round(16 * scale)), True)


def text_size(draw, text, font):
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0], box[3] - box[1]


def text_bbox(draw, text, font):
    return draw.textbbox((0, 0), str(text), font=font)


def centered(draw, box, text, font, fill):
    left, top, right, bottom = box
    x1, y1, x2, y2 = text_bbox(draw, text, font)
    width = x2 - x1
    height = y2 - y1
    x = left + (right - left - width) / 2 - x1
    y = top + (bottom - top - height) / 2 - y1
    draw.text((x, y), str(text), font=font, fill=rgb(fill))


def right_text(draw, x, y, text, font, fill):
    width, _height = text_size(draw, text, font)
    draw.text((x - width, y), str(text), font=font, fill=rgb(fill))


def left_text_vcenter(draw, x, center_y, text, font, fill):
    x1, y1, x2, y2 = text_bbox(draw, text, font)
    height = y2 - y1
    y = center_y - height / 2 - y1
    draw.text((x, y), str(text), font=font, fill=rgb(fill))


def rect(draw, box, fill=None, outline=None, width=1):
    draw.rectangle(
        box,
        fill=rgb(fill) if fill is not None else None,
        outline=rgb(outline) if outline is not None else None,
        width=width,
    )


def divider(draw, x1, y, x2, color):
    draw.line((x1, y, x2, y), fill=rgb(color), width=1)


def wrapped_lines(draw, text, font, max_width):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def slugify(value):
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = value.strip("-._")
    return value or "theme"


def _parse_contract_source(path):
    source = Path(path).read_text(encoding="utf-8")

    color_match = re.search(
        r"class\s+ThemeColor\s*\([^)]*\)\s*:(.*?)(?=\nclass\s|\Z)",
        source, re.S)
    if color_match is None:
        raise RuntimeError("ThemeColor enum not found in %s" % path)
    colors = tuple(re.findall(
        r'^\s+[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"',
        color_match.group(1), re.M))

    role_match = re.search(
        r"class\s+ThemeRole\s*\([^)]*\)\s*:(.*?)(?=\n(?:class|DEFAULT_THEME_ROLES)\b|\Z)",
        source, re.S)
    if role_match is None:
        anchor = source.find('BUTTON_BACKGROUND = "button_background"')
        if anchor < 0:
            raise RuntimeError("ThemeRole enum not found in %s" % path)
        start = source.rfind("class ", 0, anchor)
        end = source.find("DEFAULT_THEME_ROLES", anchor)
        role_body = source[start:end if end >= 0 else None]
    else:
        role_body = role_match.group(1)

    roles = tuple(re.findall(
        r'^\s+[A-Z][A-Z0-9_]*\s*=\s*"([^"]+)"',
        role_body, re.M))

    defaults = {}
    for role_symbol, color_symbol in re.findall(
            r"ThemeRole\.([A-Z][A-Z0-9_]*)\s*:\s*ThemeColor\.([A-Z][A-Z0-9_]*)",
            source):
        role_name = re.search(
            r"^\s*%s\s*=\s*\"([^\"]+)\"" % re.escape(role_symbol),
            role_body, re.M)
        color_name = re.search(
            r"^\s*%s\s*=\s*\"([^\"]+)\"" % re.escape(color_symbol),
            color_match.group(1), re.M)
        if role_name and color_name:
            defaults[role_name.group(1)] = color_name.group(1)

    missing = [role for role in roles if role not in defaults]
    if not colors or not roles or missing:
        raise RuntimeError(
            "unable to recover complete theme contract from %s" % path)

    return {
        "module": None,
        "colors": colors,
        "roles": roles,
        "defaults": defaults,
        "mode": "parsed-source",
    }


def load_contract(path):
    if path is None:
        return {
            "module": None,
            "colors": EMBEDDED_COLORS,
            "roles": EMBEDDED_ROLES,
            "defaults": dict(EMBEDDED_DEFAULTS),
            "mode": "embedded",
        }

    path = Path(path)
    spec = importlib.util.spec_from_file_location("feather_theme_contract", path)
    if spec is None or spec.loader is None:
        return _parse_contract_source(path)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (SyntaxError, NameError):
        return _parse_contract_source(path)

    return {
        "module": module,
        "colors": tuple(item.value for item in module.ThemeColor),
        "roles": tuple(item.value for item in module.ThemeRole),
        "defaults": {
            role.value: module.DEFAULT_THEME_ROLES[role].value
            for role in module.ThemeRole
        },
        "mode": "imported",
    }


def resolve_standalone(colors, roles, contract):
    resolved_colors = {
        name: normalize_hex(colors[name], name)
        for name in contract["colors"]
    }

    resolved_roles = {}
    for name in contract["roles"]:
        raw = str(roles.get(name, contract["defaults"][name])).strip().lower()
        if raw in resolved_colors:
            resolved_roles[name] = resolved_colors[raw]
        else:
            resolved_roles[name] = normalize_hex(raw, name)

    return resolved_colors, resolved_roles


def resolve_theme(colors, roles, contract):
    module = contract["module"]
    if module is None:
        return resolve_standalone(colors, roles, contract)

    resolved = module.resolve_theme(colors, roles).as_dict()
    return (
        {name: resolved[name] for name in contract["colors"]},
        {name: resolved[name] for name in contract["roles"]},
    )


def load_theme(path, contract):
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))

    if "colors" not in document:
        raise ValueError("not a theme document: %s" % path)

    colors_raw = dict(document.get("colors") or {})
    roles_raw = dict(document.get("roles") or {})

    missing = sorted(set(contract["colors"]) - set(colors_raw))
    unknown_colors = sorted(set(colors_raw) - set(contract["colors"]))
    unknown_roles = sorted(set(roles_raw) - set(contract["roles"]))

    if missing:
        raise ValueError("missing base colors: " + ", ".join(missing))
    if unknown_colors:
        raise ValueError("unknown base colors: " + ", ".join(unknown_colors))
    if unknown_roles:
        raise ValueError("unknown roles: " + ", ".join(unknown_roles))

    colors, roles = resolve_theme(colors_raw, roles_raw, contract)
    _same_colors, fallback_roles = resolve_theme(colors_raw, {}, contract)

    return {
        "path": path,
        "document": document,
        "colors_raw": colors_raw,
        "roles_raw": roles_raw,
        "colors": colors,
        "roles": roles,
        "fallback_roles": fallback_roles,
    }


def section_canvas(theme, title, subtitle, body_height, width=1800):
    colors = theme["colors"]
    fonts = Fonts(1.18)
    margin = 36
    header_height = 112

    image = Image.new(
        "RGB",
        (width, header_height + body_height + margin),
        rgb(colors["background"]),
    )
    draw = ImageDraw.Draw(image)

    draw.text(
        (margin, 20), title,
        font=fonts.section, fill=rgb(colors["text"]))
    draw.text(
        (margin, 58), subtitle,
        font=fonts.small, fill=rgb(colors["dim"]))
    divider(draw, margin, 94, width - margin, colors["border"])

    return image, draw, fonts, margin, header_height


def physical_color_map(draw, x, y, width, theme, contract, fonts):
    colors = theme["colors"]
    roles = theme["roles"]
    fallback = theme["fallback_roles"]

    clusters = {}
    for token in contract["colors"]:
        clusters.setdefault(colors[token], {
            "tokens": [], "roles": [], "fallback_roles": []})
        clusters[colors[token]]["tokens"].append(token)

    for role in contract["roles"]:
        clusters.setdefault(roles[role], {
            "tokens": [], "roles": [], "fallback_roles": []})
        clusters[roles[role]]["roles"].append(role)

        clusters.setdefault(fallback[role], {
            "tokens": [], "roles": [], "fallback_roles": []})
        clusters[fallback[role]]["fallback_roles"].append(role)

    ordered = sorted(
        clusters.items(),
        key=lambda item: (
            -len(item[1]["tokens"]),
            -len(item[1]["roles"]),
            item[0],
        ))

    columns = 2
    gap = 18
    card_width = (width - gap) // columns
    card_height = 150

    for index, (value, info) in enumerate(ordered):
        row, column = divmod(index, columns)
        left = x + column * (card_width + gap)
        top = y + row * (card_height + gap)
        right = left + card_width
        bottom = top + card_height

        rect(draw, (left, top, right, bottom),
             colors["panel"], colors["border"], 1)

        swatch_box = (left + 12, top + 12, left + 108, bottom - 12)
        rect(draw, swatch_box, value, colors["border"], 1)
        centered(
            draw,
            (swatch_box[0], bottom - 38, swatch_box[2], bottom - 12),
            "#" + value.upper(),
            fonts.tiny,
            best_text(value),
        )

        text_x = left + 128
        draw.text(
            (text_x, top + 12),
            "#" + value.upper(),
            font=fonts.body_bold,
            fill=rgb(colors["text"]),
        )

        rows = (
            ("ThemeColor", ", ".join(info["tokens"]) or "—", colors["text"]),
            ("configured roles", ", ".join(info["roles"]) or "—",
             colors["secondary"]),
            ("fallback roles", ", ".join(info["fallback_roles"]) or "—",
             colors["dim"]),
        )

        cursor = top + 40
        available = right - text_x - 14
        for label, value_text, color in rows:
            prefix = label + ": "
            lines = wrapped_lines(
                draw, prefix + value_text, fonts.small, available)
            for line in lines[:2]:
                draw.text(
                    (text_x, cursor), line,
                    font=fonts.small, fill=rgb(color))
                cursor += 17
            cursor += 3

    rows_count = int(math.ceil(len(ordered) / float(columns)))
    return rows_count * (card_height + gap) - gap


def palette(draw, x, y, width, theme, fonts):
    colors = theme["colors"]
    cursor = y

    for group_name, names in PALETTE_GROUPS:
        draw.text(
            (x, cursor), group_name,
            font=fonts.title, fill=rgb(colors["text"]))
        cursor += 30

        gap = 10
        card_width = (width - gap * (len(names) - 1)) // len(names)
        card_height = 96

        for index, name in enumerate(names):
            left = x + index * (card_width + gap)
            value = colors[name]
            rect(
                draw,
                (left, cursor, left + card_width, cursor + card_height),
                value,
                colors["border"],
                1,
            )
            foreground = best_text(value)
            draw.text(
                (left + 9, cursor + 9), name,
                font=fonts.small_bold, fill=rgb(foreground))
            draw.text(
                (left + 9, cursor + 35), "#" + value.upper(),
                font=fonts.small, fill=rgb(foreground))
            draw.text(
                (left + 9, cursor + 62),
                "bg %.2f  panel %.2f" % (
                    contrast_ratio(value, colors["background"]),
                    contrast_ratio(value, colors["panel"]),
                ),
                font=fonts.tiny,
                fill=rgb(foreground),
            )

        cursor += card_height + 24

    return cursor - y


def draw_button(draw, box, background, border, text, label, fonts):
    rect(draw, box, background, border, 2)
    centered(draw, box, label, fonts.specimen, text)


def control_gallery(draw, x, y, width, theme, fonts):
    colors = theme["colors"]
    roles = theme["roles"]

    gap = 18
    columns = 2
    card_width = (width - gap) // columns
    card_height = 165

    cards = (
        ("NORMAL BUTTON", "button roles"),
        ("SELECTED BUTTON", "selected button roles"),
        ("HEADER", "header roles"),
        ("TEMPERATURES", "temperature roles"),
        ("STATUS COLORS", "direct ThemeColor use"),
        ("BASE TEXT TOKENS", "direct ThemeColor use"),
    )

    for index, (title, subtitle) in enumerate(cards):
        row, column = divmod(index, columns)
        left = x + column * (card_width + gap)
        top = y + row * (card_height + gap)
        right = left + card_width
        bottom = top + card_height

        rect(draw, (left, top, right, bottom),
             colors["panel"], colors["border"], 1)
        draw.text(
            (left + 14, top + 12), title,
            font=fonts.title, fill=rgb(colors["text"]))
        right_text(
            draw, right - 14, top + 14, subtitle,
            fonts.tiny, colors["dim"])

        specimen = (left + 22, top + 52, right - 22, bottom - 22)

        if index == 0:
            draw_button(
                draw, specimen,
                roles["button_background"],
                roles["button_border"],
                roles["button_text"],
                "BUTTON", fonts)
        elif index == 1:
            draw_button(
                draw, specimen,
                roles["button_selected_background"],
                roles["button_selected_border"],
                roles["button_selected_text"],
                "SELECTED", fonts)
        elif index == 2:
            rect(
                draw, specimen,
                roles["header_background"],
                roles["header_border"], 2)
            centered(
                draw, specimen, "HEADER",
                fonts.specimen, roles["header_text"])
        elif index == 3:
            rect(draw, specimen, colors["background"], colors["border"], 1)
            entries = (
                ("NOZZLE 150C", roles["temperature_nozzle"]),
                ("BED 60C", roles["temperature_bed"]),
                ("FAN 80%", roles["temperature_fan"]),
            )
            cursor = specimen[1] + 12
            for label, value in entries:
                left_text_vcenter(
                    draw, specimen[0] + 18, cursor + 12,
                    label, fonts.temperature, value)
                cursor += 29
        elif index == 4:
            rect(draw, specimen, colors["background"], colors["border"], 1)
            status = (
                ("WARNING", colors["warning"]),
                ("DANGER", colors["danger"]),
                ("SUCCESS", colors["success"]),
            )
            gap_inner = 12
            chip_width = (
                specimen[2] - specimen[0] - 36 - gap_inner * 2
            ) // 3
            chip_x = specimen[0] + 18
            for label, value in status:
                chip = (
                    chip_x, specimen[1] + 20,
                    chip_x + chip_width, specimen[3] - 20)
                rect(draw, chip, colors["panel"], value, 2)
                centered(draw, chip, label, fonts.small_bold, value)
                chip_x += chip_width + gap_inner
        else:
            rect(draw, specimen, colors["background"], colors["border"], 1)
            token_rows = (
                ("TEXT", colors["text"]),
                ("BRIGHT", colors["bright"]),
                ("PRIMARY", colors["primary"]),
                ("SECONDARY", colors["secondary"]),
                ("DIM", colors["dim"]),
                ("MUTED", colors["muted"]),
            )
            half = (specimen[2] - specimen[0] - 44) // 2
            for item_index, (label, value) in enumerate(token_rows):
                column = item_index % 2
                row = item_index // 2
                tx = specimen[0] + 18 + column * (half + 8)
                ty = specimen[1] + 12 + row * 28
                left_text_vcenter(
                    draw, tx, ty + 8,
                    label, fonts.small_bold, value)

    rows_count = int(math.ceil(len(cards) / float(columns)))
    return rows_count * (card_height + gap) - gap


def role_group(role):
    if role.startswith("button_selected_"):
        return "button_selected"
    if role.startswith("button_"):
        return "button"
    if role.startswith("accent_"):
        return "accent"
    if role.startswith("header_"):
        return "header"
    if role.startswith("temperature_"):
        return "temperature"
    return "generic"


def role_component(draw, box, role, roles, colors, fonts):
    group = role_group(role)

    if group == "button":
        draw_button(
            draw, box,
            roles["button_background"],
            roles["button_border"],
            roles["button_text"],
            "BUTTON", fonts)
    elif group == "button_selected":
        draw_button(
            draw, box,
            roles["button_selected_background"],
            roles["button_selected_border"],
            roles["button_selected_text"],
            "SELECTED", fonts)
    elif group == "accent":
        rect(
            draw, box,
            roles["accent_background"],
            roles["accent_border"], 2)
        centered(draw, box, "ACCENT", fonts.specimen, roles["accent_text"])
    elif group == "header":
        rect(
            draw, box,
            roles["header_background"],
            roles["header_border"], 2)
        centered(draw, box, "HEADER", fonts.specimen, roles["header_text"])
    elif group == "temperature":
        rect(draw, box, colors["panel"], colors["border"], 1)
        label = {
            "temperature_nozzle": "NOZZLE 150C",
            "temperature_bed": "BED 60C",
            "temperature_fan": "FAN 80%",
        }.get(role, role.upper())
        centered(draw, box, label, fonts.temperature, roles[role])
    else:
        rect(draw, box, roles[role], colors["border"], 1)
        centered(
            draw, box, role.upper(),
            fonts.small, best_text(roles[role]))


def role_contrast(role, roles, colors):
    group = role_group(role)

    if group == "button":
        return contrast_ratio(
            roles["button_text"], roles["button_background"])
    if group == "button_selected":
        return contrast_ratio(
            roles["button_selected_text"],
            roles["button_selected_background"])
    if group == "accent":
        return contrast_ratio(
            roles["accent_text"], roles["accent_background"])
    if group == "header":
        return contrast_ratio(
            roles["header_text"], roles["header_background"])
    if group == "temperature":
        return contrast_ratio(roles[role], colors["panel"])

    return contrast_ratio(roles[role], colors["background"])


def role_gallery(draw, x, y, width, theme, contract, fonts):
    colors = theme["colors"]
    roles = theme["roles"]
    fallback = theme["fallback_roles"]
    roles_raw = theme["roles_raw"]

    columns = 2
    gap = 18
    card_width = (width - gap) // columns
    card_height = 210

    for index, role in enumerate(contract["roles"]):
        row, column = divmod(index, columns)
        left = x + column * (card_width + gap)
        top = y + row * (card_height + gap)
        right = left + card_width
        bottom = top + card_height

        rect(draw, (left, top, right, bottom),
             colors["panel"], colors["border"], 1)

        draw.text(
            (left + 14, top + 11), role,
            font=fonts.body_bold, fill=rgb(colors["text"]))

        state = "override" if role in roles_raw else "inherited"
        state_color = colors["secondary"] if state == "override" else colors["dim"]
        right_text(
            draw, right - 14, top + 13,
            state, fonts.tiny, state_color)

        inner_left = left + 14
        inner_right = right - 14
        inner_gap = 12
        half = (inner_right - inner_left - inner_gap) // 2

        theme_box = (
            inner_left, top + 52,
            inner_left + half, top + 126)
        omitted_box = (
            inner_left + half + inner_gap, top + 52,
            inner_right, top + 126)

        draw.text(
            (theme_box[0], top + 35), "THEME",
            font=fonts.tiny, fill=rgb(colors["dim"]))
        draw.text(
            (omitted_box[0], top + 35), "ROLE OMITTED",
            font=fonts.tiny, fill=rgb(colors["dim"]))

        role_component(draw, theme_box, role, roles, colors, fonts)

        omitted = dict(roles)
        omitted[role] = fallback[role]
        role_component(draw, omitted_box, role, omitted, colors, fonts)

        configured_source = roles_raw.get(role, "<omitted>")
        default_source = contract["defaults"][role]

        draw.text(
            (inner_left, top + 139),
            "theme %s -> #%s" % (
                configured_source, roles[role].upper()),
            font=fonts.tiny, fill=rgb(colors["text"]))
        draw.text(
            (inner_left, top + 157),
            "default %s -> #%s" % (
                default_source, fallback[role].upper()),
            font=fonts.tiny, fill=rgb(colors["dim"]))

        ratio_theme = role_contrast(role, roles, colors)
        ratio_omitted = role_contrast(role, omitted, colors)
        ratio_color = (
            colors["success"]
            if ratio_theme >= 4.5
            else colors["warning"]
            if ratio_theme >= 3.0
            else colors["danger"]
        )
        draw.text(
            (inner_left, top + 180),
            "contrast %.2f : 1   omitted %.2f : 1" % (
                ratio_theme, ratio_omitted),
            font=fonts.tiny, fill=rgb(ratio_color))

    rows_count = int(math.ceil(len(contract["roles"]) / float(columns)))
    return rows_count * (card_height + gap) - gap


def contrast_matrix(draw, x, y, width, theme, fonts):
    colors = theme["colors"]

    foregrounds = (
        "text", "bright", "primary", "secondary",
        "dim", "muted", "warning", "danger", "success",
    )
    backgrounds = (
        "background", "panel", "primary", "primary_dark",
        "secondary_dark", "danger_background", "pressed_background",
    )

    label_width = 145
    gap = 6
    cell_width = (
        width - label_width - gap * len(backgrounds)
    ) // len(backgrounds)
    cell_height = 54

    for column, background_name in enumerate(backgrounds):
        cx = x + label_width + column * (cell_width + gap)
        value = colors[background_name]
        rect(
            draw,
            (cx, y, cx + cell_width, y + 42),
            value, colors["border"], 1)
        centered(
            draw,
            (cx, y, cx + cell_width, y + 42),
            background_name, fonts.tiny, best_text(value))

    row_y = y + 50
    for foreground_name in foregrounds:
        draw.text(
            (x, row_y + 16),
            foreground_name,
            font=fonts.small_bold,
            fill=rgb(colors["text"]),
        )

        for column, background_name in enumerate(backgrounds):
            cx = x + label_width + column * (cell_width + gap)
            foreground = colors[foreground_name]
            background = colors[background_name]
            ratio = contrast_ratio(foreground, background)

            outline = (
                colors["success"]
                if ratio >= 4.5
                else colors["warning"]
                if ratio >= 3.0
                else colors["danger"]
            )

            rect(
                draw,
                (cx, row_y, cx + cell_width, row_y + cell_height),
                background, outline, 2)
            centered(
                draw,
                (cx, row_y + 2, cx + cell_width, row_y + 30),
                "Aa", fonts.body_bold, foreground)
            centered(
                draw,
                (cx, row_y + 28, cx + cell_width, row_y + cell_height),
                "%.2f" % ratio, fonts.tiny, foreground)

        row_y += cell_height + gap

    draw.text(
        (x, row_y + 8),
        "green >= 4.5:1   olive >= 3.0:1   maroon < 3.0:1",
        font=fonts.small,
        fill=rgb(colors["dim"]),
    )

    return row_y + 30 - y


def render_overview(theme, output_path, width=1800):
    colors = theme["colors"]
    roles = theme["roles"]
    fonts = Fonts(1.28)
    height = 520

    image = Image.new("RGB", (width, height), rgb(colors["background"]))
    draw = ImageDraw.Draw(image)
    margin = 34

    name = theme["document"].get(
        "name", theme["path"].stem).upper()
    description = theme["document"].get("description", "")

    draw.text(
        (margin, 20), name,
        font=fonts.hero, fill=rgb(colors["text"]))
    draw.text(
        (margin, 62), description,
        font=fonts.body, fill=rgb(colors["dim"]))

    # Palette strip.
    key_tokens = (
        "background", "panel", "primary", "primary_dark",
        "secondary", "secondary_dark", "text", "bright",
        "warning", "danger", "success",
    )
    strip_y = 102
    gap = 8
    swatch_width = (
        width - margin * 2 - gap * (len(key_tokens) - 1)
    ) // len(key_tokens)

    for index, token in enumerate(key_tokens):
        left = margin + index * (swatch_width + gap)
        value = colors[token]
        rect(
            draw,
            (left, strip_y, left + swatch_width, strip_y + 85),
            value, colors["border"], 1)
        foreground = best_text(value)
        centered(
            draw,
            (left + 3, strip_y + 7, left + swatch_width - 3, strip_y + 40),
            token, fonts.tiny, foreground)
        centered(
            draw,
            (left + 3, strip_y + 42, left + swatch_width - 3, strip_y + 76),
            "#" + value.upper(), fonts.tiny, foreground)

    # Three compact control specimens.
    control_y = 224
    control_gap = 22
    control_width = (
        width - margin * 2 - control_gap * 2
    ) // 3
    control_height = 125

    normal = (
        margin, control_y,
        margin + control_width, control_y + control_height)
    selected = (
        normal[2] + control_gap, control_y,
        normal[2] + control_gap + control_width,
        control_y + control_height)
    header = (
        selected[2] + control_gap, control_y,
        selected[2] + control_gap + control_width,
        control_y + control_height)

    draw_button(
        draw, normal,
        roles["button_background"],
        roles["button_border"],
        roles["button_text"],
        "BUTTON", fonts)
    draw_button(
        draw, selected,
        roles["button_selected_background"],
        roles["button_selected_border"],
        roles["button_selected_text"],
        "SELECTED", fonts)
    rect(
        draw, header,
        roles["header_background"],
        roles["header_border"], 2)
    centered(
        draw, header, "HEADER",
        fonts.specimen, roles["header_text"])

    # Temperature / status line.
    footer = (margin, 385, width - margin, 484)
    rect(draw, footer, colors["panel"], colors["border"], 1)

    entries = (
        ("NOZZLE 150C", roles["temperature_nozzle"]),
        ("BED 60C", roles["temperature_bed"]),
        ("FAN 80%", roles["temperature_fan"]),
        ("WARNING", colors["warning"]),
        ("DANGER", colors["danger"]),
        ("SUCCESS", colors["success"]),
    )

    cursor = footer[0] + 24
    baseline = footer[1] + 34
    for label, value in entries:
        left_text_vcenter(
            draw, cursor, baseline + 8,
            label, fonts.small_bold, value)
        cursor += text_size(draw, label, fonts.small_bold)[0] + 42

    image.save(output_path)


def render_thumb_palette(theme, output_path):
    colors = theme["colors"]
    fonts = Fonts(0.9)
    width, height = 560, 220
    image = Image.new("RGB", (width, height), rgb(colors["background"]))
    draw = ImageDraw.Draw(image)

    tokens = (
        "background", "panel", "primary", "secondary",
        "text", "bright", "warning", "danger", "success",
    )
    columns = 3
    gap = 7
    margin = 12
    card_width = (
        width - margin * 2 - gap * (columns - 1)
    ) // columns
    card_height = 58

    for index, token in enumerate(tokens):
        row, column = divmod(index, columns)
        left = margin + column * (card_width + gap)
        top = margin + row * (card_height + gap)
        value = colors[token]
        rect(
            draw,
            (left, top, left + card_width, top + card_height),
            value, colors["border"], 1)
        foreground = best_text(value)
        draw.text(
            (left + 7, top + 7), token,
            font=fonts.tiny, fill=rgb(foreground))
        draw.text(
            (left + 7, top + 31), "#" + value.upper(),
            font=fonts.tiny, fill=rgb(foreground))

    image.save(output_path)


def render_thumb_controls(theme, output_path):
    colors = theme["colors"]
    roles = theme["roles"]
    fonts = Fonts(0.86)
    width, height = 560, 220
    image = Image.new("RGB", (width, height), rgb(colors["background"]))
    draw = ImageDraw.Draw(image)
    margin = 12
    gap = 10

    header = (margin, margin, width - margin, 66)
    rect(
        draw, header,
        roles["header_background"],
        roles["header_border"], 2)
    centered(
        draw, header, "HEADER",
        fonts.specimen, roles["header_text"])

    button_width = (width - margin * 2 - gap) // 2
    normal = (margin, 82, margin + button_width, 145)
    selected = (
        normal[2] + gap, 82,
        width - margin, 145)
    draw_button(
        draw, normal,
        roles["button_background"],
        roles["button_border"],
        roles["button_text"],
        "BUTTON", fonts)
    draw_button(
        draw, selected,
        roles["button_selected_background"],
        roles["button_selected_border"],
        roles["button_selected_text"],
        "SELECTED", fonts)

    rect(
        draw, (margin, 158, width - margin, height - margin),
        colors["panel"], colors["border"], 1)
    draw.text(
        (margin + 12, 174), "NOZZLE",
        font=fonts.small_bold, fill=rgb(roles["temperature_nozzle"]))
    draw.text(
        (margin + 128, 174), "BED",
        font=fonts.small_bold, fill=rgb(roles["temperature_bed"]))
    draw.text(
        (margin + 210, 174), "FAN",
        font=fonts.small_bold, fill=rgb(roles["temperature_fan"]))
    draw.text(
        (margin + 302, 174), "WARN",
        font=fonts.small_bold, fill=rgb(colors["warning"]))
    draw.text(
        (margin + 392, 174), "DANGER",
        font=fonts.small_bold, fill=rgb(colors["danger"]))
    draw.text(
        (margin + 486, 174), "OK",
        font=fonts.small_bold, fill=rgb(colors["success"]))

    image.save(output_path)


def render_thumb_roles(theme, contract, output_path):
    colors = theme["colors"]
    roles = theme["roles"]
    fallback = theme["fallback_roles"]
    fonts = Fonts(0.78)
    width, height = 560, 220
    image = Image.new("RGB", (width, height), rgb(colors["background"]))
    draw = ImageDraw.Draw(image)
    margin = 12

    draw.text(
        (margin, 10), "THEME ROLE RESOLUTION",
        font=fonts.title, fill=rgb(colors["text"]))

    columns = 2
    rows = int(math.ceil(len(contract["roles"]) / float(columns)))
    row_height = (height - 48) // rows

    for index, role in enumerate(contract["roles"]):
        column = index % columns
        row = index // columns
        left = margin + column * 270
        top = 42 + row * row_height

        configured = roles[role]
        default = fallback[role]

        rect(
            draw, (left, top, left + 18, top + 18),
            configured, colors["border"], 1)
        rect(
            draw, (left + 24, top, left + 42, top + 18),
            default, colors["border"], 1)
        draw.text(
            (left + 50, top + 2),
            role,
            font=fonts.tiny,
            fill=rgb(colors["text"]),
        )

    image.save(output_path)


def render_theme_site(theme_path, output_dir, contract, detail_width=1800):
    theme = load_theme(theme_path, contract)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    colors = theme["colors"]
    name = theme["document"].get("name", theme["path"].stem).upper()
    description = theme["document"].get("description", "")

    overview_path = output_dir / "00-overview.png"
    render_overview(theme, overview_path, detail_width)

    # Physical map.
    unique_count = len(set(
        list(theme["colors"].values())
        + list(theme["roles"].values())
        + list(theme["fallback_roles"].values())
    ))
    physical_rows = int(math.ceil(unique_count / 2.0))
    body_height = physical_rows * (150 + 18) - 18 + 16
    image, draw, fonts, margin, body_y = section_canvas(
        theme,
        name + " — PHYSICAL COLOR MAP",
        "Unique HEX values and every base token / configured role / fallback role resolving to them.",
        body_height,
        detail_width,
    )
    physical_color_map(
        draw, margin, body_y, detail_width - margin * 2,
        theme, contract, fonts)
    physical_path = output_dir / "01-physical-color-map.png"
    image.save(physical_path)

    # Palette.
    body_height = 4 * (30 + 96 + 24) + 12
    image, draw, fonts, margin, body_y = section_canvas(
        theme,
        name + " — COMPLETE THEMECOLOR PALETTE",
        "All ThemeColor tokens, grouped by purpose, with HEX and contrast against background / panel.",
        body_height,
        detail_width,
    )
    palette(
        draw, margin, body_y, detail_width - margin * 2,
        theme, fonts)
    palette_path = output_dir / "02-theme-color-palette.png"
    image.save(palette_path)

    # Controls.
    control_rows = 3
    body_height = control_rows * (165 + 18) - 18 + 16
    image, draw, fonts, margin, body_y = section_canvas(
        theme,
        name + " — CONTROL / TOKEN GALLERY",
        "Representative role-based controls plus direct ThemeColor usage.",
        body_height,
        detail_width,
    )
    control_gallery(
        draw, margin, body_y, detail_width - margin * 2,
        theme, fonts)
    controls_path = output_dir / "03-control-token-gallery.png"
    image.save(controls_path)

    # Roles.
    role_rows = int(math.ceil(len(contract["roles"]) / 2.0))
    body_height = role_rows * (210 + 18) - 18 + 16
    image, draw, fonts, margin, body_y = section_canvas(
        theme,
        name + " — THEMEROLE / FALLBACK AUDIT",
        "Every ThemeRole: fully configured component vs the same component with only that role omitted.",
        body_height,
        detail_width,
    )
    role_gallery(
        draw, margin, body_y, detail_width - margin * 2,
        theme, contract, fonts)
    roles_path = output_dir / "04-theme-role-fallback-audit.png"
    image.save(roles_path)

    # Contrast.
    body_height = 50 + 9 * (54 + 6) + 52
    image, draw, fonts, margin, body_y = section_canvas(
        theme,
        name + " — BASE TOKEN CONTRAST MATRIX",
        "Common text/accent tokens against common surfaces.",
        body_height,
        detail_width,
    )
    contrast_matrix(
        draw, margin, body_y, detail_width - margin * 2,
        theme, fonts)
    contrast_path = output_dir / "05-base-token-contrast-matrix.png"
    image.save(contrast_path)

    # Thumbnails specifically designed for the root batch index.
    render_thumb_palette(
        theme, output_dir / "thumb-palette.png")
    render_thumb_controls(
        theme, output_dir / "thumb-controls.png")
    render_thumb_roles(
        theme, contract, output_dir / "thumb-roles.png")

    sections = (
        ("Overview", "00-overview.png",
         "A compact first impression of the palette and common controls."),
        ("Physical color map", "01-physical-color-map.png",
         "Which ThemeColor and ThemeRole values collapse to the same physical HEX."),
        ("ThemeColor palette", "02-theme-color-palette.png",
         "Every base token, including tokens that have no semantic role."),
        ("Controls and direct tokens", "03-control-token-gallery.png",
         "Representative controls and direct ThemeColor usage."),
        ("ThemeRole / fallback audit", "04-theme-role-fallback-audit.png",
         "Every role with only that role removed on the fallback side."),
        ("Contrast matrix", "05-base-token-contrast-matrix.png",
         "Common token/surface combinations and their contrast ratios."),
    )

    detail_html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — Feather theme preview</title>
<style>
:root {{
  color-scheme: light dark;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 30px;
  background: #{background};
  color: #{text};
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
.wrap {{ max-width: 1840px; margin: 0 auto; }}
nav {{ margin-bottom: 24px; }}
nav a {{
  color: #{primary};
  text-decoration: none;
  font-weight: 700;
}}
h1 {{ margin: 0 0 8px; }}
.lead {{ margin: 0 0 30px; color: #{dim}; }}
section {{
  margin: 0 0 44px;
}}
section h2 {{
  margin: 0 0 6px;
}}
section p {{
  margin: 0 0 14px;
  color: #{dim};
}}
img {{
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid #{border};
  background: #{background};
}}
</style>
</head>
<body>
<div class="wrap">
<nav><a href="../../index.html">← All themes</a></nav>
<h1>{name}</h1>
<p class="lead">{description}</p>
{sections}
</div>
</body>
</html>
""".format(
        name=html.escape(name),
        description=html.escape(description),
        background=colors["background"],
        text=colors["text"],
        dim=colors["dim"],
        primary=colors["primary"],
        border=colors["border"],
        sections="\n".join(
            "<section><h2>%s</h2><p>%s</p><img src=\"%s\" alt=\"%s\"></section>"
            % (
                html.escape(title),
                html.escape(description_text),
                filename,
                html.escape(title),
            )
            for title, filename, description_text in sections
        ),
    )
    (output_dir / "index.html").write_text(detail_html, encoding="utf-8")

    return {
        "name": name,
        "description": description,
        "theme": theme,
        "directory": output_dir,
    }


def find_theme_files(directory, recursive=True):
    directory = Path(directory)
    candidates = (
        directory.rglob("*.json")
        if recursive else directory.glob("*.json")
    )

    result = []
    for path in sorted(candidates):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(document, dict):
            continue
        if "colors" not in document:
            continue

        result.append(path)

    return result


def build_batch_index(entries, output_dir, source_dir, contract_mode):
    output_dir = Path(output_dir)

    cards = []
    for entry in entries:
        theme = entry["theme"]
        colors = theme["colors"]
        slug = entry["slug"]
        name = entry["name"]
        description = entry["description"]

        cards.append(
            """<article class="theme-card" style="
                --card-bg: #{panel};
                --card-text: #{text};
                --card-border: #{border};
                --card-accent: #{primary};
              ">
              <a class="card-link" href="themes/{slug}/index.html">
                <div class="card-head">
                  <div>
                    <h2>{name}</h2>
                    <p>{description}</p>
                  </div>
                  <span class="open">Open →</span>
                </div>
                <div class="thumbs">
                  <figure>
                    <img src="themes/{slug}/thumb-palette.png" alt="{name} palette">
                    <figcaption>Palette</figcaption>
                  </figure>
                  <figure>
                    <img src="themes/{slug}/thumb-controls.png" alt="{name} controls">
                    <figcaption>Controls</figcaption>
                  </figure>
                  <figure>
                    <img src="themes/{slug}/thumb-roles.png" alt="{name} roles">
                    <figcaption>Roles</figcaption>
                  </figure>
                </div>
              </a>
            </article>""".format(
                slug=html.escape(slug),
                name=html.escape(name),
                description=html.escape(description),
                panel=colors["panel"],
                text=colors["text"],
                border=colors["border"],
                primary=colors["primary"],
            )
        )

    index_html = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feather UI theme previews</title>
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  padding: 34px;
  background: #f1f1f1;
  color: #202020;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}}
.wrap {{ max-width: 1900px; margin: 0 auto; }}
header {{ margin-bottom: 30px; }}
h1 {{ margin: 0 0 8px; }}
header p {{ margin: 0; color: #666; }}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(720px, 1fr));
  gap: 24px;
}}
.theme-card {{
  background: var(--card-bg);
  color: var(--card-text);
  border: 1px solid var(--card-border);
  min-width: 0;
}}
.card-link {{
  display: block;
  padding: 18px;
  color: inherit;
  text-decoration: none;
}}
.card-head {{
  display: flex;
  gap: 20px;
  align-items: start;
  justify-content: space-between;
  margin-bottom: 16px;
}}
.card-head h2 {{ margin: 0 0 6px; }}
.card-head p {{
  margin: 0;
  opacity: .72;
  line-height: 1.35;
}}
.open {{
  color: var(--card-accent);
  white-space: nowrap;
  font-weight: 700;
}}
.thumbs {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}}
figure {{
  margin: 0;
  border: 1px solid var(--card-border);
  min-width: 0;
  background: var(--card-bg);
}}
figure img {{
  display: block;
  width: 100%;
  height: auto;
}}
figcaption {{
  padding: 7px 9px;
  font-size: 12px;
  opacity: .7;
  border-top: 1px solid var(--card-border);
}}
@media (max-width: 900px) {{
  body {{ padding: 16px; }}
  .grid {{ grid-template-columns: 1fr; }}
  .thumbs {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>Feather UI theme previews</h1>
  <p>{count} themes from <code>{source}</code> • contract mode: {contract_mode}</p>
</header>
<div class="grid">
{cards}
</div>
</div>
</body>
</html>
""".format(
        count=len(entries),
        source=html.escape(str(source_dir)),
        contract_mode=html.escape(contract_mode),
        cards="\n".join(cards),
    )

    (output_dir / "index.html").write_text(index_html, encoding="utf-8")


def render_batch(
        batch_dir, output_dir, contract_path=None,
        detail_width=1800, recursive=True, clean=True):
    contract = load_contract(contract_path)
    batch_dir = Path(batch_dir)
    output_dir = Path(output_dir)

    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    themes_output = output_dir / "themes"
    themes_output.mkdir(parents=True, exist_ok=True)

    theme_files = find_theme_files(batch_dir, recursive=recursive)
    if not theme_files:
        raise RuntimeError("no theme JSON files found in %s" % batch_dir)

    entries = []
    used_slugs = set()

    for theme_file in theme_files:
        try:
            theme = load_theme(theme_file, contract)
        except Exception as exc:
            print("SKIP %s: %s" % (theme_file, exc))
            continue

        relative = theme_file.relative_to(batch_dir)
        base_slug = slugify(str(relative.with_suffix("")).replace("/", "__"))
        slug = base_slug
        counter = 2
        while slug in used_slugs:
            slug = "%s-%d" % (base_slug, counter)
            counter += 1
        used_slugs.add(slug)

        theme_dir = themes_output / slug
        rendered = render_theme_site(
            theme_file, theme_dir, contract, detail_width=detail_width)
        rendered["slug"] = slug
        entries.append(rendered)
        print("RENDER %s -> %s" % (theme_file, theme_dir))

    entries.sort(key=lambda item: item["name"].lower())
    build_batch_index(
        entries, output_dir, batch_dir, contract["mode"])

    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "theme", nargs="?", type=Path,
        help="single theme JSON; omit when --batch-dir is used")
    parser.add_argument(
        "--batch-dir", type=Path,
        help="generate every theme JSON found in this directory")
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="output directory")
    parser.add_argument(
        "--contract", type=Path,
        help="real Feather theme.py; enums/defaults/resolution are loaded from it")
    parser.add_argument(
        "--detail-width", type=int, default=1800,
        help="width of detailed PNGs (default: 1800)")
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="batch mode: only scan the immediate directory")
    parser.add_argument(
        "--no-clean", action="store_true",
        help="batch mode: do not remove the existing output directory first")
    args = parser.parse_args()

    if bool(args.theme) == bool(args.batch_dir):
        parser.error("specify exactly one of THEME or --batch-dir")

    contract = load_contract(args.contract)

    if args.batch_dir is not None:
        entries = render_batch(
            args.batch_dir,
            args.output_dir,
            args.contract,
            detail_width=max(1400, min(args.detail_width, 2600)),
            recursive=not args.no_recursive,
            clean=not args.no_clean,
        )
        print("INDEX %s" % (args.output_dir / "index.html"))
        print("THEMES %d" % len(entries))
        return

    if args.output_dir.exists() and not args.no_clean:
        shutil.rmtree(args.output_dir)

    render_theme_site(
        args.theme,
        args.output_dir,
        contract,
        detail_width=max(1400, min(args.detail_width, 2600)),
    )
    print("INDEX %s" % (args.output_dir / "index.html"))


if __name__ == "__main__":
    main()
