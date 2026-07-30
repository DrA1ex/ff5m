## Read-only comparison of saved single-model visual regression reports.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Read-only comparison of saved single-model visual regression reports."""

import argparse
import json
import pathlib
import statistics


def summarize(path):
    path = pathlib.Path(path).resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    verdicts = []
    valid = 0
    total = 0
    elapsed = []
    errors = 1 if isinstance(value.get("infrastructure_error"), dict) else 0
    for frame in value.get("screenshots", ()):
        for result in frame.get("models", ()):
            total += 1
            if result.get("json_validation", {}).get("status") == "valid":
                valid += 1
            if result.get("verdict"):
                verdicts.append(result["verdict"])
            if result.get("elapsed_seconds") is not None:
                elapsed.append(float(result["elapsed_seconds"]))
            if result.get("error"):
                errors += 1
    reviewed = sum(item in ("warn", "fail") for item in verdicts)
    configuration = value.get("configuration", {})
    benchmark = value.get("benchmark", {})
    if not isinstance(benchmark, dict):
        benchmark = {}
    return {
        "path": str(path),
        "model": str(configuration.get("model") or "unknown"),
        "status": value.get("status"),
        "cases": len(value.get("screenshots", ())),
        "json_valid": valid,
        "json_valid_rate": (float(valid) / total if total else 0.0),
        "pass": verdicts.count("pass"),
        "warn": verdicts.count("warn"),
        "fail": verdicts.count("fail"),
        "review_rate": (float(reviewed) / len(verdicts)
                        if verdicts else 0.0),
        "mean_elapsed_seconds": (
            statistics.mean(elapsed) if elapsed else None),
        "load_time_seconds": benchmark.get("load_time_seconds"),
        "load_wall_time_seconds": benchmark.get(
            "load_wall_time_seconds"),
        "wall_time_seconds": benchmark.get("wall_time_seconds"),
        "model_size_bytes": benchmark.get("model_size_bytes"),
        "estimated_memory_bytes": benchmark.get(
            "estimated_memory_bytes"),
        "errors": errors,
        "infrastructure_error": isinstance(
            value.get("infrastructure_error"), dict),
    }


def compare(paths):
    return {
        "schema_version": 1,
        "reports": [summarize(path) for path in paths],
    }


def _arguments(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare saved FF5M single-model UI regression reports.")
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = _arguments(argv)
    value = compare(args.reports)
    if args.json:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        for item in value["reports"]:
            elapsed = item["mean_elapsed_seconds"]
            print(
                "%s: status=%s cases=%d valid=%.1f%% "
                "pass=%d warn=%d fail=%d review=%.1f%% latency=%s errors=%d"
                % (
                    item["model"], item["status"], item["cases"],
                    item["json_valid_rate"] * 100.0, item["pass"],
                    item["warn"], item["fail"],
                    item["review_rate"] * 100.0,
                    ("%.3fs" % elapsed if elapsed is not None else "n/a"),
                    item["errors"],
                ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
