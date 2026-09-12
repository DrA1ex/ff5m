"""Reusable Python contracts for optional Typer render receipts."""

import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


PLUGINS = pathlib.Path(__file__).parents[1] / ".py" / "klipper" / "plugins"
sys.path.insert(0, str(PLUGINS))

import feather_screen  # noqa: E402
from ui import (  # noqa: E402
    ReceiptTracker, RenderReceipt, parse_render_receipt,
    validate_render_receipt_token,
)


class RenderReceiptProtocolTest(unittest.TestCase):
    def test_parser_accepts_success_and_failed_receipts(self):
        self.assertEqual(
            parse_render_receipt("render 7:142 ok 18420 17610 820"),
            RenderReceipt("7:142", True, 18420, 17610, 820))
        self.assertEqual(
            parse_render_receipt("render 7:143 failed 630 590 0"),
            RenderReceipt("7:143", False, 630, 590, 0))

    def test_parser_rejects_malformed_or_inconsistent_receipts(self):
        invalid = (
            "tap nav.back",
            "render",
            "render bad token ok 1 1 1",
            "render 1:2 pending 1 1 1",
            "render 1:2 ok -1 1 0",
            "render 1:2 ok 10 8 11",
            "render 1:2 ok ten 8 1",
        )
        self.assertTrue(all(parse_render_receipt(line) is None
                            for line in invalid))

    def test_token_validation_matches_typer_wire_contract(self):
        self.assertEqual(validate_render_receipt_token("session-1.frame:2"),
                         "session-1.frame:2")
        for token in ("", "bad token", "x" * 65, "bad/slash"):
            with self.assertRaises(ValueError):
                validate_render_receipt_token(token)


class ScreenReceiptRoutingTest(unittest.TestCase):
    def test_event_fifo_receipt_is_routed_without_touch_dispatch(self):
        screen = feather_screen.FeatherScreen.__new__(
            feather_screen.FeatherScreen)
        screen.renderer = SimpleNamespace(event_fd=7)
        screen.event_partial = ""
        notifications = []
        screen.feature_manager = SimpleNamespace(
            notify=lambda *args: notifications.append(args))

        with mock.patch(
                "feather_screen.os.read",
                return_value=b"render 3:5 ok 1200 900 100\n"):
            screen._process_touch_events(42.0)

        self.assertEqual(len(notifications), 1)
        hook, receipt, eventtime = notifications[0]
        self.assertEqual(hook, "on_render_receipt")
        self.assertEqual(receipt, RenderReceipt("3:5", True, 1200, 900, 100))
        self.assertEqual(eventtime, 42.0)


class ReceiptTrackerTest(unittest.TestCase):
    def test_exact_pending_token_resolves_once_with_latency_and_metadata(self):
        tracker = ReceiptTracker(timeout=1.0)
        metadata = {"python_ms": 0.7}
        tracker.expect("4:9", 10.0, metadata)

        self.assertIsNone(tracker.resolve(
            RenderReceipt("old:8", True, 1, 1, 0), 10.2))
        self.assertIsNotNone(tracker.pending)
        measurement = tracker.resolve(
            RenderReceipt("4:9", True, 8000, 7000, 1000), 10.025)

        self.assertAlmostEqual(measurement.latency_ms, 25.0)
        self.assertEqual(measurement.metadata, metadata)
        self.assertIsNone(tracker.pending)
        self.assertIsNone(tracker.resolve(measurement.receipt, 10.03))

    def test_one_in_flight_timeout_and_cancel_contract(self):
        tracker = ReceiptTracker(timeout=0.5)
        tracker.expect("1:1", 20.0)
        with self.assertRaises(RuntimeError):
            tracker.expect("1:2", 20.1)
        self.assertFalse(tracker.expired(20.499))
        self.assertTrue(tracker.expired(20.5))
        pending = tracker.cancel()
        self.assertEqual(pending.token, "1:1")
        self.assertIsNone(tracker.pending)
        self.assertIsNone(tracker.cancel())


if __name__ == "__main__":
    unittest.main()
