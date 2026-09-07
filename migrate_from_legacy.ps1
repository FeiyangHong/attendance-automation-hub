param(
    [string]$LegacyPath = "E:\PhoneRemote\feishu_dryrun",
    [switch]$IncludeLogs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Create the new repository Python environment first."
}
$arguments = @("-m", "attendance_hub.migrate", "--legacy", $LegacyPath)
if ($IncludeLogs) { $arguments += "--include-logs" }
& $PythonExe @arguments
exit $LASTEXITCODE
