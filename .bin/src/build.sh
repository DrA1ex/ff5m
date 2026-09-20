#!/bin/sh

# Build the native printer utilities (typer and logged).
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

RUN_TYPER=1
RUN_LOGGED=1
DO_CLEAN=0
COMMON_FLAGS=""

usage() {
    cat <<EOF
Usage: build.sh [OPTIONS]

Build the native printer utilities (typer and logged) as soft-float ARM EABI
binaries, delegating to the per-utility scripts under src/typer and
src/logged.

Options (each accepts on|1|true|yes or off|0|false|no):
  --typer <on|off>    build typer
  --logged <on|off>   build logged
  --root <dir>        toolchain root, forwarded to both utilities
  --triplet <triplet> toolchain triple, forwarded to both utilities
  --build-type <type> CMake build type, forwarded to both utilities
  --testing <on|off>  BUILD_TESTING value, forwarded to both utilities
  --clean             clean builds before compiling
  --help              show this help and exit

Without arguments both utilities are built. All flags also accept
environment variables of the same name via the per-utility scripts.
EOF
}

parse_bool() {
    case "$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')" in
        on|1|true|yes) echo 1 ;;
        off|0|false|no) echo 0 ;;
        *)
            echo "build.sh: bad boolean for $1: '$2'" >&2
            return 1
            ;;
    esac
}

while [ "$#" -gt 0 ] && [ -n "$1" ]; do
    case "$1" in
        --typer) RUN_TYPER=$(parse_bool "$1" "$2"); shift 2 ;;
        --logged) RUN_LOGGED=$(parse_bool "$1" "$2"); shift 2 ;;
        --root) COMMON_FLAGS="$COMMON_FLAGS --root $2"; shift 2 ;;
        --triplet) COMMON_FLAGS="$COMMON_FLAGS --triplet $2"; shift 2 ;;
        --build-type) COMMON_FLAGS="$COMMON_FLAGS --build-type $2"; shift 2 ;;
        --testing) COMMON_FLAGS="$COMMON_FLAGS --testing $2"; shift 2 ;;
        --clean) DO_CLEAN=1; shift ;;
        --help) usage; exit 0 ;;
        *)
            echo "build.sh: unknown option '$1'" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$DO_CLEAN" = 1 ]; then
    COMMON_FLAGS="$COMMON_FLAGS --clean"
fi

[ "$RUN_TYPER" = 1 ] && "$SCRIPT_DIR/typer/build.sh" $COMMON_FLAGS
[ "$RUN_LOGGED" = 1 ] && "$SCRIPT_DIR/logged/build.sh" $COMMON_FLAGS

echo "==> Done"
