# Reproducible Zynq board build

The build packages the current nine RTL sources into a fresh accelerator IP,
then creates PS7 -> AXI Interconnect -> accelerator, plus clock/reset wiring.
It does not open the old GUI project or use its packaged IP. The fixed contract
is 50 MHz, 0x43C00000/64 KiB, IMG_PTR/IMG_DATA input, baked weights and polling.
Only DDR and FIXED_IO are external; AXI is not exported as physical FPGA pins.

## Before selecting a board

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-board.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin
```

The default `prepare` stage packages IP, validates the Block Design, verifies
active-low PS reset propagation, saves the project and exports BD Tcl. PS7
defaults are placeholders for graph validation only. No implementation,
Bitstream, XSA, DDR/UART hardware operation or hardware readiness is claimed.
The default part remains `xc7z020clg400-1`.

## Review-only EES-331 candidate

`configs/ees331-candidate.tcl` explicitly transcribes the teaching PS7 XML,
plus UART1/MIO48-49 from the manual. On 2026-10-21 the user confirmed the
physical board as EES-331 Rev1.0 and that this manual matches it. The target
`xc7z020clg484-1` is therefore based on the matching manual, not JTAG alone.
The historical candidate remains `confirmed=0`; the approved offline baseline
is a separate file described below. Legacy DDR delays are not measured board data. Do not use this
file for hardware or merely flip its confirmation. No further chip photo or
board-identity confirmation is required; CLG400 remains a historical placeholder.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-board.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin `
  -Stage prepare -Config board/vivado/configs/ees331-candidate.tcl
```

Prepare now applies any supplied PS profile even when unconfirmed, while final
stages still reject it. After BD validation, all requested properties (including
the fixed internal contract) are read back into `ps-config-audit.tcl`. Boolean
spelling and floating-point rounding are normalized; mismatches retain evidence
and fail. This checks tool configuration, not DDR operation or board identity.
The candidate passed prepare with 46 audited properties; see
`../../docs/verification/ees331-candidate-prepare-2026-10-17.md`.

The workflow also exports `ps-config-audit.tsv` (requested/effective/status)
and `ps-config-effective.tsv` (all PS7 CONFIG.PCW_* properties). TSV fields are
quoted, including embedded tabs/newlines. These new exports have independent
Tcl coverage. On 2026-10-21 real Vivado candidate prepare passed: 46 audited
properties matched, the full snapshot contained 876 unique properties, and all
41 profile properties passed source/readback comparison. These new exports are
now verified for the candidate; default CLG400 revalidation was not run this time.
The profile remains unconfirmed and no hardware readiness is implied. See
`../../docs/verification/ees331-prepare-rerun-2026-10-21.md`.

The generated `system-export.tcl` is diagnostic evidence tied to a particular
run's IP repository and weight paths. Rebuild using `build.tcl`, not that export.

## After the lab confirms the board

### Frozen EES-331 Rev1.0 baseline v1 (2026-10-21)

The user relayed team approval and authorized freeze/synthesis. Use
`configs/ees331-rev1-v1.tcl` (`confirmed=1`, `xc7z020clg484-1`). All 41 explicit
PS values are identical to the reviewed candidate; the build adds the same five
internal-contract properties. This approves an offline-build baseline, not
DDR stability or hardware readiness. The teaching example's DDR delays remain
unmeasured; hardware initialization and bounded memory tests are still required.
No UART packet-protocol freeze or shared-interface change is included.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-board.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin `
  -Stage synth -Config board/vivado/configs/ees331-rev1-v1.tcl
```

Approval/provenance: [frozen handoff](../../docs/handoffs/ees331-freeze-handoff-v1-2026-10-21.md).
Run evidence and remaining gates: [synthesis record](../../docs/verification/ees331-frozen-synth-2026-10-21.md).

The same hardware baseline also completed `impl` on 2026-10-21. At 50 MHz,
routed WNS is +4.497 ns and WHS +0.027 ns, with no negative total slack.
Full default/bitstream DRC has no Error/Critical Warning, but 109 Warnings remain.
At that point the AXI bridge's BRAM asynchronous-control risk remained open;
that implementation did not authorize a board-ready release. No bit/XSA was generated.
See the [routed risk review](../../docs/verification/ees331-routed-risk-review-2026-10-21.md).

On 2026-10-22 C fixed the BRAM-facing reset registers without changing normal
AXI cycle timing or B's accelerator. Fresh regression, reset recovery and full
implementation passed: REQP-1839=0 at synthesis and route, final WNS +3.665 ns,
WHS +0.019 ns, no negative total slack. The remaining 41 DRC Warnings are DSP
recommendations and verified unused weight-port checks. No bit/XSA or board
test was performed; see the [reset fix record](../../docs/verification/ees331-bram-reset-fix-2026-10-22.md).

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-board.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin `
  -Stage impl -Config board/vivado/configs/ees331-rev1-v1.tcl
