/*
 * RB3E StageKit Bridge - Main Application (DEBUG VERSION)
 *
 * Wireless bridge for RB3Enhanced Stage Kit integration.
 * Receives UDP packets and sends HID commands to Santroller Stage Kit.
 *
 * DEBUG LED PATTERNS:
 * - 1 blink  = CYW43 init failed
 * - 2 blinks = WiFi CONNECTED (success!)
 * - 3 blinks = No settings file
 * - 4 blinks = Failed to load WiFi config
 * - 5 blinks = No filesystem
 * - 6 blinks = Network init failed
 * - 7 blinks = WiFi failed: SSID not found
 * - 8 blinks = WiFi failed: Wrong password
 * - 9 blinks = WiFi failed: Timeout
 * - 10 blinks = WiFi failed: General error
 * 
 * HEARTBEAT (in main loop):
 * - Slow (2s) toggle = Running, WiFi connected
 * - Fast (500ms) toggle = Running, WiFi NOT connected
 * - Rapid burst every 5s = Discovery packet received from dashboard
 */

#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "hardware/watchdog.h"
#include "hardware/sync.h"
#include <stdio.h>
#include <string.h>

#include "littlefs_hal.h"
#include "config_parser.h"
#include "usb_host.h"
#include "network.h"
#include "rb3e_protocol.h"
#include "ap_server.h"

//--------------------------------------------------------------------
// Timing Constants (in milliseconds)
//--------------------------------------------------------------------

#define WATCHDOG_TIMEOUT_MS     8000    // Reset if frozen for 8 seconds
#define HEARTBEAT_CONNECTED_MS  2000    // LED blink interval when WiFi connected
#define HEARTBEAT_DISCONNECTED_MS 500   // LED blink interval when WiFi disconnected
#define TELEMETRY_INTERVAL_MS   5000    // Telemetry broadcast interval
#define SAFETY_TIMEOUT_MS       5000    // Turn off lights if no packets
#define USB_RECONNECT_INTERVAL_MS 5000  // USB reconnection check interval
#define WIFI_CHECK_INTERVAL_MS  10000   // WiFi connection check interval
#define LOOP_DELAY_ACTIVE_US    100     // 0.1ms when active
#define LOOP_DELAY_IDLE_US      1000    // 1ms when idle
#define WIFI_MAX_RETRIES        3

//--------------------------------------------------------------------
// Shared State (for interrupt callbacks)
//--------------------------------------------------------------------

// Volatile for interrupt safety (network callbacks run in background)
static volatile bool stagekit_command_pending = false;
static volatile uint8_t pending_left_weight = 0;
static volatile uint8_t pending_right_weight = 0;
static wifi_config_t stored_wifi_cfg;

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
static bool wifi_is_connected = false; // Track WiFi state for heartbeat speed

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

static void blink_led_simple(int times, int delay_ms)
{
    // Simple blink without USB servicing (for early boot)
    for (int i = 0; i < times; i++) {
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 1);
        sleep_ms(delay_ms);
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 0);
        sleep_ms(delay_ms);
    }
}

static void blink_led(int times, int delay_ms)
{
    for (int i = 0; i < times; i++) {
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 1);
        // Service USB and watchdog during LED delays to prevent starvation
        for (int j = 0; j < delay_ms; j++) {
            usb_host_task();
            watchdog_update();
            sleep_ms(1);
        }
        cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 0);
        for (int j = 0; j < delay_ms; j++) {
            usb_host_task();
            watchdog_update();
            sleep_ms(1);
        }
    }
}

static void heartbeat_led_toggle(void)
{
    led_state = !led_state;
    cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, led_state);
}

