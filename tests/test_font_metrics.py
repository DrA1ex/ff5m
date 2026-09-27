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

from ff5m_ui.benchmark import page as benchmark_page  # noqa: E402
from ff5m_ui.benchmark import layout_page as benchmark_layout_page  # noqa: E402
from ff5m_ui.filament.action import page as filament_action_page  # noqa: E402
from ff5m_ui.filament.material import page as filament_material_page  # noqa: E402
from ff5m_ui.heat import page as heat_page  # noqa: E402
from ff5m_ui.home import page as home_page  # noqa: E402
from ff5m_ui.move import runtime as move_ui  # noqa: E402
from ff5m_ui.z_offset import runtime as z_offset_ui  # noqa: E402
from ui import FeatherRenderer, font_metrics  # noqa: E402
from ui import renderer as ui_renderer  # noqa: E402


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

    def test_packaged_manifest_is_valid_and_covers_product_font_families(self):
        loaded = font_metrics.load_fallback_metrics()

        self.assertTrue(loaded.fonts)
        self.assertEqual(tuple(sorted(loaded.names)), loaded.names)
        for family in ("JetBrainsMono", "JetBrainsMono Bold", "Roboto", "Roboto Bold"):
            self.assertEqual(
                {name for name in loaded.names if name.rsplit(" ", 1)[0] == family},
                {f"{family} {size}pt" for size in (8, 10, 12, 16)})
        self.assertIn(loaded.default_font, loaded.fonts)
        self.assertFalse(hasattr(loaded, "catalog"))
        for metric in loaded.fonts.values():
            self.assertGreater(metric.advance_y, 0)
            self.assertLess(metric.top, metric.bottom)
            self.assertTrue(metric.unicode_ranges)

        requested = set()

        class RecordingMetrics:
            def __getattr__(self, name):
                return getattr(loaded, name)

            def normalize_font(self, font, allow_proportional=False):
                requested.add(font)
                return loaded.normalize_font(font, allow_proportional)

            def normalize_for_text(self, font, value):
                requested.add(font)
                return loaded.normalize_for_text(font, value)

        pages = (
            home_page.PAGE,
            benchmark_page.PAGE,
            benchmark_layout_page.PAGE,
            heat_page.create_page(("PLA", "ABS")),
            filament_material_page.create_page((("PLA", 220), ("ABS", 250))),
            filament_action_page.create_page(False),
            move_ui.STEP_PAGE,
            move_ui.JOYSTICK_PAGE,
            z_offset_ui.SAFE_BRIEFING_PAGE,
            z_offset_ui.SAFE_PAGE,
            z_offset_ui.BRIEFING_PAGE,
            z_offset_ui.PAPER_BRIEFING_PAGE,
            z_offset_ui.PAPER_PAGE,
            z_offset_ui.SUMMARY_PAGE,
        )
        with mock.patch.object(
                ui_renderer, "get_font_metrics",
                return_value=RecordingMetrics()):
            for page in pages:
                page.draw(FeatherRenderer(), page.initial_state())

        self.assertTrue(requested)
        missing_families = []
        for requested_font in sorted(requested):
            normalized = loaded.normalize_font(requested_font)
            requested_family = requested_font.rsplit(" ", 1)[0]
            normalized_family = normalized.rsplit(" ", 1)[0]
            if requested_family != normalized_family:
                missing_families.append(requested_font)
        self.assertEqual(missing_families, [])
        self.assertEqual(
            loaded.normalize_for_text("Roboto 10pt", "ẞ"),
            "JetBrainsMono 10pt")

        with open(font_metrics.FALLBACK_PATH, "r", encoding="utf-8") as stream:
            generated = json.load(stream)
        self.assertNotIn("default_font", generated)
        for entry in generated["fonts"]:
            self.assertNotIn("fallback", entry)
            self.assertNotIn("preview", entry)

    def test_runtime_metrics_inherit_project_fallback_policy(self):
        policy_value = {
            "schema": "font-metrics/v1",
            "wrap_algorithm": "word-v1",
            "fonts": [
                {
                    "name": "Display 12pt",
                    "advance_x": 8,
                    "monospaced": True,
                    "advance_y": 16,
                    "glyph_bounds": {"top": -12, "bottom": 3},
                    "unicode_ranges": [[32, 126]],
                },
                {
                    "name": "Text 12pt",
                    "advance_x": 9,
                    "monospaced": True,
                    "advance_y": 17,
                    "glyph_bounds": {"top": -13, "bottom": 3},
                    "unicode_ranges": [[32, 126], [1040, 1103]],
                },
            ],
        }
        runtime_value = json.loads(json.dumps(policy_value))
        completed = subprocess.CompletedProcess(
            ["typer", "--font-manifest"], 0,
            stdout=json.dumps(runtime_value).encode("utf-8"), stderr=b"")

        with mock.patch("subprocess.run", return_value=completed):
            loaded = font_metrics.load_runtime_metrics(
                "/real/typer",
                fallback=font_metrics.apply_font_policy(
                    font_metrics.parse_manifest(policy_value),
                    default_font="Text 12pt",
                    fallbacks={"Display 12pt": "Text 12pt"}))

        self.assertEqual(loaded.default_font, "Text 12pt")
        self.assertEqual(loaded.fonts["Display 12pt"].fallback, "Text 12pt")

    def test_project_policy_constants_can_be_overridden(self):
        value = {
            "schema": "font-metrics/v1",
            "wrap_algorithm": "word-v1",
            "fonts": [
                {
                    "name": "Display 12pt",
                    "advance_x": 8,
                    "monospaced": True,
                    "advance_y": 16,
                    "glyph_bounds": {"top": -12, "bottom": 3},
                    "unicode_ranges": [[32, 126]],
                },
                {
                    "name": "Text 12pt",
                    "advance_x": 9,
                    "monospaced": True,
                    "advance_y": 17,
                    "glyph_bounds": {"top": -13, "bottom": 3},
                    "unicode_ranges": [[32, 126], [1040, 1103]],
                },
            ],
        }
        with mock.patch.object(font_metrics, "DEFAULT_FONT", "Text 12pt"), \
                mock.patch.object(
                    font_metrics, "FONT_FALLBACKS",
                    {"Display 12pt": "Text 12pt"}):
            parsed = font_metrics.parse_manifest(value)
            configured = font_metrics.parse_project_manifest(value)

        self.assertIs(font_metrics.apply_font_policy(
            parsed, default_font="Text 12pt",
            fallbacks={"Display 12pt": "Text 12pt"}), parsed)
        self.assertEqual(configured.default_font, "Text 12pt")
        self.assertEqual(
            configured.fonts["Display 12pt"].fallback, "Text 12pt")

    def test_text_fallback_is_declared_by_project_policy(self):
        value = {
            "schema": "font-metrics/v1",
            "wrap_algorithm": "word-v1",
            "fonts": [
                {
                    "name": "Display 12pt",
                    "advance_x": 8,
                    "monospaced": True,
                    "advance_y": 16,
                    "glyph_bounds": {"top": -12, "bottom": 3},
                    "unicode_ranges": [[32, 126]],
                },
                {
                    "name": "Text 12pt",
                    "advance_x": 9,
                    "monospaced": True,
                    "advance_y": 17,
                    "glyph_bounds": {"top": -13, "bottom": 3},
                    "unicode_ranges": [[32, 126], [1040, 1103]],
                },
            ],
        }
        metrics = font_metrics.apply_font_policy(
            font_metrics.parse_manifest(value),
            default_font="Text 12pt",
            fallbacks={"Display 12pt": "Text 12pt"})

        self.assertEqual(
            metrics.normalize_for_text("Display 12pt", "HELLO"),
            "Display 12pt")
        self.assertEqual(
            metrics.normalize_for_text("Display 12pt", "ПРИВЕТ"),
            "Text 12pt")
        self.assertEqual(
            metrics.normalize_font("Missing 12pt"),
            "Text 12pt")


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
