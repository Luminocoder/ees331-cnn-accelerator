[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [string]$VivadoBin = '',
    [ValidateSet('prepare', 'synth', 'impl', 'bitstream')][string]$Stage = 'prepare',
    [string]$Config = '',
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 3600
)
. (Join-Path $PSScriptRoot 'workflow-common.ps1')
$repo = Split-Path -Parent $PSScriptRoot
if ($Stage -ne 'prepare' -and -not $Config) {
    throw 'BOARD_CONFIG_REQUIRED: supply a reviewed configuration under board/vivado/configs/.'
}
$run = New-WorkflowRun -RepoRoot $repo -WorkRoot $WorkRoot -Kind "board-$Stage"
$status = 'FAIL'
$detail = ''
$timer = [Diagnostics.Stopwatch]::StartNew()
try {
    $tool = Resolve-WorkflowTool 'vivado.bat' $VivadoBin
    $arguments = @('-mode', 'batch', '-notrace', '-source', (Join-Path $run.Source 'board/vivado/build.tcl'),
                   '-tclargs', $Stage, (Join-Path $run.Root 'board-output'))
    if ($Config) {
        $configPath = [IO.Path]::GetFullPath((Join-Path $repo $Config))
        $configRoot = [IO.Path]::GetFullPath((Join-Path $repo 'board/vivado/configs')) + '\'
        if (-not $configPath.StartsWith($configRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Config must be a repository-relative file under board/vivado/configs/.'
        }
        $arguments += Join-Path $run.Source $Config
    }
    $marker = if ($Stage -eq 'prepare') { 'BOARD_PREPARE_PASS' } else { "BOARD_BUILD_PASS: $Stage" }
    Write-Host "Run directory: $($run.Root)"
    Invoke-WorkflowProcess -Tool $tool -Arguments $arguments -WorkingDirectory $run.Source `
        -Log (Join-Path $run.Artifacts 'board-build.log') -TimeoutSeconds $TimeoutSeconds -PassPattern $marker
    $status = 'PASS'
} catch { $detail = $_.Exception.Message; Write-Host "BOARD_WORKFLOW_FAIL: $detail" }
finally {
    $timer.Stop()
    Save-WorkflowReports $run
    foreach ($name in @('build-info.txt', 'system-export.tcl', 'ps-config-audit.tcl',
                        'ps-config-audit.tsv', 'ps-config-effective.tsv',
                        'drc-audit-post-synth.tcl', 'drc-audit-post-route.tcl')) {
        $path = Join-Path $run.Root "board-output/$name"
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $run.Artifacts }
    }
    [ordered]@{ stage = $Stage; status = $status; config = $Config; seconds = $timer.Elapsed.TotalSeconds;
        run_directory = $run.Root; detail = $detail; hardware_verified = $false
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $run.Artifacts 'summary.json') -Encoding UTF8
}
Write-Host "Evidence: $($run.Artifacts)"
if ($status -ne 'PASS') { exit 1 }
Write-Host "BOARD_WORKFLOW_PASS: $Stage (not hardware verification)"
