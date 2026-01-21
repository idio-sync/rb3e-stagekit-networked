/*
 * Network Handler for RB3E StageKit Bridge
 *
 * Implements WiFi connection and UDP handling using LwIP raw API
 */

#include "network.h"
#include "rb3e_protocol.h"
#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "hardware/watchdog.h"
#include "lwip/udp.h"
#include "lwip/pbuf.h"
#include "lwip/ip_addr.h"
#include <stdio.h>
#include <string.h>

//--------------------------------------------------------------------
// State Variables
//--------------------------------------------------------------------

static network_state_t net_state = NETWORK_STATE_DISCONNECTED;
static network_stats_t net_stats = {0};
static wifi_config_t wifi_config;
static wifi_fail_reason_t wifi_fail_reason = WIFI_FAIL_NONE;

// UDP PCBs (Protocol Control Blocks)
static struct udp_pcb *udp_listener = NULL;      // Port 21070 - RB3E StageKit commands
static struct udp_pcb *udp_telemetry = NULL;     // Port 21071 - Discovery & Telemetry (single socket like RB3E)

// Callback for StageKit packets
static stagekit_packet_cb packet_callback = NULL;

// Callback for servicing other tasks during blocking operations
static void (*service_callback)(void) = NULL;

// MAC address storage
static uint8_t mac_address[6];

// Dashboard discovery state
static bool dashboard_discovered = false;
static ip_addr_t dashboard_addr;
static absolute_time_t last_discovery_time;
#define DISCOVERY_TIMEOUT_MS 30000  // Consider dashboard lost after 30 seconds

//--------------------------------------------------------------------
// Simple JSON Parser Helper
//--------------------------------------------------------------------

/**
 * Check if a JSON string contains a specific key-value pair
 * Very simple parser - looks for "key":"value" pattern
 */
static bool json_contains(const char *json, size_t len, const char *key, const char *value)
{
    // Build search pattern: "key":"value" or "key": "value"
    char pattern[64];
    int pattern_len = snprintf(pattern, sizeof(pattern), "\"%s\":\"%s\"", key, value);
    if (pattern_len >= (int)sizeof(pattern)) {
        return false;
    }
    
    // Search for pattern
    if (len >= (size_t)pattern_len) {
        for (size_t i = 0; i <= len - pattern_len; i++) {
            if (memcmp(json + i, pattern, pattern_len) == 0) {
                return true;
            }
        }
    }
    
    // Also try with space after colon: "key": "value"
    pattern_len = snprintf(pattern, sizeof(pattern), "\"%s\": \"%s\"", key, value);
    if (pattern_len < (int)sizeof(pattern) && len >= (size_t)pattern_len) {
        for (size_t i = 0; i <= len - pattern_len; i++) {
            if (memcmp(json + i, pattern, pattern_len) == 0) {
                return true;
            }
        }
    }
    
    return false;
}

//--------------------------------------------------------------------
// UDP Receive Callbacks
//--------------------------------------------------------------------

/**
 * Callback for RB3E StageKit packets on port 21070
 */
static void udp_stagekit_callback(void *arg, struct udp_pcb *pcb,
                                   struct pbuf *p, const ip_addr_t *addr, u16_t port)
{
    (void)arg;
    (void)pcb;
    (void)addr;
    (void)port;

    if (p == NULL) {
        return;
    }

    net_stats.packets_received++;

    // Process packet if callback is set
    if (packet_callback && p->len >= 10) {
        uint8_t left, right;

        // Parse RB3E StageKit packet
        if (rb3e_parse_stagekit((uint8_t*)p->payload, p->len, &left, &right)) {
            net_stats.packets_processed++;
            packet_callback(left, right);
        } else {
            net_stats.packets_invalid++;
        }
    }

    // Free the pbuf
    pbuf_free(p);
}

/**
 * Callback for telemetry port (21071) - handles discovery packets
 * 
 * Dashboard sends: {"type": "discovery"}
 * We extract the sender's IP and send telemetry directly to them
 * 
 * This uses the same PCB that we send telemetry from, matching RB3E's pattern
 * where one socket is used for both send and receive.
 */
static void udp_telemetry_callback(void *arg, struct udp_pcb *pcb,
                                    struct pbuf *p, const ip_addr_t *addr, u16_t port)
{
    (void)arg;
    (void)pcb;
    (void)port;

