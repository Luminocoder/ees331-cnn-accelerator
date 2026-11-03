/* start.S —— 极简启动代码（无 BSP）
 * 复位后 C 环境准备：关 MMU/Cache、清 .bss、设栈、进 main
 */
    .section .vectors, "ax"
    .global _start
    .arm

_start:
    /* 关 MMU / D-Cache / I-Cache（确保 AXI 访问直通，不被缓存挡住） */
    mrc     p15, 0, r0, c1, c0, 0
    bic     r0, r0, #(1 << 0)          /* M   */
    bic     r0, r0, #(1 << 2)          /* C   */
    bic     r0, r0, #(1 << 12)         /* I   */
    mcr     p15, 0, r0, c1, c0, 0
    isb

    /* 清 .bss */
    ldr     r1, =_bss_start
    ldr     r2, =_bss_end
    mov     r3, #0
1:  cmp     r1, r2
    strlo   r3, [r1], #4
    blo     1b

    /* 设栈 */
    ldr     sp, =_stack_top

    bl      main

2:  b       2b

    .size _start, . - _start