```

### Adding another reviewed configuration

Add a reviewed configuration under `board/vivado/configs/`, based on
`unselected.tcl`, recording:

- `id`: actual board model and revision, not `unselected`.
- `part`: exact Vivado chip part.
- `confirmed`: 1 only after reviewing board documents.
- `provenance`: vendor document/example name and revision.
- `ps_config`: dictionary of CONFIG.PCW_* settings from the matching vendor
  PS7 configuration, including DDR type/timing/width, PS input clock, MIO,
  UART enable/pins and bank voltages. Enabling DDR alone is not configuration.
- `xdc_files`: list of repository-relative constraints, or an empty list when
  only dedicated PS I/O is used and the reviewed design needs no PL constraints.
- `board_part`: optional installed Vivado board identifier. This records board
  identity but does NOT automatically apply a PS preset; ps_config is required.

Do not reuse another board's DDR/MIO defaults. Board config and required XDC
must be kept in the repository so they are included in the source mirror and
hash manifest. A confirmation flag alone never establishes hardware readiness.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-board.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin `
  -Stage bitstream -Config board/vivado/configs/lab-board.tcl
```

`lab-board.tcl` above is a future filename, not an existing usable preset.

| Stage | Output and completion boundary |
|---|---|
| prepare | Packaged IP and validated graph; does not imply board readiness |
| synth | Configured BD output products/wrapper and synthesis reports |
| impl | Routed design, setup/hold timing, DRC, resources and estimated power |
| bitstream | Above plus system.bit and fixed hardware handoff system.xsa |

Every invocation uses a fresh workspace and rebuilds prerequisites. Final
stages refuse missing/unconfirmed configurations. Confirmed profiles require
explicit DDR timing/geometry/training/delays, PS clocks, bank voltages and both
UART enable flags. An enabled UART needs a MIO route, baud rate and disabled
modem signals; this build does not wire an EMIO UART. Completeness checks do not
validate board identity or electrical correctness. Routed setup/hold timing,
Error/Critical Warning DRC and untruncated DRC output are gates.
Any REQP-1839 asynchronous BRAM-control violation is also a hard gate, even
when Vivado labels it only Warning. Its native audit count is recorded as
`async_bram_controls`; no waiver or severity reduction can bypass this gate.
Native Vivado bitstream checks remain enabled; no DRC severity is downgraded.
Critical Warnings also fail the PowerShell workflow. Remaining ordinary
warnings must be reviewed, not silently treated as hardware validation.

`implementation-checks.tcl` expands the four known limited DSP/BRAM rule reports
to 100000 messages/names, ignores waivers and refuses a CHECK-3 report-limit result.
The routed report includes both default and bitstream rule decks, plus separate
setup/hold/unconstrained-path, route-status, CDC and methodology reports.
DRC severity counts and actual violation names are saved in native Tcl audit files.
No severity or IS_ENABLED property is weakened. Other warning-only DRC results
can pass the tool gate while an engineering risk remains open; consult the risk
review before release.

`scripts/review-routed-memory.ps1 -WorkRoot <ASCII-root> -VivadoBin <bin> -Checkpoint <routed.dcp>`
opens a checkpoint read-only and archives BRAM port connections. It asserts that
the baked-weight A port is disabled, all writes are grounded and B-port cascade
addresses are signals, recording the unchanged input checkpoint SHA-256.
This is not a memory functional test, INIT-content verification or hardware test.

Build logs, manifest, reports, build-info, PS readback audit and BD export are archived under
`metrics/logs/`. Projects/IP/cache/checkpoints/bit/xsa stay in the ASCII work
directory printed by the wrapper. `report_power` is an estimate, not measured
board power. Check the linked run record for actual verified stages;
implementation/bitstream and hardware tests are not implied by synthesis.

Direct Tcl usage from an ASCII source mirror:

```text
vivado -mode batch -source board/vivado/build.tcl -tclargs prepare <fresh-output-directory>
```

For final stages append the configuration Tcl path. Direct invocation refuses
an existing output directory and returns nonzero on configuration/build errors.
