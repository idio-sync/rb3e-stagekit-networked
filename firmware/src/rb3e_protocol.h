/*
 * RB3Enhanced Network Protocol Definitions
 *
 * Clean C structs for parsing RB3E network packets.
 * Based on RB3E_Network.cpp reference implementation.
 */

#ifndef _RB3E_PROTOCOL_H_
#define _RB3E_PROTOCOL_H_

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

//--------------------------------------------------------------------
// Protocol Constants
//--------------------------------------------------------------------

// RB3E Magic number: "RB3E" = 0x52423345 (big-endian)
#define RB3E_MAGIC              0x52423345
#define RB3E_MAGIC_BYTE0        0x52  // 'R'
#define RB3E_MAGIC_BYTE1        0x42  // 'B'
#define RB3E_MAGIC_BYTE2        0x33  // '3'
#define RB3E_MAGIC_BYTE3        0x45  // 'E'

// Event Types
#define RB3E_EVENT_ALIVE        0
#define RB3E_EVENT_STATE        1
#define RB3E_EVENT_SONG_NAME    2
#define RB3E_EVENT_SONG_ARTIST  3
#define RB3E_EVENT_SONG_SHORT   4
#define RB3E_EVENT_SCORE        5
#define RB3E_EVENT_STAGEKIT     6
#define RB3E_EVENT_BAND_INFO    7

// Network Ports
#define RB3E_LISTEN_PORT        21070
#define RB3E_TELEMETRY_PORT     21071

//--------------------------------------------------------------------
// StageKit Command Constants
//--------------------------------------------------------------------

#define SK_FOG_ON               0x01
#define SK_FOG_OFF              0x02
#define SK_STROBE_SPEED_1       0x03
#define SK_STROBE_SPEED_2       0x04
#define SK_STROBE_SPEED_3       0x05
#define SK_STROBE_SPEED_4       0x06
#define SK_STROBE_OFF           0x07
#define SK_LED_BLUE             0x20
#define SK_LED_GREEN            0x40
#define SK_LED_YELLOW           0x60
#define SK_LED_RED              0x80
#define SK_ALL_OFF              0xFF

//--------------------------------------------------------------------
// Packet Structures (packed for network byte order)
//--------------------------------------------------------------------

// RB3E Packet Header (8 bytes)
typedef struct __attribute__((packed)) {
    uint8_t magic[4];           // Protocol magic: "RB3E" (0x52, 0x42, 0x33, 0x45)
    uint8_t protocol_version;   // Protocol version
    uint8_t packet_type;        // Event type (RB3E_EVENT_*)
    uint8_t packet_size;        // Size of payload data
    uint8_t platform;           // Platform identifier
} rb3e_header_t;

// StageKit Event Data (2 bytes)
typedef struct __attribute__((packed)) {
    uint8_t left_channel;       // LED pattern byte (which LEDs 1-8 are on)
    uint8_t right_channel;      // Command byte (color/strobe/fog)
} rb3e_stagekit_event_t;

// Complete StageKit Packet (header + data = 10 bytes minimum)
typedef struct __attribute__((packed)) {
    rb3e_header_t header;
    rb3e_stagekit_event_t data;
} rb3e_stagekit_packet_t;

// Generic Event Packet (for parsing any packet type)
typedef struct __attribute__((packed)) {
    rb3e_header_t header;
    uint8_t data[256];          // Variable length payload
} rb3e_packet_t;

//--------------------------------------------------------------------
// Validation Functions
//--------------------------------------------------------------------

/**
 * Check if buffer contains valid RB3E magic bytes
 *
 * @param data Pointer to packet data (minimum 4 bytes)
 * @return 1 if valid magic, 0 otherwise
 */
static inline int rb3e_check_magic(const uint8_t *data)
{
    return (data[0] == RB3E_MAGIC_BYTE0 &&
            data[1] == RB3E_MAGIC_BYTE1 &&
            data[2] == RB3E_MAGIC_BYTE2 &&
            data[3] == RB3E_MAGIC_BYTE3);
}

/**
 * Parse a StageKit event from raw packet data
 *
 * @param data Pointer to raw packet data
 * @param len Length of packet data
 * @param left_out Pointer to store left channel value
 * @param right_out Pointer to store right channel value
 * @return 1 if valid StageKit packet, 0 otherwise
 */
static inline int rb3e_parse_stagekit(const uint8_t *data, size_t len,
                                       uint8_t *left_out, uint8_t *right_out)
{
    // Minimum packet size: header (8) + stagekit data (2) = 10 bytes
    if (len < 10) {
        return 0;
    }

    // Fast-fail: Check magic bytes directly
    if (!rb3e_check_magic(data)) {
        return 0;
    }

    // Check packet type (offset 5)
    if (data[5] != RB3E_EVENT_STAGEKIT) {
        return 0;
    }

    // Extract StageKit data (offsets 8 and 9)
    *left_out = data[8];
    *right_out = data[9];

    return 1;
}

#ifdef __cplusplus
}
#endif

#endif /* _RB3E_PROTOCOL_H_ */
