# Automated preboard workflows

PC camera input: see [DECXIN photo, preprocessing and UART](mnist-camera-input.md).
This is a PC-side tool with a draft serial protocol, not a completed board receiver.

Run from the repository root with Windows PowerShell 5.1. Supply an ASCII work
directory outside the repository and the Vivado `bin` directory. Each invocation
creates a new source mirror; it never edits the authoritative model or RTL and
never overwrites an older run. The work drive needs space for Vivado outputs.
ExecutionPolicy Bypass below applies only to that process, not system policy.

## Full regression

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run-regression.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin `
  -AllowLegacyModelLayout
```

The default stages are `golden`, `axi`, `soc`, `elab`, and `synth`. To select a
subset inside PowerShell, use the script directly:

```powershell
& ./scripts/run-regression.ps1 -WorkRoot D:/aic/workflow-runs `
  -VivadoBin D:/2025.1/Vivado/bin -Stages axi,soc
```

Python is discovered from PATH, or from the supplied Vivado installation.
Use `-PythonExe` to select a specific Python. Install NumPy into that Python
using `scripts/requirements-regression.txt`; installation is never automatic.
`-PythonPath` optionally points to an existing dependency directory.

The legacy-layout switch explicitly permits restoring `weights_fp32/` from the
existing root-level `.npy` files in the mirror. If `scales.txt` is missing, the
existing quantization script regenerates it in another disposable directory;
all four regenerated INT8 weight byte arrays must equal the frozen weights.
The model bias must equal the baked BRAM bias, and generated predictions/logits
must equal the frozen files byte-for-byte and `golden_all.hex` numerically.
Without the switch, incomplete model layout fails. This adapter does not close
teammate A's repository-layout task or change quantization/network definitions.

AXI and SoC stages use Vivado XSim and the existing Testbenches. The SoC stage
requires all 20 distinct per-image bit-exact markers, not merely a final PASS.
The AXI stage also instantiates the unchanged feature-map BRAM and checks reset
assertions 2/9/19 ns after a rising clock, pending writes, unpaired AW/W,
stalled responses and software-reset pulses. BRAM-facing controls must change
only on rising clocks; a write already issued before reset may complete once
on the next clock. Reset is not a BRAM erase or transactional rollback.
An AXI VCD is archived even when the test fails. With Pillow installed, run
`python scripts/render-axi-reset.py metrics/logs/<regression-id>` to audit the
trace and render `axi-reset-boundary.png`; detected off-clock changes return 1.
The SoC stage additionally enables `RESET_STRESS`: abort a real inference with
global reset, reload an image, soft-reset without clearing it, and compare ten
results before the normal 20-image batch. This is behavioral RTL verification,
not a post-route simulation, ARM execution or hardware test.
Nonzero exits, timeouts, FAIL/ERROR and Critical Warning markers fail a stage.
Later selected stages still run, but any failed stage makes the overall exit
code 1. A subset passing does not mean the omitted stages passed.

`-TimeoutSeconds` limits each tool process. Timeout terminates that process tree.
Raw tool output, source SHA-256 manifest, reports and `summary.json` are copied to
`metrics/logs/<unique-run-id>/`. WDB, projects, checkpoints and caches remain in
the work directory. Work directories are retained for diagnosis, not auto-deleted.

## Complete system waveform

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run-system-waveform.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin
```

Python must have Pillow installed (`scripts/requirements-waveform.txt`); use
`-PythonExe` to select that interpreter. Dependencies are never auto-installed.
This workflow records one complete image through the real SoC and accelerator,
including six layers, FC2, DONE and AXI readback. It reuses the existing SoC
Testbench without changing RTL/model behavior and stops after img00 is checked.
It is not the 20-image regression, ARM instruction simulation or a board test.

Outputs under `metrics/logs/<run-id>/`: `system-full.wdb`, four grouped WCFG views,
four PNG previews rendered from actual VCD transitions, `events.csv`,
`timing-summary.json`, `viewing-guide.md`, source hashes and raw logs. Unlike
the ordinary regression, this waveform workflow also archives its WDB/WCFG.
Both copies are retained: the ASCII work directory and the repository evidence
folder. Keep WDB/WCFG together when moving them. WDB/WCFG are ignored by Git;
include them explicitly in the team's file-exchange package.

