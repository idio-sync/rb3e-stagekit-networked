#include <stdio.h>
#include <string.h>
#include "pico/stdlib.h"
#include "pico/cyw43_arch.h"
#include "lwip/pbuf.h"
#include "lwip/udp.h"
#include "tusb.h"
#include "hardware/gpio.h"

// Santroller Stage Kit definitions
#define SANTROLLER_VID 0x1209
#define SANTROLLER_PID 0x2882
#define SANTROLLER_STAGEKIT 0x0900

// Network configuration
#define UDP_LISTEN_PORT 21070
#define WIFI_SSID "YOUR_SSID"
#define WIFI_PASSWORD "YOUR_PASSWORD"

// RB3Enhanced packet definitions
#define RB3E_NETWORK_MAGICKEY 0x52423345

typedef struct {
    uint32_t ProtocolMagic;
    uint8_t  ProtocolVersion;
    uint8_t  PacketType;
    uint8_t  PacketSize;
    uint8_t  Platform;
} RB3E_EventHeader;

typedef enum {
    RB3E_EVENT_ALIVE = 0,
    RB3E_EVENT_STATE,
    RB3E_EVENT_SONG_NAME,
    RB3E_EVENT_SONG_ARTIST,
    RB3E_EVENT_SONG_SHORTNAME,
    RB3E_EVENT_SCORE,
    RB3E_EVENT_STAGEKIT,
    RB3E_EVENT_BAND_INFO
} RB3E_EventType;

typedef struct {
    uint8_t LeftChannel;
    uint8_t RightChannel;
} RB3E_EventStagekit;

// Stage Kit rumble data constants
enum {
    SK_FOG_ON = 0x01,
    SK_FOG_OFF = 0x02,
    SK_STROBE_SPEED_1 = 0x03,
    SK_STROBE_SPEED_2 = 0x04,
    SK_STROBE_SPEED_3 = 0x05,
    SK_STROBE_SPEED_4 = 0x06,
    SK_STROBE_OFF = 0x07,
    SK_LED_BLUE = 0x20,
    SK_LED_GREEN = 0x40,
    SK_LED_YELLOW = 0x60,
    SK_LED_RED = 0x80,
    SK_ALL_OFF = 0xFF
};

// Global state
static uint8_t usb_device_address = 0;
static bool stage_kit_connected = false;
static struct udp_pcb *udp_server_pcb = NULL;

// USB Host callbacks
void tuh_mount_cb(uint8_t dev_addr) {
    printf("Device attached, address = %d\n", dev_addr);
    
    // Get device descriptor
    tuh_descriptor_get_device(dev_addr, &usb_device_descriptor_callback, 0);
}

void tuh_umount_cb(uint8_t dev_addr) {
    printf("Device removed, address = %d\n", dev_addr);
    if (dev_addr == usb_device_address) {
        stage_kit_connected = false;
        usb_device_address = 0;
    }
}

void usb_device_descriptor_callback(tuh_xfer_t* xfer) {
    if (xfer->result == XFER_RESULT_SUCCESS) {
        tusb_desc_device_t* desc = (tusb_desc_device_t*)xfer->buffer;
        
        // Check if this is a Santroller Stage Kit
        if (desc->idVendor == SANTROLLER_VID && 
            desc->idProduct == SANTROLLER_PID && 
            desc->bcdDevice == SANTROLLER_STAGEKIT) {
            
            printf("Santroller Stage Kit detected!\n");
            usb_device_address = xfer->daddr;
            stage_kit_connected = true;
            
            // Set configuration
            tuh_configuration_set(usb_device_address, 1, NULL, 0);
        }
    }
}

// Send HID report to stage kit
bool send_stage_kit_report(uint8_t left_weight, uint8_t right_weight) {
    if (!stage_kit_connected) {
        return false;
    }
    
    uint8_t report[4] = {
        0x01,         // Report ID
        0x5A,         // Command
        left_weight,  // Left channel
        right_weight  // Right channel
    };
    
    // Send HID output report
    tuh_hid_set_report(usb_device_address, 0, 0, HID_REPORT_TYPE_OUTPUT, 
                       report, sizeof(report), NULL, 0);
    
    return true;
}

