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
static struct udp_pcb *udp_listener = NULL;
static struct udp_pcb *udp_telemetry = NULL;

// Callback for StageKit packets
static stagekit_packet_cb packet_callback = NULL;

// MAC address storage
static uint8_t mac_address[6];

//--------------------------------------------------------------------
// UDP Receive Callback
//--------------------------------------------------------------------

static void udp_recv_callback(void *arg, struct udp_pcb *pcb,
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

        // Poll CYW43 to process events
        cyw43_arch_poll();

        // Check connection status
        int status = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);

        // Print status changes
        if (status != last_status) {
            printf("Network: Status changed to %d\n", status);
            last_status = status;
        }

        // Print periodic status every 5 seconds
        if (++poll_count % 50 == 0) {
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
            return false;
        } else if (status == CYW43_LINK_BADAUTH) {
            printf("Network: WiFi connect failed: Wrong password\n");
            wifi_fail_reason = WIFI_FAIL_BADAUTH;
            net_state = NETWORK_STATE_ERROR;
            return false;
        } else if (status == CYW43_LINK_FAIL) {
            printf("Network: WiFi connect failed: General failure\n");
            wifi_fail_reason = WIFI_FAIL_GENERAL;
            net_state = NETWORK_STATE_ERROR;
            return false;
        }

        // Status 0-2 means still connecting, keep waiting
        sleep_ms(100);
    }

    // Timeout - print final status
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

    // Create UDP PCB for RB3E listener
    printf("Network: Starting UDP listener on port %d...\n", RB3E_LISTEN_PORT);

    udp_listener = udp_new();
    if (udp_listener == NULL) {
        printf("Network: Failed to create UDP PCB\n");
        return false;
    }

    // Bind to listen port
    err_t err = udp_bind(udp_listener, IP_ADDR_ANY, RB3E_LISTEN_PORT);
    if (err != ERR_OK) {
        printf("Network: UDP bind failed (err=%d)\n", err);
        udp_remove(udp_listener);
        udp_listener = NULL;
        return false;
    }

    // Set receive callback
    udp_recv(udp_listener, udp_recv_callback, NULL);

    // Create UDP PCB for telemetry
    udp_telemetry = udp_new();
    if (udp_telemetry == NULL) {
        printf("Network: Failed to create telemetry PCB\n");
        // Continue anyway - telemetry is optional
    }

    net_state = NETWORK_STATE_LISTENING;
    printf("Network: Listening for RB3E packets on port %d\n", RB3E_LISTEN_PORT);

    return true;
}

void network_stop_listener(void)
{
    if (udp_listener != NULL) {
        udp_remove(udp_listener);
        udp_listener = NULL;
    }

    if (udp_telemetry != NULL) {
        udp_remove(udp_telemetry);
        udp_telemetry = NULL;
    }

    packet_callback = NULL;

    if (net_state == NETWORK_STATE_LISTENING) {
        net_state = NETWORK_STATE_CONNECTED;
    }

    printf("Network: Listener stopped\n");
}

void network_poll(void)
{
    // CYW43 architecture handles polling internally in background mode
    cyw43_arch_poll();
}

void network_send_telemetry(bool usb_connected)
{
    if (udp_telemetry == NULL || net_state != NETWORK_STATE_LISTENING) {
        return;
    }

    // Update RSSI
    cyw43_wifi_get_rssi(&cyw43_state, &net_stats.wifi_rssi);

    // Build JSON telemetry
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

    // Allocate pbuf
    struct pbuf *p = pbuf_alloc(PBUF_TRANSPORT, len, PBUF_RAM);
    if (p == NULL) {
        return;
    }

    memcpy(p->payload, json, len);

    // Send to broadcast address
    ip_addr_t broadcast_addr;
    IP_ADDR4(&broadcast_addr, 255, 255, 255, 255);

    err_t err = udp_sendto(udp_telemetry, p, &broadcast_addr, RB3E_TELEMETRY_PORT);
    pbuf_free(p);

    if (err == ERR_OK) {
        net_stats.telemetry_sent++;
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
