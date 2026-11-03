#ifndef AIC_HOST_TEST_XIL_IO_H
#define AIC_HOST_TEST_XIL_IO_H

#include <stdint.h>

/* Host-only replacements. These functions never access physical MMIO. */
void Xil_Out32(uintptr_t address, uint32_t value);
uint32_t Xil_In32(uintptr_t address);

#endif
