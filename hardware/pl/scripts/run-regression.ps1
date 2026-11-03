[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [string]$VivadoBin = '',
    [string]$PythonExe = '',
    [string]$PythonPath = '',
    [ValidateSet('golden', 'axi', 'soc', 'elab', 'synth')]
    [ValidateNotNullOrEmpty()]
    [string[]]$Stages = @('golden', 'axi', 'soc', 'elab', 'synth'),
    [switch]$AllowLegacyModelLayout,
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 1800
)
. (Join-Path $PSScriptRoot 'workflow-common.ps1')
$repo = Split-Path -Parent $PSScriptRoot
$run = New-WorkflowRun -RepoRoot $repo -WorkRoot $WorkRoot -Kind 'regression'
$results = @()
$tools = @{}
$started = Get-Date
$failed = $false
Write-Host "Run directory: $($run.Root)"
Write-Host "Evidence: $($run.Artifacts)"
try {
    foreach ($stage in ($Stages | Select-Object -Unique)) {
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $status = 'PASS'
        $detail = ''
        Write-Host "Running $stage ..."
        try {
            if ($stage -eq 'golden') {
                if (-not $PythonExe) {
                    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
                    if ($pythonCommand) { $PythonExe = $pythonCommand.Source }
                    elseif ($VivadoBin) { $PythonExe = Join-Path $VivadoBin '../tps/win64/python-3.13.0/python.exe' }
                    else { throw 'Supply PythonExe, or VivadoBin for its bundled Python.' }
                }
                $tools['python'] = $PythonExe
                $arguments = @((Join-Path $run.Source 'scripts/verify-golden.py'), '--repo', $run.Source)
                if ($AllowLegacyModelLayout) { $arguments += '--allow-legacy-layout' }
                $environment = @{}
                if ($PythonPath) { $environment['PYTHONPATH'] = $PythonPath }
                Invoke-WorkflowProcess -Tool $PythonExe -Arguments $arguments -Environment $environment `
                    -WorkingDirectory $run.Source -Log (Join-Path $run.Artifacts 'golden.log') `
                    -TimeoutSeconds $TimeoutSeconds -PassPattern 'GOLDEN_REGRESSION_PASS: 20/20'
            } elseif ($stage -in @('axi', 'soc')) {
                $top = if ($stage -eq 'axi') { 'axi_if_v2_tb' } else { 'mnist_soc_top_tb' }
                $testbench = "rtl/soc/$top.sv"
                [string[]]$rtl = if ($stage -eq 'axi') { @('rtl/accelerator/buffers.v', 'rtl/soc/axi_if_v2.v') } else {
                    @('rtl/accelerator/buffers.v', 'rtl/accelerator/mac_array.sv', 'rtl/accelerator/controller.v',
                      'rtl/accelerator/conv_engine.sv', 'rtl/accelerator/fc_engine.sv', 'rtl/accelerator/pool_engine.sv',
                      'rtl/accelerator/cnn_accel_top.sv', 'rtl/soc/axi_if_v2.v', 'rtl/soc/mnist_soc_top.sv')
                }
                $cwd = Join-Path $run.Root $stage
                New-Item -ItemType Directory -Path $cwd | Out-Null
                Copy-Item -LiteralPath (Join-Path $run.Source 'ref_model') -Destination $cwd -Recurse
                foreach ($toolName in @('xvlog', 'xelab', 'xsim')) {
                    $tool = Resolve-WorkflowTool "$toolName.bat" $VivadoBin
                    $tools[$toolName] = $tool
                    $arguments = switch ($toolName) {
                        'xvlog' { @('--sv') + @($rtl + $testbench | ForEach-Object { Join-Path $run.Source $_ }) }
                        'xelab' {
                            $elabArguments = @($top, '-s', "${stage}_snapshot")
                            if ($stage -eq 'axi') { $elabArguments += @('-debug', 'typical') }
                            $elabArguments
                        }
                        'xsim' {
                            if ($stage -eq 'axi') {
                                @("${stage}_snapshot", '-onfinish', 'stop', '-tclbatch',
                                  (Join-Path $run.Source 'scripts/axi-reset-waveform.tcl').Replace('\', '/'))
                            } else { @("${stage}_snapshot", '-testplusarg', 'RESET_STRESS', '-runall') }
                        }
                    }
                    $pass = ''
                    $images = 0
                    if ($toolName -eq 'xsim') {
                        if ($stage -eq 'axi') { $pass = '\[PASS\] axi_if_v2 regression passed' }
                        else {
                            $pass = '(?s)\[PASS\] SoC reset recovery: soft reset preserves image, 10/10 bit-exact.*\[PASS\] mnist_soc_top: 20/20 bit-exact'
                            $images = 20
                        }
                    }
                    Invoke-WorkflowProcess -Tool $tool -Arguments $arguments -WorkingDirectory $cwd `
                        -Log (Join-Path $run.Artifacts "$stage-$toolName.log") -TimeoutSeconds $TimeoutSeconds `
                        -PassPattern $pass -ExpectedImages $images
                }
            } else {
                $tool = Resolve-WorkflowTool 'vivado.bat' $VivadoBin
                $tools['vivado'] = $tool
                $scriptName = if ($stage -eq 'elab') { 'vivado_elab_check.tcl' } else { 'vivado_synth_check.tcl' }
                $marker = if ($stage -eq 'elab') { 'VIVADO_ELAB_CHECK_DONE' } else { 'VIVADO_SYNTH_CHECK_DONE' }
                Invoke-WorkflowProcess -Tool $tool `
                    -Arguments @('-mode', 'batch', '-notrace', '-source', (Join-Path $run.Source "scripts/$scriptName")) `
                    -WorkingDirectory $run.Source -Log (Join-Path $run.Artifacts "$stage.log") `
                    -TimeoutSeconds $TimeoutSeconds -PassPattern $marker
            }
        } catch { $status = 'FAIL'; $detail = $_.Exception.Message; $failed = $true }
        $timer.Stop()
        $results += [ordered]@{ stage = $stage; status = $status; seconds = $timer.Elapsed.TotalSeconds; detail = $detail }
        Write-Host "$stage : $status $detail"
    }
} finally {
    Save-WorkflowReports $run
    $resetTrace = Join-Path $run.Root 'axi/axi-reset-boundaries.vcd'
    if (Test-Path -LiteralPath $resetTrace) { Copy-Item -LiteralPath $resetTrace -Destination $run.Artifacts }
    [ordered]@{ started = $started.ToString('o'); finished = (Get-Date).ToString('o');
        run_directory = $run.Root; tools = $tools; legacy_model_layout = [bool]$AllowLegacyModelLayout;
        selected_stages = $Stages; status = $(if ($failed) { 'FAIL' } else { 'PASS' }); stages = $results
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $run.Artifacts 'summary.json') -Encoding UTF8
}
if ($failed) { exit 1 }
Write-Host 'REGRESSION_PASS (selected stages only; not hardware verification)'
