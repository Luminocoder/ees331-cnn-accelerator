/* ===========================================================================
 * fw_seg.c —— EES-331 裸机固件（无 BSP 依赖）
 *
 * 职责：
 *   1) UART1 (MIO48/49, 115200 8N1) 上实现既有的 MNST 串口协议，与原来
 *      app.elf 逐位兼容：HELLO/HELLO_ACK、IMAGE/SCORES、ERROR。
 *   2) 通过 AXI-Lite 驱动 PL 里的 MNIST 加速器（送图 -> START -> 轮询 DONE
 *      -> 读 10 路 INT32 logits），流程与 rtl/cpu/driver.c 完全一致。
 *   3) 【新增】把本次识别结果写到板载 8 位数码管：
 *        0x43C10000 VALUE = digit
 *        0x43C10004 CTRL  = 0  (bit0 BLANK=0, bit2:1 FMT=00 十进制)
 *      上电先读 0x43C10008 MAGIC = 0x53454731，确认 PL 里真的挂了数码管从机。
 *
 * 旧HELLO/IMAGE/DISPLAY保持兼容；v5.3扩展八位分页，v5.4增加7/8/9性能命令。
 * 性能命令直接使用冻结输入，无相机；在同一个ARM计时器上比较两条路径。
 *
 * 构建：python hardware/build_firmware.py（LLVM，无BSP）
 * =========================================================================*/

#include <stdint.h>
#include "benchmark.h"
#include "bench_platform.h"

#ifdef EES331_HOST_TEST
uint32_t firmware_read32(uint32_t address);
void firmware_write32(uint32_t address, uint32_t value);
#define R32(a) firmware_read32(a)
#define W32(a, v) firmware_write32(a, (uint32_t)(v))
#else
#define R32(a)      (*(volatile uint32_t *)(a))
#define W32(a, v)   do { *(volatile uint32_t *)(a) = (uint32_t)(v); } while (0)
#endif

/* ---------------- UART1（PS 外设，直接寄存器操作） ---------------- */
#define UART1_BASE   0xE0001000u
#define U_CR         (UART1_BASE + 0x00u)
#define U_MR         (UART1_BASE + 0x04u)
#define U_BAUDGEN    (UART1_BASE + 0x18u)
#define U_RXTRIG     (UART1_BASE + 0x20u)
#define U_SR         (UART1_BASE + 0x2Cu)
#define U_FIFO       (UART1_BASE + 0x30u)
#define U_BAUDDIV    (UART1_BASE + 0x34u)

#define SR_RXEMPTY   0x02u          /* RX FIFO 空 */
#define SR_TXFULL    0x10u          /* TX FIFO 满 */

/* ---------------- PL：MNIST 加速器 ---------------- */
#define ACC_BASE     0x43C00000u
#define A_CTRL       (ACC_BASE + 0x00u)   /* b0 START, b1 SOFT_RESET */
#define A_STATUS     (ACC_BASE + 0x04u)   /* b0 BUSY, b1 DONE, b2 ERROR */
#define A_RESULT(i)  (ACC_BASE + 0x10u + 4u * (uint32_t)(i))
#define A_IMG_DATA   (ACC_BASE + 0x40u)   /* 写一字节，内部指针自增 */
#define A_IMG_PTR    (ACC_BASE + 0x44u)   /* 写 0 复位像素指针 */

#define ST_BUSY      0x01u
#define ST_DONE      0x02u
#define ST_ERROR     0x04u
#define CT_START     0x01u
#define CT_SOFT_RST  0x02u

/* ---------------- PL：8 位数码管 ---------------- */
#define SEG_BASE     0x43C10000u
#define SEG_VALUE    (SEG_BASE + 0x00u)
#define SEG_CTRL     (SEG_BASE + 0x04u)
#define SEG_MAGIC_R  (SEG_BASE + 0x08u)
#define SEG_PAGE_ID  (SEG_BASE + 0x0Cu)
#define SEG_PAGE_V1  0x50414731u
#define SEG_MAGIC    0x53454731u          /* "SEG1" */
#define SEG_CTRL_BLANK     0x01u
#define SEG_CTRL_FMT_DEC   0x00u
#define SEG_CTRL_FMT_HEX   0x02u
#define SEG_CTRL_FMT_PAGE  0x06u

