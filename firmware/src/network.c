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

    // CYW43 is already initialized in main() for LED support
    // Just need to deinitialize and reinitialize with country code for WiFi
    printf("Network: Reinitializing CYW43 with country code...\n");
    cyw43_arch_deinit();
    if (cyw43_arch_init_with_country(CYW43_COUNTRY_USA)) {
        printf("Network: CYW43 init failed\n");
        net_state = NETWORK_STATE_ERROR;
        return false;
    }

    // Enable station mode
    cyw43_arch_enable_sta_mode();

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

    printf("Network: Connecting to '%s'...\n", wifi_config.ssid);
    net_state = NETWORK_STATE_CONNECTING;

    // Start async WiFi connection (non-blocking)
    int result = cyw43_arch_wifi_connect_async(
        wifi_config.ssid,
        wifi_config.password,
        CYW43_AUTH_WPA2_AES_PSK
    );

    if (result != 0) {
        printf("Network: WiFi connect start failed (err=%d)\n", result);
        net_state = NETWORK_STATE_ERROR;
        return false;
    }

    // Poll for connection with watchdog updates
    // This prevents the watchdog from firing during long connects
    printf("Network: Waiting for connection...\n");
    absolute_time_t timeout = make_timeout_time_ms(WIFI_CONNECT_TIMEOUT_MS);

    while (!time_reached(timeout)) {
        // Feed the watchdog to prevent reset
        watchdog_update();

        // Check connection status
        int status = cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);

        if (status == CYW43_LINK_UP) {
            // Connected successfully!
            cyw43_wifi_get_rssi(&cyw43_state, &net_stats.wifi_rssi);
            printf("Network: Connected! IP=%s RSSI=%d dBm\n",
                   ip4addr_ntoa(netif_ip4_addr(netif_default)),
                   net_stats.wifi_rssi);
            net_state = NETWORK_STATE_CONNECTED;
            return true;
        } else if (status == CYW43_LINK_FAIL || status == CYW43_LINK_BADAUTH ||
                   status == CYW43_LINK_NONET) {
            // Connection failed
            printf("Network: WiFi connect failed (status=%d)\n", status);
            net_state = NETWORK_STATE_ERROR;
            return false;
        }

        // Still connecting - poll CYW43 and wait a bit
        cyw43_arch_poll();
        sleep_ms(100);
    }

    // Timeout
    printf("Network: WiFi connect timeout\n");
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
