/*
 * RB3E StageKit Bridge - Main Application
 *
 * Wireless bridge for RB3Enhanced Stage Kit integration.
 * Receives UDP packets and sends HID commands to Santroller Stage Kit.
 *
 * Uses pico_cyw43_arch_lwip_threadsafe_background for automatic
 * WiFi/network polling via background interrupts.
 */

#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "hardware/watchdog.h"
#include <stdio.h>
#include <string.h>

#include "littlefs_hal.h"
#include "config_parser.h"
#include "usb_host.h"
#include "network.h"
#include "rb3e_protocol.h"

//--------------------------------------------------------------------
// Timing Constants (in milliseconds)
//--------------------------------------------------------------------

#define WATCHDOG_TIMEOUT_MS     8000    // Reset if frozen for 8 seconds
#define HEARTBEAT_INTERVAL_MS   2000    // LED blink interval
#define TELEMETRY_INTERVAL_MS   5000    // Telemetry broadcast interval
#define SAFETY_TIMEOUT_MS       5000    // Turn off lights if no packets
#define USB_RECONNECT_INTERVAL_MS 5000  // USB reconnection check interval
#define WIFI_CHECK_INTERVAL_MS  10000   // WiFi connection check interval
#define LOOP_DELAY_ACTIVE_US    100     // 0.1ms when active
#define LOOP_DELAY_IDLE_US      1000    // 1ms when idle

//--------------------------------------------------------------------
// Shared State (for interrupt callbacks)
//--------------------------------------------------------------------

// Volatile for interrupt safety (network callbacks run in background)
static volatile bool stagekit_command_pending = false;
static volatile uint8_t pending_left_weight = 0;
static volatile uint8_t pending_right_weight = 0;

//--------------------------------------------------------------------
// Core 0 State
//--------------------------------------------------------------------

static bool lights_active = false;
static absolute_time_t last_packet_time;
static absolute_time_t last_heartbeat_time;
static absolute_time_t last_telemetry_time;
static absolute_time_t last_usb_reconnect_time;
static absolute_time_t last_wifi_check_time;
static bool led_state = false;
static wifi_config_t stored_wifi_cfg;  // Stored for reconnection

//--------------------------------------------------------------------
// StageKit Packet Callback (called from background interrupt)
//--------------------------------------------------------------------

static void on_stagekit_packet(uint8_t left, uint8_t right)
{
    // Queue command for main loop to process
    pending_left_weight = left;
    pending_right_weight = right;
    stagekit_command_pending = true;
}

//--------------------------------------------------------------------
// LED Blink Functions
//--------------------------------------------------------------------

static void blink_led(int times, int delay_ms)
{
    for (int i = 0; i < times; i++) {
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 1);
        sleep_ms(delay_ms);
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 0);
        sleep_ms(delay_ms);
    }
}

static void heartbeat_led_toggle(void)
{
    led_state = !led_state;
    cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, led_state);
}

//--------------------------------------------------------------------
// Main Application (Core 0)
//--------------------------------------------------------------------