    if (p == NULL || addr == NULL) {
        return;
    }

    // Check if this looks like a discovery packet
    // Dashboard sends: {"type":"discovery"} or {"type": "discovery"}
    if (p->len > 0 && p->len < 256) {
        char *payload = (char*)p->payload;
        
        // Check for discovery packet
        if (json_contains(payload, p->len, "type", "discovery")) {
            // Store the dashboard's IP address
            ip_addr_copy(dashboard_addr, *addr);
            dashboard_discovered = true;
            last_discovery_time = get_absolute_time();
            
            printf("Network: Dashboard discovered at %s\n", ip4addr_ntoa(addr));
            
            // Increment discovery count in stats
            net_stats.discovery_received++;
        }
    }

    // Free the pbuf
    pbuf_free(p);
}

//--------------------------------------------------------------------
// WiFi Status Callback
//--------------------------------------------------------------------

static void wifi_link_callback(struct netif *netif)
{
    if (netif_is_link_up(netif)) {
        printf("Network: WiFi link up\n");
    } else {
        printf("Network: WiFi link down\n");
        net_state = NETWORK_STATE_DISCONNECTED;
    }
}

static void wifi_status_callback(struct netif *netif)
{
    if (netif_is_up(netif)) {
        printf("Network: Interface up, IP: %s\n",
               ip4addr_ntoa(netif_ip4_addr(netif)));
    } else {
        printf("Network: Interface down\n");
    }
}

//--------------------------------------------------------------------
// Public API Implementation
//--------------------------------------------------------------------

void network_set_service_callback(void (*callback)(void))
{
    service_callback = callback;
}

bool network_init(const wifi_config_t *config)
{
    if (!config || !config->valid) {
        printf("Network: Invalid WiFi config\n");
        return false;
    }

    // Copy config
    memcpy(&wifi_config, config, sizeof(wifi_config_t));

    // CYW43 is already initialized in main() with country code
    // No deinit/reinit needed - that was causing a "zombie radio" state
    // where the SPI bus worked but the RF circuitry wasn't properly configured
    printf("Network: Configuring WiFi (CYW43 already initialized)...\n");

    // Note: Using pico_cyw43_arch_lwip_threadsafe_background - polling is
    // handled automatically by background interrupts, no manual poll needed

    // Enable station mode
    cyw43_arch_enable_sta_mode();

    // Disable power save and boost receive sensitivity:
    cyw43_wifi_pm(&cyw43_state, cyw43_pm_value(CYW43_NO_POWERSAVE_MODE, 20, 1, 1, 1));

    // Get MAC address
    cyw43_wifi_get_mac(&cyw43_state, CYW43_ITF_STA, mac_address);
    printf("Network: MAC = %02x:%02x:%02x:%02x:%02x:%02x\n",
           mac_address[0], mac_address[1], mac_address[2],
           mac_address[3], mac_address[4], mac_address[5]);

    // Set callbacks
    netif_set_link_callback(netif_default, wifi_link_callback);
    netif_set_status_callback(netif_default, wifi_status_callback);

    // Initialize discovery state
    dashboard_discovered = false;
    IP_ADDR4(&dashboard_addr, 0, 0, 0, 0);

    net_state = NETWORK_STATE_DISCONNECTED;
    printf("Network: Initialized\n");

    return true;
}

