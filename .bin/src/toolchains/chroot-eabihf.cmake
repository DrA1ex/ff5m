# Buildroot chroot hard-float cross-toolchain.
#
# Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR arm)

# Forge-X Buildroot chroot: hard-float EABI and /lib/ld-linux-armhf.so.3.
# Do not use this toolchain for binaries started by the stock printer system.
set(TOOLCHAIN_TRIPLET arm-unknown-linux-gnueabihf)
set(TOOLCHAIN_ROOT "/Volumes/x-tools/${TOOLCHAIN_TRIPLET}")
set(TOOLCHAIN_BIN "${TOOLCHAIN_ROOT}/bin")

set(CMAKE_C_COMPILER "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-gcc")
set(CMAKE_CXX_COMPILER "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-g++")
set(CMAKE_AR "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-ar")
set(CMAKE_AS "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-as")
set(CMAKE_LD "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-ld")
set(CMAKE_STRIP "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-strip")
set(CMAKE_RANLIB "${TOOLCHAIN_BIN}/${TOOLCHAIN_TRIPLET}-ranlib")

set(CMAKE_FIND_ROOT_PATH "${TOOLCHAIN_ROOT}/${TOOLCHAIN_TRIPLET}/sysroot")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
