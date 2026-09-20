#!/bin/sh

# Build logged for the native printer environment (soft-float ARM EABI).
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

set -e

# --------------------------------------------------------------------------
# Locate the project. Paths inside the project are guaranteed; everything
# outside (the cross-toolchain install) is environment-specific and can be
# overridden with flags/defaults below.
# --------------------------------------------------------------------------
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
SRC_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)

# --------------------------------------------------------------------------
# Defaults for this machine. Override each with the matching flag below;
# these are baked in so the common case needs no arguments.
# --------------------------------------------------------------------------
TOOLCHAIN_ROOT=${TOOLCHAIN_ROOT:-/Volumes/x-tools}
TOOLCHAIN_TRIPLET=${TOOLCHAIN_TRIPLET:-arm-unknown-linux-gnueabi}
BUILD_TYPE=${BUILD_TYPE:-Release}
BUILD_TESTING=${BUILD_TESTING:-OFF}
BUILD_DIR=${BUILD_DIR:-"$SCRIPT_DIR/cmake-build-printer-eabi"}
DO_CLEAN=0
TOOLCHAIN_FILE="$SRC_DIR/toolchains/printer-eabi.cmake"

usage() {
    cat <<EOF
Usage: build.sh [OPTIONS]

Build the 'logged' native printer utility as a soft-float ARM EABI binary.

Options (all optional; machine-specific defaults are baked in):
  --root <dir>        Toolchain root containing the triplet dirs
                      (default: ${TOOLCHAIN_ROOT})
  --triplet <triplet> Toolchain triple such as arm-unknown-linux-gnueabi
                      (default: ${TOOLCHAIN_TRIPLET})
  --build-type <type> CMake build type (default: ${BUILD_TYPE})
  --build-dir <dir>   CMake build directory (default: ${BUILD_DIR})
  --testing <on|off>  BUILD_TESTING value (default: ${BUILD_TESTING})
  --clean             Clean build before compiling
  --help              Show this help and exit

All flags also accept environment variables of the same name
(TOOLCHAIN_ROOT, TOOLCHAIN_TRIPLET, BUILD_TYPE, BUILD_DIR, BUILD_TESTING).
EOF
}

while [ "$#" -gt 0 ] && [ -n "$1" ]; do
    case "$1" in
        --root) TOOLCHAIN_ROOT=$2; shift 2 ;;
        --triplet) TOOLCHAIN_TRIPLET=$2; shift 2 ;;
        --build-type) BUILD_TYPE=$2; shift 2 ;;
        --build-dir) BUILD_DIR=$2; shift 2 ;;
        --testing) BUILD_TESTING=$2; shift 2 ;;
        --clean) DO_CLEAN=1; shift ;;
        --help) usage; exit 0 ;;
        *)
            echo "build.sh: unknown option '$1'" >&2
            usage >&2
            exit 2
            ;;
    esac
done

# --------------------------------------------------------------------------
# The checked-in toolchain file hardcodes the local /Volumes path and triplet.
# Use it verbatim when the defaults hold; otherwise write an overridden copy
# next to the build directory so the same toolchain logic drives the build.
# --------------------------------------------------------------------------
NEED_TOOLCHAIN_OVERRIDE=0
[ "$TOOLCHAIN_ROOT" = "/Volumes/x-tools" ] || NEED_TOOLCHAIN_OVERRIDE=1
[ "$TOOLCHAIN_TRIPLET" = "arm-unknown-linux-gnueabi" ] || NEED_TOOLCHAIN_OVERRIDE=1

if [ "$NEED_TOOLCHAIN_OVERRIDE" = 1 ]; then
    OVERRIDE_DIR="$BUILD_DIR/.toolchain"
    mkdir -p "$OVERRIDE_DIR"
    TOOLCHAIN_FILE="$OVERRIDE_DIR/${TOOLCHAIN_TRIPLET}.cmake"
    cp "$SRC_DIR/toolchains/printer-eabi.cmake" "$TOOLCHAIN_FILE"
    sed -i.bak \
        -e "s|set(TOOLCHAIN_TRIPLET arm-unknown-linux-gnueabi)|set(TOOLCHAIN_TRIPLET ${TOOLCHAIN_TRIPLET})|" \
        -e "s|set(TOOLCHAIN_ROOT \"/Volumes/x-tools/\${TOOLCHAIN_TRIPLET}\")|set(TOOLCHAIN_ROOT \"${TOOLCHAIN_ROOT}/\${TOOLCHAIN_TRIPLET}\")|" \
        "$TOOLCHAIN_FILE"
    rm -f "$TOOLCHAIN_FILE.bak"
fi

# --------------------------------------------------------------------------
echo "==> Configuring logged (${TOOLCHAIN_TRIPLET}, ${BUILD_TYPE})"
cmake \
    -S "$SCRIPT_DIR" \
    -B "$BUILD_DIR" \
    -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
    -DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN_FILE" \
    -DBUILD_TESTING="$BUILD_TESTING"

echo "==> Compiling"
if [ "$DO_CLEAN" = 1 ]; then
    cmake --build "$BUILD_DIR" --clean-first --parallel
else
    cmake --build "$BUILD_DIR" --parallel
fi

echo "==> Result"
file "$SRC_DIR/../exec/logged"