bool network_connect_wifi(void)
{
    if (net_state == NETWORK_STATE_CONNECTED ||
        net_state == NETWORK_STATE_LISTENING) {
        return true;
    }

    wifi_fail_reason = WIFI_FAIL_NONE;
    printf("Network: Connecting to '%s'...\n", wifi_config.ssid);
    net_state = NETWORK_STATE_CONNECTING;

    // Brief delay for radio readiness before scan
    // Note: Using threadsafe_background - polling handled automatically
    sleep_ms(50);

    // Start async WiFi connection (non-blocking)
    int result = cyw43_arch_wifi_connect_async(
        wifi_config.ssid,
        wifi_config.password,
        CYW43_AUTH_WPA2_MIXED_PSK
    );

    if (result != 0) {
        printf("Network: WiFi connect start failed (err=%d)\n", result);
        wifi_fail_reason = WIFI_FAIL_GENERAL;
        net_state = NETWORK_STATE_ERROR;
        return false;
    }

    // Poll for connection with watchdog updates
    // This prevents the watchdog from firing during long connects
    printf("Network: Waiting for connection...\n");
    absolute_time_t timeout = make_timeout_time_ms(WIFI_CONNECT_TIMEOUT_MS);
    int last_status = -99;
    int poll_count = 0;

    // Status values:
    // CYW43_LINK_DOWN (0), CYW43_LINK_JOIN (1), CYW43_LINK_NOIP (2), CYW43_LINK_UP (3)
    // CYW43_LINK_FAIL (-1), CYW43_LINK_NONET (-2), CYW43_LINK_BADAUTH (-3)

    while (!time_reached(timeout)) {
        // Feed the watchdog to prevent reset
        watchdog_update();

        // Service other tasks (e.g., USB) to prevent starvation
        if (service_callback) {
            service_callback();
        }

        // Check connection status
        int status = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);

        // Print status changes
        if (status != last_status) {
            printf("Network: Status changed to %d\n", status);
            last_status = status;
        }

        // Print periodic status every 5 seconds (500 iterations * 10ms = 5s)
        if (++poll_count % 500 == 0) {
            printf("Network: Still waiting... status=%d\n", status);
        }

        if (status == CYW43_LINK_UP) {
            // Connected successfully with IP!
            wifi_fail_reason = WIFI_FAIL_NONE;
            cyw43_wifi_get_rssi(&cyw43_state, &net_stats.wifi_rssi);
            printf("Network: Connected! IP=%s RSSI=%d dBm\n",
                   ip4addr_ntoa(netif_ip4_addr(netif_default)),
                   net_stats.wifi_rssi);
            net_state = NETWORK_STATE_CONNECTED;
            return true;
        } else if (status == CYW43_LINK_NONET) {
            printf("Network: WiFi connect failed: SSID not found\n");
            wifi_fail_reason = WIFI_FAIL_NONET;
            net_state = NETWORK_STATE_ERROR;
            cyw43_wifi_leave(&cyw43_state, CYW43_ITF_STA);  // Clean up driver state
            return false;
        } else if (status == CYW43_LINK_BADAUTH) {
            printf("Network: WiFi connect failed: Wrong password\n");
            wifi_fail_reason = WIFI_FAIL_BADAUTH;
            net_state = NETWORK_STATE_ERROR;
            cyw43_wifi_leave(&cyw43_state, CYW43_ITF_STA);  // Clean up driver state
            return false;
        } else if (status == CYW43_LINK_FAIL) {
            printf("Network: WiFi connect failed: General failure\n");
            wifi_fail_reason = WIFI_FAIL_GENERAL;
            net_state = NETWORK_STATE_ERROR;
            cyw43_wifi_leave(&cyw43_state, CYW43_ITF_STA);  // Clean up driver state
            return false;
        }

        // Status 0-2 means still connecting, keep waiting
        // Use short sleep (10ms) for responsive USB enumeration during boot
        sleep_ms(10);
    }

    // Timeout - clean up driver state and report error
    cyw43_wifi_leave(&cyw43_state, CYW43_ITF_STA);
    int final_status = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);
    printf("Network: WiFi connect timeout (final status=%d)\n", final_status);
    wifi_fail_reason = WIFI_FAIL_TIMEOUT;
    net_state = NETWORK_STATE_ERROR;
    return false;
}

