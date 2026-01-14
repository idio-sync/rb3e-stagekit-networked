/*
 * USB Host Handler for Santroller Stage Kit
 *
 * Handles USB HID communication with the Santroller Stage Kit device
 */

#ifndef _USB_HOST_H_
#define _USB_HOST_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

//--------------------------------------------------------------------
// Santroller Device Constants
//--------------------------------------------------------------------

#define SANTROLLER_VID          0x1209
#define SANTROLLER_PID          0x2882
#define SANTROLLER_STAGEKIT_BCD 0x0900

//--------------------------------------------------------------------
// HID Constants (USB HID Specification)
// Note: Use SK_ prefix to avoid conflicts with TinyUSB's hid.h
//--------------------------------------------------------------------

#define SK_HID_SET_REPORT          0x09
#define SK_HID_REPORT_TYPE_OUTPUT  0x02

//--------------------------------------------------------------------
// USB Host State
//--------------------------------------------------------------------

typedef enum {
    USB_STATE_DISCONNECTED = 0,
    USB_STATE_MOUNTED,
    USB_STATE_CONFIGURED,
    USB_STATE_ERROR
} usb_state_t;

//--------------------------------------------------------------------
// Public API
//--------------------------------------------------------------------

/**
 * Initialize USB Host
 *
 * Must be called once at startup
 */
void usb_host_init(void);

/**
 * Process USB Host tasks
 *
 * Must be called regularly from main loop
 */
void usb_host_task(void);

/**
 * Send lighting command to Stage Kit
 *
 * @param left_weight LED pattern byte (which LEDs 1-8 are on)
 * @param right_weight Command byte (color/strobe/fog)
 * @return true if command sent successfully
 */
bool usb_send_stagekit_command(uint8_t left_weight, uint8_t right_weight);

/**
 * Turn off all Stage Kit lights
 *
 * Sends SK_ALL_OFF command
 *
 * @return true if command sent successfully
 */
bool usb_stagekit_all_off(void);

/**
 * Check if Stage Kit is connected
 *
 * @return true if device is connected and ready
 */
bool usb_stagekit_connected(void);

/**
 * Get current USB state
 *
 * @return Current USB state enum
 */
usb_state_t usb_get_state(void);

/**
 * Get USB connection error string (if any)
 *
 * @return Error string or NULL
 */
const char* usb_get_error(void);

#ifdef __cplusplus
}
#endif

#endif /* _USB_HOST_H_ */
