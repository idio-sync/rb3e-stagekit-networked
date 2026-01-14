/*
 * TinyUSB Configuration for RB3E StageKit Bridge
 *
 * Configures TinyUSB for USB Host mode with HID support
 */

#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

//--------------------------------------------------------------------
// COMMON CONFIGURATION
//--------------------------------------------------------------------

// Defined by board.h
#ifndef CFG_TUSB_MCU
#define CFG_TUSB_MCU OPT_MCU_RP2040
#endif

#define CFG_TUSB_OS OPT_OS_PICO

// Enable debug (0=off, 1=error, 2=warn, 3=info)
#ifndef CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG 0
#endif

// Memory section for placing driver data
#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))

//--------------------------------------------------------------------
// HOST CONFIGURATION
//--------------------------------------------------------------------

// Enable USB Host mode
#define CFG_TUH_ENABLED 1

// Use native USB hardware (not PIO-USB)
#define CFG_TUH_RPI_PIO_USB 0

// Maximum number of devices (including hub devices)
#define CFG_TUH_DEVICE_MAX 1

// Hub support - disabled to save memory
#define CFG_TUH_HUB 0

// Endpoint buffer size
#define CFG_TUH_ENDPOINT0_SIZE 64

//--------------------------------------------------------------------
// HOST HID CONFIGURATION
//--------------------------------------------------------------------

// Enable HID host class
#define CFG_TUH_HID 1

// Maximum HID interfaces per device
#define CFG_TUH_HID_EPIN_BUFSIZE 64
#define CFG_TUH_HID_EPOUT_BUFSIZE 64

//--------------------------------------------------------------------
// DEVICE CONFIGURATION (Disabled - Host mode only)
//--------------------------------------------------------------------

#define CFG_TUD_ENABLED 0

#ifdef __cplusplus
}
#endif

#endif /* _TUSB_CONFIG_H_ */
