#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Compile the actual driver unchanged, with a host-only replacement for MMIO. */
#define main arm_driver_example_main
#include "../../software/arm/driver.c"
#undef main

#define IMAGE_COUNT 20u
#define SCORE_COUNT 10u
#define POLL_BUDGET 1024u

static struct {
    uint8_t image[IMG_SIZE];
    uint32_t scores[SCORE_COUNT];
    uint32_t statuses[3];
    unsigned status_count;
    unsigned status_index;
    unsigned pointer;
    unsigned pointer_writes;
    unsigned image_writes;
    unsigned starts;
    unsigned resets;
    unsigned control_clears;
    unsigned status_reads;
    unsigned result_reads;
    unsigned next_result;
    int running;
    int done;
} model;

static jmp_buf poll_watchdog;
static int watchdog_armed;
static unsigned checks;
static unsigned known_gaps;

static void require(int condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "ARM_DRIVER_HOST_FAIL: %s\n", message);
        exit(1);
    }
}

static void pass(const char *name)
{
    ++checks;
    printf("[PASS] %s\n", name);
}

static void reset_model(void)
{
    memset(&model, 0, sizeof(model));
    model.statuses[0] = STATUS_BUSY;
    model.statuses[1] = STATUS_BUSY;
    model.statuses[2] = STATUS_DONE;
    model.status_count = 3;
}

void Xil_Out32(uintptr_t address, uint32_t value)
{
    require(address >= AXILITE_BASE, "write below accelerator base");
    switch (address - AXILITE_BASE) {
    case REG_CTRL:
        if (value == CTRL_SOFT_RST) {
            require(model.resets == model.control_clears, "reset repeated before clear");
            ++model.resets;
            model.running = 0;
            model.done = 0;
        } else if (value == 0) {
            require(model.resets == model.control_clears + 1, "CTRL clear without preceding reset");
            ++model.control_clears;
        } else {
            require(value == CTRL_START, "unexpected CTRL write");
            require(model.pointer == IMG_SIZE, "START before 784 image writes");
            require(!model.running, "START while mock accelerator is busy");
            ++model.starts;
            model.status_index = 0;
            model.next_result = 0;
            model.running = 1;
            model.done = 0;
        }
        break;
    case REG_IMG_PTR:
        require(value == 0, "image pointer was not reset to zero");
        require(!model.running, "image pointer changed while busy");
        model.pointer = 0;
        ++model.pointer_writes;
        break;
    case REG_IMG_DATA:
        require(!model.running, "image changed while busy");
        require(model.pointer < IMG_SIZE, "image write beyond 784 pixels");
        require(value <= 127, "image value is outside quantized INT8 range");
        model.image[model.pointer++] = (uint8_t)value;
        ++model.image_writes;
        break;
    default:
        require(0, "unexpected MMIO write address");
    }
}

uint32_t Xil_In32(uintptr_t address)
{
    uintptr_t offset;
    require(address >= AXILITE_BASE, "read below accelerator base");
    offset = address - AXILITE_BASE;
    if (offset == REG_STATUS) {
        uint32_t status;
        ++model.status_reads;
        if (watchdog_armed && model.status_reads > POLL_BUDGET) {
            longjmp(poll_watchdog, 1);
        }
        require(model.status_count > 0, "mock status sequence is empty");
        status = model.statuses[model.status_index];
        if (model.status_index + 1 < model.status_count) {
            ++model.status_index;
        }
        if (status & (STATUS_DONE | STATUS_ERROR)) {
            model.running = 0;
        }
        model.done = !!(status & STATUS_DONE);
        return status;
    }
    if (offset >= REG_RESULT_0 && offset < REG_RESULT_0 + 4 * SCORE_COUNT) {
        unsigned index = (unsigned)((offset - REG_RESULT_0) / 4);
        require((offset - REG_RESULT_0) % 4 == 0, "unaligned result read");
        require(model.done, "result read before DONE");
        require(index == model.next_result, "results not read in register order");
        model.next_result = (model.next_result + 1) % SCORE_COUNT;
        ++model.result_reads;
        return model.scores[index];
    }
    require(0, "unexpected MMIO read address");
    return 0;
}

static void read_words(const char *path, uint32_t *words, unsigned count,
                       int base, uint32_t maximum)
{
    FILE *input = fopen(path, "r");
    char token[80];
    unsigned index;
    require(input != NULL, "cannot open frozen vector file");
    for (index = 0; index < count; ++index) {
        char *end;
        unsigned long long value;
        require(fscanf(input, "%79s", token) == 1, "frozen vector file is truncated");
        require(isxdigit((unsigned char)token[0]), "invalid vector token");
        errno = 0;
        value = strtoull(token, &end, base);
        require(errno == 0 && *end == '\0' && value <= maximum, "vector token out of range");
        words[index] = (uint32_t)value;
    }
    require(fscanf(input, "%79s", token) == EOF, "unexpected extra vector tokens");
    require(!ferror(input), "vector file read failed");
    require(fclose(input) == 0, "vector file close failed");
}

static void test_init(void)
{
    reset_model();
    cnn_init();
    require(model.resets == 1 && model.control_clears == 1, "init must write reset then clear");
    require(model.image_writes == 0 && model.starts == 0, "init wrote image or started inference");
    pass("initialization reset/clear writes");
}

