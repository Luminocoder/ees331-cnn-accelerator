# EES-331 Rev1.0 offline-build baseline v1, frozen 2026-10-21.
# Team approval relayed by the user; see docs/handoffs/ees331-freeze-handoff-v1-2026-10-21.md.
# Sources: matching EES331_UserManual_v1.0_1220 and teaching uart.rar PS7 XML.
# uart.rar SHA256: 7CA217DFBB96E6A4746495C9D1BF6DEB7F2983071D10CAA99830AC568A8AB28A
# All 41 PS values match ees331-candidate.tcl; only release metadata differs.
# confirmed means approved for offline build, NOT DDR or hardware validation.
# DDR board delays remain legacy example values, NOT measured PCB data.
set board_config [dict create \
    id ees331-rev1-v1 \
    part xc7z020clg484-1 \
    confirmed 1 \
    provenance {EES-331 Rev1.0 and matching manual confirmed by user 2026-10-21; manual 2017.12 v1.0 plus teaching uart.rar PS7 XML; source/readback reviewed 2026-10-21; team approval relayed by user for offline baseline freeze; legacy DDR delays accepted pending hardware memory test; see docs/handoffs/ees331-freeze-handoff-v1-2026-10-21.md} \
    ps_config [dict create \
        CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ 33.333333 \
        CONFIG.PCW_APU_PERIPHERAL_FREQMHZ 666.666666 \
        CONFIG.PCW_PRESET_BANK0_VOLTAGE {LVCMOS 3.3V} \
        CONFIG.PCW_PRESET_BANK1_VOLTAGE {LVCMOS 1.8V} \
        CONFIG.PCW_UIPARAM_DDR_ENABLE 1 \
        CONFIG.PCW_UIPARAM_DDR_MEMORY_TYPE {DDR 3} \
        CONFIG.PCW_UIPARAM_DDR_PARTNO {MT41K256M16 RE-15E} \
        CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH {32 Bit} \
        CONFIG.PCW_UIPARAM_DDR_DRAM_WIDTH {16 Bits} \
        CONFIG.PCW_UIPARAM_DDR_DEVICE_CAPACITY {4096 MBits} \
        CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ 533.333333 \
        CONFIG.PCW_UIPARAM_DDR_ROW_ADDR_COUNT 15 \
        CONFIG.PCW_UIPARAM_DDR_COL_ADDR_COUNT 10 \
        CONFIG.PCW_UIPARAM_DDR_BANK_ADDR_COUNT 3 \
        CONFIG.PCW_UIPARAM_DDR_SPEED_BIN DDR3_1066F \
        CONFIG.PCW_UIPARAM_DDR_CL 7 \
        CONFIG.PCW_UIPARAM_DDR_CWL 6 \
        CONFIG.PCW_UIPARAM_DDR_BL 8 \
        CONFIG.PCW_UIPARAM_DDR_AL 0 \
        CONFIG.PCW_UIPARAM_DDR_T_RCD 7 \
        CONFIG.PCW_UIPARAM_DDR_T_RP 7 \
        CONFIG.PCW_UIPARAM_DDR_T_RC 49.5 \
        CONFIG.PCW_UIPARAM_DDR_T_RAS_MIN 36.0 \
        CONFIG.PCW_UIPARAM_DDR_T_FAW 45.0 \
        CONFIG.PCW_UIPARAM_DDR_ECC Disabled \
        CONFIG.PCW_UIPARAM_DDR_TRAIN_WRITE_LEVEL 1 \
        CONFIG.PCW_UIPARAM_DDR_TRAIN_READ_GATE 1 \
        CONFIG.PCW_UIPARAM_DDR_TRAIN_DATA_EYE 1 \
        CONFIG.PCW_UIPARAM_DDR_BOARD_DELAY0 0.25 \
        CONFIG.PCW_UIPARAM_DDR_BOARD_DELAY1 0.25 \
        CONFIG.PCW_UIPARAM_DDR_BOARD_DELAY2 0.25 \
        CONFIG.PCW_UIPARAM_DDR_BOARD_DELAY3 0.25 \
        CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_0 0.0 \
        CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_1 0.0 \
        CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_2 0.0 \
        CONFIG.PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_3 0.0 \
        CONFIG.PCW_UART0_PERIPHERAL_ENABLE 0 \
        CONFIG.PCW_UART1_PERIPHERAL_ENABLE 1 \
        CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49} \
        CONFIG.PCW_UART1_GRP_FULL_ENABLE 0 \
        CONFIG.PCW_UART1_BAUD_RATE 115200] \
    xdc_files {constraints/ees331_sevenseg.xdc}]
