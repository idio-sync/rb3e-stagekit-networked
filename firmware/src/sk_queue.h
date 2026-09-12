/*
 * Stage Kit Command Queue
 *
 * A single-producer/single-consumer FIFO holding Stage Kit commands between
 * the network receive callback and the main loop.
 *
 * Stage Kit commands are NOT redundant: the device keeps separate state per
 * colour ring, so a dense lighting cue is several distinct writes. Coalescing
 * them loses state, and reordering them corrupts it - a strict FIFO is the
 * only safe structure here.
 *
 * The consumer peeks, attempts the USB transfer, and pops only once the
 * transfer has been accepted, so a busy control endpoint delays a command
 * instead of discarding it.
 *
 * Deliberately free of Pico SDK dependencies so it can be tested on a host.
 */

#ifndef _SK_QUEUE_H_
#define _SK_QUEUE_H_

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Must be a power of two. Sixteen entries is ~1.6ms of burst at the main
   loop's 100us active cadence, against a densest-case cue of four packets. */
#define SK_QUEUE_CAPACITY 16u

typedef struct {
    uint8_t left;   /* LED pattern byte (which of LEDs 1-8 are lit) */
    uint8_t right;  /* Command byte (colour ring, strobe, or fog) */
} sk_command_t;

typedef struct {
    sk_command_t      entries[SK_QUEUE_CAPACITY];
    volatile uint32_t head;     /* written by the producer only */
    volatile uint32_t tail;     /* written by the consumer only */
    volatile uint32_t dropped;  /* written by the producer only */
} sk_queue_t;

/* Reset to empty. Call before the producer can run. */
void sk_queue_init(sk_queue_t *q);

/* Producer: append a command. Returns false and counts a drop if full. */
bool sk_queue_push(sk_queue_t *q, uint8_t left, uint8_t right);

/* Consumer: copy the oldest command to *out without removing it.
   Returns false if the queue is empty. */
bool sk_queue_peek(const sk_queue_t *q, sk_command_t *out);

/* Consumer: discard the oldest command. Harmless when empty. */
void sk_queue_pop(sk_queue_t *q);

/* Consumer: whether a peek would fail. */
bool sk_queue_empty(const sk_queue_t *q);

/* Commands rejected because the queue was full, since init. */
uint32_t sk_queue_dropped(const sk_queue_t *q);

#ifdef __cplusplus
}
#endif

#endif /* _SK_QUEUE_H_ */