/* ---------------- 串口协议常量（必须与 mnist_serial.py 一致） -------- */
#define PKT_MAGIC0 'M'
#define PKT_VERSION 1u
#define K_HELLO      1u
#define K_IMAGE      2u
#define K_DISPLAY    3u          /* 新增：PC 直接指定数码管显示值（向后兼容） */
#define K_DISPLAY_CAPS 4u
#define K_DISPLAY_PAGE 5u
#define K_DISPLAY_MODE 6u
#define K_HELLO_ACK  0x81u
#define K_SCORES     0x82u
#define K_DISPLAY_ACK 0x83u
#define K_DISPLAY_CAPS_ACK 0x84u
#define K_DISPLAY_PAGE_ACK 0x85u
#define K_DISPLAY_MODE_ACK 0x86u
#define K_ERROR      0xFFu
#define HEADER_SIZE  12u                                  /* 4s B B I H */
#define MAX_PAYLOAD  1024u
#define IMG_PIXELS   784u

static const uint8_t IDENTITY[14] = {      /* b"MNIST-U8-28x28"，无结尾 0 */
    'M','N','I','S','T','-','U','8','-','2','8','x','2','8'
};

static uint8_t  g_buf[HEADER_SIZE + MAX_PAYLOAD + 4u];   /* 收包/组包共用 */
static int      g_seg_ok;                                /* 数码管从机在位 */
static uint8_t  g_auto_display = 1u;

/* ======================= 极简串口收发 ======================= */
static void uart_init(void)
{
    W32(U_CR, 0x0u);          /* 先关收发，准备配置 */
    W32(U_MR, 0x20u);         /* 8 数据位、无校验（与原固件实测值一致） */
    W32(U_BAUDGEN, 124u);     /* 100 MHz / (6+1) / 124 = 115207 baud   */
    W32(U_BAUDDIV, 6u);
    W32(U_RXTRIG, 0x20u);
    W32(U_CR, 0x114u);        /* TXEN|RXEN（+STOPBRK，与原固件一致） */
}

static void uart_putc(uint8_t c)
{
    while (R32(U_SR) & SR_TXFULL) { }
    W32(U_FIFO, c);
}

static void uart_write(const uint8_t *p, uint32_t n)
{
    while (n--) uart_putc(*p++);
}

static void uart_puts(const char *s)
{
    while (*s) uart_putc((uint8_t)*s++);
}


static uint8_t uart_getc(void)
{
    while (R32(U_SR) & SR_RXEMPTY) { }
    return (uint8_t)(R32(U_FIFO) & 0xFFu);
}

static void uart_read(uint8_t *p, uint32_t n)
{
    while (n--) *p++ = uart_getc();
}

static void uart_put_dec(uint32_t v)
{
    char t[12];
    int i = 0;
    if (v == 0) { uart_putc('0'); return; }
    while (v && i < 11) { t[i++] = (char)('0' + v % 10u); v /= 10u; }
    while (i--) uart_putc((uint8_t)t[i]);
}

static void uart_put_hex32(uint32_t v)
{
    static const char *H = "0123456789ABCDEF";
    int i;
    for (i = 7; i >= 0; --i) uart_putc((uint8_t)H[(v >> (4 * i)) & 0xFu]);
}


/* ======================= CRC32（与 zlib.crc32 相同） =============== */
static uint32_t crc32_buf(const uint8_t *p, uint32_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    uint32_t i;
    int b;
    for (i = 0; i < n; ++i) {
        crc ^= p[i];
        for (b = 0; b < 8; ++b)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1u)));
    }
    return crc ^ 0xFFFFFFFFu;
}

/* ======================= 数码管 ======================= */
static void seg_init(void)
{
    g_seg_ok = (R32(SEG_MAGIC_R) == SEG_MAGIC) ? 1 : 0;
    if (g_seg_ok) W32(SEG_CTRL, SEG_CTRL_BLANK);       /* 先全灭，等第一次结果 */
}

