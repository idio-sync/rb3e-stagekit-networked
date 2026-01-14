/*
 * Configuration Parser for settings.toml
 *
 * Parses WiFi credentials from LittleFS settings file
 */

#ifndef _CONFIG_PARSER_H_
#define _CONFIG_PARSER_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Maximum lengths for configuration strings
#define CONFIG_SSID_MAX_LEN     64
#define CONFIG_PASSWORD_MAX_LEN 64
#define CONFIG_FILE_PATH        "/settings.toml"

// WiFi Configuration structure
typedef struct {
    char ssid[CONFIG_SSID_MAX_LEN];
    char password[CONFIG_PASSWORD_MAX_LEN];
    int valid;
} wifi_config_t;

/**
 * Load WiFi configuration from settings.toml
 *
 * Parses the settings.toml file from LittleFS and extracts:
 * - CIRCUITPY_WIFI_SSID
 * - CIRCUITPY_WIFI_PASSWORD
 *
 * @param config Pointer to wifi_config_t structure to fill
 * @return 0 on success, negative error code on failure
 */
int config_load_wifi(wifi_config_t *config);

/**
 * Create a default settings.toml file
 *
 * Creates a template settings.toml with placeholder values
 *
 * @return 0 on success, negative error code on failure
 */
int config_create_default(void);

/**
 * Check if settings file exists
 *
 * @return 1 if file exists, 0 otherwise
 */
int config_file_exists(void);

#ifdef __cplusplus
}
#endif

#endif /* _CONFIG_PARSER_H_ */
