/* Cortex-A9 single-core benchmark scope. Normal firmware startup stays uncached.
 * Only the firmware's own 1 MiB DDR section is cacheable, identity mapped.
 * Inner WBWA, outer noncacheable, nonshareable (single CPU); other sections are
 * Strongly Ordered, XN. No DMA is used: CPU writes individual AXI-Lite pixels.
 * Restore/clean before returning to the original uncached firmware environment.
 */
#include "bench_platform.h"
#ifndef EES331_HOST_TEST
static uint32_t table[4096] __attribute__((aligned(16384)));
static uint32_t saved_sctlr, saved_ttbr0, saved_ttbr1, saved_ttbcr, saved_dacr, saved_gt;
static int active;
#define READ_CP(name,op) uint32_t name(void) { uint32_t v; __asm__ volatile(op : "=r"(v)); return v; }
READ_CP(bench_sctlr,"mrc p15,0,%0,c1,c0,0")
READ_CP(bench_actlr,"mrc p15,0,%0,c1,c0,1")
READ_CP(bench_mpidr,"mrc p15,0,%0,c0,c0,5")
READ_CP(bench_midr,"mrc p15,0,%0,c0,c0,0")
uint32_t bench_read32(uint32_t address) { return *(volatile uint32_t *)address; }
void bench_barrier(void) { __asm__ volatile("dsb sy\n isb" ::: "memory"); }
uint32_t bench_irq_lock(void) {
    uint32_t v; __asm__ volatile("mrs %0,cpsr\n cpsid if" : "=r"(v) :: "memory"); return v;
}
void bench_irq_restore(uint32_t v) { __asm__ volatile("msr cpsr_c,%0" :: "r"(v) : "memory"); }
uint64_t bench_ticks(void) {
    uint32_t hi,lo,again;
    bench_barrier();
    do {
        hi=bench_read32(0xF8F00204u); lo=bench_read32(0xF8F00200u);
        again=bench_read32(0xF8F00204u);
    } while (hi!=again);
    bench_barrier();
    return ((uint64_t)hi<<32)|lo;
}
static void l1_dcache(int clean) {
    uint32_t sel=0, ccsidr, way, set, ws, sets, ways, ls;
    __asm__ volatile("mcr p15,2,%0,c0,c0,0\n isb\n mrc p15,1,%1,c0,c0,0"
                     : "+r"(sel), "=r"(ccsidr) :: "memory");
    ls=(ccsidr&7u)+4u; ways=((ccsidr>>3)&1023u)+1u;
    sets=((ccsidr>>13)&32767u)+1u;
    ws=ways>1u ? (uint32_t)__builtin_clz(ways-1u) : 0u;
    for (way=0; way<ways; ++way) for (set=0; set<sets; ++set) {
        uint32_t v=(ways>1u ? way<<ws : 0u)|(set<<ls);
        if (clean) __asm__ volatile("mcr p15,0,%0,c7,c14,2" :: "r"(v) : "memory");
        else __asm__ volatile("mcr p15,0,%0,c7,c6,2" :: "r"(v) : "memory");
    }
    bench_barrier();
}
int bench_platform_active(void) { return active; }
int bench_platform_begin(void) {
    uint32_t i,v,irq;
    if (active) return -1;
    /* This scope deliberately supports only the shipped uncached CPU0 startup. */
    if ((bench_sctlr()&0x1005u) || (bench_mpidr()&255u)) return -2;
    saved_gt=bench_read32(0xF8F00208u);
    if (saved_gt&14u) return -3; /* Do not borrow a comparator/interrupt timer. */
    irq=bench_irq_lock();
    saved_sctlr=bench_sctlr();
    __asm__ volatile("mrc p15,0,%0,c2,c0,0" : "=r"(saved_ttbr0));
    __asm__ volatile("mrc p15,0,%0,c2,c0,1" : "=r"(saved_ttbr1));
    __asm__ volatile("mrc p15,0,%0,c2,c0,2" : "=r"(saved_ttbcr));
    __asm__ volatile("mrc p15,0,%0,c3,c0,0" : "=r"(saved_dacr));
    for (i=0; i<4096; ++i) table[i]=(i<<20)|0x10C12u;
    table[1]=0x00100000u|0x4C0Eu; /* TEX=100 C=B=1: outer NC, inner WBWA */
    l1_dcache(0);
    v=0;
    __asm__ volatile("mcr p15,0,%0,c7,c5,0\n mcr p15,0,%0,c7,c5,6\n mcr p15,0,%0,c8,c7,0" :: "r"(v) : "memory");
    __asm__ volatile("mcr p15,0,%0,c2,c0,2" :: "r"(v) : "memory");
    v=(uint32_t)table;
    __asm__ volatile("mcr p15,0,%0,c2,c0,0" :: "r"(v) : "memory");
    v=1;
    __asm__ volatile("mcr p15,0,%0,c3,c0,0" :: "r"(v) : "memory");
    bench_barrier();
    v=(saved_sctlr|0x00801005u)&~0x30000000u; /* XP=1, TRE=AFE=0 */
    __asm__ volatile("mcr p15,0,%0,c1,c0,0\n isb" :: "r"(v) : "memory");
    *(volatile uint32_t *)0xF8F00208u=saved_gt|1u;
    active=1;
    bench_barrier();
    bench_irq_restore(irq);
    return 0;
}
void bench_platform_end(void) {
    uint32_t irq,v;
    if (!active) return;
    irq=bench_irq_lock();
    active=0; /* This write must be included in the cache clean. */
    l1_dcache(1);
    v=saved_sctlr;
    __asm__ volatile("mcr p15,0,%0,c1,c0,0\n isb" :: "r"(v) : "memory");
    __asm__ volatile("mcr p15,0,%0,c2,c0,0" :: "r"(saved_ttbr0) : "memory");
    __asm__ volatile("mcr p15,0,%0,c2,c0,1" :: "r"(saved_ttbr1) : "memory");
    __asm__ volatile("mcr p15,0,%0,c2,c0,2" :: "r"(saved_ttbcr) : "memory");
    __asm__ volatile("mcr p15,0,%0,c3,c0,0" :: "r"(saved_dacr) : "memory");
    v=0;
    __asm__ volatile("mcr p15,0,%0,c8,c7,0\n mcr p15,0,%0,c7,c5,0" :: "r"(v) : "memory");
    *(volatile uint32_t *)0xF8F00208u=saved_gt;
    bench_barrier(); bench_irq_restore(irq);
}
#endif
