$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../workflow-common.ps1')
$script:checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:checks++
}
function Must-Fail([scriptblock]$Action, [string]$Message) {
    $caught = $false
    try { & $Action } catch { $caught = $true }
    Check $caught $Message
}
foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot '..') -Filter '*.ps1' -Recurse -File)) {
    $tokens = $null
    $parseErrors = $null
    [void][Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
    Check ($parseErrors.Count -eq 0) "PowerShell parse failed: $($file.Name) $parseErrors"
}
Assert-WorkflowLog 'FIXTURE_PASS' 'FIXTURE_PASS'
Must-Fail { Assert-WorkflowLog 'nothing completed' 'FIXTURE_PASS' } 'Missing PASS was accepted'
Must-Fail { Assert-WorkflowLog "[FAIL] sample`nFIXTURE_PASS" 'FIXTURE_PASS' } 'FAIL before PASS was accepted'
Must-Fail { Assert-WorkflowLog "ERROR: tool error`nFIXTURE_PASS" 'FIXTURE_PASS' } 'Tool error was accepted'
Must-Fail { Assert-WorkflowLog "CRITICAL WARNING: clock mismatch`nFIXTURE_PASS" 'FIXTURE_PASS' } 'Critical warning was accepted'
$imageLog = (@(0..19 | ForEach-Object { '[PASS] img{0:d2}: label=0 argmax=0 (10/10 bit-exact)' -f $_ }) -join "`n")
Assert-WorkflowLog $imageLog '' 20
Must-Fail { Assert-WorkflowLog ($imageLog -replace 'img19', 'img18') '' 20 } 'Duplicate sample was accepted'
Must-Fail { Assert-WorkflowLog $imageLog '' 21 } 'Incomplete batch was accepted'
[string[]]$singleRtl = 'rtl/soc/axi_if_v2.v'
$compileFiles = @($singleRtl + 'rtl/soc/axi_if_v2_tb.sv')
Check ($compileFiles.Count -eq 2) 'Single-source compilation list collapsed into one path'
Must-Fail { New-WorkflowRun 'C:/repo' 'C:/repo/output' 'test' } 'Nested work root was accepted'
Must-Fail { New-WorkflowRun 'C:/repo' 'C:/' 'test' } 'Ancestor work root was accepted'
Must-Fail { New-WorkflowRun 'C:/repo' 'C:/unsafe&path' 'test' } 'Shell metacharacter was accepted'
$scratch = Join-Path $env:TEMP ('workflow-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$fixture = Join-Path $PSScriptRoot 'process-fixture.ps1'
Invoke-WorkflowProcess -Tool $powershell -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $fixture, '-Mode', 'pass') `
    -WorkingDirectory $scratch -Log (Join-Path $scratch 'pass.log') -PassPattern 'FIXTURE_PASS' -TimeoutSeconds 10
Must-Fail {
    Invoke-WorkflowProcess -Tool $powershell -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $fixture, '-Mode', 'fail') `
        -WorkingDirectory $scratch -Log (Join-Path $scratch 'fail.log') -TimeoutSeconds 10
} 'Nonzero exit was accepted'
Must-Fail {
    Invoke-WorkflowProcess -Tool $powershell -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $fixture, '-Mode', 'wait') `
        -WorkingDirectory $scratch -Log (Join-Path $scratch 'timeout.log') -TimeoutSeconds 1
} 'Timeout was accepted'
$boardEntry = Join-Path $PSScriptRoot '../build-board.ps1'
$gateLog = Join-Path $scratch 'board-gate.log'
Must-Fail {
    Invoke-WorkflowProcess -Tool $powershell `
        -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $boardEntry, '-Stage', 'synth', '-WorkRoot', $scratch) `
        -WorkingDirectory $scratch -Log $gateLog -TimeoutSeconds 10
} 'Board synthesis without configuration was accepted'
Check ((Get-Content -LiteralPath $gateLog -Raw) -match 'BOARD_CONFIG_REQUIRED') 'Board entry failed outside the configuration gate'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$evidence = Join-Path $repo ('metrics/logs/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-workflow-selftest-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $evidence | Out-Null
Get-ChildItem -LiteralPath $scratch -File -Filter '*.log' | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $evidence
}
[ordered]@{ status = 'PASS'; checks = $script:checks; finished = (Get-Date).ToString('o'); fixture_directory = $scratch } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding UTF8
Write-Host "WORKFLOW_TESTS_PASS: $script:checks checks; evidence: $evidence"
