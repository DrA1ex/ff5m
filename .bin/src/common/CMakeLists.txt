## Shared screen rendering primitives.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

cmake_minimum_required(VERSION 3.10)

add_library(forge_screen_text STATIC text.cpp)
target_compile_features(forge_screen_text PUBLIC cxx_std_20)
target_include_directories(forge_screen_text PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(forge_screen_text PUBLIC m)
