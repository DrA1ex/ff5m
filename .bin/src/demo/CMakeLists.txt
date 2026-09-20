cmake_minimum_required(VERSION 3.10)

project(demo VERSION 0.1)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED True)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/../../exec)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O3")

# Dynamic runtime libs
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-rpath,/opt/lib/14.2.0")

# For dynamic, but static c++ runtime
# set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -static-libstdc++")

# For static Build
# set(CMAKE_EXE_LINKER_FLAGS "-static")
# set(CMAKE_FIND_LIBRARY_SUFFIXES ".a")
# set(BUILD_SHARED_LIBS OFF)

add_executable(demo main.cpp)

target_link_libraries(demo m)
