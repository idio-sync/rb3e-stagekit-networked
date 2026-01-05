#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

//--------------------------------------------------------------------
// COMMON CONFIGURATION
//--------------------------------------------------------------------

// defined by compiler flags for flexibility
#ifndef CFG_TUSB_MCU
#error CFG_TUSB_MCU must be defined
#endif

#ifndef CFG_TUSB_OS
#define CFG_TUSB_OS           OPT_OS_PICO
#endif

// Enable host mode
#ifndef CFG_TUSB_RHPORT0_MODE
#define CFG_TUSB_RHPORT0_MODE OPT_MODE_HOST
#endif

#define CFG_TUSB_DEBUG        0

// USB DMA on some MCUs can only access a specific SRAM region with restriction on alignment.
#ifndef CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_SECTION
#endif

#ifndef CFG_TUSB_MEM_ALIGN
#define CFG_TUSB_MEM_ALIGN    __attribute__ ((aligned(4)))
#endif

//--------------------------------------------------------------------
// HOST CONFIGURATION
//--------------------------------------------------------------------

// Size of buffer to hold descriptors and other data used for enumeration
#define CFG_TUH_ENUMERATION_BUFSIZE 256

#define CFG_TUH_HUB                 0  // No hub support needed
#define CFG_TUH_CDC                 0  // No CDC support needed
#define CFG_TUH_HID                 1  // HID support for Stage Kit
#define CFG_TUH_MSC                 0  // No MSC support needed
#define CFG_TUH_VENDOR              0  // No vendor specific support needed

// Max device support (excluding hub device)
#define CFG_TUH_DEVICE_MAX          1  // Only need one device (Stage Kit)

// Max Endpoints per device
#define CFG_TUH_ENDPOINT_MAX        8

//--------------------------------------------------------------------
// HID
//--------------------------------------------------------------------

// Maximum number of HID interfaces
#define CFG_TUH_HID     1

// Typical keyboard + mouse as endpoint count
#define CFG_TUH_HID_EPIN_BUFSIZE  64
#define CFG_TUH_HID_EPOUT_BUFSIZE 64

#ifdef __cplusplus
}
#endif

#endif /* _TUSB_CONFIG_H_ */
