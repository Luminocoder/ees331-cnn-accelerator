/* Native MMIO/UART harness: compiles the actual ARM C implementation unchanged. */
#define EES331_HOST_TEST 1
#define main firmware_main
#include "../hardware/arm/fw_seg.c"
#include "../hardware/arm/cpu_infer.h"
#undef main

static uint8_t rx[1200], tx[1200];
static uint32_t rx_n, rx_i, tx_n, seg_value, seg_ctrl, page_id = SEG_PAGE_V1;
static uint32_t image_count, first_pixel, last_pixel;
static int corrupt_readback;
static int bench_active, bench_fault;
static uint64_t fake_ticks;
static uint8_t captured[784];
static int32_t fake_scores[10];

int bench_platform_begin(void) { if (bench_active) return -1; bench_active=1; return 0; }
void bench_platform_end(void) { bench_active=0; }
int bench_platform_active(void) { return bench_active; }
uint32_t bench_sctlr(void) { return bench_active ? 0x00801005u : 0u; }
uint32_t bench_actlr(void) { return 0; }
uint32_t bench_mpidr(void) { return 0; }
uint32_t bench_midr(void) { return 0x413fc090u; }
void bench_barrier(void) { }
uint32_t bench_irq_lock(void) { return 0xc0u; }
void bench_irq_restore(uint32_t v) { (void)v; }
uint64_t bench_ticks(void) { if (bench_fault==2) return 0; fake_ticks+=100; return fake_ticks; }
void bench_test_elapsed(unsigned lane) { fake_ticks += lane ? 20000 : 100000; }
uint32_t bench_read32(uint32_t address) {
    switch (address) {
        case 0xF8F00208u: return bench_active ? 1u : 0u;
        case 0xF8000100u: return 40u<<12;
        case 0xF8000104u: return 32u<<12;
        case 0xF8000108u: return 48u<<12;
        case 0xF8000120u: return 0x1f000200u;
        case 0xF8000170u: return 0x00400800u;
        case 0xF80001C4u: return 1u;
        default: return 0;
    }
}

uint32_t firmware_read32(uint32_t address) {
    if (address == U_SR) return 0;
    if (address == U_FIFO) return rx_i < rx_n ? rx[rx_i++] : 0;
    if (address == SEG_MAGIC_R) return SEG_MAGIC;
    if (address == SEG_PAGE_ID) return page_id;
    if (address == SEG_VALUE) return seg_value ^ (corrupt_readback ? 1u : 0u);
    if (address == SEG_CTRL) return seg_ctrl;
    if (address == A_STATUS) return bench_active && bench_fault==3 ? ST_ERROR : ST_DONE;
    if (address >= A_RESULT(0) && address <= A_RESULT(9))
        return bench_active ? (uint32_t)fake_scores[(address-A_RESULT(0))/4] ^ (bench_fault==1 ? 1u:0u) : (address - A_RESULT(0)) * 7u;
    return 0;
}

void firmware_write32(uint32_t address, uint32_t value) {
    if (address == U_FIFO && tx_n < sizeof(tx)) tx[tx_n++] = (uint8_t)value;
    else if (address == SEG_VALUE) seg_value = value;
    else if (address == SEG_CTRL) seg_ctrl = value;
    else if (address == A_IMG_PTR) image_count = 0;
    else if (address == A_IMG_DATA) {
        if (image_count<784) captured[image_count]=(uint8_t)(value*2u);
        if (image_count == 0) first_pixel = value;
        last_pixel = value;
        image_count++;
    }
    else if (address == A_CTRL && value==CT_START && bench_active) cpu_infer(captured,fake_scores);
}

__declspec(dllexport) void reset_emulator(uint32_t identity, int corrupt) {
    page_id = identity;
    corrupt_readback = corrupt;
    seg_value = 0;
    seg_ctrl = 1;
    g_auto_display = 1;
    bench_active=0; bench_fault=0; fake_ticks=0;
    seg_init();
}

__declspec(dllexport) void set_bench_fault(int fault) { bench_fault=fault; }
__declspec(dllexport) void test_cpu(const uint8_t *pixels,int32_t *scores) { cpu_infer(pixels,scores); }

__declspec(dllexport) uint32_t exchange_packet(const uint8_t *request, uint32_t n, uint8_t *response) {
    uint32_t i, sequence = 0;
    uint8_t kind = 0;
    uint16_t size = 0;
    int rc;
    if (n > sizeof(rx)) return 0;
    rx_n = n; rx_i = 0; tx_n = 0;
    for (i = 0; i < n; ++i) rx[i] = request[i];
    rc = recv_packet(&kind, &sequence, &size);
    if (rc) send_error(sequence, "invalid packet");
    else handle_packet(kind, sequence, size);
    for (i = 0; i < tx_n; ++i) response[i] = tx[i];
    return tx_n;
}

__declspec(dllexport) uint32_t inspect(uint32_t which) {
    switch (which) {
        case 0: return seg_value;
        case 1: return seg_ctrl;
        case 2: return image_count;
        case 3: return first_pixel;
        case 4: return last_pixel;
        default: return g_auto_display;
    }
}