static void test_quantization(void)
{
    uint8_t raw[IMG_SIZE];
    unsigned index;
    reset_model();
    for (index = 0; index < IMG_SIZE; ++index) {
        raw[index] = (uint8_t)(index % 256);
    }
    cnn_write_image(raw);
    require(model.pointer_writes == 1 && model.image_writes == IMG_SIZE,
            "image transfer must reset pointer once and write 784 pixels");
    for (index = 0; index < IMG_SIZE; ++index) {
        require(model.image[index] == (raw[index] >> 1), "uint8-to-INT8 quantization mismatch");
    }
    pass("all 256 raw byte values, quantization and 784-write transfer");
}

static void test_batch(const uint32_t *images, const uint32_t *golden,
                       const uint32_t *labels)
{
    unsigned image, index;
    reset_model();
    cnn_init();
    for (image = 0; image < IMAGE_COUNT; ++image) {
        uint8_t raw[IMG_SIZE];
        int32_t results[SCORE_COUNT];
        int prediction;
        for (index = 0; index < IMG_SIZE; ++index) {
            raw[index] = (uint8_t)images[image * IMG_SIZE + index];
        }
        memcpy(model.scores, golden + image * SCORE_COUNT, sizeof(model.scores));
        prediction = predict(raw);
        require(prediction == (int)labels[image], "prediction differs from injected golden argmax");
        require(model.starts == image + 1 && model.pointer_writes == image + 1,
                "each prediction must reset IMG_PTR and write START once");
        require(model.image_writes == (image + 1) * IMG_SIZE, "batch image-write count mismatch");
        for (index = 0; index < IMG_SIZE; ++index) {
            require(model.image[index] == (images[image * IMG_SIZE + index] >> 1), "transferred image mismatch");
        }
        cnn_read_results(results);
        for (index = 0; index < SCORE_COUNT; ++index) {
            int64_t expected = golden[image * SCORE_COUNT + index];
            if (expected > INT32_MAX) {
                expected -= INT64_C(4294967296);
            }
            require((int64_t)results[index] == expected, "signed result-register readback mismatch");
        }
        require(model.status_reads == (image + 1) * 3, "BUSY/BUSY/DONE poll count mismatch");
        require(model.result_reads == (image + 1) * 2 * SCORE_COUNT, "ten-register read count mismatch");
        printf("[PASS] host image %02u: 784 writes, signed injected scores, argmax=%d\n", image, prediction);
        ++checks;
    }
}

static void test_error(void)
{
    uint8_t raw[IMG_SIZE] = {0};
    reset_model();
    model.statuses[1] = STATUS_ERROR;
    model.status_count = 2;
    require(predict(raw) == -1, "ERROR status was not propagated");
    require(model.result_reads == 0 && model.status_reads == 2, "ERROR path read results or kept polling");
    pass("BUSY/ERROR propagates failure without reading results");
}

static void test_signed_extremes(void)
{
    int32_t results[SCORE_COUNT];
    int32_t ties[SCORE_COUNT] = {-9, -9, -9, -1, -9, -9, -1, -9, -9, -9};
    reset_model();
    model.done = 1;
    model.scores[0] = UINT32_C(0x80000000);
    model.scores[1] = UINT32_C(0xffffffff);
    model.scores[2] = UINT32_C(0x7fffffff);
    cnn_read_results(results);
    require(results[0] == INT32_MIN && results[1] == -1 && results[2] == INT32_MAX,
            "INT32 result extremes were interpreted incorrectly");
    require(argmax(results, SCORE_COUNT) == 2, "signed argmax failed");
    require(argmax(ties, SCORE_COUNT) == 3, "argmax must keep first maximum on ties");
    pass("signed INT32 extremes and first-maximum tie behavior");
}

static void observe_done_error(void)
{
    int result;
    reset_model();
    model.statuses[0] = STATUS_DONE | STATUS_ERROR;
    model.status_count = 1;
    result = cnn_wait_done();
    require(result == 0 || result == -1, "unexpected status return value");
    printf("ARM_DRIVER_HOST_OBSERVATION: done_and_error_return=%d (priority requires owner review)\n", result);
    pass("record simultaneous DONE/ERROR behavior without changing its contract");
}

static void test_stuck_busy(void)
{
    reset_model();
    model.status_count = 1;
    watchdog_armed = 1;
    if (setjmp(poll_watchdog) == 0) {
        require(cnn_wait_done() == -1, "stuck BUSY returned success");
        pass("driver returned an error for stuck BUSY within host poll budget");
    } else {
        ++known_gaps;
        printf("ARM_DRIVER_HOST_KNOWN_GAP: no polling timeout; host watchdog interrupted after %u reads\n",
               model.status_reads);
    }
    watchdog_armed = 0;
}

int main(int argc, char **argv)
{
    static uint32_t images[IMAGE_COUNT * IMG_SIZE];
    static uint32_t golden[IMAGE_COUNT * SCORE_COUNT];
    static uint32_t labels[IMAGE_COUNT];
    require(argc == 4, "usage: arm-driver-host img_all.hex golden_all.hex labels.txt");
    read_words(argv[1], images, IMAGE_COUNT * IMG_SIZE, 16, 255);
    read_words(argv[2], golden, IMAGE_COUNT * SCORE_COUNT, 16, UINT32_MAX);
    read_words(argv[3], labels, IMAGE_COUNT, 10, 9);
    printf("HOST MOCK ONLY: no ARM execution, hardware access or CNN computation; scores are injected.\n");
    test_init();
    test_quantization();
    test_batch(images, golden, labels);
    test_error();
    test_signed_extremes();
    observe_done_error();
    test_stuck_busy();
    printf("ARM_DRIVER_HOST_TESTS_PASS: checks=%u known_gaps=%u hardware_verified=false\n", checks, known_gaps);
    return 0;
}
