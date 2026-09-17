# libariabridge — native C++ consumer for the Aria ARM64 bridge.
#
#   cmake -S src/libariabridge -B build/cpp -DCMAKE_BUILD_TYPE=Release
#   cmake --build build/cpp -j
#   ./scripts/test_cpp_consumer.sh        # unit + integration against the mock
#
# ZeroMQ is located in three steps so the project builds on a machine without
# root: pkg-config, then a plain find_library, and only if both fail does it
# fetch and build libzmq itself. Nothing here needs sudo.
cmake_minimum_required(VERSION 3.20)
project(ariabridge VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

option(ARIABRIDGE_BUILD_TESTS "Build the test executable" ON)
option(ARIABRIDGE_BUILD_EXAMPLES "Build the example consumer" ON)

# --- ZeroMQ -----------------------------------------------------------------
set(ARIABRIDGE_ZMQ_SOURCE "")

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(PC_LIBZMQ QUIET libzmq)
endif()

if(PC_LIBZMQ_FOUND)
    set(ARIABRIDGE_ZMQ_SOURCE "pkg-config (${PC_LIBZMQ_VERSION})")
    add_library(ariabridge_zmq INTERFACE)
    target_include_directories(ariabridge_zmq INTERFACE ${PC_LIBZMQ_INCLUDE_DIRS})
    target_link_libraries(ariabridge_zmq INTERFACE ${PC_LIBZMQ_LIBRARIES})
    target_link_directories(ariabridge_zmq INTERFACE ${PC_LIBZMQ_LIBRARY_DIRS})
else()
    find_path(ZMQ_INCLUDE_DIR NAMES zmq.h)
    find_library(ZMQ_LIBRARY NAMES zmq libzmq)
    if(ZMQ_INCLUDE_DIR AND ZMQ_LIBRARY)
        set(ARIABRIDGE_ZMQ_SOURCE "system (${ZMQ_LIBRARY})")
        add_library(ariabridge_zmq INTERFACE)
        target_include_directories(ariabridge_zmq INTERFACE ${ZMQ_INCLUDE_DIR})
        target_link_libraries(ariabridge_zmq INTERFACE ${ZMQ_LIBRARY})
    else()
        # No system ZeroMQ and no way to apt-install it without root: build it.
        # Costs a couple of minutes once, then it is cached in the build tree.
        message(STATUS "ZeroMQ not found on the system — fetching and building libzmq")
        include(FetchContent)
        set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
        set(BUILD_SHARED OFF CACHE BOOL "" FORCE)
        set(BUILD_STATIC ON CACHE BOOL "" FORCE)
        set(WITH_PERF_TOOL OFF CACHE BOOL "" FORCE)
        set(WITH_DOC OFF CACHE BOOL "" FORCE)
        set(ENABLE_CPACK OFF CACHE BOOL "" FORCE)
        FetchContent_Declare(
            libzmq
            GIT_REPOSITORY https://github.com/zeromq/libzmq.git
            GIT_TAG v4.3.5
            GIT_SHALLOW TRUE
        )
        # libzmq 4.3.5 still declares cmake_minimum_required(VERSION 3.0.2),
        # which CMake 4.x refuses outright. Relax the floor for the duration of
        # the subproject only, then put it back so this project keeps its own
        # policy settings.
        set(_ariabridge_saved_policy_min "${CMAKE_POLICY_VERSION_MINIMUM}")
        set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
        FetchContent_MakeAvailable(libzmq)
        set(CMAKE_POLICY_VERSION_MINIMUM "${_ariabridge_saved_policy_min}")
        set(ARIABRIDGE_ZMQ_SOURCE "FetchContent (libzmq v4.3.5, static)")
        add_library(ariabridge_zmq INTERFACE)
        target_link_libraries(ariabridge_zmq INTERFACE libzmq-static)
    endif()
endif()

message(STATUS "libariabridge: ZeroMQ from ${ARIABRIDGE_ZMQ_SOURCE}")

# --- library ----------------------------------------------------------------
add_library(ariabridge src/consumer.cpp)
add_library(ariabridge::ariabridge ALIAS ariabridge)
target_include_directories(ariabridge PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>)
target_link_libraries(ariabridge PRIVATE ariabridge_zmq)
target_compile_options(ariabridge PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>)

# --- tests and examples -----------------------------------------------------
if(ARIABRIDGE_BUILD_TESTS)
    enable_testing()
    add_executable(ariabridge_tests tests/test_consumer.cpp)
    target_link_libraries(ariabridge_tests PRIVATE ariabridge ariabridge_zmq)
    target_compile_options(ariabridge_tests PRIVATE
        $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic>)
    # Unit tests only — the integration run needs the Python mock receiver and
    # is driven by scripts/test_cpp_consumer.sh.
    add_test(NAME ariabridge_unit COMMAND ariabridge_tests --unit)
endif()

if(ARIABRIDGE_BUILD_EXAMPLES)
    add_executable(consume_frames examples/consume_frames.cpp)
    target_link_libraries(consume_frames PRIVATE ariabridge)
endif()
