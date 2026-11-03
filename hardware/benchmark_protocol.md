> v5.6: the ARM ELF, CPU baseline, clock and measurement boundary are unchanged; the PL convolution dataflow IS optimized and the v5.5 ghost suppression is retained. New board measurements are pending. Current evidence and instructions: ../优化说明与验收_20261101.md and ../README.md. The following v5.4 development/connection notes are historical.

# v5.4 ARM/PL benchmark protocol and agent handoff

Date: 2026-11-01. Scope: add a camera-free single-core scalar C baseline and paired board timing. The v5.3 PL bitstream is reused unchanged. No real board has been accessed in this development run. Primary task entry and human instructions: `../README.md`.

## Packet contract

All packets retain MNST framing, version1, sequence, length and CRC32. Three request kinds were added; reply kind = request | 0x80. Existing HELLO/IMAGE/DISPLAY and v5.3 page commands remain compatible outside the benchmark session.

| Kind | Request | Reply |
|---|---|---|
| 7 INFO | empty | 224-byte BNC1 info |
| 8 CONTROL | one byte, 1 begin / 0 end | BNC1 info after transition |
| 9 RUN | two bytes: frozen index 0..19, order 0 CPU-first / 1 PL-first | 132-byte BNR1 paired result |

Malformed payloads, unsupported initial cache/timer state, nested begin and run outside a session return ERROR. During a session, commands other than INFO/CONTROL/RUN/HELLO return ERROR. End is idempotent. UART, checks, CRC and response generation are outside timing. Normal display is not modified by RUN.

INFO: `BNC1` + 4×32 raw SHA256 bytes (model, dataset, golden, implementation) + 21 little-endian u32 words + u64 current global-counter ticks.

Words in order: samples, profile=1, optimization=2, neon=0, active, SCTLR, ACTLR, PL310 control, global-timer control, ARM_PLL_CTRL, DDR_PLL_CTRL, IO_PLL_CTRL, ARM_CLK_CTRL, FPGA0_CLK_CTRL, CLK_621_TRUE, configured reference_hz=33333333, MPIDR, MIDR, minimum empty timer-read overhead_ticks, BOOT_MODE, SCU control. Current ticks at offset216.

RUN: `BNR1` + 8×u32 (index, order, accelerator status, CPU golden-match mask, PL golden-match mask, input CRC32, SCTLR, timer control) + CPU u64 ticks + PL u64 ticks + 10 CPU int32 logits + 10 PL int32 logits. Both masks must be0x3FF; status must be0. Tick offsets36/44; logits offsets52/92. Host also compares every score to its own frozen expected array and verifies CRC, sequence and order.

## Data and numerical identity

Generator: `generate_benchmark.py`. Weights come from `pl/ref_model/weights_int8`, verified byte-equal to host weights. PL bias_layout stores MSB-first signed INT32 words; generated ARM constants decode that exact layout. Canonical model hash concatenates each layer weights + LE int32 biases, then LE u32 shifts9/9/10. Dataset hash is20×784 raw uint8 bytes, golden hash is200 LE int32 words. Implementation hash includes CPU/platform/protocol/firmware/runtime/start/link sources and headers, compiler version and declared flags.

Generated `arm/benchmark_data.[ch]` and `../resources/benchmark/metadata.json` are reproducible inputs. Same data are compiled into the ELF; no camera/UART image loading participates. Intermediate activation layout CHW, convolution OIHW, FC weights [input][output]. INT32 accumulation bounds are checked when generating constants. ReLU-before-right-shift for nonpositive sums is mathematically equivalent to the reference clamp and avoids relying on a compiler-specific negative shift. Nonnegative values shift9/9/10; FC2 is unshifted INT32.

## Timing and platform scope

CPU and accelerator functions receive the same ARM RAM address; copy to this buffer is outside timing. Timer wrapper uses DSB/ISB, reads global-timer high/low/high until stable, and includes the final output writes. No LTO; CPU is a separate noninline function. Both paths have the same wrapper overhead. Only RUN timed work is IRQ/FIQ masked; restore afterwards. Guard errors abort reporting.

Startup remains uncached. Begin requires CPU0 with SCTLR M/C/I all off and a global timer without comparator/interrupt/autoincrement active. It saves SCTLR/TTBR0/TTBR1/TTBCR/DACR/timer control. A16KiB aligned4096-entry identity section table covers4GiB. Only section1 (0x00100000..0x001FFFFF) is normal, nonshareable, inner WBWA/outer NC (TEX=100,C=B=1); all others Strongly Ordered and XN. Firmware, stack and scratch must fit in section1; link asserts at least64KiB stack headroom. Normal CPU single-core L1 semantics need no SCU SMP change; no DMA is used. Existing L2 control is observed, not changed, and outer-NC mapping excludes L2 use.

Begin invalidates L1/TLB/I-cache and enables MMU/L1. End cleans/invalidates L1 before restoring original SCTLR and translation registers, invalidates TLB/I-cache and restores timer control. Counter value is not reset/restored. A dropped connection can leave the session active; normal commands then refuse until end or a fresh guarded JTAG initialization.

Host derives CPU frequency from SLCR reference/PLL/source/divider and global-counter frequency as CPU/2/(prescaler+1). It derives PL clock from FPGA0 source/dividers and records clock_621 and all raw values. This assumes the documented Zynq-A9 global-timer clock relation;3 host-time intervals are a coarse8% cross-check, not oscillator calibration. Primary speedup is ratio of summed ticks, so reference-frequency conversion cancels. Before/after environment equality and final restore are required.

20 correctness pairs then10 warmup pairs then100 formal pairs (20 inputs×5 rounds). Order=(round+index)%2, giving50/50 formal first order. Report all values and nearest-rank P95; no discarded outliers. Any score/CRC/clock/protocol/restore error, cancellation or emulated environment suppresses hardware speedup and valid flag. This is a scalar -O2 L1 baseline, not an optimized NEON/L2/multicore ceiling. No pure-PL cycle counter is added.

## Verification and remaining work

Host test DLL compiles the actual CPU and firmware C. Tests verify frozen200 scores plus extreme/random/border inputs against Python; trace130-pair workflow, reject malformed state, mismatch, halted timer and cancellation. Emulator MMIO/time are explicit, never accepted as real speedup. Real Tk test validates button, background worker, progress, cancel and restore. Final source/actual-EXE checks are indexed at `../integration/latest_validation.json`.

Pending: actual CPU0 MMU/cache transitions, actual global timer/frequency check, both complete benchmark runs and original inference/page regression after restore. Current machine enumerates COM3/COM4 only; COM5 is absent. No accelerations are claimed. Actual runtime artifacts belong in `logs/benchmark/`; preserve whole directory. If a first board run fails, inspect raw wire, last reply and build hashes before changing numerical code. Reinitialize after an unrecoverable serial/restore failure.

Build/install/test instructions are in README. Current ELF build inputs/commands are at `../integration/firmware-build.json`; source/exe/resource and delivery hashes are separate. Base package is v5.3, originals remain in their sibling directories; recipient does not need them for runtime. Full LLVM/Vivado/Python dependency installers are not bundled. Stage deliverable is MD plus runnable EXE/resources and sources, no external publication.
