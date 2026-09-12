/*
 * Host tests for the Stage Kit command queue.
 *
 * Builds with any host C compiler; sk_queue.c must not depend on the Pico SDK.
 */

#include "../src/sk_queue.h"

#include <stdio.h>

static int failures = 0;
static int checks = 0;

#define CHECK(cond, ...)                                             \
    do {                                                             \
        checks++;                                                    \
        if (!(cond)) {                                               \
            failures++;                                              \
            printf("  FAIL %s:%d: ", __func__, __LINE__);            \
            printf(__VA_ARGS__);                                     \
            printf("\n");                                            \
        }                                                            \
    } while (0)

#define RUN(test)                                                    \
    do {                                                             \
        int before = failures;                                       \
        test();                                                      \
        printf("%-52s %s\n", #test, failures == before ? "ok" : "FAILED"); \
    } while (0)


static void a_fresh_queue_is_empty(void)
{
    sk_queue_t q;
    sk_queue_init(&q);

    CHECK(sk_queue_empty(&q), "expected a new queue to be empty");
}

static void a_fresh_queue_has_dropped_nothing(void)
{
    sk_queue_t q;
    sk_queue_init(&q);

    CHECK(sk_queue_dropped(&q) == 0, "expected 0 drops, got %u",
          (unsigned)sk_queue_dropped(&q));
}

static void pushing_a_command_makes_the_queue_non_empty(void)
{
    sk_queue_t q;
    sk_queue_init(&q);

    CHECK(sk_queue_push(&q, 0xFF, 0x80), "expected push to be accepted");
    CHECK(!sk_queue_empty(&q), "expected queue to be non-empty after push");
}

static void peek_returns_the_pushed_command(void)
{
    sk_queue_t q;
    sk_command_t out;
    sk_queue_init(&q);
    sk_queue_push(&q, 0x0F, 0x40);

    CHECK(sk_queue_peek(&q, &out), "expected peek to succeed");
    CHECK(out.left == 0x0F, "expected left 0x0F, got 0x%02X", out.left);
    CHECK(out.right == 0x40, "expected right 0x40, got 0x%02X", out.right);
}

static void peek_on_an_empty_queue_fails(void)
{
    sk_queue_t q;
    sk_command_t out;
    sk_queue_init(&q);

    CHECK(!sk_queue_peek(&q, &out), "expected peek on empty queue to fail");
}

static void peek_does_not_remove_the_command(void)
{
    /* The drain loop peeks, attempts a USB transfer, and only pops on success.
       A busy transfer must leave the command queued for the next iteration. */
    sk_queue_t q;
    sk_command_t first, second;
    sk_queue_init(&q);
    sk_queue_push(&q, 0xAA, 0x20);

    sk_queue_peek(&q, &first);
    CHECK(sk_queue_peek(&q, &second), "expected a second peek to succeed");
    CHECK(second.left == first.left && second.right == first.right,
          "expected repeated peek to return the same command");
    CHECK(!sk_queue_empty(&q), "expected command to still be queued");
}

static void pop_removes_the_peeked_command(void)
{
    sk_queue_t q;
    sk_queue_init(&q);
    sk_queue_push(&q, 0x01, 0x60);

    sk_queue_pop(&q);

    CHECK(sk_queue_empty(&q), "expected queue to be empty after pop");
}

static void commands_are_drained_in_push_order(void)
{
    /* The regression this change exists for: the four per-colour ring writes
       of a dense lighting cue must all survive, in order. */
    const sk_command_t cue[] = {
        {0x0F, 0x80}, {0xF0, 0x40}, {0x33, 0x20}, {0xCC, 0x60},
    };
    sk_queue_t q;
    sk_queue_init(&q);

    for (unsigned i = 0; i < 4; i++) {
        CHECK(sk_queue_push(&q, cue[i].left, cue[i].right),
              "expected push %u to be accepted", i);
    }

    for (unsigned i = 0; i < 4; i++) {
        sk_command_t out;
        CHECK(sk_queue_peek(&q, &out), "expected peek %u to succeed", i);
        CHECK(out.left == cue[i].left && out.right == cue[i].right,
              "at %u expected L=0x%02X R=0x%02X, got L=0x%02X R=0x%02X",
              i, cue[i].left, cue[i].right, out.left, out.right);
        sk_queue_pop(&q);
    }

    CHECK(sk_queue_empty(&q), "expected queue to be empty after draining the cue");
}

static void the_queue_holds_its_full_capacity(void)
{
    sk_queue_t q;
    sk_queue_init(&q);

    for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
        CHECK(sk_queue_push(&q, (uint8_t)i, 0x80),
              "expected push %u of %u to be accepted", i, (unsigned)SK_QUEUE_CAPACITY);
    }
    CHECK(sk_queue_dropped(&q) == 0,
          "expected no drops when filling to capacity, got %u",
          (unsigned)sk_queue_dropped(&q));
}

static void pushing_to_a_full_queue_is_rejected(void)
{
    sk_queue_t q;
    sk_queue_init(&q);
    for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
        sk_queue_push(&q, (uint8_t)i, 0x80);
    }

    CHECK(!sk_queue_push(&q, 0xEE, 0x20),
          "expected push to a full queue to be rejected");
    CHECK(sk_queue_dropped(&q) == 1, "expected 1 drop, got %u",
          (unsigned)sk_queue_dropped(&q));
}

