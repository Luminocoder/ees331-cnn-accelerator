#ifndef CPU_INFER_H
#define CPU_INFER_H
#include <stdint.h>
void cpu_infer(const uint8_t pixels[784], int32_t logits[10]);
#endif
