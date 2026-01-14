/*
 * USB Host Handler for Santroller Stage Kit
 *
 * Implements TinyUSB host callbacks and HID control transfers
 */

#include "usb_host.h"
#include "rb3e_protocol.h"
#include "tusb.h"
#include "pico/stdlib.h"
#include <stdio.h>
#include <string.h>

//--------------------------------------------------------------------
// State Variables
//--------------------------------------------------------------------

static usb_state_t usb_state = USB_STATE_DISCONNECTED;
static uint8_t stagekit_dev_addr = 0;
static bool stagekit_is_santroller = false;
static const char *usb_error = NULL;

// Control transfer buffer
static uint8_t ctrl_buffer[8] __attribute__((aligned(4)));

//--------------------------------------------------------------------
// Internal Functions
//--------------------------------------------------------------------

static bool is_santroller_stagekit(uint16_t vid, uint16_t pid, uint16_t bcd_device)
{
    return (vid == SANTROLLER_VID &&
            pid == SANTROLLER_PID &&
            bcd_device == SANTROLLER_STAGEKIT_BCD);
}

//--------------------------------------------------------------------
// TinyUSB Host Callbacks
//--------------------------------------------------------------------

// Called when a device is mounted
void tuh_mount_cb(uint8_t dev_addr)
{
    uint16_t vid, pid;
    tuh_vid_pid_get(dev_addr, &vid, &pid);

    printf("USB: Device mounted - addr=%d VID=0x%04x PID=0x%04x\n",
           dev_addr, vid, pid);

    // Check if this is a Santroller device
    if (vid == SANTROLLER_VID && pid == SANTROLLER_PID) {
        // Get device descriptor to check bcdDevice
        tusb_desc_device_t desc;
        if (tuh_descriptor_get_device_sync(dev_addr, &desc, sizeof(desc)) == XFER_RESULT_SUCCESS) {
            printf("USB: Device bcdDevice=0x%04x\n", desc.bcdDevice);

            if (desc.bcdDevice == SANTROLLER_STAGEKIT_BCD) {
                printf("USB: Santroller Stage Kit detected!\n");
                stagekit_dev_addr = dev_addr;
                stagekit_is_santroller = true;
                usb_state = USB_STATE_CONFIGURED;
                usb_error = NULL;
            } else {
                printf("USB: Santroller device but not Stage Kit (bcd=0x%04x)\n",
                       desc.bcdDevice);
                usb_error = "Device is not a Stage Kit";
            }
        } else {
            printf("USB: Failed to get device descriptor\n");
            usb_error = "Failed to get device descriptor";
        }
    } else {
        printf("USB: Unknown device (VID/PID mismatch)\n");
    }
}

// Called when a device is unmounted
void tuh_umount_cb(uint8_t dev_addr)
{
    printf("USB: Device unmounted - addr=%d\n", dev_addr);

    if (dev_addr == stagekit_dev_addr) {
        stagekit_dev_addr = 0;
        stagekit_is_santroller = false;
        usb_state = USB_STATE_DISCONNECTED;
        printf("USB: Stage Kit disconnected\n");
    }
}

// HID mount callback (required by TinyUSB)
void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const* desc_report, uint16_t desc_len)
{
    (void)desc_report;
    (void)desc_len;
    printf("USB HID: Mounted - addr=%d instance=%d\n", dev_addr, instance);
}

// HID unmount callback (required by TinyUSB)
void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance)
{
    printf("USB HID: Unmounted - addr=%d instance=%d\n", dev_addr, instance);
}

// HID report received callback (required by TinyUSB)
void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const* report, uint16_t len)
{
    (void)dev_addr;
    (void)instance;
    (void)report;
    (void)len;
    // We don't process incoming HID reports
}

//--------------------------------------------------------------------
// Public API Implementation
//--------------------------------------------------------------------

void usb_host_init(void)
{
    printf("USB: Initializing TinyUSB host...\n");
    tusb_init();

    usb_state = USB_STATE_DISCONNECTED;
    stagekit_dev_addr = 0;
    stagekit_is_santroller = false;
    usb_error = NULL;

    printf("USB: Host initialized\n");
}

void usb_host_task(void)
{
    tuh_task();
}

bool usb_send_stagekit_command(uint8_t left_weight, uint8_t right_weight)
{
    if (!stagekit_is_santroller || stagekit_dev_addr == 0) {
        return false;
    }

    // Santroller Stage Kit HID report format:
    // [0] = 0x01 (Report ID)
    // [1] = 0x5A (Command marker)
    // [2] = left_weight (LED pattern)
    // [3] = right_weight (Color/command)
    ctrl_buffer[0] = 0x01;
    ctrl_buffer[1] = 0x5A;
    ctrl_buffer[2] = left_weight;
    ctrl_buffer[3] = right_weight;

    // USB Control Transfer setup:
    // bmRequestType: 0x21 = Host to Device, Class, Interface
    // bRequest: 0x09 = SET_REPORT
    // wValue: (HID_REPORT_TYPE_OUTPUT << 8) | Report ID = 0x0200
    // wIndex: Interface 0
    tusb_control_request_t const request = {
        .bmRequestType_bit = {
            .recipient = TUSB_REQ_RCPT_INTERFACE,
            .type = TUSB_REQ_TYPE_CLASS,
            .direction = TUSB_DIR_OUT
        },
        .bRequest = SK_HID_SET_REPORT,
        .wValue = (SK_HID_REPORT_TYPE_OUTPUT << 8) | 0x00,
        .wIndex = 0,
        .wLength = 4
    };

    // Send synchronous control transfer
    tuh_xfer_t xfer = {
        .daddr = stagekit_dev_addr,
        .ep_addr = 0,
        .setup = &request,
        .buffer = ctrl_buffer,
        .complete_cb = NULL,
        .user_data = 0
    };

    bool result = tuh_control_xfer(&xfer);

    if (!result) {
        printf("USB: Control transfer failed\n");
    }

    return result;
}

bool usb_stagekit_all_off(void)
{
    return usb_send_stagekit_command(0x00, SK_ALL_OFF);
}

bool usb_stagekit_connected(void)
{
    return (usb_state == USB_STATE_CONFIGURED &&
            stagekit_is_santroller &&
            stagekit_dev_addr != 0);
}

usb_state_t usb_get_state(void)
{
    return usb_state;
}

const char* usb_get_error(void)
{
    return usb_error;
}
