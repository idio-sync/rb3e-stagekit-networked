cmake_minimum_required(VERSION 3.13)

# Initialize the SDK
include(pico_sdk_import.cmake)

project(pico_stagekit C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

# Initialize the Raspberry Pi Pico SDK
pico_sdk_init()

# Define the executable
# NOTE: Ensure your source file is named 'main.cpp'
add_executable(pico_stagekit
    main.cpp
)

# Link necessary libraries
target_link_libraries(pico_stagekit
    pico_stdlib
    pico_cyw43_arch_lwip_poll  # Using poll mode as you call cyw43_arch_poll() in main
    tinyusb_host
    tinyusb_board
)

# Enable UART output for printf, Disable USB output
# (USB port is occupied by the Stage Kit in Host Mode)
pico_enable_stdio_usb(pico_stagekit 0)
pico_enable_stdio_uart(pico_stagekit 1)

# Enable generation of the .uf2 file
pico_add_extra_outputs(pico_stagekit)
