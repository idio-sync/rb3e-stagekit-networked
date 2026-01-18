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
// TCP Configuration (Web Server)
//--------------------------------------------------------------------
#define LWIP_TCP 1
#define LWIP_HTTPD 1
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
#define LWIP_AUTOIP 0

//--------------------------------------------------------------------
// Checksums (Hardware Accelerated)
//--------------------------------------------------------------------
#define CHECKSUM_GEN_IP 1
#define CHECKSUM_GEN_UDP 1
#define CHECKSUM_GEN_TCP 0
#define CHECKSUM_GEN_ICMP 1
#define CHECKSUM_CHECK_IP 1
#define CHECKSUM_CHECK_UDP 1
#define CHECKSUM_CHECK_TCP 0
#define CHECKSUM_CHECK_ICMP 1

//--------------------------------------------------------------------
// Debugging
//--------------------------------------------------------------------
#define LWIP_STATS 0
#define LWIP_DEBUG 0

#endif /* _LWIPOPTS_H_ */