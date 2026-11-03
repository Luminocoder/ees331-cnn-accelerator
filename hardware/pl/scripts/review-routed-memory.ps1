[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [Parameter(Mandatory = $true)][string]$Checkpoint,
    [string]$VivadoBin = '',
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 300
)
. (Join-Path $PSScriptRoot 'workflow-common.ps1')
$repo = Split-Path -Parent $PSScriptRoot
$checkpointPath = (Resolve-Path -LiteralPath $Checkpoint -ErrorAction Stop).Path
if ($checkpointPath -match '[^\x20-\x7e]') { throw 'Use an ASCII checkpoint path.' }
$checkpointHash = (Get-FileHash -LiteralPath $checkpointPath -Algorithm SHA256).Hash
$run = New-WorkflowRun -RepoRoot $repo -WorkRoot $WorkRoot -Kind 'routed-memory-review'
$status = 'FAIL'
$detail = ''
try {
    $tool = Resolve-WorkflowTool 'vivado.bat' $VivadoBin
    Invoke-WorkflowProcess -Tool $tool -Arguments @('-mode', 'batch', '-notrace', '-source',
        (Join-Path $run.Source 'board/vivado/review-routed-memory.tcl'), '-tclargs',
        $checkpointPath, (Join-Path $run.Root 'memory-output')) -WorkingDirectory $run.Source `
        -Log (Join-Path $run.Artifacts 'memory-review.log') -TimeoutSeconds $TimeoutSeconds `
        -PassPattern 'MEMORY_REVIEW_COMPLETE'
    if ((Get-FileHash -LiteralPath $checkpointPath -Algorithm SHA256).Hash -ne $checkpointHash) {
        throw 'Source checkpoint changed during review.'
    }
    $status = 'PASS'
} catch { $detail = $_.Exception.Message; Write-Host "MEMORY_REVIEW_WORKFLOW_FAIL: $detail" }
finally {
    Save-WorkflowReports $run
    $nativeAudit = Join-Path $run.Root 'memory-output/memory-review.tcl'
    if (Test-Path -LiteralPath $nativeAudit) { Copy-Item -LiteralPath $nativeAudit -Destination $run.Artifacts }
    [ordered]@{ status = $status; detail = $detail; checkpoint = $checkpointPath;
        checkpoint_sha256 = $checkpointHash; run_directory = $run.Root; hardware_verified = $false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $run.Artifacts 'summary.json') -Encoding UTF8
}
Write-Host "Evidence: $($run.Artifacts)"
if ($status -ne 'PASS') { exit 1 }
Write-Host 'MEMORY_REVIEW_WORKFLOW_PASS: netlist evidence only, not a functional memory test'
