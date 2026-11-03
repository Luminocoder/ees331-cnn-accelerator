[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$TclShell,
    [Parameter(Mandatory = $true)][string]$ProfileTable
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$evidence = Join-Path $repo ('metrics/logs/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-ees331-review-tests-' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $evidence | Out-Null
$powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$review = Join-Path $PSScriptRoot '../review-ees331-ps.ps1'
$checks = 0
function Check-Review([string]$Name, $Rows, [bool]$ShouldPass) {
    $fixture = Join-Path $evidence "$Name.tsv"
    $Rows | Export-Csv -LiteralPath $fixture -Delimiter "`t" -NoTypeInformation -Encoding UTF8
    $output = & $powershell -NoProfile -ExecutionPolicy Bypass -File $review -Archive $Archive -TclShell $TclShell -Readback $fixture
    $result = $LASTEXITCODE
    $output | Set-Content -LiteralPath (Join-Path $evidence "$Name.log") -Encoding UTF8
    if (($result -eq 0) -ne $ShouldPass) { throw "Unexpected review result for $Name, exit=$result" }
    $script:checks++
}
$baseline = @(Import-Csv -LiteralPath $ProfileTable -Delimiter "`t" | ForEach-Object {
    [pscustomobject]@{ property = $_.property; requested = $_.requested; effective = $_.requested; status = 'MATCH' }
})
Check-Review 'matching' $baseline $true
$rounded = $baseline | ConvertTo-Json | ConvertFrom-Json
($rounded | Where-Object property -eq 'CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ').effective = '533.333313'
Check-Review 'rounded-frequency' $rounded $true
$falsePass = $baseline | ConvertTo-Json | ConvertFrom-Json
($falsePass | Where-Object property -eq 'CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ').effective = '400'
Check-Review 'false-match-marker' $falsePass $false
Check-Review 'missing-property' @($baseline | Select-Object -Skip 1) $false
Check-Review 'duplicate-property' @($baseline + $baseline[0]) $false
$stale = $baseline | ConvertTo-Json | ConvertFrom-Json
($stale | Where-Object property -eq 'CONFIG.PCW_UART1_UART1_IO').requested = 'MIO 14 .. 15'
Check-Review 'stale-profile' $stale $false
[ordered]@{ status = 'PASS'; checks = $checks; fixtures_only = $true; hardware_verified = $false } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding UTF8
Write-Host "EES331_REVIEW_TESTS_PASS: $checks checks; evidence: $evidence"
