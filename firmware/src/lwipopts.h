#ifndef _LWIPOPTS_H_
#define _LWIPOPTS_H_

//--------------------------------------------------------------------
// Platform Configuration
//--------------------------------------------------------------------
#define NO_SYS 1
#define LWIP_SOCKET 0
#define LWIP_NETCONN 0
#define LWIP_NETIF_STATUS_CALLBACK 1
#define LWIP_NETIF_LINK_CALLBACK 1
#define LWIP_NETIF_HOSTNAME 1

#define CYW43_LWIP 1

//--------------------------------------------------------------------
// Memory Configuration (Optimized for Stability)
//--------------------------------------------------------------------
#define MEM_LIBC_MALLOC 0
#define MEM_ALIGNMENT 4

// Increased to 32KB to guarantee space for DHCP + HTTP
#define MEM_SIZE 32000  

// Increased PBUF pool to handle burst traffic during connection
#define PBUF_POOL_SIZE 48
#define PBUF_POOL_BUFSIZE 1600

// We need plenty of PCBs for: DHCP(67), DNS(53), HTTP(80), Telemetry
#define MEMP_NUM_UDP_PCB 12
#define MEMP_NUM_TCP_PCB 12
#define MEMP_NUM_TCP_PCB_LISTEN 6
#define MEMP_NUM_TCP_SEG 64

#define MEMP_NUM_SYS_TIMEOUT 32
#define MEMP_NUM_ARP_QUEUE 10

//--------------------------------------------------------------------
// DHCP & Broadcast (CRITICAL FOR AP MODE)
//--------------------------------------------------------------------
#define LWIP_DHCP 1
#define DHCP_DOES_ARP_CHECK 0

// These two flags are REQUIRED for the DHCP Server to reply to broadcasts
#define IP_SOF_BROADCAST 1
#define IP_SOF_BROADCAST_RECV 1

// Trust the MAC from the DHCP packet to speed up connection
#define ETHARP_TRUST_IP_MAC 1

//--------------------------------------------------------------------
// Link-local fallback (RFC 3927) for networks with no DHCP server,
// such as band3's Steam Deck hotspot. DHCP still goes first; AutoIP
// only starts when DHCP has not answered. Only the station interface
// runs a DHCP client, so setup AP mode never starts AutoIP.
//--------------------------------------------------------------------
#define LWIP_AUTOIP 1
#define LWIP_DHCP_AUTOIP_COOP 1

// Timing, from lwIP 2.2.1 as pinned by pico-sdk (dhcp.c, acd.c,
// prot/acd.h). t=0 is link up, when cyw43 calls dhcp_start() and
// DISCOVER #1 goes out.
//
//   DHCP: retry n waits DHCP_REQUEST_BACKOFF_SEQUENCE = 2^n s (2, 4, 8,
//   16, 32, then 60 s), counted in 500 ms dhcp_fine_tmr ticks, so the
//   first wait is 1.5-2.0 s. Each DISCOVER calls autoip_start() once
//   tries >= LWIP_DHCP_AUTOIP_COOP_TRIES.
//     TRIES 9 (default): DISCOVER #10 at 2+4+8+16+32+60*4 = 302 s
//     TRIES 2:           DISCOVER #3  at 5.5-6.0 s
//     TRIES 1:           DISCOVER #2  at 1.5-2.0 s
//
//   AutoIP: ACD (100 ms acd_tmr) must finish before autoip_bind() puts
//   the address on the netif:
//     PROBE_WAIT          random 0-0.9 s, then probe 1
//     2 x PROBE_MIN..MAX  random 1.0-1.9 s each, probes 2 and 3
//     ANNOUNCE_WAIT       2 s, announce 1
//     ANNOUNCE_INTERVAL   2 s, announce 2, then ACD_IP_OK -> bind
//   = 6.0-8.7 s, plus up to 0.1 s of timer phase.
//
//   TRIES 1: address up 7.5-10.8 s after link up.
//   TRIES 2: 11.5-14.8 s, too close to WIFI_CONNECT_TIMEOUT_MS
//   (network.h) once scan and join are added.
//
// TRIES 1 costs a normal network nothing: lwIP 2.2 also runs ACD on a
// DHCP lease before binding it (LWIP_DHCP_DOES_ACD_CHECK defaults to
// LWIP_DHCP; the DHCP_DOES_ARP_CHECK above is the pre-2.2 name and is
// ignored), so a lease takes 6-9 s after link up anyway. AutoIP only
// starts if DISCOVER #1 got no OFFER, and if a lease does turn up
// later, network.c stops AutoIP and DHCP's address wins.
#define LWIP_DHCP_AUTOIP_COOP_TRIES 1

// AutoIP adds no sys timeout of its own in lwIP 2.2: it runs on
// acd_tmr, which DHCP's ACD check already enables. It allocates one
// struct autoip from the MEM_SIZE heap and sends ARP from pbufs, and
// uses no PCBs, so MEMP_NUM_SYS_TIMEOUT (32 against about 7 used) and
// the pools above still cover it.

//--------------------------------------------------------------------
// TCP Configuration (Web Server)
//--------------------------------------------------------------------
#define LWIP_TCP 1
#define LWIP_HTTPD 0  // Disabled - using custom HTTP implementation
#define TCP_MSS 1460
#define TCP_WND (8 * TCP_MSS)      // Larger window for faster page loads
#define TCP_SND_BUF (8 * TCP_MSS)

// Queue limits to satisfy sanity checks
#define TCP_SND_QUEUELEN 32
#define TCP_SNDQUEUELOWAT 10

//--------------------------------------------------------------------
// Protocols
//--------------------------------------------------------------------
#define LWIP_IPV4 1
#define LWIP_UDP 1
#define LWIP_ICMP 1
#define LWIP_ARP 1
#define LWIP_DNS 1  // Enabled so you can use it in Station Mode later
#define LWIP_IGMP 0
#define LWIP_RAW 0

//--------------------------------------------------------------------
// Checksums (Software - more reliable than hardware offload on CYW43)
//--------------------------------------------------------------------
#define CHECKSUM_GEN_IP 1
#define CHECKSUM_GEN_UDP 1
#define CHECKSUM_GEN_TCP 1
#define CHECKSUM_GEN_ICMP 1
#define CHECKSUM_CHECK_IP 1
#define CHECKSUM_CHECK_UDP 1
#define CHECKSUM_CHECK_TCP 1
#define CHECKSUM_CHECK_ICMP 1

//--------------------------------------------------------------------
// Debugging
//--------------------------------------------------------------------
#define LWIP_STATS 0
#define LWIP_DEBUG 0

#endif /* _LWIPOPTS_H_ */