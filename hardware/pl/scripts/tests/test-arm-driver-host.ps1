[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkRoot,
    [string]$Compiler = ''
)
. (Join-Path $PSScriptRoot '../workflow-common.ps1')
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$run = New-WorkflowRun -RepoRoot $repo -WorkRoot $WorkRoot -Kind 'arm-driver-host'
$status = 'FAIL'
$detail = ''
$knownGaps = @()
$doneErrorReturn = $null
$checks = 0
$rejectionChecks = 0
try {
    $tool = if ($Compiler) { (Resolve-Path -LiteralPath $Compiler).Path } else { Resolve-WorkflowTool 'gcc.exe' }
    $driver = Join-Path $repo 'software/arm/driver.c'
    $mirroredDriver = Join-Path $run.Source 'software/arm/driver.c'
    New-Item -ItemType Directory -Path (Split-Path -Parent $mirroredDriver) | Out-Null
    Copy-Item -LiteralPath $driver -Destination $mirroredDriver
    $manifestPath = Join-Path $run.Artifacts 'source-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $manifest += [ordered]@{ path = 'software/arm/driver.c'; sha256 = (Get-FileHash -LiteralPath $mirroredDriver -Algorithm SHA256).Hash }
    foreach ($entry in $manifest) {
        if (-not ($entry.path -is [string]) -or -not ($entry.sha256 -is [string])) {
            throw 'Source manifest must remain a flat list of file hashes.'
        }
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    $executable = Join-Path $run.Root 'arm-driver-host.exe'
    Invoke-WorkflowProcess -Tool $tool -Arguments @('--version') -WorkingDirectory $run.Source `
        -Log (Join-Path $run.Artifacts 'compiler-version.log') -TimeoutSeconds 30
    Invoke-WorkflowProcess -Tool $tool -Arguments @('-std=c11', '-Wall', '-Wextra', '-Werror',
        '-finput-charset=UTF-8', '-fexec-charset=UTF-8', '-I', (Join-Path $run.Source 'scripts/tests/arm-driver-host'),
        (Join-Path $run.Source 'scripts/tests/arm-driver-host.c'), '-o', $executable) `
        -WorkingDirectory $run.Source -Log (Join-Path $run.Artifacts 'compile.log') -TimeoutSeconds 60
    $log = Join-Path $run.Artifacts 'host-tests.log'
    Invoke-WorkflowProcess -Tool $executable -Arguments @(
        (Join-Path $run.Source 'ref_model/test_vectors/img_all.hex'),
        (Join-Path $run.Source 'ref_model/test_vectors/golden_all.hex'),
        (Join-Path $run.Source 'ref_model/test_vectors/labels.txt')) `
        -WorkingDirectory $run.Source -Log $log -TimeoutSeconds 30 -PassPattern 'ARM_DRIVER_HOST_TESTS_PASS:'
    $text = Get-Content -LiteralPath $log -Raw -Encoding UTF8
    if ($text -notmatch 'ARM_DRIVER_HOST_TESTS_PASS: checks=(\d+) known_gaps=(\d+)') {
        throw 'Missing structured host-test summary.'
    }
    $checks = [int]$Matches[1]
    $gapCount = [int]$Matches[2]
    if ($checks + $gapCount -ne 26) { throw 'Unexpected host-test coverage count.' }
    $images = [regex]::Matches($text, '\[PASS\] host image (\d{2}):')
    if ($images.Count -ne 20 -or (@($images | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)).Count -ne 20) {
        throw 'Expected 20 distinct host image-transfer cases.'
    }
    if ($text -match 'ARM_DRIVER_HOST_KNOWN_GAP: no polling timeout;') { $knownGaps += 'no_polling_timeout' }
    if ($gapCount -ne $knownGaps.Count) { throw 'Unrecognized host-test known gap.' }
    if ($text -notmatch 'done_and_error_return=(-?\d+)') { throw 'Missing DONE/ERROR observation.' }
    $doneErrorReturn = [int]$Matches[1]
    $shortImage = Join-Path $run.Root 'short-image.hex'
    '00' | Set-Content -LiteralPath $shortImage -Encoding ASCII
    $badLabels = Join-Path $run.Root 'invalid-labels.txt'
    '10' | Set-Content -LiteralPath $badLabels -Encoding ASCII
    foreach ($fixture in @(
        @{ name = 'truncated-image'; image = $shortImage;
           labels = (Join-Path $run.Source 'ref_model/test_vectors/labels.txt'); reason = 'frozen vector file is truncated' },
        @{ name = 'invalid-label'; image = (Join-Path $run.Source 'ref_model/test_vectors/img_all.hex');
           labels = $badLabels; reason = 'vector token out of range' }
    )) {
        $negativeLog = Join-Path $run.Artifacts ($fixture.name + '.log')
        $rejected = $false
        try {
            Invoke-WorkflowProcess -Tool $executable -Arguments @($fixture.image,
                (Join-Path $run.Source 'ref_model/test_vectors/golden_all.hex'), $fixture.labels) `
                -WorkingDirectory $run.Source -Log $negativeLog -TimeoutSeconds 30
        } catch {
            if (Test-Path -LiteralPath $negativeLog) {
                $negativeText = Get-Content -LiteralPath $negativeLog -Raw -Encoding UTF8
                $rejected = $negativeText.Contains('ARM_DRIVER_HOST_FAIL: ' + $fixture.reason) -and
                    $negativeText.Contains('exit=1')
            }
        }
        if (-not $rejected) { throw "Invalid fixture was not rejected as expected: $($fixture.name)" }
        ++$rejectionChecks
    }
    $status = if ($knownGaps.Count) { 'TESTS_PASS_WITH_KNOWN_GAPS' } else { 'TESTS_PASS' }
} catch { $detail = $_.Exception.Message; Write-Host "ARM_DRIVER_HOST_WORKFLOW_FAIL: $detail" }
finally {
    [ordered]@{ status = $status; checks = $checks; rejection_checks = $rejectionChecks;
        known_gaps = @($knownGaps); detail = $detail;
        done_and_error_return = $doneErrorReturn; run_directory = $run.Root;
        hardware_verified = $false; arm_target_compiled = $false; cnn_computation_tested = $false;
        result_source = 'injected frozen golden registers; unchanged raw img_all.hex pixels'
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $run.Artifacts 'summary.json') -Encoding UTF8
}
Write-Host "Evidence: $($run.Artifacts)"
if ($status -eq 'FAIL') { exit 1 }
Write-Host "ARM_DRIVER_HOST_WORKFLOW: $status (not ARM or hardware verification)"
