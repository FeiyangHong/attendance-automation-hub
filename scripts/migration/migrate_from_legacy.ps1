param(
    [string]$LegacyPath = "E:\PhoneRemote\feishu_dryrun",
    [switch]$IncludeLogs,
    [switch]$LogsOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Create the new repository Python environment first."
}
$arguments = @("-m", "attendance_hub.migrate", "--legacy", $LegacyPath)
if ($IncludeLogs) { $arguments += "--include-logs" }
if ($LogsOnly) { $arguments += "--logs-only" }
$SourceDir = Join-Path $ProjectDir "src"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceDir
}
else {
    "$SourceDir;$env:PYTHONPATH"
}
Push-Location $ProjectDir
try {
    & $PythonExe @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $exitCode
