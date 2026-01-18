/*
 * LittleFS Hardware Abstraction Layer for RP2040/RP2350
 *
 * Implements flash read/write/erase operations for LittleFS
 */

#include "littlefs_hal.h"
#include "pico/stdlib.h"
#include "hardware/flash.h"
#include "hardware/sync.h"
#include <stdio.h>
#include <string.h>

/*
 * Flash size detection - use SDK-provided PICO_FLASH_SIZE_BYTES
 * 
 * The Pico SDK automatically defines PICO_FLASH_SIZE_BYTES based on the
 * board configuration (set via PICO_BOARD in CMake). This is the correct
 * and portable way to detect flash size:
 *   - pico_w:   2MB (0x200000)
 *   - pico2_w:  4MB (0x400000)
 */
#ifndef PICO_FLASH_SIZE_BYTES
    #error "PICO_FLASH_SIZE_BYTES not defined - check your board configuration"
#endif

#define FLASH_TOTAL_SIZE PICO_FLASH_SIZE_BYTES

#if FLASH_TOTAL_SIZE < LFS_FLASH_SIZE
    #error "LittleFS size exceeds total flash size"
#endif

#define FLASH_TARGET_OFFSET (FLASH_TOTAL_SIZE - LFS_FLASH_SIZE)

// Read buffer for flash operations - use smaller cache to save RAM
static uint8_t lfs_read_buffer[256];
static uint8_t lfs_prog_buffer[256];
static uint8_t lfs_lookahead_buffer[16];

// LittleFS instance
static lfs_t lfs;
static int lfs_mounted = 0;

// HAL: Read a block from flash
static int lfs_flash_read(const struct lfs_config *c, lfs_block_t block,
                          lfs_off_t off, void *buffer, lfs_size_t size)
{
    (void)c;
    uint32_t flash_addr = XIP_BASE + FLASH_TARGET_OFFSET + (block * LFS_BLOCK_SIZE) + off;
    memcpy(buffer, (void*)flash_addr, size);
    return LFS_ERR_OK;
}

// HAL: Program (write) a block to flash
static int lfs_flash_prog(const struct lfs_config *c, lfs_block_t block,
                          lfs_off_t off, const void *buffer, lfs_size_t size)
{
    (void)c;
    uint32_t flash_offset = FLASH_TARGET_OFFSET + (block * LFS_BLOCK_SIZE) + off;

    // Disable interrupts during flash write
    uint32_t ints = save_and_disable_interrupts();
    flash_range_program(flash_offset, buffer, size);
    restore_interrupts(ints);

    return LFS_ERR_OK;
}

// HAL: Erase a block
static int lfs_flash_erase(const struct lfs_config *c, lfs_block_t block)
{
    (void)c;
    uint32_t flash_offset = FLASH_TARGET_OFFSET + (block * LFS_BLOCK_SIZE);

    // Disable interrupts during flash erase
    uint32_t ints = save_and_disable_interrupts();
    flash_range_erase(flash_offset, LFS_BLOCK_SIZE);
    restore_interrupts(ints);

    return LFS_ERR_OK;
}

// HAL: Sync (no-op for flash)
static int lfs_flash_sync(const struct lfs_config *c)
{
    (void)c;
    return LFS_ERR_OK;
}

// LittleFS configuration
static const struct lfs_config lfs_cfg = {
    .read = lfs_flash_read,
    .prog = lfs_flash_prog,
    .erase = lfs_flash_erase,
    .sync = lfs_flash_sync,

    .read_size = 1,
    .prog_size = FLASH_PAGE_SIZE,  // 256 bytes
    .block_size = LFS_BLOCK_SIZE,
    .block_count = LFS_BLOCK_COUNT,
    .cache_size = 256,             // Must match buffer sizes
    .lookahead_size = 16,
    .block_cycles = 500,

    .read_buffer = lfs_read_buffer,
    .prog_buffer = lfs_prog_buffer,
    .lookahead_buffer = lfs_lookahead_buffer,
};

lfs_t* littlefs_init(void)
{
    return &lfs;
}

uint32_t littlefs_get_flash_size(void)
{
    return FLASH_TOTAL_SIZE;
}

uint32_t littlefs_get_fs_offset(void)
{
    return FLASH_TARGET_OFFSET;
}

int littlefs_mount(void)
{
    // Try to mount existing filesystem
    int err = lfs_mount(&lfs, &lfs_cfg);

    if (err < 0) {
        printf("LittleFS: Mount failed (error %d)\n", err);
        printf("LittleFS: Flash size = %u bytes (0x%X)\n", FLASH_TOTAL_SIZE, FLASH_TOTAL_SIZE);
        printf("LittleFS: LFS offset = 0x%X\n", FLASH_TARGET_OFFSET);
        printf("LittleFS: This usually means no filesystem exists yet.\n");
        printf("LittleFS: Flash a wifi_config.uf2 file to create the filesystem.\n");
        return err;
    }

    lfs_mounted = 1;
    printf("LittleFS: Mounted successfully\n");
    printf("LittleFS: Flash size = %u bytes, offset = 0x%X\n", 
           FLASH_TOTAL_SIZE, FLASH_TARGET_OFFSET);
    return 0;
}

int littlefs_format_and_mount(void)
{
    // Format filesystem (destroys all data!)
    printf("LittleFS: Formatting filesystem...\n");
    int err = lfs_format(&lfs, &lfs_cfg);
    if (err < 0) {
        printf("LittleFS: Format failed (%d)\n", err);
        return err;
    }

    // Mount the freshly formatted filesystem
    err = lfs_mount(&lfs, &lfs_cfg);
    if (err < 0) {
        printf("LittleFS: Mount after format failed (%d)\n", err);
        return err;
    }

    lfs_mounted = 1;
    printf("LittleFS: Formatted and mounted successfully\n");
    return 0;
}

void littlefs_unmount(void)
{
    if (lfs_mounted) {
        lfs_unmount(&lfs);
        lfs_mounted = 0;
    }
}

lfs_t* littlefs_get(void)
{
    return &lfs;
}

int littlefs_is_mounted(void)
{
    return lfs_mounted;
}
