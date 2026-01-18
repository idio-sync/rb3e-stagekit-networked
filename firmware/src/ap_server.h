#ifndef AP_SERVER_H
#define AP_SERVER_H

// Enters an infinite loop, starts WiFi AP, serves a config page,
// saves settings.toml, and reboots the device.
void run_ap_setup_mode(void);

#endif