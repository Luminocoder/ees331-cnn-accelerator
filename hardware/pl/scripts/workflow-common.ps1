Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-WorkflowTool([string]$Name, [string]$Directory = '') {
    if ($Directory) {
        $path = Join-Path $Directory $Name
        if (Test-Path -LiteralPath $path -PathType Leaf) { return (Resolve-Path -LiteralPath $path).Path }
        throw "Tool not found: $path"
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "Tool not found: $Name. Supply the tool directory explicitly."
}

function New-WorkflowRun([string]$RepoRoot, [string]$WorkRoot, [string]$Kind) {
    $root = [IO.Path]::GetFullPath($WorkRoot)
    if ($root -match '[^\x20-\x7e]' -or $root -match '["%!&|<>^]') {
        throw 'WorkRoot must be an ASCII path without shell metacharacters.'
    }
    $repo = [IO.Path]::GetFullPath($RepoRoot).TrimEnd('\', '/')
    if ($root.Equals($repo, [StringComparison]::OrdinalIgnoreCase) -or
        $root.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $repo.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'WorkRoot must be separate from the authoritative repository, not its ancestor.'
    }
    $id = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Kind + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $run = Join-Path $root $id
    $source = Join-Path $run 'source'
    $artifacts = Join-Path $RepoRoot ('metrics/logs/' + $id)
    New-Item -ItemType Directory -Path $source, $artifacts | Out-Null
    $manifest = @()
    foreach ($scope in @('scripts', 'rtl', 'ref_model', 'sim', 'board')) {
        $origin = Join-Path $RepoRoot $scope
        Copy-Item -LiteralPath $origin -Destination $source -Recurse
        foreach ($file in (Get-ChildItem -LiteralPath $origin -Recurse -File | Sort-Object FullName)) {
            $relative = $file.FullName.Substring($repo.Length + 1).Replace('\', '/')
            $mirrored = Join-Path $source $relative
            $manifest += [ordered]@{ path = $relative; sha256 = (Get-FileHash -LiteralPath $mirrored -Algorithm SHA256).Hash }
        }
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $artifacts 'source-manifest.json') -Encoding UTF8
    [ordered]@{ authoritative_source = $repo; run_id = $id } | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $source '.workflow-mirror.json') -Encoding UTF8
    return [pscustomobject]@{ Id = $id; Root = $run; Source = $source; Artifacts = $artifacts }
}

function Assert-WorkflowLog([string]$Text, [string]$PassPattern = '', [int]$ExpectedImages = 0) {
    if ($Text -match '(?im)\[FAIL\]|BOARD_BUILD_FAIL|\bFATAL_ERROR\b|^\s*(ERROR:|FATAL:|CRITICAL WARNING:)|\$fatal') {
        throw 'The tool log contains a failure marker.'
    }
    if ($PassPattern -and $Text -notmatch $PassPattern) { throw "Missing completion marker: $PassPattern" }
    if ($ExpectedImages -gt 0) {
        $matches = [regex]::Matches($Text, '\[PASS\] img(\d{2}):.*\(10/10 bit-exact\)')
        $indices = @($matches | ForEach-Object { [int]$_.Groups[1].Value } | Sort-Object -Unique)
        if ($matches.Count -ne $ExpectedImages -or $indices.Count -ne $ExpectedImages -or
            $indices[0] -ne 0 -or $indices[-1] -ne ($ExpectedImages - 1)) {
            throw "Expected $ExpectedImages distinct bit-exact image results."
        }
    }
}

function Invoke-WorkflowProcess {
    param([string]$Tool, [string[]]$Arguments, [string]$WorkingDirectory, [string]$Log,
          [int]$TimeoutSeconds = 1200, [string]$PassPattern = '', [int]$ExpectedImages = 0,
          [hashtable]$Environment = @{})
    foreach ($argument in (@($Tool) + $Arguments)) {
        if ($argument -match '["%!&|<>^\r\n]') { throw "Unsafe command argument: $argument" }
    }
    $quoted = @($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $start = New-Object Diagnostics.ProcessStartInfo
    if ([IO.Path]::GetExtension($Tool) -eq '.bat') {
        $start.FileName = Join-Path $env:SystemRoot 'System32/cmd.exe'
        $start.Arguments = '/d /s /c ""' + $Tool + '" ' + $quoted + '"'
    } else {
        $start.FileName = $Tool
        $start.Arguments = $quoted
    }
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $start.EnvironmentVariables['PROCESSOR_ARCHITECTURE'] = 'AMD64'
    $start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    foreach ($key in $Environment.Keys) { $start.EnvironmentVariables[$key] = $Environment[$key] }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    $started = Get-Date
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) {
            & (Join-Path $env:SystemRoot 'System32/taskkill.exe') /PID $process.Id /T /F | Out-Null
            $process.WaitForExit()
        }
        $text = $stdout.Result + "`n" + $stderr.Result
        $header = "tool=$Tool`narguments=$quoted`ncwd=$WorkingDirectory`nstarted=$($started.ToString('o'))`nexit=$($process.ExitCode)`ntimeout=$timedOut`n"
        ($header + $text) | Set-Content -LiteralPath $Log -Encoding UTF8
        if ($timedOut) { throw "Tool timed out after $TimeoutSeconds seconds. See $Log" }
        if ($process.ExitCode -ne 0) { throw "Tool exited with $($process.ExitCode). See $Log" }
        Assert-WorkflowLog -Text $text -PassPattern $PassPattern -ExpectedImages $ExpectedImages
    } finally { $process.Dispose() }
}

function Save-WorkflowReports($Run) {
    foreach ($file in (Get-ChildItem -LiteralPath $Run.Root -Recurse -File -Filter '*.rpt')) {
        $relative = $file.FullName.Substring($Run.Root.Length + 1)
        $target = Join-Path $Run.Artifacts ('reports/' + $relative)
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
}
