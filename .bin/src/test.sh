#!/bin/sh

# Run the native C++ test suites (host CMake builds + CTest).
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

set -e

# --------------------------------------------------------------------------
# Locate the project. Paths inside the project are guaranteed; the script is
# location-independent and can be run from any working directory.
# --------------------------------------------------------------------------
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)

RUN_TYPER_HOST=1
RUN_LOGGED_HOST=1
DO_CLEAN=0

usage() {
    cat <<EOF
Usage: test.sh [OPTIONS]

Run the native C++ test suites from the repo root. Everything runs on the
host; no cross-toolchain is required.

Options (each accepts on|1|true|yes or off|0|false|no):
  --typer-host <on|off>   host CMake build + CTest for typer
  --logged-host <on|off>  host CMake build + CTest for logged
  --clean                 clean-first/force-reconfigure host builds
  --help                  show this help and exit

Without arguments every suite runs.
EOF
}

parse_bool() {
    case "$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')" in
        on|1|true|yes) echo 1 ;;
        off|0|false|no) echo 0 ;;
        *)
            echo "test.sh: bad boolean for $1: '$2'" >&2
            return 1
            ;;
    esac
}

failures=0

fail() {
    echo "FAILED: $*" >&2
    failures=$((failures + 1))
}

host_ctest() {
    local name=$1 target=$2 build_dir="$ROOT/.bin/src/$name/cmake-build-host"
    echo "==> host tests: $name"
    if [ "$DO_CLEAN" = 1 ] || [ ! -f "$build_dir/CMakeCache.txt" ]; then
        cmake \
            -S "$ROOT/.bin/src/$name" \
            -B "$build_dir" \
            -DCMAKE_BUILD_TYPE=Debug \
            -DCMAKE_RUNTIME_OUTPUT_DIRECTORY="$build_dir/bin" \
            -DBUILD_TESTING=ON \
            || fail "$name configure"
    fi
    local build_args=(--build "$build_dir" --parallel)
    if [ -n "$target" ]; then
        build_args+=(--target "$target")
    fi
    cmake "${build_args[@]}" || fail "$name build"
    ctest --test-dir "$build_dir" --output-on-failure --no-tests=error \
        || fail "$name ctest"
}

while [ "$#" -gt 0 ] && [ -n "$1" ]; do
    case "$1" in
        --typer-host) RUN_TYPER_HOST=$(parse_bool "$1" "$2"); shift 2 ;;
        --logged-host) RUN_LOGGED_HOST=$(parse_bool "$1" "$2"); shift 2 ;;
        --clean) DO_CLEAN=1; shift ;;
        --help) usage; exit 0 ;;
        *)
            echo "test.sh: unknown option '$1'" >&2
            usage >&2
            exit 2
            ;;
    esac
done

cd "$ROOT"

[ "$RUN_TYPER_HOST" = 1 ] && host_ctest typer typer_tests
[ "$RUN_LOGGED_HOST" = 1 ] && host_ctest logged ""

if [ "$failures" -gt 0 ]; then
    echo "FAILURES: $failures" >&2
    exit 1
fi
echo "All tests passed."