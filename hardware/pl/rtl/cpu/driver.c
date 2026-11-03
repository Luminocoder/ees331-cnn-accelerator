/* ============================================================
 * driver.c —— MNIST 加速器 ARM C 驱动 (任务 A 交付物⑥)
 *
 * 作用: 让 Zynq 的 ARM Cortex-A9 通过 AXI-Lite 访问加速器寄存器,
 *       完成"送图 → 触发推理 → 等结果 → 读分数"的完整流程。
 *
 * 依赖:
 *   - 寄存器定义与 ⑤ register_map 一致(基址 0x43C0_0000)
 *   - 在 Xilinx Vitis SDK 中编译(需真 Zynq 板子运行验证)
 *
 * 编译方式(PC 上): 由 Vitis 交叉编译为 ARM 可执行文件。
 * ============================================================ */

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

/* ---------- 寄存器基地址与偏移(对齐 register_map) ---------- */
#define AXILITE_BASE    0x43C00000ul   /* 控制寄存器基地址 */
#define SHARED_MEM_BASE 0x40000000ul   /* 共享内存基地址    */

#define REG_CTRL        0x00           /* 控制寄存器         */
#define REG_STATUS      0x04           /* 状态寄存器         */
#define REG_IMG_ADDR    0x08           /* 图片基地址(兼容, 方案A下未用) */
#define REG_WEIGHT_ADDR 0x0C           /* 权重基地址(兼容, 方案A下未用) */
#define REG_RESULT_0    0x10           /* 结果寄存器0, 后续+4  */
#define REG_IMG_DATA    0x40           /* 图片数据(写一字节, 指针自增) */
#define REG_IMG_PTR     0x44           /* 图片写指针重置       */

/* STATUS 位 */
#define STATUS_BUSY     (1u << 0)
#define STATUS_DONE     (1u << 1)
#define STATUS_ERROR    (1u << 2)

/* CTRL 位 */
#define CTRL_START      (1u << 0)
#define CTRL_SOFT_RST   (1u << 1)

/* 图片/输入尺寸 */
#define IMG_SIZE        (28*28)        /* 784 像素  */

/* ---------- 用 Xil_In/Out32 访问(需包含 xil_io.h) ---------- */
#include "xil_io.h"
#define WR_REG(addr, val)  Xil_Out32((AXILITE_BASE+(addr)), (val))
#define RD_REG(addr)       Xil_In32((AXILITE_BASE+(addr)))
#define WR_MEM(addr, val)  Xil_Out32((SHARED_MEM_BASE+(addr)), (val))

/* 指向共享内存的指针(方便写像素) */
#define SHARED_MEM ((volatile uint8_t*)(SHARED_MEM_BASE))

/* ---------- 初始化 ---------- */
void cnn_init(void)
{
    /* 软复位一次, 清状态(不清 BRAM) */
    WR_REG(REG_CTRL, CTRL_SOFT_RST);
    WR_REG(REG_CTRL, 0x0);
}

/* 共享内存布局 (与冻结 register_map 一致, 不冲突) */
#define W_OFFSET     0x0000        /* 权重: 共享内存偏移 0 开始 (~51KB) */
#define IMG_OFFSET   0x8000        /* 图片: 偏移 32KB 开始(避开权重)     */
#define MEM_WEIGHT   (SHARED_MEM + W_OFFSET)
#define MEM_IMG      (SHARED_MEM + IMG_OFFSET)

/* ---------- 写权重（Phase 4 方案A：权重已烘焙进 bitstream，运行时无需加载） ---------- */
void cnn_load_weights(const uint8_t *weight_data, uint32_t size)
{
    (void)weight_data;
    (void)size;
    /* 权重在综合时经 $readmemh 烘焙进 weight_bram，无需 ARM 写入 */
}

/* ---------- 写一张图片（经 IMG_DATA 寄存器，指针自动递增） ----------
 * ADR-0002 决定3: 输入量化 x_int8 = uint8_pixel >> 1 (0~127)
 */
void cnn_write_image(const uint8_t img[IMG_SIZE])
{
    uint32_t i;
    WR_REG(REG_IMG_PTR, 0);               /* 重置图片写指针 */
    for (i = 0; i < IMG_SIZE; i++) {
        WR_REG(REG_IMG_DATA, (img[i] >> 1));  /* 写一字节, 硬件指针自增 */
    }
}

/* ---------- 触发一次推理(轮询 DONE) ---------- */
void cnn_start(void)
{
    WR_REG(REG_CTRL, CTRL_START);         /* 写 START */
}

/* ---------- 等待完成: 轮询 STATUS.DONE ---------- */
int cnn_wait_done(void)
{
    uint32_t st;
    while (1) {
        st = RD_REG(REG_STATUS);
        if (st & STATUS_DONE)  return 0;  /* 完成 */
        if (st & STATUS_ERROR) return -1; /* 出错  */
    }
}

/* ---------- 读 10 个分类置信度 ---------- */
void cnn_read_results(int32_t result[10])
{
    uint32_t i;
    for (i = 0; i < 10; i++) {
        result[i] = (int32_t)RD_REG(REG_RESULT_0 + i*4);
    }
}

/* ---------- 取置信度最大的类别(0-9) ---------- */
int argmax(const int32_t *a, int n)
{
    int i, m = 0;
    for (i = 1; i < n; i++) if (a[i] > a[m]) m = i;
    return m;
}

/* ============================================================
 * 对外主接口: 喂一张图, 返回识别结果(0-9), -1 出错
 * ============================================================ */
int predict(const uint8_t img[IMG_SIZE])
{
    int32_t result[10];
    int      digit;

    cnn_write_image(img);                 /* 1. 写图     */
    cnn_start();                          /* 2. 触发推理  */
    if (cnn_wait_done() != 0) return -1;  /* 3. 等完成   */
    cnn_read_results(result);             /* 4. 读分数   */
    digit = argmax(result, 10);           /* 5. 取最大   */

    return digit;
}

/* ---------- 简单测试入口(可删) ---------- */
int main(void)
{
    uint8_t test_img[IMG_SIZE] = {0};
    int d;

    cnn_init();                          /* 初始化      */
    /* 生成一张测试图(全0 = 空白, 演示流程) */
    d = predict(test_img);
    printf("识别结果(演示空白图): %d\n", d);
    return 0;
}
