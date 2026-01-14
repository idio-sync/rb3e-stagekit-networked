/*
 * LwIP Options Configuration for RB3E StageKit Bridge
 *
 * Self-contained configuration optimized for low-latency UDP communication
 * on Pico W with CYW43 WiFi chip.
 */

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

// Allow CYW43 driver to configure options
#define CYW43_LWIP 1

//--------------------------------------------------------------------
// Memory Configuration
//--------------------------------------------------------------------

#define MEM_LIBC_MALLOC 0
#define MEM_ALIGNMENT 4
#define MEM_SIZE 4000

#define MEMP_NUM_PBUF 16
#define MEMP_NUM_UDP_PCB 4
#define MEMP_NUM_TCP_PCB 0
#define MEMP_NUM_TCP_PCB_LISTEN 0
#define MEMP_NUM_TCP_SEG 0
#define MEMP_NUM_REASSDATA 0
#define MEMP_NUM_ARP_QUEUE 10
#define MEMP_NUM_NETBUF 0
#define MEMP_NUM_NETCONN 0
#define MEMP_NUM_SYS_TIMEOUT 12

#define PBUF_POOL_SIZE 16
#define PBUF_POOL_BUFSIZE 1600

//--------------------------------------------------------------------
// ARP Configuration
//--------------------------------------------------------------------

#define LWIP_ARP 1
#define ARP_TABLE_SIZE 10
#define ARP_QUEUEING 1
#define ETHARP_TRUST_IP_MAC 0

//--------------------------------------------------------------------
// IP Configuration
//--------------------------------------------------------------------

#define LWIP_IPV4 1
#define IP_FORWARD 0
#define IP_OPTIONS_ALLOWED 1
#define IP_REASSEMBLY 0
#define IP_FRAG 0
#define IP_REASS_MAX_PBUFS 0
#define IP_DEFAULT_TTL 64

//--------------------------------------------------------------------
// ICMP Configuration (Ping)
//--------------------------------------------------------------------

#define LWIP_ICMP 1
#define ICMP_TTL 64

//--------------------------------------------------------------------
// RAW Socket Configuration
//--------------------------------------------------------------------

#define LWIP_RAW 0

//--------------------------------------------------------------------
// DHCP Configuration
//--------------------------------------------------------------------

#define LWIP_DHCP 1
#define DHCP_DOES_ARP_CHECK 0

//--------------------------------------------------------------------
// AUTOIP Configuration
//--------------------------------------------------------------------

#define LWIP_AUTOIP 0

//--------------------------------------------------------------------
// UDP Configuration
//--------------------------------------------------------------------

#define LWIP_UDP 1
#define UDP_TTL 64
#define LWIP_UDPLITE 0

//--------------------------------------------------------------------
// TCP Configuration (Disabled)
//--------------------------------------------------------------------

#define LWIP_TCP 0
#define TCP_TTL 64
#define TCP_QUEUE_OOSEQ 0
#define TCP_MSS 1460
#define TCP_SND_BUF 2920
#define TCP_SND_QUEUELEN 4
#define TCP_WND 2920
#define TCP_MAXRTX 0
#define TCP_SYNMAXRTX 0

//--------------------------------------------------------------------
// Network Interface
//--------------------------------------------------------------------

#define LWIP_SINGLE_NETIF 1
#define LWIP_NETIF_TX_SINGLE_PBUF 1

//--------------------------------------------------------------------
// IGMP Configuration
//--------------------------------------------------------------------

#define LWIP_IGMP 0

//--------------------------------------------------------------------
// DNS Configuration
//--------------------------------------------------------------------

#define LWIP_DNS 0

//--------------------------------------------------------------------
// Statistics (Disabled for performance)
//--------------------------------------------------------------------

#define LWIP_STATS 0
#define LWIP_STATS_DISPLAY 0

//--------------------------------------------------------------------
// Checksum Configuration
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
// Debug Configuration (Disabled)
//--------------------------------------------------------------------

#define LWIP_DEBUG 0
#define LWIP_DBG_MIN_LEVEL LWIP_DBG_LEVEL_ALL
#define LWIP_DBG_TYPES_ON 0

#define ETHARP_DEBUG LWIP_DBG_OFF
#define NETIF_DEBUG LWIP_DBG_OFF
#define PBUF_DEBUG LWIP_DBG_OFF
#define API_LIB_DEBUG LWIP_DBG_OFF
#define API_MSG_DEBUG LWIP_DBG_OFF
#define SOCKETS_DEBUG LWIP_DBG_OFF
#define ICMP_DEBUG LWIP_DBG_OFF
#define IGMP_DEBUG LWIP_DBG_OFF
#define INET_DEBUG LWIP_DBG_OFF
#define IP_DEBUG LWIP_DBG_OFF
#define IP_REASS_DEBUG LWIP_DBG_OFF
#define RAW_DEBUG LWIP_DBG_OFF
#define MEM_DEBUG LWIP_DBG_OFF
#define MEMP_DEBUG LWIP_DBG_OFF
#define SYS_DEBUG LWIP_DBG_OFF
#define TIMERS_DEBUG LWIP_DBG_OFF
#define TCP_DEBUG LWIP_DBG_OFF
#define TCP_INPUT_DEBUG LWIP_DBG_OFF
#define TCP_FR_DEBUG LWIP_DBG_OFF
#define TCP_RTO_DEBUG LWIP_DBG_OFF
#define TCP_CWND_DEBUG LWIP_DBG_OFF
#define TCP_WND_DEBUG LWIP_DBG_OFF
#define TCP_OUTPUT_DEBUG LWIP_DBG_OFF
#define TCP_RST_DEBUG LWIP_DBG_OFF
#define TCP_QLEN_DEBUG LWIP_DBG_OFF
#define UDP_DEBUG LWIP_DBG_OFF
#define TCPIP_DEBUG LWIP_DBG_OFF
#define DHCP_DEBUG LWIP_DBG_OFF
#define AUTOIP_DEBUG LWIP_DBG_OFF
#define DNS_DEBUG LWIP_DBG_OFF

#endif /* _LWIPOPTS_H_ */
