# Negative fixture only: stop before packaging even if the validation regresses.
set board_config [dict create id test-incomplete confirmed 1 \
    provenance {Negative test; not a board profile} \
    ps_config [dict create CONFIG.PCW_UIPARAM_DDR_ENABLE 1 CONFIG.PCW_UART1_PERIPHERAL_ENABLE 1]]
proc package_accelerator {args} {
    error {TEST_INCOMPLETE_CONFIG_REACHED_PACKAGING: validation did not reject missing DDR/MIO settings}
}
