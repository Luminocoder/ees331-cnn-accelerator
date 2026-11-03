/* link.ld —— 与原 app.elf 一致的加载地址（DDR 0x00100000） */
ENTRY(_start)

MEMORY
{
    DDR : ORIGIN = 0x00100000, LENGTH = 0x00100000
}

PHDRS { text PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }

SECTIONS
{
    .text : {
        *(.vectors)
        *(.text*)
        *(.rodata*)
    } > DDR :text

    .data : {
        *(.data*)
    } > DDR :data

    .bss (NOLOAD) : {
        _bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > DDR :data

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }

    _stack_top = ORIGIN(DDR) + LENGTH(DDR);
    ASSERT(_bss_end <= _stack_top - 0x10000, "Benchmark image must leave at least 64 KiB stack in the cacheable section")
}
