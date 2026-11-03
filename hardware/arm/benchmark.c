#include "benchmark.h"
#include "benchmark_data.h"
#include "bench_platform.h"
#include "cpu_infer.h"

static uint8_t input[784];
static uint32_t overhead;
#ifdef EES331_HOST_TEST
void bench_test_elapsed(unsigned lane);
#define TEST_ELAPSED(lane) bench_test_elapsed(lane)
#else
#define TEST_ELAPSED(lane) ((void)0)
#endif
static void put32(uint8_t *p,uint32_t v) {
    p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24);
}
static void put64(uint8_t *p,uint64_t v) { put32(p,(uint32_t)v); put32(p+4,(uint32_t)(v>>32)); }
static uint32_t crc(const uint8_t *p,unsigned n) {
    uint32_t v=0xffffffffu; unsigned i,b;
    for (i=0;i<n;++i) { v^=p[i]; for (b=0;b<8;++b) v=(v>>1)^(0xedb88320u&(0u-(v&1u))); }
    return v^0xffffffffu;
}
static int info(uint8_t *reply) {
    unsigned i,j;
    const uint8_t *hashes[4]={bench_hash_model,bench_hash_dataset,bench_hash_golden,bench_hash_implementation};
    uint32_t words[21]={20,1,2,0,0};
    reply[0]='B'; reply[1]='N'; reply[2]='C'; reply[3]='1';
    for (i=0;i<4;++i) for (j=0;j<32;++j) reply[4+i*32+j]=hashes[i][j];
    words[4]=(uint32_t)bench_platform_active(); words[5]=bench_sctlr(); words[6]=bench_actlr();
    words[7]=bench_read32(0xF8F02100u); words[8]=bench_read32(0xF8F00208u);
    words[9]=bench_read32(0xF8000100u); words[10]=bench_read32(0xF8000104u);
    words[11]=bench_read32(0xF8000108u); words[12]=bench_read32(0xF8000120u);
    words[13]=bench_read32(0xF8000170u); words[14]=bench_read32(0xF80001C4u);
    words[15]=33333333u; words[16]=bench_mpidr(); words[17]=bench_midr(); words[18]=overhead;
    words[19]=bench_read32(0xF800025Cu); words[20]=bench_read32(0xF8F00000u);
    for (i=0;i<21;++i) put32(reply+132+4*i,words[i]);
    put64(reply+216,bench_ticks());
    return 224;
}
int benchmark_command(uint8_t kind,const uint8_t *request,uint16_t size,
                      uint8_t *reply,int (*accelerator)(const uint8_t *,int32_t *)) {
    unsigned i,idx,order;
    int32_t cpu[10],pl[10]={0};
    int status=0;
    uint64_t t0,cpu_ticks=0,pl_ticks=0;
    uint32_t irq,cmask=0,pmask=0;
    if (kind==7u) return size==0u ? info(reply) : -1;
    if (kind==8u) {
        if (size!=1u || request[0]>1u) return -1;
        if (request[0]==0u) bench_platform_end();
        else {
            if (bench_platform_begin()!=0) return -2;
            irq=bench_irq_lock(); overhead=0xffffffffu;
            for (i=0;i<32;++i) {
                uint64_t a=bench_ticks(),b=bench_ticks();
                if (b-a<overhead) overhead=(uint32_t)(b-a);
            }
            bench_irq_restore(irq);
        }
        return info(reply);
    }
    if (kind!=9u || size!=2u || request[0]>=20u || request[1]>1u || !bench_platform_active()) return -3;
    idx=request[0]; order=request[1];
    /* Same source address for both paths. Copy and CRC are outside both timers. */
    for (i=0;i<784;++i) input[i]=bench_images[idx*784+i];
    irq=bench_irq_lock();
    for (i=0;i<2;++i) {
        bench_barrier();
        if ((i^order)==0u) {
            t0=bench_ticks(); cpu_infer(input,cpu); TEST_ELAPSED(0); bench_barrier(); cpu_ticks=bench_ticks()-t0;
        } else {
            t0=bench_ticks(); status=accelerator(input,pl); TEST_ELAPSED(1); bench_barrier(); pl_ticks=bench_ticks()-t0;
        }
    }
    bench_irq_restore(irq);
    for (i=0;i<10;++i) {
        if (cpu[i]==bench_expected[idx*10+i]) cmask|=1u<<i;
        if (status==0 && pl[i]==bench_expected[idx*10+i]) pmask|=1u<<i;
    }
    reply[0]='B'; reply[1]='N'; reply[2]='R'; reply[3]='1';
    put32(reply+4,idx); put32(reply+8,order); put32(reply+12,(uint32_t)status);
    put32(reply+16,cmask); put32(reply+20,pmask); put32(reply+24,crc(input,784));
    put32(reply+28,bench_sctlr()); put32(reply+32,bench_read32(0xF8F00208u));
    put64(reply+36,cpu_ticks); put64(reply+44,pl_ticks);
    for (i=0;i<10;++i) { put32(reply+52+i*4,(uint32_t)cpu[i]); put32(reply+92+i*4,(uint32_t)pl[i]); }
    return 132;
}