static void seg_show_digit(int d)
{
    if (!g_seg_ok) return;
    if (d < 0 || d > 9) {                              /* 出错：显示 FFFFFFFF */
        W32(SEG_VALUE, 0xFFFFFFFFu);
        W32(SEG_CTRL, SEG_CTRL_FMT_HEX);
        return;
    }
    W32(SEG_VALUE, (uint32_t)d);
    W32(SEG_CTRL, SEG_CTRL_FMT_DEC);                   /* BLANK=0 -> 点亮 */
}

/* 粗延时：只用来看清上电自检，精度不重要，~0.5 s */
static void delay_loops(uint32_t n)
{
    volatile uint32_t i;
    for (i = 0; i < n; i++) {
    }
}

static void seg_selftest_blink(void)
{
    if (!g_seg_ok) return;
    seg_show_digit(-1);            /* 亮 FFFFFFFF：证明数码管通路活着 */
    delay_loops(20000000u);        /* 约 0.5 s */
    W32(SEG_CTRL, SEG_CTRL_BLANK); /* 然后全灭，等第一次识别出结果 */
}

/* PC 端融合后直接指定显示值（0~99，十进制）。
   为什么需要：三路融合时 PC 会发三张 28x28，固件每张各算一次、各写一次，
   最后留下的是最后一路的单路 argmax，跟 PC 显示的融合结果可能不同。
   有了这条命令，PC 算完融合把最终数字写进来，两边一定一致。 */
static void seg_set_value(uint32_t v)
{
    if (!g_seg_ok) return;
    if (v > 99u) v = 99u;
    W32(SEG_VALUE, v);
    W32(SEG_CTRL, SEG_CTRL_FMT_DEC);   /* BLANK=0 -> 点亮 */
}

/* ======================= 加速器驱动 ======================= */
static void acc_init(void)
{
    W32(A_CTRL, CT_SOFT_RST);
    W32(A_CTRL, 0x0u);
}

static int acc_infer(const uint8_t img[IMG_PIXELS], int32_t out[10])
{
    uint32_t i;
    uint32_t guard;

    W32(A_IMG_PTR, 0u);
    for (i = 0; i < IMG_PIXELS; ++i)
        W32(A_IMG_DATA, (uint32_t)(img[i] >> 1));      /* x_int8 = pixel >> 1 */

    W32(A_CTRL, CT_START);

    guard = 200000000u;                                /* 死等兜底，避免挂死 */
    while (guard--) {
        uint32_t st = R32(A_STATUS);
        if (st & ST_ERROR) return -1;
        if (st & ST_DONE)  break;
    }
    if (!(R32(A_STATUS) & ST_DONE)) return -2;

    for (i = 0; i < 10u; ++i) out[i] = (int32_t)R32(A_RESULT(i));
    return 0;
}

static int argmax10(const int32_t *a)
{
    int i, m = 0;
    for (i = 1; i < 10; ++i) if (a[i] > a[m]) m = i;
    return m;
}

/* ======================= 组包发送 ======================= */
static void send_packet(uint8_t kind, uint32_t seq, const uint8_t *payload, uint16_t size)
{
    uint32_t crc;
    g_buf[0] = PKT_MAGIC0;
    g_buf[1] = 'N';
    g_buf[2] = 'S';
    g_buf[3] = 'T';
    g_buf[4] = (uint8_t)PKT_VERSION;
    g_buf[5] = kind;
    g_buf[6] = (uint8_t)(seq & 0xFFu);
    g_buf[7] = (uint8_t)((seq >> 8) & 0xFFu);
    g_buf[8] = (uint8_t)((seq >> 16) & 0xFFu);
    g_buf[9] = (uint8_t)((seq >> 24) & 0xFFu);
    g_buf[10] = (uint8_t)(size & 0xFFu);
    g_buf[11] = (uint8_t)((size >> 8) & 0xFFu);

    if (payload && size) {
        uint16_t i;
        for (i = 0; i < size; ++i) g_buf[HEADER_SIZE + i] = payload[i];
    }
    crc = crc32_buf(g_buf, HEADER_SIZE + size);
    g_buf[HEADER_SIZE + size + 0] = (uint8_t)(crc & 0xFFu);
    g_buf[HEADER_SIZE + size + 1] = (uint8_t)((crc >> 8) & 0xFFu);
    g_buf[HEADER_SIZE + size + 2] = (uint8_t)((crc >> 16) & 0xFFu);
    g_buf[HEADER_SIZE + size + 3] = (uint8_t)((crc >> 24) & 0xFFu);

    uart_write(g_buf, HEADER_SIZE + size + 4u);
}

