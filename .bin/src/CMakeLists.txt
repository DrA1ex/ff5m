# Build configuration for native utilities.
#
# Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license

cmake_minimum_required(VERSION 3.10)
project(root)

include(CTest)

message(STATUS "CMake Build Type: ${CMAKE_BUILD_TYPE}")

add_subdirectory(demo)
add_subdirectory(typer)
add_subdirectory(boot_mcu)
add_subdirectory(logged)
