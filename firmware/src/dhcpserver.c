/*
 * DHCP Server for Pico W Access Point
 * Based on the Raspberry Pi pico-examples access_point implementation
 * Originally derived from MicroPython's DHCP server
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#include <string.h>
#include <stdio.h>

#include "dhcpserver.h"
#include "lwip/udp.h"

// DHCP message types
#define DHCP_DISCOVER   1
#define DHCP_OFFER      2
#define DHCP_REQUEST    3
#define DHCP_DECLINE    4
#define DHCP_ACK        5
#define DHCP_NAK        6
#define DHCP_RELEASE    7
#define DHCP_INFORM     8

// DHCP option codes
#define DHCP_OPT_SUBNET_MASK    1
#define DHCP_OPT_ROUTER         3
#define DHCP_OPT_DNS            6
#define DHCP_OPT_HOST_NAME      12
#define DHCP_OPT_REQUESTED_IP   50
#define DHCP_OPT_LEASE_TIME     51
#define DHCP_OPT_MSG_TYPE       53
#define DHCP_OPT_SERVER_ID      54
#define DHCP_OPT_PARAM_REQUEST  55
#define DHCP_OPT_MAX_MSG_SIZE   57
#define DHCP_OPT_VENDOR_ID      60
#define DHCP_OPT_CLIENT_ID      61
#define DHCP_OPT_END            255

// DHCP ports
#define PORT_DHCP_SERVER    67
#define PORT_DHCP_CLIENT    68

// DHCP magic cookie
#define DHCP_MAGIC          0x63825363

// DHCP client lease entry
typedef struct {
    uint8_t mac[6];
    uint32_t expiry;
} dhcp_lease_t;

// Lease table - one entry per possible IP address
static dhcp_lease_t leases[DHCPS_MAX_IP - DHCPS_BASE_IP + 1];

// DHCP message structure
typedef struct {
    uint8_t op;         // 1 = request, 2 = reply
    uint8_t htype;      // Hardware type (1 = Ethernet)
    uint8_t hlen;       // Hardware address length (6 for MAC)
    uint8_t hops;
    uint32_t xid;       // Transaction ID
    uint16_t secs;
    uint16_t flags;
    uint8_t ciaddr[4];  // Client IP
    uint8_t yiaddr[4];  // Your (client) IP
    uint8_t siaddr[4];  // Server IP
    uint8_t giaddr[4];  // Gateway IP
    uint8_t chaddr[16]; // Client hardware address
    uint8_t sname[64];  // Server name
    uint8_t file[128];  // Boot filename
    uint32_t magic;     // Magic cookie
    uint8_t options[312]; // Options
} __attribute__((packed)) dhcp_msg_t;

// Find an available IP for a client MAC, or return existing lease
static int dhcp_find_ip(const uint8_t *mac) {
    int empty = -1;
    
    // First, look for existing lease with this MAC
    for (int i = 0; i < (DHCPS_MAX_IP - DHCPS_BASE_IP + 1); i++) {
        if (memcmp(leases[i].mac, mac, 6) == 0) {
            return i;  // Found existing lease
        }
        if (empty < 0 && leases[i].expiry == 0) {
            empty = i;  // Remember first empty slot
        }
    }
    
    // No existing lease, return empty slot
    return empty;
}

// Build DHCP response options
static uint8_t *dhcp_add_option(uint8_t *opt, uint8_t code, uint8_t len, const void *data) {
    *opt++ = code;
    *opt++ = len;
    memcpy(opt, data, len);
    return opt + len;
}

static void dhcp_recv_cb(void *arg, struct udp_pcb *upcb, struct pbuf *p,
                         const ip_addr_t *src_addr, u16_t src_port) {
    dhcp_server_t *d = (dhcp_server_t *)arg;
    dhcp_msg_t *msg = (dhcp_msg_t *)p->payload;
    
    // Validate packet
    if (p->len < sizeof(dhcp_msg_t) - sizeof(msg->options)) {
        goto done;
    }
    if (msg->op != 1 || msg->htype != 1 || msg->hlen != 6) {
        goto done;
    }
    if (msg->magic != PP_HTONL(DHCP_MAGIC)) {
        goto done;
    }
    
    // Parse options to find message type
    uint8_t msg_type = 0;
    uint8_t *opt = msg->options;
    uint8_t *opt_end = (uint8_t *)p->payload + p->len;
    uint32_t requested_ip = 0;
    
    while (opt < opt_end && *opt != DHCP_OPT_END) {
        if (*opt == 0) {  // Padding
            opt++;
            continue;
        }
        uint8_t opt_code = *opt++;
        uint8_t opt_len = *opt++;
        
        if (opt + opt_len > opt_end) {
            break;
        }
        
        switch (opt_code) {
            case DHCP_OPT_MSG_TYPE:
                if (opt_len >= 1) msg_type = opt[0];
                break;
            case DHCP_OPT_REQUESTED_IP:
                if (opt_len >= 4) memcpy(&requested_ip, opt, 4);
                break;
        }
        opt += opt_len;
    }
    
    if (msg_type != DHCP_DISCOVER && msg_type != DHCP_REQUEST) {
        goto done;  // We only handle DISCOVER and REQUEST
    }
    
    // Find/allocate IP for this client
    int lease_idx = dhcp_find_ip(msg->chaddr);
    if (lease_idx < 0) {
        printf("DHCP: No available IP addresses\n");
        goto done;
    }
    
    // Calculate client IP address
    uint32_t client_ip;
    uint8_t *server_ip = (uint8_t *)&d->ip.addr;
    uint8_t ip_bytes[4] = {server_ip[0], server_ip[1], server_ip[2], 
                           (uint8_t)(DHCPS_BASE_IP + lease_idx)};
    memcpy(&client_ip, ip_bytes, 4);
    
    // Build response
    dhcp_msg_t reply;
    memset(&reply, 0, sizeof(reply));
    reply.op = 2;  // Reply
    reply.htype = 1;
    reply.hlen = 6;
    reply.xid = msg->xid;
    memcpy(reply.yiaddr, &client_ip, 4);
    memcpy(reply.siaddr, &d->ip.addr, 4);
    memcpy(reply.chaddr, msg->chaddr, 16);
    reply.magic = PP_HTONL(DHCP_MAGIC);
    
    // Build options
    uint8_t *opt_ptr = reply.options;
    
    // Message type (OFFER or ACK)
    uint8_t reply_type = (msg_type == DHCP_DISCOVER) ? DHCP_OFFER : DHCP_ACK;
    opt_ptr = dhcp_add_option(opt_ptr, DHCP_OPT_MSG_TYPE, 1, &reply_type);
    
    // Server identifier
    opt_ptr = dhcp_add_option(opt_ptr, DHCP_OPT_SERVER_ID, 4, &d->ip.addr);
    
    // Lease time
    uint32_t lease_time = PP_HTONL(DHCPS_LEASE_TIME);
    opt_ptr = dhcp_add_option(opt_ptr, DHCP_OPT_LEASE_TIME, 4, &lease_time);
    
    // Subnet mask
    opt_ptr = dhcp_add_option(opt_ptr, DHCP_OPT_SUBNET_MASK, 4, &d->nm.addr);
    
    // Router (gateway) - use our IP
    opt_ptr = dhcp_add_option(opt_ptr, DHCP_OPT_ROUTER, 4, &d->ip.addr);
    
    // DNS server - use our IP (or could use 8.8.8.8)
    opt_ptr = dhcp_add_option(opt_ptr, DHCP_OPT_DNS, 4, &d->ip.addr);
    
    // End option
    *opt_ptr++ = DHCP_OPT_END;
    
    // Update lease table on ACK
    if (reply_type == DHCP_ACK) {
        memcpy(leases[lease_idx].mac, msg->chaddr, 6);
        leases[lease_idx].expiry = 1;  // Mark as used (simplified)
        // Printf removed to prevent network stack hangs
    }
    
    // Send response
    size_t reply_len = opt_ptr - (uint8_t *)&reply;
    struct pbuf *reply_pbuf = pbuf_alloc(PBUF_TRANSPORT, reply_len, PBUF_RAM);
    if (reply_pbuf) {
        memcpy(reply_pbuf->payload, &reply, reply_len);
        
        ip_addr_t dest;
        IP4_ADDR(&dest, 255, 255, 255, 255);  // Broadcast
        
        udp_sendto(upcb, reply_pbuf, &dest, PORT_DHCP_CLIENT);
        pbuf_free(reply_pbuf);
    }
    
done:
    pbuf_free(p);
}

void dhcp_server_init(dhcp_server_t *d, ip_addr_t *ip, ip_addr_t *nm) {
    printf("DHCP: Starting server on %s\n", ip4addr_ntoa(ip));
    
    // Clear lease table
    memset(leases, 0, sizeof(leases));
    
    // Store configuration
    ip_addr_copy(d->ip, *ip);
    ip_addr_copy(d->nm, *nm);
    
    // Create UDP PCB
    d->udp = udp_new();
    if (!d->udp) {
        printf("DHCP: Failed to create UDP PCB\n");
        return;
    }

    ip_set_option(d->udp, SOF_BROADCAST);
    
    // Bind to DHCP server port
    err_t err = udp_bind(d->udp, IP_ADDR_ANY, PORT_DHCP_SERVER);
    if (err != ERR_OK) {
        printf("DHCP: Failed to bind to port %d (err=%d)\n", PORT_DHCP_SERVER, err);
        udp_remove(d->udp);
        d->udp = NULL;
        return;
    }
    
    // Set receive callback
    udp_recv(d->udp, dhcp_recv_cb, d);
    
    printf("DHCP: Server ready, handing out %d.%d.%d.%d - %d.%d.%d.%d\n",
           ip4_addr1(ip), ip4_addr2(ip), ip4_addr3(ip), DHCPS_BASE_IP,
           ip4_addr1(ip), ip4_addr2(ip), ip4_addr3(ip), DHCPS_MAX_IP);
}

void dhcp_server_deinit(dhcp_server_t *d) {
    if (d->udp) {
        udp_remove(d->udp);
        d->udp = NULL;
    }

}