static void send_error(uint32_t seq, const char *msg)
{
    uint16_t n = 0;
    while (msg[n] && n < 200u) ++n;
    send_packet(K_ERROR, seq, (const uint8_t *)msg, n);
}

/* ======================= 收包（同步等待一个完整包） ================ */
static int recv_packet(uint8_t *kind, uint32_t *seq, uint16_t *size)
{
    uint32_t window = 0;
    uint32_t tries = 0;

    /* 等待 "MNST"，容忍前面的杂字节（上电噪声、上一包的残尾） */
    while (tries++ < 8192u) {
        uint8_t c = uart_getc();
        window = (window >> 8) | ((uint32_t)c << 24);
        if (window == 0x54534E4Du) break;      /* "M","N","S","T" 依次移入后的值 */
    }
    if (tries >= 8192u) return -1;

    g_buf[0] = 'M'; g_buf[1] = 'N'; g_buf[2] = 'S'; g_buf[3] = 'T';
    uart_read(&g_buf[4], HEADER_SIZE - 4u);

    if (g_buf[4] != (uint8_t)PKT_VERSION) return -2;

    *kind = g_buf[5];
    *seq  = (uint32_t)g_buf[6] | ((uint32_t)g_buf[7] << 8) |
            ((uint32_t)g_buf[8] << 16) | ((uint32_t)g_buf[9] << 24);
    *size = (uint16_t)((uint16_t)g_buf[10] | ((uint16_t)g_buf[11] << 8));
    if (*size > MAX_PAYLOAD) return -3;

    uart_read(&g_buf[HEADER_SIZE], *size);
    uart_read(&g_buf[HEADER_SIZE + *size], 4u);

    {
        uint32_t got = (uint32_t)g_buf[HEADER_SIZE + *size + 0] |
                       ((uint32_t)g_buf[HEADER_SIZE + *size + 1] << 8) |
                       ((uint32_t)g_buf[HEADER_SIZE + *size + 2] << 16) |
                       ((uint32_t)g_buf[HEADER_SIZE + *size + 3] << 24);
        if (got != crc32_buf(g_buf, HEADER_SIZE + *size)) return -4;
    }
    return 0;
}

