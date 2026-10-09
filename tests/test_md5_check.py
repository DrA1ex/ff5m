## Behavioral checks for cooperative G-code checksum verification.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import contextlib
import hashlib
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MD5 = load_module("md5_test_plugin", ".py/klipper/plugins/md5_check.py")
GCODE = load_module("md5_test_gcode", ".py/klipper/patches/gcode.py")


class Reactor:
    NOW = 0.
    mutex = staticmethod(contextlib.nullcontext)

    def __init__(self):
        self.now = 100.
        self.pauses = []
        self.on_pause = None

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.pauses.append((self.now, waketime))
        if self.on_pause:
            self.on_pause()
        return self.now


class Printer:
    config_error = ValueError
    command_error = GCODE.CommandError

    def __init__(self):
        self.reactor = Reactor()
        self.objects = {}
        self.events = {}
        self.shutdowns = []

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_start_args(self):
        return {}

    def register_event_handler(self, name, handler):
        self.events.setdefault(name, []).append(handler)

    def send_event(self, name, *args):
        for handler in self.events.get(name, ()):
            handler(*args)

    def invoke_shutdown(self, reason):
        self.shutdowns.append(reason)


class TimedFile:
    """Keep real file bytes, but account for deterministic read latency."""

    def __init__(self, stream, reactor, read_time):
        self.stream = stream
        self.reactor = reactor
        self.read_time = read_time

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, size=-1):
        data = self.stream.read(size)
        if data:
            self.reactor.now += self.read_time
        return data

    def readline(self, size=-1):
        data = self.stream.readline(size)
        if data:
            self.reactor.now += self.read_time
        return data


