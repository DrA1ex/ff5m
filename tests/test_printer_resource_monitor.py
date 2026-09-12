## Tests for the printer-side resource sampler.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
MONITOR = ROOT / ".shell" / "printer_resource_monitor.sh"
SAMPLER = ROOT / ".shell" / "printer_resource_sample.awk"

# The sampler is a /proc reader driven by a shell loop, so the loop itself can
# only run on the printer.  The awk pass, which owns every value in the file the
# host parses, takes its tree from `root` and is therefore testable against a
# fixture here.
KLIPPY_STAT = (
    "210 (python3 klippy.py) R 1 210 210 0 -1 4194560 5000 0 7 0 "
    "1200 340 0 0 20 0 4 0 900 79691776 11148 4294967295 65536 1"
)
TYPER_STAT = (
    "97 (Typer) S 1 97 97 0 -1 4194304 812 0 0 0 "
    "45 12 0 0 20 0 9 0 640 33554432 2048 4294967295 65536 1"
)


COLUMNS = (
    "epoch", "uptime", "load1", "mem_available_kb", "swap_free_kb",
    "role", "pid", "cpu_ticks", "rss_kb", "state", "threads",
    "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches",
    "minor_faults", "major_faults", "scheduler_runtime_ns",
    "scheduler_wait_ns", "scheduler_timeslices", "read_bytes",
    "write_bytes", "syscr", "syscw", "wchan", "command",
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def proc_tree(directory):
    root = pathlib.Path(directory) / "proc"
    write(root / "uptime", "12345.67 20000.00\n")
    write(root / "loadavg", "1.42 0.99 0.71 2/198 4242\n")
    write(root / "meminfo",
          "MemTotal:         112640 kB\n"
          "MemFree:            8192 kB\n"
          "MemAvailable:      45120 kB\n"
          "SwapTotal:             0 kB\n"
          "SwapFree:           1024 kB\n")
    write(root / "stat",
          "cpu  100 200 300 400 0 0 0 0 0 0\n"
          "cpu0 50 100 150 200 0 0 0 0 0 0\n"
          "intr 12345 0 0\n")

    write(root / "210" / "stat", KLIPPY_STAT + "\n")
    write(root / "210" / "status",
          "Name:\tpython3\n"
          "State:\tR (running)\n"
          "VmRSS:\t   45678 kB\n"
          "Threads:\t4\n"
          "voluntary_ctxt_switches:\t812\n"
          "nonvoluntary_ctxt_switches:\t39\n")
    write(root / "210" / "schedstat", "987654321 123456789 4321\n")
    write(root / "210" / "io",
          "rchar: 1024\n"
          "read_bytes: 8589934592\n"
          "write_bytes: 40960\n"
          "syscr: 91\n"
          "syscw: 77\n")
    write(root / "210" / "wchan", "poll_schedule_timeout")

    write(root / "97" / "stat", TYPER_STAT + "\n")
    return root


def sample(root, processes, epoch="1770000000", uptime="12345.67"):
    result = subprocess.run(
        ["awk", "-v", "root=%s" % root, "-v", "epoch=%s" % epoch,
         "-v", "uptime=%s" % uptime, "-f", str(SAMPLER)],
        env={"FF5M_PROCESSES": processes, "PATH": "/usr/bin:/bin"},
        text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", result.stderr
    return [line.split("\t") for line in result.stdout.splitlines()]


class ResourceSamplerTest(unittest.TestCase):
    def test_monitor_script_is_valid_posix_shell(self):
        # The sampler runs under the printer's busybox ash, so it is checked
        # with sh rather than bash.
        result = subprocess.run(
            ["sh", "-n", str(MONITOR)], text=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sampler_reports_the_system_row_from_one_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = proc_tree(directory)
            rows = sample(root, "")
        self.assertEqual(len(rows), 1)
        row = dict(zip(COLUMNS, rows[0]))
        self.assertEqual(row["epoch"], "1770000000")
        self.assertEqual(row["uptime"], "12345.67")
        self.assertEqual(row["load1"], "1.42")
        self.assertEqual(row["mem_available_kb"], "45120")
        self.assertEqual(row["swap_free_kb"], "1024")
        self.assertEqual(row["role"], "system")
        # Every jiffy of the "cpu" line, so the host can turn two samples into
        # a busy percentage without knowing the kernel's column layout.
        self.assertEqual(row["cpu_ticks"], "1000")

    def test_sampler_reads_every_process_source_in_the_same_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = proc_tree(directory)
            rows = sample(root, "210\tklippy\tpython3 klippy.py -l printer.log\n")
        row = dict(zip(COLUMNS, rows[1]))
        self.assertEqual(row["role"], "klippy")
        self.assertEqual(row["pid"], "210")
        # /proc/<pid>/stat: comm holds a space, so the offsets are taken from
        # after the closing bracket.  utime + stime = 1200 + 340.
        self.assertEqual(row["cpu_ticks"], "1540")
        self.assertEqual(row["state"], "R")
        self.assertEqual(row["threads"], "4")
        self.assertEqual(row["minor_faults"], "5000")
        self.assertEqual(row["major_faults"], "7")
        self.assertEqual(row["rss_kb"], "45678")
        self.assertEqual(row["voluntary_ctxt_switches"], "812")
        self.assertEqual(row["nonvoluntary_ctxt_switches"], "39")
        self.assertEqual(row["scheduler_runtime_ns"], "987654321")
        self.assertEqual(row["scheduler_wait_ns"], "123456789")
        self.assertEqual(row["scheduler_timeslices"], "4321")
        # Formatted as %.0f: an eight-gigabyte counter must not reach the
        # report as 8.58993e+09.
        self.assertEqual(row["read_bytes"], "8589934592")
        self.assertEqual(row["write_bytes"], "40960")
        self.assertEqual(row["syscr"], "91")
        self.assertEqual(row["syscw"], "77")
        self.assertEqual(row["wchan"], "poll_schedule_timeout")
        self.assertEqual(row["command"], "python3 klippy.py -l printer.log")

    def test_sampler_defaults_the_sources_a_process_does_not_expose(self):
        # Typer has no status, schedstat, io or wchan in the fixture, which is
        # what a sampler without privileges sees.  The row still has to be
        # emitted: dropping it would hide the process from the report entirely.
        with tempfile.TemporaryDirectory() as directory:
            root = proc_tree(directory)
            rows = sample(root, "97\ttyper\tTyper\n")
        row = dict(zip(COLUMNS, rows[1]))
        self.assertEqual(row["role"], "typer")
        self.assertEqual(row["state"], "S")
        self.assertEqual(row["cpu_ticks"], "57")
        self.assertEqual(row["rss_kb"], "0")
        self.assertEqual(row["voluntary_ctxt_switches"], "0")
        self.assertEqual(row["scheduler_runtime_ns"], "0")
        self.assertEqual(row["read_bytes"], "0")
        self.assertEqual(row["wchan"], "-")
        self.assertEqual(row["command"], "Typer")

    def test_sampler_skips_a_process_that_exited_mid_pass(self):
        # Discovery and sampling are one iteration apart, so a pid can vanish
        # in between.  A partial row would corrupt the column layout for the
        # host parser, so the process is simply absent from this instant.
        with tempfile.TemporaryDirectory() as directory:
            root = proc_tree(directory)
            rows = sample(root, "210\tklippy\tpython3 klippy.py\n"
                                "555\tmoonraker\tpython3 moonraker.py\n")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][6], "210")


if __name__ == "__main__":
    unittest.main()