static void overflow_discards_the_newest_and_keeps_queued_order(void)
{
    /* Dropping the incoming command preserves the ordering guarantee for
       everything already accepted. */
    sk_queue_t q;
    sk_queue_init(&q);
    for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
        sk_queue_push(&q, (uint8_t)i, 0x80);
    }
    sk_queue_push(&q, 0xEE, 0x20);

    for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
        sk_command_t out;
        CHECK(sk_queue_peek(&q, &out), "expected peek %u to succeed", i);
        CHECK(out.left == (uint8_t)i, "at %u expected left 0x%02X, got 0x%02X",
              i, (uint8_t)i, out.left);
        sk_queue_pop(&q);
    }
    CHECK(sk_queue_empty(&q), "expected queue to be empty after draining");
}

static void popping_an_empty_queue_is_harmless(void)
{
    sk_queue_t q;
    sk_command_t out;
    sk_queue_init(&q);

    sk_queue_pop(&q);

    CHECK(sk_queue_empty(&q), "expected queue to stay empty");
    CHECK(sk_queue_push(&q, 0x12, 0x40), "expected queue to still accept a push");
    CHECK(sk_queue_peek(&q, &out) && out.left == 0x12,
          "expected the pushed command to be readable");
}

static void indices_wrap_without_losing_order(void)
{
    /* Drive several times capacity through the ring so the internal indices
       wrap past the buffer boundary. */
    sk_queue_t q;
    sk_queue_init(&q);

    for (unsigned round = 0; round < 5; round++) {
        for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
            uint8_t value = (uint8_t)(round * SK_QUEUE_CAPACITY + i);
            CHECK(sk_queue_push(&q, value, 0x80),
                  "round %u: expected push %u to be accepted", round, i);
        }
        for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
            uint8_t expected = (uint8_t)(round * SK_QUEUE_CAPACITY + i);
            sk_command_t out;
            CHECK(sk_queue_peek(&q, &out),
                  "round %u: expected peek %u to succeed", round, i);
            CHECK(out.left == expected, "round %u at %u: expected 0x%02X, got 0x%02X",
                  round, i, expected, out.left);
            sk_queue_pop(&q);
        }
        CHECK(sk_queue_empty(&q), "round %u: expected queue to be empty", round);
    }
    CHECK(sk_queue_dropped(&q) == 0, "expected no drops across wraparound, got %u",
          (unsigned)sk_queue_dropped(&q));
}

static void interleaved_push_and_pop_preserves_order(void)
{
    /* Mirrors real traffic: the callback pushes a burst while the loop drains,
       sustained long enough to wrap the indices many times over. Pushes and
       pops are balanced so the queue never fills - overflow has its own test. */
    sk_queue_t q;
    sk_queue_init(&q);
    unsigned next_push = 0;
    unsigned next_expect = 0;

    for (unsigned step = 0; step < 100; step++) {
        sk_queue_push(&q, (uint8_t)next_push++, 0x80);
        sk_queue_push(&q, (uint8_t)next_push++, 0x40);

        for (unsigned i = 0; i < 2; i++) {
            sk_command_t out;
            CHECK(sk_queue_peek(&q, &out), "step %u: expected peek %u to succeed",
                  step, i);
            CHECK(out.left == (uint8_t)next_expect,
                  "step %u: expected 0x%02X, got 0x%02X",
                  step, (uint8_t)next_expect, out.left);
            next_expect++;
            sk_queue_pop(&q);
        }
    }
    CHECK(sk_queue_empty(&q), "expected a balanced run to leave the queue empty");
    CHECK(sk_queue_dropped(&q) == 0, "expected no drops, got %u",
          (unsigned)sk_queue_dropped(&q));
}

static void a_full_queue_accepts_again_after_a_pop(void)
{
    sk_queue_t q;
    sk_queue_init(&q);
    for (unsigned i = 0; i < SK_QUEUE_CAPACITY; i++) {
        sk_queue_push(&q, (uint8_t)i, 0x80);
    }

    sk_queue_pop(&q);

    CHECK(sk_queue_push(&q, 0x99, 0x20),
          "expected a push to be accepted after a pop");
}

int main(void)
{
    RUN(a_fresh_queue_is_empty);
    RUN(a_fresh_queue_has_dropped_nothing);
    RUN(pushing_a_command_makes_the_queue_non_empty);
    RUN(peek_returns_the_pushed_command);
    RUN(peek_on_an_empty_queue_fails);
    RUN(peek_does_not_remove_the_command);
    RUN(pop_removes_the_peeked_command);
    RUN(commands_are_drained_in_push_order);
    RUN(the_queue_holds_its_full_capacity);
    RUN(pushing_to_a_full_queue_is_rejected);
    RUN(overflow_discards_the_newest_and_keeps_queued_order);
    RUN(popping_an_empty_queue_is_harmless);
    RUN(indices_wrap_without_losing_order);
    RUN(interleaved_push_and_pop_preserves_order);
    RUN(a_full_queue_accepts_again_after_a_pop);

    printf("\n%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
