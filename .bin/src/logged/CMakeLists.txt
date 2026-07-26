## Build configuration for the logged utility.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

cmake_minimum_required(VERSION 3.10)

project(logged VERSION 0.1)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED True)

if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../../exec)
endif()

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    include(CTest)
endif()

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3 -Wno-psabi")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-rpath,/opt/lib/14.2.0")

if(NOT TARGET forge_screen_text)
    add_subdirectory(../common ${CMAKE_CURRENT_BINARY_DIR}/common)
endif()

add_executable(logged main.cpp
        logger.cpp
        screen_queue.cpp)

target_link_libraries(logged forge_screen_text)

if(BUILD_TESTING)
    add_executable(logged_screen_queue_test
            tests/screen_queue_test.cpp
            screen_queue.cpp)
    target_link_libraries(logged_screen_queue_test forge_screen_text)

    add_test(NAME logged_screen_queue_test
            COMMAND $<TARGET_FILE:logged_screen_queue_test>)
endif()
