/*
 * Configuration Parser for settings.toml
 *
 * Simple string search implementation for TOML parsing
 */

#include "config_parser.h"
#include "littlefs_hal.h"
#include "lfs.h"
#include <stdio.h>
#include <string.h>
#include <ctype.h>

// Maximum file size to read
#define MAX_FILE_SIZE 1024

// Buffer for file contents
static char file_buffer[MAX_FILE_SIZE];

/**
 * Unescape a TOML string (handle \" and \\)
 */
static void unescape_toml_string(char *str) {
    char *src = str;
    char *dst = str;

    while (*src) {
        if (*src == '\\' && (src[1] == '"' || src[1] == '\\')) {
            // Skip the backslash, copy the next character
            src++;
        }
        *dst++ = *src++;
    }
    *dst = '\0';
}

/**
 * Extract a quoted string value from a TOML line
 *
 * Searches for pattern like: KEY = "value" or KEY = 'value'
 * Handles escaped quotes (\" and \\)
 *
 * @param content File content to search
 * @param key Key to find (e.g., "CIRCUITPY_WIFI_SSID")
 * @param value Buffer to store extracted value
 * @param max_len Maximum length of value buffer
 * @return 1 if found, 0 otherwise
 */
static int extract_toml_string(const char *content, const char *key,
                                char *value, size_t max_len)
{
    const char *key_pos = strstr(content, key);
    if (!key_pos) {
        return 0;
    }

    // Find the '=' after the key
    const char *equals = strchr(key_pos, '=');
    if (!equals) {
        return 0;
    }

    // Skip whitespace after '='
    const char *start = equals + 1;
    while (*start && isspace((unsigned char)*start)) {
        start++;
    }

    // Check for quote character
    char quote_char = *start;
    if (quote_char != '"' && quote_char != '\'') {
        return 0;
    }
    start++;  // Skip opening quote

    // Find closing quote (handle escaped quotes)
    const char *end = start;
    while (*end) {
        if (*end == '\\' && end[1]) {
            end += 2;  // Skip escaped character
        } else if (*end == quote_char) {
            break;  // Found unescaped closing quote
        } else {
            end++;
        }
    }

    if (*end != quote_char) {
        return 0;  // No closing quote found
    }

    // Copy value
    size_t len = end - start;
    if (len >= max_len) {
        len = max_len - 1;
    }
    memcpy(value, start, len);
    value[len] = '\0';

    // Unescape the string
    unescape_toml_string(value);

    return 1;
}

int config_load_wifi(wifi_config_t *config)
{
    if (!config) {
        return -1;
    }

    // Initialize config
    memset(config, 0, sizeof(wifi_config_t));

    // Check if filesystem is mounted
    if (!littlefs_is_mounted()) {
        printf("Config: Filesystem not mounted\n");
        return -1;
    }

    lfs_t *lfs = littlefs_get();
    lfs_file_t file;

    // Open settings file
    int err = lfs_file_open(lfs, &file, CONFIG_FILE_PATH, LFS_O_RDONLY);
    if (err < 0) {
        printf("Config: Cannot open %s (%d)\n", CONFIG_FILE_PATH, err);
        return -2;
    }

    // Read file contents
    lfs_ssize_t size = lfs_file_read(lfs, &file, file_buffer, MAX_FILE_SIZE - 1);
    lfs_file_close(lfs, &file);

    if (size < 0) {
        printf("Config: Cannot read file (%d)\n", (int)size);
        return -3;
    }
    file_buffer[size] = '\0';

    printf("Config: Read %d bytes from %s\n", (int)size, CONFIG_FILE_PATH);

    // Parse SSID
    if (!extract_toml_string(file_buffer, "CIRCUITPY_WIFI_SSID",
                              config->ssid, CONFIG_SSID_MAX_LEN)) {
        printf("Config: CIRCUITPY_WIFI_SSID not found\n");
        return -4;
    }

    // Parse Password
    if (!extract_toml_string(file_buffer, "CIRCUITPY_WIFI_PASSWORD",
                              config->password, CONFIG_PASSWORD_MAX_LEN)) {
        printf("Config: CIRCUITPY_WIFI_PASSWORD not found\n");
        return -5;
    }

    // Validate
    if (strlen(config->ssid) == 0) {
        printf("Config: SSID is empty\n");
        return -6;
    }

    config->valid = 1;
    printf("Config: Loaded WiFi config for SSID: '%s' (len=%d)\n",
           config->ssid, (int)strlen(config->ssid));
    printf("Config: Password length: %d chars\n", (int)strlen(config->password));

    return 0;
}

int config_create_default(void)
{
    if (!littlefs_is_mounted()) {
        printf("Config: Filesystem not mounted\n");
        return -1;
    }

    lfs_t *lfs = littlefs_get();
    lfs_file_t file;

    // Create default settings file
    int err = lfs_file_open(lfs, &file, CONFIG_FILE_PATH,
                            LFS_O_WRONLY | LFS_O_CREAT | LFS_O_TRUNC);
    if (err < 0) {
        printf("Config: Cannot create %s (%d)\n", CONFIG_FILE_PATH, err);
        return -2;
    }

    // Default content
    const char *default_content =
        "# RB3E StageKit Bridge Configuration\n"
        "# Edit these values with your WiFi credentials\n"
        "\n"
        "CIRCUITPY_WIFI_SSID = \"placeholder_wifi\"\n"
        "CIRCUITPY_WIFI_PASSWORD = \"placeholder_pass\"\n";

    lfs_file_write(lfs, &file, default_content, strlen(default_content));
    lfs_file_close(lfs, &file);

    printf("Config: Created default %s\n", CONFIG_FILE_PATH);
    return 0;
}

int config_file_exists(void)
{
    if (!littlefs_is_mounted()) {
        return 0;
    }

    lfs_t *lfs = littlefs_get();
    struct lfs_info info;

    int err = lfs_stat(lfs, CONFIG_FILE_PATH, &info);
    return (err >= 0 && info.type == LFS_TYPE_REG);
}
