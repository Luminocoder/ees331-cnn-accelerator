#ifndef BENCHMARK_DATA_H
#define BENCHMARK_DATA_H
#include <stdint.h>
extern const int8_t bench_w_Conv1[72];
extern const int32_t bench_b_Conv1[8];
extern const int8_t bench_w_Conv2[1152];
extern const int32_t bench_b_Conv2[16];
extern const int8_t bench_w_FC1[50176];
extern const int32_t bench_b_FC1[64];
extern const int8_t bench_w_FC2[640];
extern const int32_t bench_b_FC2[10];
extern const uint8_t bench_images[15680];
extern const int32_t bench_expected[200];
extern const uint8_t bench_hash_model[32];
extern const uint8_t bench_hash_dataset[32];
extern const uint8_t bench_hash_golden[32];
extern const uint8_t bench_hash_implementation[32];
#endif
