cmake_minimum_required(VERSION 3.13)

# Initialize Pico SDK from GIT
set(PICO_SDK_FETCH_FROM_GIT on)

# Allow board selection via cmake argument: -DPICO_BOARD=pico_w or -DPICO_BOARD=pico2_w
if(NOT DEFINED PICO_BOARD)
    set(PICO_BOARD pico_w)
endif()

message(STATUS "Building for board: ${PICO_BOARD}")

include(pico_sdk_import.cmake)

project(rb3e_stagekit C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Initialize the SDK
pico_sdk_init()

# Fetch LittleFS library
include(FetchContent)
FetchContent_Declare(
    littlefs
    GIT_REPOSITORY https://github.com/littlefs-project/littlefs.git
    GIT_TAG v2.5.1
)
FetchContent_MakeAvailable(littlefs)

# Create LittleFS library target
add_library(littlefs_lib STATIC
    ${littlefs_SOURCE_DIR}/lfs.c
    ${littlefs_SOURCE_DIR}/lfs_util.c
    src/littlefs_hal.c
)

target_include_directories(littlefs_lib PUBLIC
    ${littlefs_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(littlefs_lib PUBLIC
    pico_stdlib
    hardware_flash
    hardware_sync
)

# Main executable
add_executable(rb3e_stagekit
    src/main.c
    src/config_parser.c
    src/usb_host.c
    src/network.c
)

# Include directories (src contains tusb_config.h and lwipopts.h)
target_include_directories(rb3e_stagekit PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

# Link libraries
target_link_libraries(rb3e_stagekit
    pico_stdlib
    pico_multicore
    pico_cyw43_arch_lwip_threadsafe_background
    tinyusb_host
    tinyusb_board
    hardware_watchdog
    littlefs_lib
)

# Enable UART stdio for debugging, disable USB stdio
pico_enable_stdio_usb(rb3e_stagekit 0)
pico_enable_stdio_uart(rb3e_stagekit 1)

# Set output name based on board type
set_target_properties(rb3e_stagekit PROPERTIES OUTPUT_NAME "rb3e_stagekit_${PICO_BOARD}")

# Create UF2 file and other outputs
pico_add_extra_outputs(rb3e_stagekit)

# Compiler definitions
target_compile_definitions(rb3e_stagekit PRIVATE
    PICO_W=1
    CYW43_HOST_NAME="RB3E-StageKit"
)
