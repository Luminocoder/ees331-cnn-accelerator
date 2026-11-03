[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [string]$VivadoBin = '',
    [string]$PythonExe = 'python.exe',
    [ValidateRange(1, 86400)][int]$TimeoutSeconds = 1800
)
. (Join-Path $PSScriptRoot 'workflow-common.ps1')
$repo = Split-Path -Parent $PSScriptRoot
$run = New-WorkflowRun -RepoRoot $repo -WorkRoot $WorkRoot -Kind 'system-waveform'
$cwd = Join-Path $run.Root 'soc'
$status = 'FAIL'
$detail = ''
Write-Host "Run directory: $($run.Root)"
Write-Host "Evidence: $($run.Artifacts)"
try {
    New-Item -ItemType Directory -Path $cwd | Out-Null
    Copy-Item -LiteralPath (Join-Path $run.Source 'ref_model') -Destination $cwd -Recurse
    $python = Resolve-WorkflowTool $PythonExe
    Invoke-WorkflowProcess -Tool $python `
        -Arguments @((Join-Path $run.Source 'scripts/tests/test-system-waveform.py')) `
        -WorkingDirectory $run.Source -Log (Join-Path $run.Artifacts 'waveform-selftest.log') `
        -TimeoutSeconds $TimeoutSeconds -PassPattern '\bOK\b'
    $rtl = @('rtl/accelerator/buffers.v', 'rtl/accelerator/mac_array.sv', 'rtl/accelerator/controller.v',
             'rtl/accelerator/conv_engine.sv', 'rtl/accelerator/fc_engine.sv', 'rtl/accelerator/pool_engine.sv',
             'rtl/accelerator/cnn_accel_top.sv', 'rtl/soc/axi_if_v2.v', 'rtl/soc/mnist_soc_top.sv',
             'rtl/soc/mnist_soc_top_tb.sv', 'sim/system_waveform_tb.sv')
    Invoke-WorkflowProcess -Tool (Resolve-WorkflowTool 'xvlog.bat' $VivadoBin) `
        -Arguments (@('--sv') + @($rtl | ForEach-Object { Join-Path $run.Source $_ })) `
        -WorkingDirectory $cwd -Log (Join-Path $run.Artifacts 'xvlog.log') -TimeoutSeconds $TimeoutSeconds
    Invoke-WorkflowProcess -Tool (Resolve-WorkflowTool 'xelab.bat' $VivadoBin) `
        -Arguments @('system_waveform_tb', '-s', 'system_waveform_snapshot', '-debug', 'all') `
        -WorkingDirectory $cwd -Log (Join-Path $run.Artifacts 'xelab.log') -TimeoutSeconds $TimeoutSeconds
    Invoke-WorkflowProcess -Tool (Resolve-WorkflowTool 'xsim.bat' $VivadoBin) `
        -Arguments @('system_waveform_snapshot', '-wdb', 'system-full.wdb', '-onfinish', 'stop',
                     '-tclbatch', (Join-Path $run.Source 'scripts/system-waveform.tcl').Replace('\', '/')) `
        -WorkingDirectory $cwd -Log (Join-Path $run.Artifacts 'xsim.log') -TimeoutSeconds $TimeoutSeconds `
        -PassPattern '\[PASS\] system waveform: 784 input writes, six real layers, 10/10 bit-exact results and AXI readback' `
        -ExpectedImages 1
    foreach ($name in @('system-full.wdb', 'system-overview.wcfg', 'system-detail.wcfg',
                       'system-input.wcfg', 'system-fc2-output.wcfg', 'system-overview.vcd', 'events.csv')) {
        $path = Join-Path $cwd $name
        if (-not (Test-Path -LiteralPath $path) -or (Get-Item -LiteralPath $path).Length -eq 0) {
            throw "Missing or empty waveform artifact: $name"
        }
        Copy-Item -LiteralPath $path -Destination $run.Artifacts
    }
    Invoke-WorkflowProcess -Tool $python `
        -Arguments @((Join-Path $run.Source 'scripts/summarize-system-waveform.py'), $run.Artifacts) `
        -WorkingDirectory $run.Source -Log (Join-Path $run.Artifacts 'waveform-check.log') `
        -TimeoutSeconds $TimeoutSeconds -PassPattern 'SYSTEM_WAVEFORM_ARTIFACTS_PASS'
    Invoke-WorkflowProcess -Tool (Resolve-WorkflowTool 'vivado.bat' $VivadoBin) `
        -Arguments @('-mode', 'batch', '-source',
                     (Join-Path $run.Source 'scripts/check-system-waveform.tcl').Replace('\', '/'),
                     '-tclargs', $run.Artifacts.Replace('\', '/')) `
        -WorkingDirectory $cwd -Log (Join-Path $run.Artifacts 'waveform-reopen.log') `
        -TimeoutSeconds $TimeoutSeconds -PassPattern 'SYSTEM_WAVEFORM_REOPEN_PASS'
    $status = 'PASS'
} catch {
    $detail = $_.Exception.Message
    throw
} finally {
    [ordered]@{ status = $status; detail = $detail; run_directory = $run.Root;
        capture_images = 1; clock_period_ns = 20; actual_arm_execution = $false;
        actual_hardware_verification = $false; artifacts = $run.Artifacts
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $run.Artifacts 'summary.json') -Encoding UTF8
}
Write-Host 'SYSTEM_WAVEFORM_PASS (one complete image; not hardware verification)'
