cmake_minimum_required(VERSION 3.10)
project(root)

include(CTest)

message(STATUS "CMake Build Type: ${CMAKE_BUILD_TYPE}")

add_subdirectory(demo)
add_subdirectory(typer)
add_subdirectory(boot_mcu)
add_subdirectory(logged)
