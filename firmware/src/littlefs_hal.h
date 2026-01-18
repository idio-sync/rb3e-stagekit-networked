/*
 * LittleFS Hardware Abstraction Layer for RP2040/RP2350
 *
 * Provides flash read/write/erase operations for LittleFS
 */

#ifndef _LITTLEFS_HAL_H_
#define _LITTLEFS_HAL_H_

#include "lfs.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// LittleFS configuration
// Reserve last 256KB of flash for filesystem
#define LFS_FLASH_SIZE      (256 * 1024)        // 256KB filesystem
#define LFS_BLOCK_SIZE      4096                // Flash sector size
#define LFS_BLOCK_COUNT     (LFS_FLASH_SIZE / LFS_BLOCK_SIZE)

/**
 * Initialize LittleFS with flash HAL
 *
 * @return Pointer to LittleFS configuration, or NULL on error
 */
lfs_t* littlefs_init(void);

/**
 * Mount LittleFS filesystem
 *
 * Attempts to mount an existing filesystem. Does NOT auto-format.
 * If mount fails, flash a wifi_config.uf2 to create the filesystem.
 *
 * @return 0 on success, negative error code on failure
 */
int littlefs_mount(void);

/**
 * Format and mount LittleFS filesystem
 *
 * WARNING: This erases all data! Only use for initial setup.
 *
 * @return 0 on success, negative error code on failure
 */
int littlefs_format_and_mount(void);

/**
 * Unmount LittleFS filesystem
 */
void littlefs_unmount(void);

/**
 * Get LittleFS instance
 *
 * @return Pointer to LittleFS instance
 */
lfs_t* littlefs_get(void);

/**
 * Check if filesystem is mounted
 *
 * @return 1 if mounted, 0 otherwise
 */
int littlefs_is_mounted(void);

/**
 * Get the detected flash size
 *
 * @return Flash size in bytes (2MB for Pico W, 4MB for Pico 2 W)
 */
uint32_t littlefs_get_flash_size(void);

/**
 * Get the filesystem offset from flash base
 *
 * @return Offset in bytes where LittleFS partition starts
 */
uint32_t littlefs_get_fs_offset(void);

#ifdef __cplusplus
}
#endif

#endif /* _LITTLEFS_HAL_H_ */
