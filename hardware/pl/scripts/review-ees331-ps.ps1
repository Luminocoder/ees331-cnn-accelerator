[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$TclShell,
    [string]$Readback = ''
)
. (Join-Path $PSScriptRoot 'workflow-common.ps1')
function Test-PsReadbackValue([string]$Expected, [string]$Actual) {
    if ($Expected -ceq $Actual) { return $true }
    $normalized = @{ 'true' = '1'; 'false' = '0' }
    if ($normalized.ContainsKey($Expected)) { $Expected = $normalized[$Expected] }
    if ($normalized.ContainsKey($Actual)) { $Actual = $normalized[$Actual] }
    $expectedNumber = 0.0
    $actualNumber = 0.0
    $style = [Globalization.NumberStyles]::Float
    $culture = [Globalization.CultureInfo]::InvariantCulture
    if ([double]::TryParse($Expected, $style, $culture, [ref]$expectedNumber) -and
        [double]::TryParse($Actual, $style, $culture, [ref]$actualNumber)) {
        return [math]::Abs($expectedNumber - $actualNumber) -le [math]::Max(1e-6, [math]::Abs($expectedNumber) * 1e-6)
    }
    return $false
}
$repo = Split-Path -Parent $PSScriptRoot
$id = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-ees331-source-review-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$evidence = Join-Path $repo "metrics/logs/$id"
New-Item -ItemType Directory -Path $evidence | Out-Null
$status = 'FAIL'
$detail = ''
$rows = @()
$sourceHash = ''
$profile = Join-Path $repo 'board/vivado/configs/ees331-candidate.tcl'
$inputs = @($profile, (Join-Path $repo 'board/vivado/ps-config.tcl'), $PSCommandPath,
            (Join-Path $PSScriptRoot 'export-ps-profile.tcl'))
if ($Readback) { $inputs += $Readback }
try {
    $sourceHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
    if ($sourceHash -ne '7CA217DFBB96E6A4746495C9D1BF6DEB7F2983071D10CAA99830AC568A8AB28A') {
        throw 'Reference archive differs from the reviewed teaching uart.rar; review its provenance first.'
    }
    $member = 'uart/uart.srcs/sources_1/bd/system/ip/system_processing_system7_0_0/ps7_parameters.xml'
    $xmlLines = & tar -xOf $Archive $member
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the reference XML member.' }
    $reference = [xml]($xmlLines -join "`n")
    $legacy = @{}
    foreach ($node in $reference.SelectNodes('//PARAMETER')) {
        if ($legacy.ContainsKey($node.NAME)) { throw "Duplicate reference parameter: $($node.NAME)" }
        $legacy[$node.NAME] = [string]$node.VALUE
    }
    $table = Join-Path $evidence 'candidate-profile.tsv'
    Invoke-WorkflowProcess -Tool (Resolve-Path -LiteralPath $TclShell).Path `
        -Arguments @((Join-Path $PSScriptRoot 'export-ps-profile.tcl'), $profile, $table) `
        -WorkingDirectory $repo -Log (Join-Path $evidence 'profile-export.log') `
        -PassPattern 'PS_PROFILE_EXPORT_PASS: 41 properties; confirmed=0' -TimeoutSeconds 30
    $overrides = @{
        'PCW_UART1_PERIPHERAL_ENABLE' = @('0', '1')
        'PCW_UART1_UART1_IO' = @('', 'MIO 48 .. 49')
        'PCW_UART1_GRP_FULL_ENABLE' = @('', '0')
    }
    $readbacks = @{}
    if ($Readback) {
        foreach ($entry in (Import-Csv -LiteralPath $Readback -Delimiter "`t")) {
            if ($readbacks.ContainsKey($entry.property)) { throw "Duplicate readback: $($entry.property)" }
            $readbacks[$entry.property] = $entry
        }
    }
    $seen = @{}
    foreach ($entry in (Import-Csv -LiteralPath $table -Delimiter "`t")) {
        $key = $entry.property.Substring('CONFIG.'.Length)
        if ($seen.ContainsKey($key)) { throw "Duplicate profile parameter: $key" }
        $seen[$key] = $true
        $result = 'MISMATCH'
        $old = if ($legacy.ContainsKey($key)) { $legacy[$key] } else { $null }
        if ($null -ne $old -and $old -ceq $entry.requested) { $result = 'MATCH' }
        elseif ($overrides.ContainsKey($key) -and $old -ceq $overrides[$key][0] -and
                $entry.requested -ceq $overrides[$key][1]) { $result = 'DOCUMENTED_UART_OVERRIDE' }
        $readbackStatus = 'NOT_CHECKED'
        if ($Readback) {
            $readbackStatus = 'MISSING_OR_MISMATCH'
            if ($readbacks.ContainsKey($entry.property)) {
                $actual = $readbacks[$entry.property]
                if ($actual.requested -ceq $entry.requested -and $actual.status -eq 'MATCH' -and
                    (Test-PsReadbackValue $entry.requested $actual.effective)) {
                    $readbackStatus = 'MATCH'
                }
            }
        }
        $rows += [ordered]@{ property = $entry.property; reference = $old; requested = $entry.requested;
            source_comparison = $result; readback = $readbackStatus }
    }
    if ($rows.Count -ne 41 -or @($rows | Where-Object { $_.source_comparison -eq 'MISMATCH' }).Count -gt 0 -or
        ($Readback -and @($rows | Where-Object { $_.readback -ne 'MATCH' }).Count -gt 0)) {
        throw 'Source/readback comparison failed; see comparison.json.'
    }
    $status = 'PASS'
} catch { $detail = $_.Exception.Message }
finally {
    ConvertTo-Json -InputObject @($rows) -Depth 4 | Set-Content -LiteralPath (Join-Path $evidence 'comparison.json') -Encoding UTF8
    @($inputs | ForEach-Object {
        $hash = if (Test-Path -LiteralPath $_ -PathType Leaf) { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash } else { $null }
        [ordered]@{ path = $_; sha256 = $hash }
    }) |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'input-hashes.json') -Encoding UTF8
    [ordered]@{ status = $status; archive_sha256 = $sourceHash; detail = $detail; properties = $rows.Count;
        readback_checked = [bool]$Readback; hardware_verified = $false; board_confirmation_changed = $false } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding UTF8
}
Write-Host "EES331_SOURCE_REVIEW: $status $detail"
Write-Host "Evidence: $evidence"
if ($status -ne 'PASS') { exit 1 }