int main(void)
{
    // Initialize stdio FIRST - before anything else
    stdio_init_all();

    // Short delay for USB enumeration
    sleep_ms(1000);

    printf("\n\n");
    printf("==================================================\n");
    printf("RB3E StageKit Bridge - Pico W Firmware\n");
    printf("Build: " __DATE__ " " __TIME__ "\n");
    printf("==================================================\n");
    printf("DEBUG: stdio initialized\n");

    // Initialize CYW43 early for LED support
    // Use init_with_country to set region code upfront - this avoids the need
    // for a destructive deinit/reinit cycle later in network_init()
    printf("DEBUG: About to init CYW43 with country code...\n");
    int cyw43_result = cyw43_arch_init_with_country(CYW43_COUNTRY_USA);
    printf("DEBUG: CYW43 init returned %d\n", cyw43_result);
    if (cyw43_result) {
        printf("ERROR: CYW43 init failed - LEDs will not work\n");
        // Continue anyway, but LEDs won't function
    } else {
        printf("DEBUG: CYW43 OK, blinking LED...\n");
        // Quick blink to show we're alive
        blink_led(1, 100);
        printf("DEBUG: LED blink done\n");

        // Allow radio to stabilize after power-up
        // The CYW43 RF subsystem needs time after init before reliable scanning
        printf("DEBUG: Waiting for radio stabilization...\n");
        sleep_ms(100);
        // Note: Using threadsafe_background - polling handled automatically
    }

    // Initialize watchdog
    printf("Initializing watchdog (%d ms timeout)...\n", WATCHDOG_TIMEOUT_MS);
    watchdog_enable(WATCHDOG_TIMEOUT_MS, true);
    watchdog_update();

    // Initialize LittleFS
    printf("\nInitializing filesystem...\n");
    littlefs_init();
    if (littlefs_mount() != 0) {
        printf("\n");
        printf("!!! NO FILESYSTEM FOUND !!!\n");
        printf("You need to flash the WiFi credentials UF2 file.\n");
        printf("Use the Dashboard or generate_config_uf2.py tool.\n");
        printf("\n");
        // Blink pattern: 5 fast blinks = no filesystem
        while (1) {
            watchdog_update();
            blink_led(5, 200);
            sleep_ms(1000);
        }
    }

    // Check for settings file
    if (!config_file_exists()) {
        printf("Settings file not found, creating default...\n");
        config_create_default();
        printf("\n");
        printf("!!! IMPORTANT !!!\n");
        printf("Please edit /settings.toml with your WiFi credentials\n");
        printf("then reset the device.\n");
        printf("\n");

        // Blink error pattern and wait
        while (1) {
            watchdog_update();
            blink_led(3, 300);
            sleep_ms(1000);
        }
    }

    // Load WiFi configuration
    printf("\nLoading WiFi configuration...\n");
    if (config_load_wifi(&stored_wifi_cfg) != 0) {
        printf("ERROR: Failed to load WiFi config\n");
        while (1) {
            watchdog_update();
            blink_led(4, 400);
            sleep_ms(1000);
        }
    }

    watchdog_update();

    // Initialize network
    printf("\nInitializing network...\n");
    if (!network_init(&stored_wifi_cfg)) {
        printf("ERROR: Network initialization failed\n");
        while (1) {
            watchdog_update();
            blink_led(5, 500);
            sleep_ms(1000);
        }
    }

    // Connect to WiFi with retries
    printf("\nConnecting to WiFi: %s\n", stored_wifi_cfg.ssid);
    bool wifi_connected = false;
    for (int attempt = 1; attempt <= WIFI_MAX_RETRIES; attempt++) {
        printf("WiFi connection attempt %d of %d...\n", attempt, WIFI_MAX_RETRIES);
        watchdog_update();

        if (network_connect_wifi()) {
            wifi_connected = true;
            break;
        }

        wifi_fail_reason_t reason = network_get_wifi_fail_reason();
        printf("Attempt %d failed: ", attempt);
        switch (reason) {
            case WIFI_FAIL_NONET:
                printf("SSID not found\n");
                break;
            case WIFI_FAIL_BADAUTH:
                printf("Wrong password\n");
                // Don't retry on bad password - it won't change
                attempt = WIFI_MAX_RETRIES;
                break;
            case WIFI_FAIL_TIMEOUT:
                printf("Connection timeout\n");
                break;
            default:
                printf("General failure\n");
                break;
        }

        if (attempt < WIFI_MAX_RETRIES) {
            printf("Retrying in %d seconds...\n", WIFI_RETRY_DELAY_MS / 1000);
            // Blink while waiting for retry
            for (int i = 0; i < WIFI_RETRY_DELAY_MS / 500; i++) {
                watchdog_update();
                blink_led(1, 200);
                sleep_ms(300);
            }
        }
    }

    if (!wifi_connected) {
        printf("WARNING: WiFi connection failed after %d attempts\n", WIFI_MAX_RETRIES);
        printf("Continuing to main loop - will retry periodically\n");
        blink_led(3, 500);  // Indicate boot without WiFi
    }

    // Show connection status
    if (wifi_connected) {
        char ip_str[16];
        network_get_ip_string(ip_str, sizeof(ip_str));
        printf("WiFi connected! IP: %s\n", ip_str);
        blink_led(2, 100);  // Quick double blink = success
    }

    watchdog_update();

    // Initialize USB Host
    printf("\nInitializing USB host...\n");
    usb_host_init();

    // Register USB task as service callback for network operations
    // This prevents USB starvation during WiFi reconnection
    network_set_service_callback(usb_host_task);

    // Start UDP listener if WiFi is connected
    if (wifi_connected) {
        printf("\nStarting UDP listener...\n");
        if (!network_start_listener(on_stagekit_packet)) {
            printf("ERROR: Failed to start listener\n");
            // Don't die - continue and try to recover
            wifi_connected = false;
        }
    }

    // Network polling is handled automatically by pico_cyw43_arch_lwip_threadsafe_background
    // via timer interrupts - no manual polling or Core 1 task needed

    // Initialize timing
    last_packet_time = get_absolute_time();
    last_heartbeat_time = get_absolute_time();
    last_telemetry_time = get_absolute_time();
    last_usb_reconnect_time = get_absolute_time();
    last_wifi_check_time = get_absolute_time();

    printf("\n");
    printf("==================================================\n");
    if (wifi_connected) {
        printf("Ready! Listening for RB3E packets on port %d\n", RB3E_LISTEN_PORT);
        printf("Telemetry broadcast on port %d every %d seconds\n",
               RB3E_TELEMETRY_PORT, TELEMETRY_INTERVAL_MS / 1000);
    } else {
        printf("Started in OFFLINE mode - waiting for WiFi\n");
        printf("Will retry connection every %d seconds\n", WIFI_CHECK_INTERVAL_MS / 1000);
    }
    printf("==================================================\n");
    printf("\n");

    // Main loop (Core 0)
    while (true) {
        absolute_time_t now = get_absolute_time();

        // Feed watchdog
        watchdog_update();

        // Process USB tasks
        usb_host_task();

        // Process pending StageKit command
        if (stagekit_command_pending) {
            stagekit_command_pending = false;
            last_packet_time = now;

            if (usb_stagekit_connected()) {
                if (usb_send_stagekit_command(pending_left_weight, pending_right_weight)) {
                    lights_active = true;
                } else {
                    printf("WARNING: Failed to send StageKit command\n");
                }
            }
        }

        // Heartbeat LED (blink every HEARTBEAT_INTERVAL)
        if (absolute_time_diff_us(last_heartbeat_time, now) > (HEARTBEAT_INTERVAL_MS * 1000)) {
            heartbeat_led_toggle();
            last_heartbeat_time = now;
        }

        // Send telemetry (every TELEMETRY_INTERVAL) - only if connected
        if (network_wifi_connected() &&
            absolute_time_diff_us(last_telemetry_time, now) > (TELEMETRY_INTERVAL_MS * 1000)) {
            network_send_telemetry(usb_stagekit_connected());
            last_telemetry_time = now;
        }

        // Safety timeout - turn off lights if no packets received
        if (lights_active &&
            absolute_time_diff_us(last_packet_time, now) > (SAFETY_TIMEOUT_MS * 1000)) {
            printf("Safety timeout - clearing lights\n");
            if (usb_stagekit_connected()) {
                usb_stagekit_all_off();
            }
            lights_active = false;
        }

        // Try to reconnect USB if disconnected
        if (!usb_stagekit_connected() &&
            absolute_time_diff_us(last_usb_reconnect_time, now) > (USB_RECONNECT_INTERVAL_MS * 1000)) {
            // USB host handles reconnection automatically via callbacks
            // Just log the status periodically
            last_usb_reconnect_time = now;
        }

        // WiFi connection check and reconnection
        if (absolute_time_diff_us(last_wifi_check_time, now) > (WIFI_CHECK_INTERVAL_MS * 1000)) {
            last_wifi_check_time = now;

            if (network_wifi_connected()) {
                // Check if connection is still alive
                if (!network_check_connection()) {
                    printf("WiFi connection lost - attempting reconnect...\n");
                    network_stop_listener();

                    // Try to reconnect
                    if (network_connect_wifi()) {
                        char ip_str[16];
                        network_get_ip_string(ip_str, sizeof(ip_str));
                        printf("WiFi reconnected! IP: %s\n", ip_str);

                        // Restart listener
                        if (network_start_listener(on_stagekit_packet)) {
                            printf("Listener restarted\n");
                            blink_led(2, 100);  // Success indication
                        }
                    } else {
                        printf("WiFi reconnect failed - will retry\n");
                        blink_led(1, 500);  // Failure indication
                    }
                }
            } else {
                // Not connected - try to connect
                printf("WiFi not connected - attempting connection...\n");
                if (network_connect_wifi()) {
                    char ip_str[16];
                    network_get_ip_string(ip_str, sizeof(ip_str));
                    printf("WiFi connected! IP: %s\n", ip_str);

                    // Start listener
                    if (network_start_listener(on_stagekit_packet)) {
                        printf("Listener started\n");
                        blink_led(2, 100);  // Success indication
                    }
                } else {
                    printf("WiFi connection failed - will retry in %d seconds\n",
                           WIFI_CHECK_INTERVAL_MS / 1000);
                }
            }
        }

        // Adaptive delay
        if (stagekit_command_pending) {
            sleep_us(LOOP_DELAY_ACTIVE_US);
        } else {
            sleep_us(LOOP_DELAY_IDLE_US);
        }
    }

    return 0;
}