// Process RB3Enhanced lighting data
void process_rb3e_packet(const uint8_t* data, uint16_t len) {
    if (len < sizeof(RB3E_EventHeader)) {
        return;
    }
    
    RB3E_EventHeader* header = (RB3E_EventHeader*)data;
    
    // Verify magic key (handle endianness)
    uint32_t magic = __builtin_bswap32(header->ProtocolMagic);
    if (magic != RB3E_NETWORK_MAGICKEY) {
        return;
    }
    
    // Only process stage kit events
    if (header->PacketType == RB3E_EVENT_STAGEKIT) {
        RB3E_EventStagekit* stagekit_data = (RB3E_EventStagekit*)(data + sizeof(RB3E_EventHeader));
        
        printf("Stage Kit Event: Left=%02x Right=%02x\n", 
               stagekit_data->LeftChannel, stagekit_data->RightChannel);
        
        send_stage_kit_report(stagekit_data->LeftChannel, stagekit_data->RightChannel);
    }
}

// UDP receive callback
void udp_recv_callback(void *arg, struct udp_pcb *pcb, struct pbuf *p,
                       const ip_addr_t *addr, u16_t port) {
    if (p != NULL) {
        // Process the received packet
        process_rb3e_packet((uint8_t*)p->payload, p->len);
        
        // Free the packet buffer
        pbuf_free(p);
    }
}

// Initialize UDP server
bool init_udp_server(void) {
    udp_server_pcb = udp_new();
    if (udp_server_pcb == NULL) {
        printf("Failed to create UDP PCB\n");
        return false;
    }
    
    err_t err = udp_bind(udp_server_pcb, IP_ADDR_ANY, UDP_LISTEN_PORT);
    if (err != ERR_OK) {
        printf("Failed to bind UDP port %d\n", UDP_LISTEN_PORT);
        udp_remove(udp_server_pcb);
        return false;
    }
    
    udp_recv(udp_server_pcb, udp_recv_callback, NULL);
    printf("UDP server listening on port %d\n", UDP_LISTEN_PORT);
    
    return true;
}

// Connect to WiFi
bool connect_wifi(void) {
    printf("Connecting to WiFi...\n");
    
    if (cyw43_arch_wifi_connect_timeout_ms(WIFI_SSID, WIFI_PASSWORD, 
                                           CYW43_AUTH_WPA2_AES_PSK, 30000)) {
        printf("Failed to connect to WiFi\n");
        return false;
    }
    
    printf("Connected to WiFi!\n");
    printf("IP Address: %s\n", ip4addr_ntoa(netif_ip4_addr(netif_list)));
    
    return true;
}

int main() {
    // Initialize stdio
    stdio_init_all();
    
    printf("\n=== Pico W 2 Stage Kit Controller ===\n");
    printf("Version: 1.0\n\n");
    
    // Initialize WiFi
    if (cyw43_arch_init()) {
        printf("Failed to initialize WiFi\n");
        return 1;
    }
    
    cyw43_arch_enable_sta_mode();
    
    // Connect to WiFi
    if (!connect_wifi()) {
        return 1;
    }
    
    // Initialize UDP server
    if (!init_udp_server()) {
        return 1;
    }
    
    // Initialize USB host
    printf("Initializing USB host...\n");
    tusb_init();
    
    printf("Waiting for Stage Kit to connect...\n");
    
    // Flash onboard LED to show we're running
    bool led_state = false;
    absolute_time_t last_led_toggle = get_absolute_time();
    
    // Main loop
    while (true) {
        // Process USB events
        tuh_task();
        
        // Process network events
        cyw43_arch_poll();
        
        // Toggle LED every second
        if (absolute_time_diff_us(last_led_toggle, get_absolute_time()) > 1000000) {
            cyw43_arch_gpio_put(CYW43_WL_GPIO_LED_PIN, led_state);
            led_state = !led_state;
            last_led_toggle = get_absolute_time();
        }
        
        // Small delay
        sleep_ms(1);
    }
    
    return 0;
}
