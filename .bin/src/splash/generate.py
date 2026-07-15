#!/usr/bin/env python3
"""Generate the 800x480 BGRA/XZ boot screens used by /dev/fb0."""

import argparse
import lzma
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 800, 480
BG = "#030607"
CYAN = "#35d9e6"
VIOLET = "#b47aff"
AMBER = "#f2c94c"
TEXT = "#d9e4e8"
DIM = "#56656c"


def first_existing(paths):
    for path in paths:
        candidate = Path(path).expanduser()
        if candidate.is_file():
            return candidate
    return None


def find_fonts(regular=None, bold=None):
    regular_path = Path(regular).expanduser() if regular else first_existing([
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/Andale Mono.ttf",
    ])
    bold_path = Path(bold).expanduser() if bold else first_existing([
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/Library/Fonts/Courier New Bold.ttf",
    ])
    if regular_path is None:
        raise SystemExit("Monospace TTF not found; pass --font /path/to/font.ttf")
    return regular_path, bold_path or regular_path


class TerminalScreen:
    def __init__(self, regular, bold):
        self.image = Image.new("RGBA", (WIDTH, HEIGHT), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.regular = regular
        self.bold = bold

    def font(self, size, bold=False):
        return ImageFont.truetype(str(self.bold if bold else self.regular), size)

    def text(self, xy, value, size, color=TEXT, anchor="mm", bold=False):
        self.draw.text(xy, value, font=self.font(size, bold), fill=color,
                       anchor=anchor, stroke_width=0)

    def line(self, points, color=CYAN, width=1):
        self.draw.line(points, fill=color, width=width)

    def box(self, xy, color=CYAN, width=2):
        self.draw.rectangle(xy, outline=color, width=width)

    def base(self, title, product="FORGE-X"):
        self.box((16, 16, 783, 463), CYAN, 1)
        self.line((16, 58, 783, 58), CYAN)
        self.text((32, 37), "%s // %s" % (product, title), 15, CYAN,
                  anchor="lm", bold=True)
        self.text((768, 37), "FLASHFORGE / AD5M", 14, DIM, anchor="rm")

    def versions(self):
        self.box((70, 238, 385, 326), VIOLET, 2)
        self.box((415, 238, 730, 326), CYAN, 2)
        self.text((228, 258), "MOD VERSION", 13, VIOLET, bold=True)
        self.text((572, 258), "FIRMWARE VERSION", 13, CYAN, bold=True)
        # screen.sh writes the live values at x=236/592, y=300.

    def logo(self, product, subtitle):
        self.text((400, 140), product, 67, CYAN, bold=True)
        self.text((400, 199), "[ %s ]" % subtitle, 16, VIOLET)


def make_splash(regular, bold):
    screen = TerminalScreen(regular, bold)
    screen.base("SYSTEM CONSOLE")
    screen.logo("FORGE-X", "PRINTER MOD // LOCAL CONTROL")
    screen.versions()
    screen.line((70, 350, 730, 350), DIM)
    screen.text((400, 382), "SYSTEM READY", 16, CYAN, bold=True)
    screen.box((200, 418, 600, 462), CYAN, 1)
    screen.text((400, 440), "LOCAL CONTROL // OFFLINE CAPABLE", 13, DIM)
    return screen.image


def make_loading(regular, bold):
    screen = TerminalScreen(regular, bold)
    screen.base("BOOT SEQUENCE")
    screen.logo("FORGE-X", "INITIALIZING SYSTEM MOD")
    screen.versions()
    screen.text((70, 337), "BOOT LOG // WAITING FOR EVENTS", 12, AMBER,
                anchor="lm", bold=True)
    screen.line((330, 337, 730, 337), DIM)
    # boot_message clears and redraws the full y=350..480 area with up to five
    # log rows plus uptime. Keep it completely empty, including the base frame,
    # so no static artwork can leak through between short messages.
    screen.draw.rectangle((0, 350, WIDTH, HEIGHT), fill=BG)
    return screen.image


def make_feather_splash(regular, bold):
    screen = TerminalScreen(regular, bold)
    screen.base("LOCAL CONSOLE", product="FEATHER")
    screen.logo("FEATHER", "LIGHTWEIGHT PRINTER INTERFACE")
    screen.versions()
    screen.line((70, 350, 730, 350), DIM)
    screen.text((400, 382), "LOCAL UI READY", 16, CYAN, bold=True)
    screen.box((200, 418, 600, 462), VIOLET, 1)
    screen.text((400, 440), "TOUCH CONTROL // OFFLINE CAPABLE", 13, DIM)
    return screen.image


def write_xz(image, destination):
    raw = image.tobytes("raw", "BGRA")
    if len(raw) != WIDTH * HEIGHT * 4:
        raise RuntimeError("Unexpected framebuffer size: %d" % len(raw))
    destination.write_bytes(lzma.compress(
        raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=9))


def main():
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", help="regular monospace TTF")
    parser.add_argument("--bold-font", help="bold monospace TTF")
    parser.add_argument("--output-dir", type=Path, default=repo)
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()

    regular, bold = find_fonts(args.font, args.bold_font)
    splash = make_splash(regular, bold)
    loading = make_loading(regular, bold)
    feather_splash = make_feather_splash(regular, bold)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_xz(splash, args.output_dir / "splash.img.xz")
    write_xz(loading, args.output_dir / "load.img.xz")
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        splash.convert("RGB").save(args.preview_dir / "forge-x-splash.png")
        loading.convert("RGB").save(args.preview_dir / "forge-x-loading.png")
        feather_splash.convert("RGB").save(
            args.preview_dir / "feather-splash.png")


if __name__ == "__main__":
    main()
