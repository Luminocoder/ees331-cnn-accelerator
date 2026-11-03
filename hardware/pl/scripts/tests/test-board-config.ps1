[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [string]$VivadoBin = '',
    [string]$TclShell = ''
)
. (Join-Path $PSScriptRoot '../workflow-common.ps1')
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$run = New-WorkflowRun -RepoRoot $repo -WorkRoot $WorkRoot -Kind 'ps-config-tests'
$status = 'FAIL'
$detail = ''
try {
    $prefix = @()
    $argumentSeparator = @()
    if ($TclShell) {
        $tool = (Resolve-Path -LiteralPath $TclShell).Path
    } else {
        $tool = Resolve-WorkflowTool 'vivado.bat' $VivadoBin
        $prefix = @('-mode', 'batch', '-notrace', '-source')
        $argumentSeparator = @('-tclargs')
    }
    $incompleteLog = Join-Path $run.Artifacts 'incomplete-config-gate.log'
    $incompleteOutput = Join-Path $run.Root 'incomplete-must-not-build'
    $incompleteRejected = $false
    try {
        Invoke-WorkflowProcess -Tool $tool -Arguments ($prefix + @(
            (Join-Path $run.Source 'board/vivado/build.tcl')) + $argumentSeparator + @('bitstream', $incompleteOutput,
            (Join-Path $run.Source 'scripts/tests/incomplete-board-config.tcl'))) `
            -WorkingDirectory $run.Source -Log $incompleteLog -TimeoutSeconds 180
    } catch {
        $incompleteRejected = (Test-Path -LiteralPath $incompleteLog) -and
            ((Get-Content -LiteralPath $incompleteLog -Raw) -match 'BOARD_PS_CONFIG_INCOMPLETE')
    }
    if (-not $incompleteRejected -or (Test-Path -LiteralPath $incompleteOutput)) {
        throw 'Incomplete DDR/MIO profile was not rejected before creating build outputs.'
    }
    Invoke-WorkflowProcess -Tool $tool -Arguments ($prefix + @(
        (Join-Path $run.Source 'scripts/tests/test-ps-config.tcl'))) -WorkingDirectory $run.Source `
        -Log (Join-Path $run.Artifacts 'ps-config-tests.log') -PassPattern 'PS_CONFIG_TESTS_PASS' -TimeoutSeconds 180
    Invoke-WorkflowProcess -Tool $tool -Arguments ($prefix + @(
        (Join-Path $run.Source 'scripts/tests/test-implementation-checks.tcl'))) -WorkingDirectory $run.Source `
        -Log (Join-Path $run.Artifacts 'implementation-checks-tests.log') -PassPattern 'IMPLEMENTATION_CHECKS_PASS' -TimeoutSeconds 180
    Invoke-WorkflowProcess -Tool $tool -Arguments ($prefix + @(
        (Join-Path $run.Source 'scripts/tests/test-hardware-handoff.tcl'))) -WorkingDirectory $run.Source `
        -Log (Join-Path $run.Artifacts 'hardware-handoff-tests.log') -PassPattern 'HARDWARE_HANDOFF_TESTS_PASS' -TimeoutSeconds 180
    $gateLog = Join-Path $run.Artifacts 'unconfirmed-gate.log'
    $rejected = $false
    try {
        Invoke-WorkflowProcess -Tool $tool -Arguments ($prefix + @(
            (Join-Path $run.Source 'board/vivado/build.tcl')) + $argumentSeparator + @('bitstream',
            (Join-Path $run.Root 'must-not-build'),
            (Join-Path $run.Source 'board/vivado/configs/ees331-candidate.tcl'))) `
            -WorkingDirectory $run.Source -Log $gateLog -TimeoutSeconds 180
    } catch {
        $rejected = (Test-Path -LiteralPath $gateLog) -and
            ((Get-Content -LiteralPath $gateLog -Raw) -match 'BOARD_CONFIG_REQUIRED')
    }
    if (-not $rejected -or (Test-Path -LiteralPath (Join-Path $run.Root 'must-not-build'))) {
        throw 'Unconfirmed candidate was not rejected at the pre-build gate.'
    }
    $status = 'PASS'
} catch { $detail = $_.Exception.Message; Write-Host "PS_CONFIG_WORKFLOW_FAIL: $detail" }
finally {
    [ordered]@{ status = $status; run_directory = $run.Root; detail = $detail;
        tcl_shell = $TclShell; hardware_verified = $false } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run.Artifacts 'summary.json') -Encoding UTF8
}
Write-Host "Evidence: $($run.Artifacts)"
if ($status -ne 'PASS') { exit 1 }
Write-Host 'PS_CONFIG_WORKFLOW_PASS: unit checks, incomplete-profile and unconfirmed bitstream gates'
