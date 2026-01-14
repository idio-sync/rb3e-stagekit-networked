/*
 * LittleFS Hardware Abstraction Layer for RP2040
 *
 * Implements flash read/write/erase operations for LittleFS
 */

#include "littlefs_hal.h"
#include "pico/stdlib.h"
#include "hardware/flash.h"
#include "hardware/sync.h"
#include <string.h>

// Flash offset where filesystem starts (end of flash - LFS_FLASH_SIZE)
// Pico W has 2MB flash, so offset is 2MB - 256KB = 0x1C0000
#define FLASH_TARGET_OFFSET (PICO_FLASH_SIZE_BYTES - LFS_FLASH_SIZE)

// Read buffer for flash operations
static uint8_t lfs_read_buffer[LFS_BLOCK_SIZE];
static uint8_t lfs_prog_buffer[LFS_BLOCK_SIZE];
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
    .cache_size = LFS_BLOCK_SIZE,
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

int littlefs_mount(void)
{
    // Try to mount existing filesystem
    int err = lfs_mount(&lfs, &lfs_cfg);

    // If mount fails, format and retry
    if (err < 0) {
        printf("LittleFS: Mount failed (%d), formatting...\n", err);
        err = lfs_format(&lfs, &lfs_cfg);
        if (err < 0) {
            printf("LittleFS: Format failed (%d)\n", err);
            return err;
        }
        err = lfs_mount(&lfs, &lfs_cfg);
        if (err < 0) {
            printf("LittleFS: Mount after format failed (%d)\n", err);
            return err;
        }
    }

    lfs_mounted = 1;
    printf("LittleFS: Mounted successfully\n");
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
