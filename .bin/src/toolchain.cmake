# Backwards-compatible native printer toolchain alias.
#
# Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

# Backwards-compatible alias for native printer binaries. New build commands
# should use toolchains/printer-eabi.cmake explicitly so it cannot be confused
# with the hard-float chroot toolchain.
include("${CMAKE_CURRENT_LIST_DIR}/toolchains/printer-eabi.cmake")