/* ======================= 主循环 ======================= */
static void handle_packet(uint8_t kind, uint32_t seq, uint16_t size)
{
    int32_t  logits[10];
    uint8_t  reply[1 + 40];
    int      d, i;
    uint32_t v;

        if (kind >= 7u && kind <= 9u) {
            uint8_t bench_reply[224];
            int n=benchmark_command(kind,&g_buf[HEADER_SIZE],size,bench_reply,acc_infer);
            if (n<0) send_error(seq,"benchmark unsupported state, payload or cache/timer environment");
            else send_packet((uint8_t)(kind|0x80u),seq,bench_reply,(uint16_t)n);
        } else if (bench_platform_active() && kind != K_HELLO) {
            send_error(seq,"benchmark active; finish benchmark before normal operations");
        } else if (kind == K_DISPLAY_CAPS) {
            static const uint8_t caps[8] = {'S','G','P','1',1,8,3,0};
            if (size != 0u || !g_seg_ok || R32(SEG_PAGE_ID) != SEG_PAGE_V1) {
                send_error(seq, "page display unavailable");
                return;
            }
            send_packet(K_DISPLAY_CAPS_ACK, seq, caps, sizeof(caps));
        } else if (kind == K_DISPLAY_MODE) {
            uint8_t mode = g_buf[HEADER_SIZE];
            if (size != 1u || mode > 1u || !g_seg_ok || R32(SEG_PAGE_ID) != SEG_PAGE_V1) {
                send_error(seq, "invalid display mode or incompatible PL");
                return;
            }
            g_auto_display = (uint8_t)(mode == 0u);
            send_packet(K_DISPLAY_MODE_ACK, seq, &mode, 1u);
        } else if (kind == K_DISPLAY_PAGE) {
            uint8_t shown[8];
            uint32_t packed = 0u;
            if (size != 8u || !g_seg_ok || R32(SEG_PAGE_ID) != SEG_PAGE_V1) {
                send_error(seq, "page requires 8 ASCII cells and PAG1 PL");
                return;
            }
            for (i = 0; i < 8; ++i) {
                uint8_t c = g_buf[HEADER_SIZE + i];
                if (c != ' ' && (c < '0' || c > '9')) {
                    send_error(seq, "page accepts digits or spaces only");
                    return;
                }
                packed = (packed << 4) | (c == ' ' ? 15u : (uint32_t)(c - '0'));
            }
            W32(SEG_CTRL, SEG_CTRL_BLANK);
            W32(SEG_VALUE, packed);
            W32(SEG_CTRL, SEG_CTRL_FMT_PAGE);
            v = R32(SEG_VALUE);
            if (v != packed || R32(SEG_CTRL) != SEG_CTRL_FMT_PAGE) {
                send_error(seq, "page register readback mismatch");
                return;
            }
            for (i = 0; i < 8; ++i) {
                uint32_t nib = (v >> (4u * (7u - (uint32_t)i))) & 15u;
                shown[i] = (uint8_t)(nib == 15u ? ' ' : '0' + nib);
            }
            send_packet(K_DISPLAY_PAGE_ACK, seq, shown, sizeof(shown));
        } else if (kind == K_HELLO) {
            send_packet(K_HELLO_ACK, seq, IDENTITY, (uint16_t)sizeof(IDENTITY));
        } else if (kind == K_IMAGE) {
            if (size != IMG_PIXELS) {
                send_error(seq, "image must be exactly 784 bytes");
                return;
            }
            if (acc_infer(&g_buf[HEADER_SIZE], logits) != 0) {
                if (g_auto_display) seg_show_digit(-1);
                send_error(seq, "accelerator error");
                return;
            }
            d = argmax10(logits);
            if (g_auto_display) seg_show_digit(d);
            reply[0] = (uint8_t)d;
            for (i = 0; i < 10; ++i) {
                uint32_t score = (uint32_t)logits[i];
                reply[1+i*4+0] = (uint8_t)score;
                reply[1+i*4+1] = (uint8_t)(score >> 8);
                reply[1+i*4+2] = (uint8_t)(score >> 16);
                reply[1+i*4+3] = (uint8_t)(score >> 24);
            }
            send_packet(K_SCORES, seq, reply, sizeof(reply));
        } else if (kind == K_DISPLAY) {
            if (size != 4u) {
                send_error(seq, "display payload must be 4 bytes");
                return;
            }
            v = (uint32_t)g_buf[HEADER_SIZE]
              | ((uint32_t)g_buf[HEADER_SIZE+1] << 8)
              | ((uint32_t)g_buf[HEADER_SIZE+2] << 16)
              | ((uint32_t)g_buf[HEADER_SIZE+3] << 24);
            if (!g_seg_ok || v > 99u) {
                send_error(seq, "display value must be 0..99 with SEG1 PL");
                return;
            }
            seg_set_value(v);
            v = R32(SEG_VALUE);
            reply[0] = (uint8_t)v;
            reply[1] = (uint8_t)(v >> 8);
            reply[2] = (uint8_t)(v >> 16);
            reply[3] = (uint8_t)(v >> 24);
            send_packet(K_DISPLAY_ACK, seq, reply, 4u);
        } else {
            send_error(seq, "unknown packet kind");
        }
}

int main(void)
{
    uint8_t kind;
    uint32_t seq;
    uint16_t size;
    uart_init();
    acc_init();
    seg_init();

    uart_puts("\r\nEES331-BENCH-FW v3  seg=");
    uart_put_dec((uint32_t)g_seg_ok);
    uart_puts(" magic=");
    uart_put_hex32(R32(SEG_MAGIC_R));
    uart_puts("\r\n");
    seg_selftest_blink();                               /* 亮一下自检，然后全灭 */

    for (;;) {
    int rc = recv_packet(&kind, &seq, &size);
        if (rc != 0) {
            if (rc == -2) send_error(0u, "bad version");
            else if (rc == -3) send_error(0u, "payload too large");
            else if (rc == -4) send_error(0u, "crc mismatch");
            continue;                                   /* 其它 = 噪声，继续等 */
        }

        handle_packet(kind, seq, size);
    }
}
