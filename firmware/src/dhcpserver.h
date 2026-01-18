/*
 * DHCP Server for Pico W Access Point
 * Based on the Raspberry Pi pico-examples access_point implementation
 * Originally derived from MicroPython's DHCP server
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifndef DHCPSERVER_H
#define DHCPSERVER_H

#include "lwip/ip_addr.h"

// DHCP configuration defaults
#define DHCPS_BASE_IP       100  // First IP to hand out: x.x.x.100
#define DHCPS_MAX_IP        104  // Last IP: x.x.x.104 (5 clients max)
#define DHCPS_LEASE_TIME    (24 * 60 * 60)  // 24 hours in seconds

typedef struct _dhcp_server_t {
    ip_addr_t ip;       // Server's own IP (usually 192.168.4.1)
    ip_addr_t nm;       // Netmask (usually 255.255.255.0)
    struct udp_pcb *udp;
} dhcp_server_t;

/**
 * Initialize the DHCP server
 * @param d     Pointer to dhcp_server_t structure to initialize
 * @param ip    Server IP address (e.g., 192.168.4.1)
 * @param nm    Network mask (e.g., 255.255.255.0)
 */
void dhcp_server_init(dhcp_server_t *d, ip_addr_t *ip, ip_addr_t *nm);

/**
 * Deinitialize the DHCP server
 * @param d     Pointer to initialized dhcp_server_t structure
 */
void dhcp_server_deinit(dhcp_server_t *d);

#endif // DHCPSERVER_H