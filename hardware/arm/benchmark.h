#ifndef BENCHMARK_H
#define BENCHMARK_H
#include <stdint.h>
int benchmark_command(uint8_t kind, const uint8_t *request, uint16_t size,
                      uint8_t *reply, int (*accelerator)(const uint8_t *,int32_t *));
#endif
