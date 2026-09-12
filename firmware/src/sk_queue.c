#include "sk_queue.h"

_Static_assert((SK_QUEUE_CAPACITY & (SK_QUEUE_CAPACITY - 1u)) == 0u,
               "SK_QUEUE_CAPACITY must be a power of two");
_Static_assert(SK_QUEUE_CAPACITY > 0u && SK_QUEUE_CAPACITY < 0x80000000u,
               "SK_QUEUE_CAPACITY must leave room for index wraparound");

#define SK_QUEUE_MASK (SK_QUEUE_CAPACITY - 1u)

/* The producer runs in the network receive callback and the consumer in the
   main loop, so an entry must be fully written before the index publishing it
   moves. Volatile alone orders the index accesses but not the entry stores. */
#if defined(__GNUC__) || defined(__clang__)
#define SK_BARRIER() __atomic_thread_fence(__ATOMIC_SEQ_CST)
#else
#define SK_BARRIER() do { } while (0)
#endif

/* head and tail run free and are masked on use, so the difference between
   them is the occupancy even across a uint32 wrap. */

void sk_queue_init(sk_queue_t *q)
{
    q->head = 0;
    q->tail = 0;
    q->dropped = 0;
    SK_BARRIER();
}

bool sk_queue_push(sk_queue_t *q, uint8_t left, uint8_t right)
{
    uint32_t head = q->head;
    uint32_t tail = q->tail;

    if ((uint32_t)(head - tail) >= SK_QUEUE_CAPACITY) {
        /* Discard the incoming command rather than an accepted one, so the
           ordering guarantee holds for everything already queued. */
        q->dropped = q->dropped + 1u;
        return false;
    }

    q->entries[head & SK_QUEUE_MASK].left = left;
    q->entries[head & SK_QUEUE_MASK].right = right;
    SK_BARRIER();
    q->head = head + 1u;
    return true;
}

bool sk_queue_peek(const sk_queue_t *q, sk_command_t *out)
{
    uint32_t head = q->head;
    uint32_t tail = q->tail;

    if (head == tail) {
        return false;
    }

    SK_BARRIER();
    out->left = q->entries[tail & SK_QUEUE_MASK].left;
    out->right = q->entries[tail & SK_QUEUE_MASK].right;
    return true;
}

void sk_queue_pop(sk_queue_t *q)
{
    uint32_t head = q->head;
    uint32_t tail = q->tail;

    if (head == tail) {
        return;
    }

    SK_BARRIER();
    q->tail = tail + 1u;
}

bool sk_queue_empty(const sk_queue_t *q)
{
    return q->head == q->tail;
}

uint32_t sk_queue_dropped(const sk_queue_t *q)
{
    return q->dropped;
}