class MD5CheckTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "job.gcode"
        self.bitmap = self.path.with_suffix(".bmp")
        self.printer = printer = Printer()
        self.gcode = printer.objects["gcode"] = GCODE.GCodeDispatch(printer)
        printer.objects["virtual_sdcard"] = SimpleNamespace(file_path=lambda: str(self.path))
        printer.objects["mod_params"] = SimpleNamespace(variables={"display": 1})
        self.cancelled = []
        self.gcode.register_command("CANCEL_PRINT", lambda gcmd:
                                    self.cancelled.append(gcmd.get_command_parameters()))
        self.messages = []
        self.gcode.register_output_handler(self.messages.append)
        self.checker = MD5.load_config(SimpleNamespace(
            get_printer=lambda: printer, getboolean=lambda name, default: default))
        printer.send_event("klippy:ready")

    def write_checked(self, payload=b"G28\nG1 X10\n", header_end=b"\n"):
        self.path.write_bytes(b"; MD5:" + hashlib.md5(payload).hexdigest().encode("ascii")
                              + header_end + payload)
        return payload

    def timed_reads(self, read_time=.008):
        streams = []

        def open_timed(*args, **kwargs):
            stream = open(*args, **kwargs)
            streams.append(stream)
            return TimedFile(stream, self.printer.reactor, read_time)

        self.enterContext(mock.patch.object(MD5, "open", open_timed, create=True))
        return streams

    def command(self, params=""):
        self.gcode.run_script_from_command("CHECK_MD5 " + params)

    def test_digest_excludes_only_first_line_and_preserves_body_bytes(self):
        for header in (b"; MD5:ignored\n", b"; MD5:ignored\r\n", b"\n"):
            for payload in (b"", b"G28\r\nG1 X10\n", b"\x00\xff" * 100000):
                with self.subTest(header=header, size=len(payload)):
                    self.path.write_bytes(header + payload)
                    self.assertEqual(self.checker.calculate_md5(self.path),
                                     hashlib.md5(payload).hexdigest())

    def test_empty_and_header_only_files_hash_empty_payload(self):
        for data in (b"", b"header without newline", b"header\n"):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                self.assertEqual(self.checker.calculate_md5(self.path),
                                 hashlib.md5(b"").hexdigest())

    def test_slow_hashing_services_reactor_without_changing_digest(self):
        payload = self.write_checked(b"G1 X10\n" * 100000)
        self.timed_reads()
        serviced = []
        self.printer.reactor.on_pause = lambda: serviced.append(self.printer.reactor.now)
        self.assertEqual(self.checker.calculate_md5(self.path), hashlib.md5(payload).hexdigest())
        self.assertGreater(len(serviced), 1)
        intervals = [end - start for start, end in zip([100.] + serviced, serviced)]
        for interval in intervals:
            self.assertGreaterEqual(interval, .020 - 1.e-9)
            self.assertLessEqual(interval, .028 + 1.e-9)
        self.assertTrue(all(wake == self.printer.reactor.NOW
                            for _, wake in self.printer.reactor.pauses))

    def test_fast_small_file_needs_no_pause(self):
        payload = self.write_checked()
        self.timed_reads(.001)
        self.assertEqual(self.checker.calculate_md5(self.path), hashlib.md5(payload).hexdigest())
        self.assertEqual(self.printer.reactor.pauses, [])

    def test_work_budget_restarts_after_time_spent_in_other_callbacks(self):
        self.write_checked(b"G1 X10\n" * 100000)
        self.timed_reads(.012)
        reactor = self.printer.reactor
        reactor.on_pause = lambda: setattr(reactor, "now", reactor.now + .5)
        self.checker.calculate_md5(self.path)
        self.assertGreater(len(reactor.pauses), 1)
        previous = 100.
        for now, _ in reactor.pauses:
            self.assertAlmostEqual(now - previous, .024)
            previous = now + .5

    def test_pause_failure_closes_file_and_propagates_original_error(self):
        self.write_checked(b"G28\n" * 100000)
        streams = self.timed_reads()
        error = RuntimeError("reactor interrupted")
        self.printer.reactor.on_pause = mock.Mock(side_effect=error)
        with self.assertRaises(RuntimeError) as caught:
            self.checker.calculate_md5(self.path)
        self.assertIs(caught.exception, error)
        self.assertTrue(all(stream.closed for stream in streams))

    def test_valid_command_uses_selected_file_and_keeps_both_files(self):
        self.write_checked()
        self.bitmap.write_bytes(b"preview")
        self.command()
        self.assertTrue(self.path.exists())
        self.assertTrue(self.bitmap.exists())
        self.assertIn("INFO: MD5 checksum correct!", self.messages)
        self.assertEqual(self.cancelled, [])

    def test_explicit_filename_overrides_selected_file(self):
        self.write_checked()
        self.printer.objects["virtual_sdcard"].file_path = lambda: "missing.gcode"
        self.command(f'FILENAME="{self.path}"')
        self.assertEqual(self.cancelled, [])

    def test_valid_checksum_does_not_require_decoding_gcode_body(self):
        self.write_checked(b"; comment: \xff\x80\nG28\r\n", header_end=b"\r\n")
        self.command()
        self.assertEqual(self.cancelled, [])
        self.assertEqual(self.printer.shutdowns, [])

    def test_unchecked_binary_file_is_allowed(self):
        self.path.write_bytes(b"; comment: \xff\nG28\n")
        self.command()
        self.assertIn("WARNING: No MD5 checksum found in G-code.", self.messages)
        self.assertEqual(self.cancelled, [])

    def test_mismatch_cancels_print_and_obeys_delete_option(self):
        for delete in (True, False):
            for display in (0, 1):
                with self.subTest(delete=delete, display=display):
                    self.write_checked()
                    with self.path.open("ab") as stream:
                        stream.write(b"corruption")
                    self.bitmap.write_bytes(b"preview")
                    self.printer.objects["mod_params"].variables["display"] = display
                    self.cancelled.clear()
                    with self.assertRaisesRegex(GCODE.CommandError, "MD5 check failed"):
                        self.command(f"DELETE={delete}")
                    self.assertEqual(self.path.exists(), not delete)
                    self.assertEqual(self.bitmap.exists(), not delete)
                    self.assertEqual(self.cancelled, [{"REASON": "MD5 Mismatch"} if display else {}])

    def test_default_delete_policy_and_missing_preview(self):
        self.path.write_bytes(b"; MD5:wrong\nG28\n")
        with self.assertRaisesRegex(GCODE.CommandError, "MD5 check failed"):
            self.command()
        self.assertFalse(self.path.exists())
        self.checker.delete_invalid_files = False
        self.path.write_bytes(b"; MD5:wrong\nG28\n")
        with self.assertRaisesRegex(GCODE.CommandError, "MD5 check failed"):
            self.command()
        self.assertTrue(self.path.exists())

    def test_missing_file_directory_and_no_selected_file_cancel_without_shutdown(self):
        for filename in (str(self.path), str(self.path.parent), ""):
            with self.subTest(filename=filename):
                self.printer.objects["virtual_sdcard"].file_path = lambda: filename
                self.cancelled.clear()
                with self.assertRaisesRegex(GCODE.CommandError, "MD5 check failed"):
                    self.command()
                self.assertEqual(len(self.cancelled), 1)
                self.assertEqual(self.printer.shutdowns, [])

    def test_read_failure_cancels_without_deleting_or_shutting_down(self):
        for stage in ("header", "body"):
            with self.subTest(stage=stage):
                self.write_checked()
                self.bitmap.write_bytes(b"preview")
                self.cancelled.clear()
                calls = []

                def fail_open(*args, **kwargs):
                    calls.append(args)
                    if stage == "header" or len(calls) == 2:
                        raise OSError("storage unavailable")
                    return open(*args, **kwargs)

                with mock.patch.object(MD5, "open", fail_open, create=True):
                    with self.assertRaisesRegex(GCODE.CommandError, "MD5 check failed"):
                        self.command()
                self.assertTrue(self.path.exists())
                self.assertTrue(self.bitmap.exists())
                self.assertEqual(len(self.cancelled), 1)
                self.assertEqual(self.printer.shutdowns, [])

    def test_malformed_checksum_is_rejected(self):
        correct = hashlib.md5(b"G28\n").hexdigest().encode("ascii")
        for checksum in (b"", b"wrong", b"\xff", correct + b":extra"):
            with self.subTest(checksum=checksum):
                self.path.write_bytes(b"; MD5:" + checksum + b"\nG28\n")
                self.assertFalse(self.checker.check_md5(self.path, delete=False))
                self.assertTrue(self.path.exists())

    def test_failed_cleanup_still_cancels_without_shutdown(self):
        for failed_path in (self.path, self.bitmap):
            with self.subTest(failed_path=failed_path):
                self.path.write_bytes(b"; MD5:wrong\nG28\n")
                self.bitmap.write_bytes(b"preview")
                self.cancelled.clear()
                remove = MD5.os.remove

                def fail_remove(path):
                    if Path(path) == failed_path:
                        raise OSError("read-only storage")
                    remove(path)

                with mock.patch.object(MD5.os, "remove", fail_remove):
                    with self.assertRaisesRegex(GCODE.CommandError, "MD5 check failed"):
                        self.command()
                self.assertTrue(failed_path.exists())
                self.assertEqual(len(self.cancelled), 1)
                self.assertEqual(self.printer.shutdowns, [])


if __name__ == "__main__":
    unittest.main()
