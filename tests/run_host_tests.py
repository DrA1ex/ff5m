"""Run independent unittest modules in separate, bounded processes."""

import argparse
import concurrent.futures
import os
import pathlib
import re
import subprocess
import sys
import time


ROOT = pathlib.Path(__file__).parents[1]
TESTS = ROOT / "tests"
INIT_MODULES = (
    "test_boot_recovery.py",
    "test_firmware_image_install.py",
    "test_network_scripts.py",
    "test_usb_swap_storage.py",
)


def run_module(filename):
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests",
         "-p", filename, "-v"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, check=False,
    )
    return result.returncode, time.monotonic() - started, result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs", type=int, default=min(4, os.cpu_count() or 1),
        help="maximum number of test processes (default: up to 4)")
    parser.add_argument(
        "--init-only", action="store_true", help="run boot and init modules")
    parser.add_argument(
        "--verbose", action="store_true", help="show output from passing modules")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")

    modules = (INIT_MODULES if args.init_only else tuple(
        path.name for path in sorted(TESTS.glob("test_*.py"))))
    missing = [name for name in modules if not (TESTS / name).is_file()]
    if missing:
        parser.error("missing test modules: " + ", ".join(missing))

    started = time.monotonic()
    failed = []
    total_tests = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_module, name): name for name in modules}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            status, duration, output = future.result()
            match = re.search(r"Ran (\d+) tests? in", output)
            total_tests += int(match.group(1)) if match else 0
            print("{} {} ({:.1f}s)".format(
                "PASS" if status == 0 else "FAIL", name, duration),
                flush=True)
            if status or args.verbose:
                print(output, end="" if output.endswith("\n") else "\n", flush=True)
            if status:
                failed.append(name)

    print("{} tests in {:.1f}s across {} modules; {} failed".format(
        total_tests, time.monotonic() - started, len(modules), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
