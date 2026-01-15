/*
 * Network Handler for RB3E StageKit Bridge
 *
 * Handles WiFi connection, UDP packet reception, and telemetry
 */

#ifndef _NETWORK_H_
#define _NETWORK_H_

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "config_parser.h"

#ifdef __cplusplus
extern "C" {
#endif

//--------------------------------------------------------------------
// Network Constants
//--------------------------------------------------------------------

#define TELEMETRY_INTERVAL_MS   5000    // Send telemetry every 5 seconds
#define WIFI_CONNECT_TIMEOUT_MS 15000   // WiFi connection timeout (must be < watchdog * 2)
#define WIFI_RETRY_DELAY_MS     3000    // Delay between connection retries
#define WIFI_MAX_RETRIES        3       // Max retries before giving up on boot

//--------------------------------------------------------------------
// Network State
//--------------------------------------------------------------------

typedef enum {
    NETWORK_STATE_DISCONNECTED = 0,
    NETWORK_STATE_CONNECTING,
    NETWORK_STATE_CONNECTED,
    NETWORK_STATE_LISTENING,
    NETWORK_STATE_ERROR
} network_state_t;

// WiFi failure reasons (for diagnostic LED patterns)
typedef enum {
    WIFI_FAIL_NONE = 0,
    WIFI_FAIL_TIMEOUT,      // Connection timed out
    WIFI_FAIL_NONET,        // SSID not found
    WIFI_FAIL_BADAUTH,      // Wrong password
    WIFI_FAIL_GENERAL       // Other failure
} wifi_fail_reason_t;

// Callback for StageKit packets
typedef void (*stagekit_packet_cb)(uint8_t left_weight, uint8_t right_weight);

//--------------------------------------------------------------------
// Network Statistics
//--------------------------------------------------------------------

typedef struct {
    uint32_t packets_received;
    uint32_t packets_processed;
    uint32_t packets_invalid;
    uint32_t telemetry_sent;
    int32_t wifi_rssi;
} network_stats_t;

//--------------------------------------------------------------------
// Public API
//--------------------------------------------------------------------

/**
 * Initialize network subsystem
 *
 * @param config WiFi configuration
 * @return true on success
 */
bool network_init(const wifi_config_t *config);

/**
 * Connect to WiFi network
 *
 * Blocking call that attempts to connect to configured WiFi network
 *
 * @return true if connected successfully
 */
bool network_connect_wifi(void);

/**
 * Start UDP listener for RB3E packets
 *
 * @param callback Function to call when StageKit packet is received
 * @return true if listener started successfully
 */
bool network_start_listener(stagekit_packet_cb callback);

/**
 * Stop UDP listener
 */
void network_stop_listener(void);

/**
 * Process network tasks
 *
 * Must be called regularly from network core
 */
void network_poll(void);

/**
 * Send telemetry broadcast
 *
 * @param usb_connected Whether Stage Kit is connected
 */
void network_send_telemetry(bool usb_connected);

/**
 * Check if WiFi is connected
 *
 * @return true if connected
 */
bool network_wifi_connected(void);

/**
 * Get current network state
 *
 * @return Current network state
 */
network_state_t network_get_state(void);

/**
 * Get network statistics
 *
 * @return Pointer to statistics structure
 */
const network_stats_t* network_get_stats(void);

/**
 * Get WiFi IP address as string
 *
 * @param buffer Buffer to write IP string
 * @param len Buffer length
 * @return Pointer to buffer
 */
char* network_get_ip_string(char *buffer, size_t len);

/**
 * Get WiFi RSSI (signal strength)
 *
 * @return RSSI in dBm
 */
int32_t network_get_rssi(void);

/**
 * Get MAC address as string
 *
 * @param buffer Buffer to write MAC string (at least 18 bytes)
 * @return Pointer to buffer
 */
char* network_get_mac_string(char *buffer);

/**
 * Get WiFi failure reason
 *
 * @return Failure reason from last failed connection attempt
 */
wifi_fail_reason_t network_get_wifi_fail_reason(void);

/**
 * Check WiFi connection status and update state
 *
 * Checks if WiFi is still connected. If disconnected, updates
 * internal state to DISCONNECTED so reconnection can occur.
 *
 * @return true if still connected, false if disconnected
 */
bool network_check_connection(void);

/**
 * Disconnect from WiFi network
 *
 * Cleanly disconnects from the current network and stops the listener.
 * Call network_connect_wifi() to reconnect.
 */
void network_disconnect(void);

#ifdef __cplusplus
}
#endif

#endif /* _NETWORK_H_ */
