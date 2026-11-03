[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [string]$ArmBin = ''
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$install = (Resolve-Path -LiteralPath $InstallRoot).Path
$searched = @()
$candidates = @()
if ($ArmBin) {
    $armDirectory = (Resolve-Path -LiteralPath $ArmBin).Path
    $searched += $armDirectory
    $gcc = Join-Path $armDirectory 'arm-none-eabi-gcc.exe'
    if (Test-Path -LiteralPath $gcc -PathType Leaf) { $candidates += $gcc }
} else {
    $searched += 'PATH'
    $command = Get-Command arm-none-eabi-gcc.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($scope in @('gnu', 'Vitis')) {
        $path = Join-Path $install $scope
        if (Test-Path -LiteralPath $path -PathType Container) {
            $searched += $path
            $candidates += @(Get-ChildItem -LiteralPath $path -Recurse -File -Filter 'arm-none-eabi-gcc.exe' |
                Select-Object -ExpandProperty FullName)
        }
    }
}
$candidates = @($candidates | Select-Object -Unique)
$tools = [ordered]@{}
foreach ($entry in @(@('vivado', 'Vivado/bin/vivado.bat'), @('vitis', 'Vitis/bin/vitis.bat'))) {
    $path = Join-Path $install $entry[1]
    $tools[$entry[0]] = [ordered]@{ path = $path; found = (Test-Path -LiteralPath $path -PathType Leaf) }
}
# Keep all ARM utilities from one compiler directory; do not mix installations.
$selected = ''
foreach ($candidate in $candidates) {
    $directory = Split-Path -Parent $candidate
    $complete = $true
    foreach ($name in @('gcc', 'g++', 'ar', 'size')) {
        if (-not (Test-Path -LiteralPath (Join-Path $directory "arm-none-eabi-$name.exe") -PathType Leaf)) {
            $complete = $false
        }
    }
    if ($complete) { $selected = $directory; break }
}
foreach ($name in @('gcc', 'g++', 'ar', 'size')) {
    $path = if ($selected) { Join-Path $selected "arm-none-eabi-$name.exe" } else { '' }
    $tools["arm-none-eabi-$name"] = [ordered]@{ path = $path; found = [bool]$selected }
}
$missing = @($tools.Keys | Where-Object { -not $tools[$_].found })
$status = if ($missing.Count) { 'MISSING_TOOLS' } else { 'TOOLS_LOCATED_NOT_BUILD_VERIFIED' }
$evidence = Join-Path $repo ('metrics/logs/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-board-toolchain-' +
    [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $evidence | Out-Null
$cmake = Join-Path $install 'Vitis/data/embeddedsw/cmake/toolchainfiles/cortexa9_toolchain.cmake'
$cmakeHash = if (Test-Path -LiteralPath $cmake -PathType Leaf) { (Get-FileHash -LiteralPath $cmake -Algorithm SHA256).Hash } else { '' }
[ordered]@{
    status = $status; checked_at = (Get-Date).ToString('o'); install_root = $install
    tools = $tools; missing = $missing; searched = $searched; compiler_candidates = $candidates
    cortexa9_toolchain_file = $cmake; cortexa9_toolchain_sha256 = $cmakeHash
    compiler_execution_verified = $false; hardware_accessed = $false
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $evidence 'summary.json') -Encoding UTF8
Write-Host "BOARD_TOOLCHAIN: $status"
if ($missing.Count) { Write-Host "Missing: $($missing -join ', ')" }
Write-Host "Evidence: $evidence"
if ($missing.Count) { exit 1 }