PASS requires 784 checked input writes, six real START/DONE pairs, per-layer
write counts, ten bit-exact result writes/readbacks, native WDB/WCFG reopening,
signed result display and waveform-parser self-tests. The RTL error output is
currently constant zero; this run does not validate an error-handling path.

See `../docs/verification/system-waveform-2026-10-12.md` for the verified capture
and Chinese viewing instructions.

## Script self-tests

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/test-workflows.ps1
```

Checks cover syntax, missing completion markers, false PASS, duplicate/missing
samples, compiler file lists, unsafe workspace paths, nonzero exits, timeout and
the missing-board-configuration gate. Fixture logs are retained in TEMP and
archived with the result under `metrics/logs/<unique-selftest-id>/`.

## ARM driver host mock

With an already installed PC GCC, test the unchanged ARM driver using host-only
MMIO replacements, without Vitis, a BSP, a board or additional downloads:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/test-arm-driver-host.ps1 `
  -WorkRoot D:/aic/workflow-runs -Compiler D:/programming/mingw64/bin/gcc.exe
```

Omit `-Compiler` to resolve `gcc.exe` from PATH. This must be a host compiler,
not an ARM/MicroBlaze/RISC-V cross compiler. The wrapper copies the unchanged
driver into the isolated source mirror and adds its hash to the manifest.
The tests check IMG_PTR/784 IMG_DATA writes, quantization, START/status handling,
signed result reads, argmax, 20 sequential input transfers and bad-input rejection.
Scores are injected golden values: there is no CNN computation or ARM execution.

A host watchdog safely interrupts stuck-BUSY polling. The current driver has
no polling timeout, so the result is `TESTS_PASS_WITH_KNOWN_GAPS`, not a complete
driver pass. DONE/ERROR priority is also recorded for owner review. Exit 0 means
the suite ran successfully, including known-gap detection; inspect `summary.json`.
Logs, compiler version and hashes are archived under `metrics/logs/`; binaries
remain in the isolated work directory. No A/B source or shared API is modified.
See `../docs/verification/arm-driver-host-2026-10-17.md` for evidence and handoff.

## Board entry

Use `scripts/build-board.ps1`; see `../board/vivado/readme.md` for stages and the
reviewed board configuration contract. The old
`vivado_fix_packaged_ip_and_synth.tcl` is a legacy repair script for the external
GUI project, not an input to either new workflow.

PS profile application/readback and the unconfirmed-candidate bitstream gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/test-board-config.ps1 `
  -WorkRoot D:/aic/workflow-runs -VivadoBin D:/2025.1/Vivado/bin
```

An independent Tcl interpreter can run the PS unit tests and the two pre-build
rejection gates without opening Vivado. It does not validate a real Block Design:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/test-board-config.ps1 `
  -WorkRoot D:/aic/workflow-runs -TclShell 'C:/Program Files/Git/mingw64/bin/tclsh.exe'
```

For the reviewed EES-331 teaching archive, use a structured XML/Tcl source comparison:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/review-ees331-ps.ps1 `
  -Archive '<absolute-path-to-uart.rar>' -TclShell 'C:/Program Files/Git/mingw64/bin/tclsh.exe'
```

The archive fingerprint must match the reviewed source. All 41 candidate fields
are checked; the only allowed differences are the three documented UART1 settings.
Optional `-Readback <ps-config-audit.tsv>` checks a real prepare audit against this
candidate; without it the result explicitly says `readback_checked=false`.
Source equality is not electrical validation, DDR training or board confirmation.
No packages are installed and no board operations occur. Raw tables, hashes and
JSON summaries are saved under `metrics/logs/`.

Audit comparison fixtures (not a real Vivado readback):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/tests/test-ees331-review.ps1 `
  -Archive '<absolute-path-to-uart.rar>' -TclShell 'C:/Program Files/Git/mingw64/bin/tclsh.exe' `
  -ProfileTable '<source-review-evidence>/candidate-profile.tsv'
```

Offline ARM tool discovery (no board, installation or PATH changes):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-board-toolchain.ps1 `
  -InstallRoot D:/2025.1
```

It checks PATH plus `gnu/` and `Vitis/` within the selected installation, or an
explicit `-ArmBin`. Missing tools return 1 and a JSON record lists the scope and
missing executables. Located files alone do not prove BSP/ELF compilation.
See `../board/vitis/readme.md` for the remaining XSA and hardware prerequisites.