// Error loop - blinks pattern forever
static void error_loop(int blinks)
{
    printf("ERROR: Entering error loop with %d blinks\n", blinks);
    while (1) {
        watchdog_update();
        blink_led(blinks, 200);
        sleep_ms(1500);  // Pause between patterns
    }
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
    printf("RB3E StageKit Bridge - Pico W Firmware (DEBUG)\n");
    printf("Build: " __DATE__ " " __TIME__ "\n");
    printf("==================================================\n");
	
	// Initialize CYW43 early for LED support
    printf("Initializing CYW43...\n");
    int cyw43_result = cyw43_arch_init_with_country(CYW43_COUNTRY_USA);
    if (cyw43_result) {
        printf("ERROR: CYW43 init failed with code %d\n", cyw43_result);
        // Can't even blink LED if CYW43 failed, but try anyway
        while(1) {
            sleep_ms(100);
        }
    }
    
    printf("CYW43 initialized OK\n");
    
    // Signal: CYW43 OK - single blink
    blink_led_simple(1, 100);
    sleep_ms(500);

    // DIAGNOSTIC: Blink LED to show detected flash size
    // 2 blinks = 2MB (Pico W), 4 blinks = 4MB (Pico 2 W)
    {
        uint32_t flash_mb = littlefs_get_flash_size() / (1024 * 1024);
        printf("DIAGNOSTIC: Flash size = %lu MB\n", flash_mb);
        printf("DIAGNOSTIC: FS offset = 0x%lX\n", littlefs_get_fs_offset());
        
        sleep_ms(300);  // Pause before diagnostic
        
        for (uint32_t i = 0; i < flash_mb; i++) {
            cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 1);
            sleep_ms(150);
            cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 0);
            sleep_ms(150);
        }
        
        sleep_ms(500);  // Pause after diagnostic before continuing
    }

    // Initialize watchdog
    printf("Initializing watchdog (%d ms timeout)...\n", WATCHDOG_TIMEOUT_MS);
    watchdog_enable(WATCHDOG_TIMEOUT_MS, true);
    watchdog_update();
	
	// 2. Initialize LittleFS
    printf("Initializing filesystem...\n");
    littlefs_init();
    
	if (littlefs_mount() != 0) {
		printf("Filesystem mount failed. Formatting...\n");
		littlefs_format_and_mount();
	}
	
	// 3. Attempt to Load Config
	bool config_loaded = false;
    
    if (config_file_exists()) {
        if (config_load_wifi(&stored_wifi_cfg) == 0) {
            config_loaded = true;
            printf("Config loaded: %s\n", stored_wifi_cfg.ssid);
        } else {
            printf("Config file invalid.\n");
        }
    } else {
        printf("No config file found.\n");
    }

    // 4. DECISION POINT: If no config, enter AP Setup Mode
    if (!config_loaded) {
        // This function NEVER returns. It saves and reboots.
        run_ap_setup_mode(); 
    }
	
    watchdog_update();
	
    // Initialize USB Host
    printf("Initializing USB host...\n");
    usb_host_init();

    // Register USB task as service callback
    network_set_service_callback(usb_host_task);

    // Initialize network
    printf("Initializing network...\n");
    if (!network_init(&stored_wifi_cfg)) {
        printf("ERROR: Network initialization failed!\n");
        error_loop(6);  // 6 blinks = network init failed
    }
    printf("Network initialized\n");

    // Connect to WiFi with retries
    printf("\n");
    printf("Connecting to WiFi: '%s'\n", stored_wifi_cfg.ssid);
    printf("Password length: %d chars\n", (int)strlen(stored_wifi_cfg.password));
    
    wifi_is_connected = false;
    for (int attempt = 1; attempt <= WIFI_MAX_RETRIES; attempt++) {
        printf("WiFi attempt %d of %d...\n", attempt, WIFI_MAX_RETRIES);
        
        // Blink to show we're trying
        blink_led(attempt, 100);
        
        watchdog_update();

        if (network_connect_wifi()) {
            wifi_is_connected = true;
            printf("WiFi CONNECTED!\n");
            break;
        }

        wifi_fail_reason_t reason = network_get_wifi_fail_reason();
        printf("Attempt %d failed: ", attempt);
        
        switch (reason) {
            case WIFI_FAIL_NONET:
                printf("SSID '%s' not found!\n", stored_wifi_cfg.ssid);
                // Show error but keep trying
                blink_led(7, 150);
                break;
            case WIFI_FAIL_BADAUTH:
                printf("Wrong password!\n");
                // Don't retry on bad password
                error_loop(8);  // 8 blinks = bad password
                break;
            case WIFI_FAIL_TIMEOUT:
                printf("Connection timeout\n");
                blink_led(9, 150);
                break;
            default:
                printf("General failure (reason=%d)\n", reason);
                blink_led(10, 150);
                break;
        }

        if (attempt < WIFI_MAX_RETRIES) {
            printf("Retrying in %d seconds...\n", WIFI_RETRY_DELAY_MS / 1000);
            for (int i = 0; i < WIFI_RETRY_DELAY_MS / 100; i++) {
                watchdog_update();
                sleep_ms(100);
            }
        }
    }

    // Show final WiFi status
    if (wifi_is_connected) {
        char ip_str[16];
        network_get_ip_string(ip_str, sizeof(ip_str));
        printf("SUCCESS! IP address: %s\n", ip_str);
        printf("RSSI: %d dBm\n", network_get_rssi());
        
        // Victory blink: 2 quick blinks
        blink_led(2, 100);
        sleep_ms(300);
        blink_led(2, 100);
    } else {
        printf("WARNING: WiFi connection failed!\n");
        printf("Will keep retrying in background...\n");
        // 3 slow blinks to indicate failure but continuing
        blink_led(3, 500);
    }

    watchdog_update();

    // Start UDP listener if WiFi connected
    if (wifi_is_connected) {
        printf("Starting UDP listener...\n");
        if (!network_start_listener(on_stagekit_packet)) {
            printf("ERROR: Failed to start listener\n");
            wifi_is_connected = false;
        } else {
            printf("UDP listener started on port %d\n", RB3E_LISTEN_PORT);
            printf("Telemetry/discovery on port %d\n", RB3E_TELEMETRY_PORT);
        }
    }

    // Initialize timing
    last_packet_time = get_absolute_time();
    last_heartbeat_time = get_absolute_time();
    last_telemetry_time = get_absolute_time();
    last_usb_reconnect_time = get_absolute_time();
    last_wifi_check_time = get_absolute_time();

    printf("\n");
    printf("==================================================\n");
    printf("MAIN LOOP STARTING\n");
    printf("Heartbeat: %s\n", wifi_is_connected ? "SLOW (2s) = connected" : "FAST (500ms) = disconnected");
    printf("==================================================\n");

    // Track last discovery count to detect new discoveries
    uint32_t last_discovery_count = 0;

    // Main loop
    while (true) {
        absolute_time_t now = get_absolute_time();
        bool was_active = false;

        // Feed watchdog
        watchdog_update();

        // Process USB tasks
        usb_host_task();

        // Process pending StageKit command
        if (stagekit_command_pending) {
            uint8_t left, right;

            uint32_t save = save_and_disable_interrupts();
            stagekit_command_pending = false;
            left = pending_left_weight;
            right = pending_right_weight;
            restore_interrupts(save);

            was_active = true;
            last_packet_time = now;

            if (usb_stagekit_connected()) {
                usb_send_stagekit_command(left, right);
                lights_active = true;
            }
        }

        // Heartbeat LED - speed indicates WiFi status
        uint32_t heartbeat_interval = wifi_is_connected ? HEARTBEAT_CONNECTED_MS : HEARTBEAT_DISCONNECTED_MS;
        if (absolute_time_diff_us(last_heartbeat_time, now) > (heartbeat_interval * 1000)) {
            heartbeat_led_toggle();
            last_heartbeat_time = now;
        }

        // Check for new dashboard discovery (blink rapidly to indicate)
        const network_stats_t *stats = network_get_stats();
        if (stats->discovery_received > last_discovery_count) {
            last_discovery_count = stats->discovery_received;
            // Rapid blink to show discovery received!
            printf("Dashboard discovered! Count: %lu\n", stats->discovery_received);
            for (int i = 0; i < 5; i++) {
                cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 1);
                sleep_ms(50);
                cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, 0);
                sleep_ms(50);
            }
        }

        // Send telemetry
        if (network_wifi_connected() &&
            absolute_time_diff_us(last_telemetry_time, now) > (TELEMETRY_INTERVAL_MS * 1000)) {
            network_send_telemetry(usb_stagekit_connected());
            last_telemetry_time = now;
        }

        // Safety timeout
        if (lights_active &&
            absolute_time_diff_us(last_packet_time, now) > (SAFETY_TIMEOUT_MS * 1000)) {
            if (usb_stagekit_connected()) {
                usb_stagekit_all_off();
            }
            lights_active = false;
        }

        // WiFi connection check
        if (absolute_time_diff_us(last_wifi_check_time, now) > (WIFI_CHECK_INTERVAL_MS * 1000)) {
            last_wifi_check_time = now;

            if (network_wifi_connected()) {
                if (!network_check_connection()) {
                    printf("WiFi lost! Reconnecting...\n");
                    wifi_is_connected = false;
                    network_stop_listener();

                    if (network_connect_wifi()) {
                        wifi_is_connected = true;
                        char ip_str[16];
                        network_get_ip_string(ip_str, sizeof(ip_str));
                        printf("Reconnected! IP: %s\n", ip_str);
                        network_start_listener(on_stagekit_packet);
                        blink_led(2, 100);
                    }
                }
            } else {
                printf("Trying to connect WiFi...\n");
                if (network_connect_wifi()) {
                    wifi_is_connected = true;
                    char ip_str[16];
                    network_get_ip_string(ip_str, sizeof(ip_str));
                    printf("Connected! IP: %s\n", ip_str);
                    network_start_listener(on_stagekit_packet);
                    blink_led(2, 100);
                } else {
                    wifi_fail_reason_t reason = network_get_wifi_fail_reason();
                    printf("WiFi failed (reason=%d)\n", reason);
                }
            }
        }

        // Adaptive delay
        if (was_active || stagekit_command_pending) {
            sleep_us(LOOP_DELAY_ACTIVE_US);
        } else {
            sleep_us(LOOP_DELAY_IDLE_US);
        }
    }

    return 0;
}
