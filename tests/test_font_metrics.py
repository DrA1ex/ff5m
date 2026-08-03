"""Typer manifest loading and word-v1 parity tests."""

import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock


PLUGINS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
           "plugins")
sys.path.insert(0, str(PLUGINS))

from ui import font_metrics  # noqa: E402


def manifest(advance=16):
    return {
        "schema": "font-metrics/v1",
        "wrap_algorithm": "word-v1",
        "fonts": [{
            "name": "JetBrainsMono 12pt",
            "advance_x": advance,
            "monospaced": True,
            "advance_y": 33,
            "glyph_bounds": {"top": -26, "bottom": 5},
            "unicode_ranges": [
                [32, 126], [1025, 1025], [1040, 1103], [1105, 1105],
            ],
        }],
    }


class FontManifestTest(unittest.TestCase):
    def test_runtime_manifest_overrides_packaged_fallback(self):
        fallback = font_metrics.parse_manifest(manifest(advance=16))
        completed = subprocess.CompletedProcess(
            ["typer", "--font-manifest"], 0,
            stdout=json.dumps(manifest(advance=19)).encode("utf-8"), stderr=b"")

        with mock.patch("subprocess.run", return_value=completed):
            loaded = font_metrics.load_runtime_metrics(
                "/real/typer", fallback=fallback)

        self.assertEqual(loaded.metric("JetBrainsMono 12pt").advance_x, 19)

    def test_corrupt_manifest_is_rejected(self):
        damaged = manifest()
        damaged["fonts"][0]["unicode_ranges"] = [[1040, 1103], [32, 126]]

        with self.assertRaisesRegex(ValueError, "sorted and disjoint"):
            font_metrics.parse_manifest(damaged)

        fallback = font_metrics.parse_manifest(manifest(advance=17))
        completed = subprocess.CompletedProcess(
            ["typer", "--font-manifest"], 0,
            stdout=json.dumps(damaged).encode("utf-8"), stderr=b"")
        with mock.patch("subprocess.run", return_value=completed):
            loaded = font_metrics.load_runtime_metrics(
                "/damaged/typer", fallback=fallback)
        self.assertIs(loaded, fallback)

    def test_old_typer_uses_packaged_fallback(self):
        fallback = font_metrics.parse_manifest(manifest(advance=17))
        completed = subprocess.CompletedProcess(
            ["typer", "--font-manifest"], 2, stdout=b"", stderr=b"old")

        with mock.patch("subprocess.run", return_value=completed):
            loaded = font_metrics.load_runtime_metrics(
                "/old/typer", fallback=fallback)

        self.assertIs(loaded, fallback)

    def test_packaged_manifest_is_valid_and_sorted(self):
        loaded = font_metrics.load_fallback_metrics()

        self.assertEqual(len(loaded.fonts), 20)
        self.assertEqual(tuple(sorted(loaded.names)), loaded.names)
        self.assertTrue(loaded.metric("JetBrainsMono 12pt").monospaced)
        self.assertEqual(
            loaded.normalize_font("Roboto 16pt"),
            "JetBrainsMono 16pt")
        self.assertEqual(
            loaded.normalize_font("Roboto 16pt", allow_proportional=True),
            "Roboto 16pt")
        self.assertFalse(loaded.fonts["Roboto 16pt"].monospaced)


class WordV1ParityTest(unittest.TestCase):
    def setUp(self):
        self.metrics = font_metrics.load_fallback_metrics()
        self.font = "JetBrainsMono 12pt"

    def assert_wrap(self, value, width, expected):
        self.assertEqual(
            self.metrics.wrap_text(value, self.font, width), expected)

    def test_short_line(self):
        self.assert_wrap("SHORT", 200, ["SHORT"])

    def test_word_boundaries(self):
        self.assert_wrap(
            "ONE TWO THREE FOUR", 7 * 16,
            ["ONE TWO", "THREE", "FOUR"])

    def test_long_word(self):
        self.assert_wrap("ABCDE", 2 * 16, ["AB", "CD", "E"])

    def test_cyrillic(self):
        self.assert_wrap("АБВГД", 2 * 16, ["АБ", "ВГ", "Д"])

    def test_explicit_line_breaks(self):
        self.assert_wrap(
            "ONE\nTWO THREE\n\nFOUR", 9 * 16,
            ["ONE", "TWO THREE", "", "FOUR"])


if __name__ == "__main__":
    unittest.main()
