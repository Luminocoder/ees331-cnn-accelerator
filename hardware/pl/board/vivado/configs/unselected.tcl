# Replace this template only after the lab confirms the board and its revision.
# ps_config is a dictionary of CONFIG.PCW_* properties from reviewed vendor PS7 data.
# Do not copy DDR timing or MIO settings from another 7020 board.
set board_config [dict create \
    id unselected \
    part xc7z020clg400-1 \
    confirmed 0 \
    provenance {} \
    ps_config {} \
    xdc_files {}]
