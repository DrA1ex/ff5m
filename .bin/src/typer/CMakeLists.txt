## Build configuration for the typer utility.
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

cmake_minimum_required(VERSION 3.10)

project(typer VERSION 0.1)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED True)

if(CMAKE_CROSSCOMPILING)
    execute_process(
            COMMAND ${CMAKE_CXX_COMPILER} -dumpmachine
            OUTPUT_VARIABLE TYPER_COMPILER_TRIPLET
            OUTPUT_STRIP_TRAILING_WHITESPACE)
    if(NOT TYPER_COMPILER_TRIPLET STREQUAL "arm-unknown-linux-gnueabi")
        message(FATAL_ERROR
                "typer runs in the native printer environment and must use "
                "arm-unknown-linux-gnueabi; got ${TYPER_COMPILER_TRIPLET}")
    endif()
endif()

if(NOT CMAKE_RUNTIME_OUTPUT_DIRECTORY)
    set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../../exec)
endif()

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    include(CTest)
endif()

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-rpath,/opt/lib/14.2.0")

file(GLOB font_headers ../common/fonts/*.h)

add_executable(typer main.cpp interactive.cpp batch_protocol.cpp framebuffer.cpp
        ../common/text.cpp
        ${font_headers})

target_link_libraries(typer m)
# Typer stays resident on a 128 MiB target. Prefer compact generated code and
# let the linker discard unused template/font sections; debug symbols are not
# useful in the deployed binary and only increase its page-cache footprint.
target_compile_options(typer PRIVATE -Os -ffunction-sections -fdata-sections)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    set_target_properties(typer PROPERTIES
            LINK_FLAGS "-Wl,--gc-sections -Wl,--strip-all")
endif()

if(BUILD_TESTING)
    find_package(Threads REQUIRED)

    function(add_typer_test_cases target)
        foreach(case_name ${ARGN})
            add_test(NAME ${target}.${case_name}
                    COMMAND $<TARGET_FILE:${target}> ${case_name})
        endforeach()
    endfunction()

    add_executable(typer_interactive_test
            tests/interactive_test.cpp
            interactive.cpp)
    add_typer_test_cases(typer_interactive_test
            empty_registry
            rectangle_boundaries
            overlap_precedence
            negative_coordinates
            clear_registry
            registry_limit
            action_length_limit
            continuous_hitbox_flag)

    add_executable(typer_touch_tracker_test
            tests/touch_tracker_test.cpp
            interactive.cpp)
    add_typer_test_cases(typer_touch_tracker_test
            basic_tap
            duplicate_release_ignored
            coordinates_after_press
            movement_threshold_inclusive
            tap_keeps_press_origin
            swipe_rejected
            returned_swipe_rejected
            unrelated_events_ignored
            continuous_reports_press_move_release)

    add_executable(typer_batch_protocol_test
            tests/batch_protocol_test.cpp
            batch_protocol.cpp)
    target_compile_definitions(typer_batch_protocol_test PRIVATE
            FEATHER_PROTOCOL_FIXTURE="${CMAKE_CURRENT_SOURCE_DIR}/../../../tests/fixtures/feather_draw_protocol.txt")
    add_typer_test_cases(typer_batch_protocol_test
            empty_and_whitespace
            basic_command
            double_and_single_quotes
            empty_and_adjacent_quotes
            escaped_characters
            unicode_and_nul
            malformed_input
            command_splitting
            composite_button_command
            feather_protocol_fixture)

    add_executable(typer_fifo_integration_test
            tests/fifo_integration_test.cpp
            interactive.cpp)
    target_link_libraries(typer_fifo_integration_test Threads::Threads)
    add_typer_test_cases(typer_fifo_integration_test
            fragmented_frames_and_touch_dispatch)

    add_executable(typer_framebuffer_test
            tests/framebuffer_test.cpp
            framebuffer.cpp)
    add_typer_test_cases(typer_framebuffer_test
            visible_page_zero
            visible_page_one
            one_page_falls_back
            invalid_bpp_falls_back
            invalid_stride_falls_back
            insufficient_memory_falls_back
            invalid_offset_falls_back
            invalid_dimensions_fall_back)

    add_executable(typer_text_buffer_test
            tests/text_buffer_test.cpp
            ../common/text.cpp)
    target_link_libraries(typer_text_buffer_test m)
    add_typer_test_cases(typer_text_buffer_test
            external_buffer_is_initialized
            external_buffer_flushes_dirty_area
            external_buffer_is_not_owned
            internal_buffer_fallback
            disable_flushes_and_restores_direct_drawing
            repeated_enable_preserves_pending_frame
            switching_buffers_flushes_and_resynchronizes
            two_bpp_font_blends_edges
            compact_font_maps_disjoint_unicode_ranges
            bundled_compact_font_renders_cyrillic
            text_wrap_uses_font_metrics_and_word_boundaries
            text_wrap_limits_by_rendered_height_and_adds_ellipsis
            text_wrap_without_truncate_has_no_ellipsis
            text_wrap_splits_long_utf8_words_without_corruption
            text_truncate_uses_font_metrics_and_preserves_utf8)

    add_custom_target(typer_tests
            DEPENDS
            typer_interactive_test
            typer_touch_tracker_test
            typer_batch_protocol_test
            typer_fifo_integration_test
            typer_framebuffer_test
            typer_text_buffer_test)
endif()