bool network_start_listener(stagekit_packet_cb callback)
{
    if (net_state != NETWORK_STATE_CONNECTED) {
        printf("Network: Cannot start listener - not connected\n");
        return false;
    }

    packet_callback = callback;

    // Acquire LwIP lock for thread safety with background processing
    cyw43_arch_lwip_begin();

    //----------------------------------------------------------------
    // Create UDP PCB for RB3E StageKit commands (port 21070)
    //----------------------------------------------------------------
    printf("Network: Starting StageKit listener on port %d...\n", RB3E_LISTEN_PORT);

    udp_listener = udp_new();
    if (udp_listener == NULL) {
        cyw43_arch_lwip_end();
        printf("Network: Failed to create StageKit UDP PCB\n");
        return false;
    }

    // Bind to listen port
    err_t err = udp_bind(udp_listener, IP_ADDR_ANY, RB3E_LISTEN_PORT);
    if (err != ERR_OK) {
        printf("Network: StageKit UDP bind failed (err=%d)\n", err);
        udp_remove(udp_listener);
        udp_listener = NULL;
        cyw43_arch_lwip_end();
        return false;
    }

    // Set receive callback
    udp_recv(udp_listener, udp_stagekit_callback, NULL);
    printf("Network: StageKit listener active on port %d\n", RB3E_LISTEN_PORT);

    //----------------------------------------------------------------
    // Create UDP PCB for telemetry & discovery (port 21071)
    // Single socket for both send and receive, like RB3E does
    // This is important - some routers handle unbound sockets differently
    //----------------------------------------------------------------
    printf("Network: Starting telemetry/discovery on port %d...\n", RB3E_TELEMETRY_PORT);

    udp_telemetry = udp_new();
    if (udp_telemetry == NULL) {
        printf("Network: Failed to create telemetry UDP PCB\n");
        // Continue anyway - StageKit will work, just no telemetry
    } else {
        // Enable broadcast for this socket - required for UDP broadcast to work
        ip_set_option(udp_telemetry, SOF_BROADCAST);

        // Bind to telemetry port - this allows both sending AND receiving on this port
        // Matching RB3E's pattern: RB3E_BindPort(RB3E_EventsSocket, BROADCAST_PORT);
        err = udp_bind(udp_telemetry, IP_ADDR_ANY, RB3E_TELEMETRY_PORT);
        if (err != ERR_OK) {
            printf("Network: Telemetry bind failed (err=%d)\n", err);
            udp_remove(udp_telemetry);
            udp_telemetry = NULL;
        } else {
            // Set receive callback for discovery packets
            udp_recv(udp_telemetry, udp_telemetry_callback, NULL);
            printf("Network: Telemetry socket bound to port %d (send + receive)\n", RB3E_TELEMETRY_PORT);
        }
    }

    cyw43_arch_lwip_end();

    net_state = NETWORK_STATE_LISTENING;
    printf("Network: Ready! Listening for StageKit on %d, telemetry on %d\n", 
           RB3E_LISTEN_PORT, RB3E_TELEMETRY_PORT);

    return true;
}

void network_stop_listener(void)
{
    // Acquire LwIP lock for thread safety
    cyw43_arch_lwip_begin();

    if (udp_listener != NULL) {
        udp_remove(udp_listener);
        udp_listener = NULL;
    }

    if (udp_telemetry != NULL) {
        udp_remove(udp_telemetry);
        udp_telemetry = NULL;
    }

    cyw43_arch_lwip_end();

    packet_callback = NULL;
    dashboard_discovered = false;

    if (net_state == NETWORK_STATE_LISTENING) {
        net_state = NETWORK_STATE_CONNECTED;
    }

    printf("Network: Listener stopped\n");
}

