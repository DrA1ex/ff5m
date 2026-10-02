## Retained-dialog visual suite contracts.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from tests.visual_checks import composition
from tests.visual_checks.composition_scenes import (
    FeatherScreen, drawing_operations, run_sequence,
)


class CompositionVisualTest(unittest.TestCase):
    def test_all_layouts_preserve_history_and_draw_contracts(self):
        for layout in composition.LAYOUTS:
            with self.subTest(layout=layout):
                steps = run_sequence(layout)
                self.assertFalse([step["failures"] for step in steps if step["failures"]])
                previous = []
                for step in steps:
                    history = step["composition_fixture"]["operations"]
                    self.assertEqual(history[:len(previous)], previous)
                    self.assertEqual(history[len(previous):], step["dirty_operations"])
                    previous = history
                self.assertTrue(any(node["node"] == "background" for node in steps[0]["render_calls"]))

    def test_hidden_data_is_revealed_after_close(self):
        steps = {step["id"].removeprefix("cards-"): step for step in run_sequence("cards")}
        held = steps["background-held"]
        self.assertEqual(held["batches"], [])
        self.assertEqual(held["composition_fixture"], dict(
            steps["text"]["composition_fixture"], title=held["composition_fixture"]["title"]))
        close = steps["close"]
        text = [op["value"] for op in close["dirty_operations"] if op["type"] == "text"]
        self.assertIn("LATEST DATA", text)
        self.assertIn("STATUS: LATEST DATA", text)
        self.assertNotIn("NEXT DIALOG", text)
        self.assertEqual(steps["button-busy"]["controls"], ["global.wake"])

    def test_size_probe_is_not_an_extra_accepted_frame(self):
        steps = {step["id"].removeprefix("cards-"): step for step in run_sequence("cards")}
        for name in ("shrink", "grow"):
            step = steps[name]
            self.assertEqual(step["counts"]["dialog"], 2)
            self.assertEqual(step["counts"]["batches"], 1)
            self.assertEqual(step["counts"]["scrim"], 1)
            self.assertEqual(step["batches"][0]["kind"], "surface")

    def test_repeated_scrim_is_recorded_and_reported_as_failure(self):
        original = FeatherScreen._paint_dialog

        def broken_paint(screen, instance):
            screen.renderer.send((screen.renderer.modal_scrim(),))
            original(screen, instance)

        with mock.patch.object(FeatherScreen, "_paint_dialog", broken_paint):
            steps = run_sequence("cards")
        opening = next(step for step in steps if step["id"] == "cards-open")
        self.assertTrue(opening["failures"])
        self.assertEqual(opening["counts"]["scrim"], 2)
        alpha_fills = [op for op in opening["dirty_operations"]
                       if op["type"] == "fill" and op["color"].startswith("rgba")]
        self.assertEqual(len(alpha_fills), 2)

    def test_protocol_adapter_keeps_alpha_and_rejects_unknown_drawing(self):
        ops = drawing_operations(["--batch fill -p 0 0 -s 800 480 -c B3000000"])
        self.assertEqual(ops[0]["color"], "rgba(0,0,0,0.70196078)")
        with self.assertRaisesRegex(ValueError, "Unsupported drawing command"):
            drawing_operations(["--batch unimplemented -p 0 0"])

    def test_missing_screenshots_preserve_trace_and_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(composition.DesignerCapture, "capture", return_value=[]):
                code = composition.main([
                    "--layout", "cards", "--designer-root", directory, "--output", directory])
            self.assertEqual(code, 1)
            output = pathlib.Path(directory)
            self.assertTrue(json.loads((output / "trace.json").read_text()))
            report = (output / "report.html").read_text()
            self.assertIn("Missing screenshots", report)
            self.assertIn("Screenshot unavailable", report)

    def test_report_escapes_trace_and_keeps_manual_review_explicit(self):
        steps = run_sequence("cards")[:1]
        steps[0]["description"] = "<script>unsafe</script>"
        with tempfile.TemporaryDirectory() as directory:
            composition.write_report(directory, steps, [])
            report = (pathlib.Path(directory) / "report.html").read_text()
        self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", report)
        self.assertIn("visual inspection required", report)
        self.assertNotIn("<script>unsafe</script>", report)

    def test_previous_run_artifacts_cannot_be_mistaken_for_current_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = pathlib.Path(directory) / "report.html"
            marker.write_text("previous run")
            with self.assertRaises(SystemExit) as stopped:
                composition.main(["--trace-only", "--output", directory])
            self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(marker.read_text(), "previous run")
