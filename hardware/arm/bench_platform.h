#ifndef BENCH_PLATFORM_H
#define BENCH_PLATFORM_H
#include <stdint.h>
int bench_platform_begin(void);
void bench_platform_end(void);
int bench_platform_active(void);
uint32_t bench_read32(uint32_t address);
uint32_t bench_sctlr(void);
uint32_t bench_actlr(void);
uint32_t bench_mpidr(void);
uint32_t bench_midr(void);
uint32_t bench_irq_lock(void);
void bench_irq_restore(uint32_t state);
uint64_t bench_ticks(void);
void bench_barrier(void);
#endif