void network_send_telemetry(bool usb_connected)
{
    if (udp_telemetry == NULL || net_state != NETWORK_STATE_LISTENING) {
        return;
    }

    // Check if dashboard discovery has timed out
    if (dashboard_discovered) {
        if (absolute_time_diff_us(last_discovery_time, get_absolute_time()) > 
            (DISCOVERY_TIMEOUT_MS * 1000)) {
            printf("Network: Dashboard discovery timeout - reverting to broadcast\n");
            dashboard_discovered = false;
        }
    }

    // Update RSSI
    cyw43_wifi_get_rssi(&cyw43_state, &net_stats.wifi_rssi);

    // Build JSON telemetry (outside of lock - no LwIP calls here)
    char mac_str[18];
    network_get_mac_string(mac_str);

    char json[256];
    int len = snprintf(json, sizeof(json),
        "{\"id\":\"%s\","
        "\"name\":\"Pico %02x:%02x\","
        "\"usb_status\":\"%s\","
        "\"wifi_signal\":%d,"
        "\"uptime\":%lu}",
        mac_str,
        mac_address[4], mac_address[5],
        usb_connected ? "Connected" : "Disconnected",
        net_stats.wifi_rssi,
        to_ms_since_boot(get_absolute_time()) / 1000
    );

    // Acquire LwIP lock for pbuf and UDP operations
    cyw43_arch_lwip_begin();

    // Allocate pbuf
    struct pbuf *p = pbuf_alloc(PBUF_TRANSPORT, len, PBUF_RAM);
    if (p == NULL) {
        cyw43_arch_lwip_end();
        return;
    }

    memcpy(p->payload, json, len);

    // Send to dashboard (unicast) if discovered, otherwise broadcast
    ip_addr_t dest_addr;
    err_t err;

    if (dashboard_discovered) {
        ip_addr_copy(dest_addr, dashboard_addr);
        err = udp_sendto(udp_telemetry, p, &dest_addr, RB3E_TELEMETRY_PORT);
    } else {
        // Send to both global broadcast and subnet broadcast for better compatibility
        // Some routers block 255.255.255.255 but allow subnet broadcasts

        // First, send to subnet broadcast (e.g., 192.168.1.255)
        if (netif_default != NULL) {
            ip4_addr_t subnet_bcast;
            subnet_bcast.addr = (netif_ip4_addr(netif_default)->addr | ~netif_ip4_netmask(netif_default)->addr);
            ip_addr_copy(dest_addr, subnet_bcast);
            err = udp_sendto(udp_telemetry, p, &dest_addr, RB3E_TELEMETRY_PORT);
        }

        // Also send to global broadcast (255.255.255.255)
        IP_ADDR4(&dest_addr, 255, 255, 255, 255);
        err = udp_sendto(udp_telemetry, p, &dest_addr, RB3E_TELEMETRY_PORT);
    }

    pbuf_free(p);

    cyw43_arch_lwip_end();

    if (err == ERR_OK) {
        net_stats.telemetry_sent++;
        // Debug output (can be removed later)
        if (dashboard_discovered) {
            printf("Network: Telemetry #%lu sent to %s:%d\n", 
                   net_stats.telemetry_sent, ip4addr_ntoa(&dest_addr), RB3E_TELEMETRY_PORT);
        } else {
            printf("Network: Telemetry #%lu broadcast to %s:%d\n", 
                   net_stats.telemetry_sent, ip4addr_ntoa(&dest_addr), RB3E_TELEMETRY_PORT);
        }
    } else {
        printf("Network: Telemetry send failed (err=%d)\n", err);
    }
}

bool network_wifi_connected(void)
{
    return (net_state == NETWORK_STATE_CONNECTED ||
            net_state == NETWORK_STATE_LISTENING);
}

network_state_t network_get_state(void)
{
    return net_state;
}

const network_stats_t* network_get_stats(void)
{
    return &net_stats;
}

char* network_get_ip_string(char *buffer, size_t len)
{
    if (netif_default != NULL && netif_is_up(netif_default)) {
        snprintf(buffer, len, "%s", ip4addr_ntoa(netif_ip4_addr(netif_default)));
    } else {
        snprintf(buffer, len, "0.0.0.0");
    }
    return buffer;
}

int32_t network_get_rssi(void)
{
    if (network_wifi_connected()) {
        cyw43_wifi_get_rssi(&cyw43_state, &net_stats.wifi_rssi);
    }
    return net_stats.wifi_rssi;
}

char* network_get_mac_string(char *buffer)
{
    snprintf(buffer, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
             mac_address[0], mac_address[1], mac_address[2],
             mac_address[3], mac_address[4], mac_address[5]);
    return buffer;
}

wifi_fail_reason_t network_get_wifi_fail_reason(void)
{
    return wifi_fail_reason;
}

bool network_check_connection(void)
{
    // Only check if we think we're connected
    if (net_state != NETWORK_STATE_CONNECTED &&
        net_state != NETWORK_STATE_LISTENING) {
        return false;
    }

    // Check actual link status from CYW43 driver
    int status = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);

    if (status != CYW43_LINK_UP) {
        printf("Network: Connection lost (status=%d)\n", status);
        net_state = NETWORK_STATE_DISCONNECTED;
        return false;
    }

    return true;
}

void network_disconnect(void)
{
    printf("Network: Disconnecting...\n");

    // Stop listener first
    network_stop_listener();

    // Leave the WiFi network
    cyw43_wifi_leave(&cyw43_state, CYW43_ITF_STA);

    net_state = NETWORK_STATE_DISCONNECTED;
    dashboard_discovered = false;
    printf("Network: Disconnected\n");
}

bool network_dashboard_discovered(void)
{
    return dashboard_discovered;
}

void network_get_dashboard_ip(char *buffer, size_t len)
{
    if (dashboard_discovered) {
        snprintf(buffer, len, "%s", ip4addr_ntoa(&dashboard_addr));
    } else {
        snprintf(buffer, len, "none");
    }
}
