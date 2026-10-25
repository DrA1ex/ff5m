## Behavioral checks for deferred calibration and sample-export imports.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license.

import pathlib
import subprocess
import sys
import textwrap
import unittest


EXTRAS = (pathlib.Path(__file__).parents[1] / ".py" / "klipper" /
          "patches" / "extras")
HARNESS = """
import importlib, pathlib, sys, time, types
extras = types.ModuleType('extras')
extras.__path__ = [sys.argv[1]]
sys.modules['extras'] = extras
for name in ('bus', 'motion_report', 'shaper_defs'):
    sys.modules['extras.' + name] = types.ModuleType('extras.' + name)
adxl = importlib.import_module('extras.adxl345')
shaper = importlib.import_module('extras.shaper_calibrate')
"""


class LazyMultiprocessingTest(unittest.TestCase):
    def run_isolated(self, code):
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(HARNESS) +
             textwrap.dedent(code), str(EXTRAS)],
            text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_and_offline_calculation_do_not_load_multiprocessing(self):
        self.run_isolated("""
            assert 'multiprocessing' not in sys.modules
            calibrator = shaper.ShaperCalibrate.__new__(shaper.ShaperCalibrate)
            calibrator.printer = None
            assert calibrator.background_process_exec(sum, ([1, 2, 3],)) == 6
            assert 'multiprocessing' not in sys.modules
        """)

    def test_sample_export_starts_the_worker_and_preserves_csv(self):
        self.run_isolated("""
            import tempfile
            workers = []
            class Process:
                def __init__(self, target):
                    self.target = target
                    workers.append(self)
                def start(self):
                    assert self.daemon
                    self.target()
            sys.modules['multiprocessing'] = types.SimpleNamespace(Process=Process)
            adxl.os.nice = lambda value: None
            helper = adxl.AccelQueryHelper.__new__(adxl.AccelQueryHelper)
            helper.samples = [(1.25, 2., 3., 4.)]
            with tempfile.TemporaryDirectory() as tmp:
                target = pathlib.Path(tmp) / 'samples.csv'
                helper.write_to_file(str(target))
                assert len(workers) == 1
                assert target.read_text() == (
                    '#time,accel_x,accel_y,accel_z\\n'
                    '1.250000,2.000000,3.000000,4.000000\\n')
        """)

    @unittest.skipIf(sys.platform == "win32", "printer workers use POSIX fork")
    def test_calibration_drains_large_results_and_reports_child_errors(self):
        self.run_isolated("""
            import multiprocessing
            context = multiprocessing.get_context('fork')
            workers = []
            def process(**kwargs):
                worker = context.Process(**kwargs)
                workers.append(worker)
                return worker
            multiprocessing.Pipe = context.Pipe
            multiprocessing.Process = process
            sys.modules['queuelogger'] = types.SimpleNamespace(
                clear_bg_logging=lambda: None)
            class Reactor:
                monotonic = staticmethod(time.monotonic)
                def pause(self, deadline):
                    time.sleep(max(0., deadline - time.monotonic()))
                    return time.monotonic()
            printer = types.SimpleNamespace(
                get_reactor=Reactor,
                lookup_object=lambda name: types.SimpleNamespace(
                    respond_info=lambda *args, **kwargs: None))
            calibrator = shaper.ShaperCalibrate.__new__(shaper.ShaperCalibrate)
            calibrator.printer, calibrator.error = printer, RuntimeError
            def large_result():
                return b'x' * (256 * 1024)
            def fail():
                raise ValueError('calculation failed')
            try:
                assert calibrator.background_process_exec(
                    large_result, ()) == large_result()
                assert not workers[-1].is_alive()
                try:
                    calibrator.background_process_exec(fail, ())
                except RuntimeError as exc:
                    assert 'calculation failed' in str(exc)
                else:
                    raise AssertionError('child error was lost')
            finally:
                for worker in workers:
                    worker.join(2)
                    if worker.is_alive():
                        worker.terminate()
                        worker.join()
                    assert worker.exitcode == 0
        """)


if __name__ == "__main__":
    unittest.main()
