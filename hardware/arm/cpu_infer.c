/* Scalar single-core INT8 reference, CHW activations, OIHW convolution.
 * No PL access, allocation, UART, floating point or NEON. Compiled at -O2.
 * Bounds are checked by generate_benchmark.py before emitting the constants.
 */
#include "cpu_infer.h"
#include "benchmark_data.h"

static int8_t input_q[784], conv[28*28], pool1[8*14*14], pool2[16*7*7], fc1[64];

static int8_t activate(int32_t value, unsigned shift)
{
    /* Clamp before shift: for nonpositive values ReLU is zero; positive shift
       is defined by ISO C and matches signed hardware shift followed by ReLU. */
    if (value <= 0) return 0;
    value >>= shift;
    return (int8_t)(value > 127 ? 127 : value);
}

static void conv_pool(const int8_t *in, int h, int channels, int outputs,
                      const int8_t *weight, const int32_t *bias,
                      unsigned shift, int8_t *pooled)
{
    int oc, y, x, ic, ky, kx, p = h/2;
    for (oc=0; oc<outputs; ++oc) {
        /* Reuse one channel of the scratch plane; pooling follows ReLU. */
        for (y=0; y<h; ++y) for (x=0; x<h; ++x) {
            int32_t sum = bias[oc];
            for (ic=0; ic<channels; ++ic) {
                const int8_t *w = weight + (oc*channels+ic)*9;
                for (ky=0; ky<3; ++ky) {
                    int iy = y+ky-1;
                    if ((unsigned)iy >= (unsigned)h) continue;
                    for (kx=0; kx<3; ++kx) {
                        int ix = x+kx-1;
                        if ((unsigned)ix < (unsigned)h)
                            sum += (int32_t)in[(ic*h+iy)*h+ix] * w[ky*3+kx];
                    }
                }
            }
            conv[y*h+x] = activate(sum, shift);
        }
        for (y=0; y<p; ++y) for (x=0; x<p; ++x) {
            int pos = 2*y*h+2*x;
            int8_t v=conv[pos], t=conv[pos+1];
            if (t>v) v=t;
            t=conv[pos+h]; if (t>v) v=t;
            t=conv[pos+h+1]; if (t>v) v=t;
            pooled[(oc*p+y)*p+x] = v;
        }
    }
}

__attribute__((noinline)) void cpu_infer(const uint8_t pixels[784], int32_t logits[10])
{
    int i, j;
    int32_t sums[64];
    for (i=0; i<784; ++i) input_q[i]=(int8_t)(pixels[i]>>1);
    conv_pool(input_q,28,1,8,bench_w_Conv1,bench_b_Conv1,9,pool1);
    conv_pool(pool1,14,8,16,bench_w_Conv2,bench_b_Conv2,9,pool2);
    for (j=0; j<64; ++j) sums[j]=bench_b_FC1[j];
    /* FC1 file layout is [input][output]; contiguous reads of 64 outputs. */
    for (i=0; i<784; ++i) {
        int32_t v=pool2[i];
        const int8_t *w=bench_w_FC1+i*64;
        for (j=0; j<64; ++j) sums[j] += v*w[j];
    }
    for (j=0; j<64; ++j) fc1[j]=activate(sums[j],10);
    for (j=0; j<10; ++j) logits[j]=bench_b_FC2[j];
    for (i=0; i<64; ++i) for (j=0; j<10; ++j)
        logits[j] += (int32_t)fc1[i]*bench_w_FC2[i*10+j];
}
