param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$SourceDir = Join-Path $ProjectDir "src"
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$RemoteConfig = Join-Path $ProjectDir "config\remote_config.json"
$LogDir = Join-Path $ProjectDir "logs\remote_service"

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python environment not found: $PythonExe"
}
if (-not (Test-Path -LiteralPath $RemoteConfig -PathType Leaf)) {
    throw "Remote configuration not found. Run scripts/setup/configure_remote.ps1 first."
}
$config = Get-Content -LiteralPath $RemoteConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$config.bind_host -notin @("127.0.0.1", "::1", "localhost")) {
    throw "Remote service must remain bound to loopback."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceDir
}
else {
    "$SourceDir;$env:PYTHONPATH"
}
$serviceLog = Join-Path $LogDir "$((Get-Date).ToString('yyyy-MM-dd')).log"
$previousErrorActionPreference = $ErrorActionPreference
try {
    # Windows PowerShell 5.1 wraps native stderr as non-terminating
    # ErrorRecord objects. Uvicorn writes normal startup messages to stderr,
    # so allow those records to flow into the log instead of stopping here.
    $ErrorActionPreference = "Continue"
    & $PythonExe -m attendance_hub.server 2>&1 |
        Tee-Object -FilePath $serviceLog -Append
    $serviceExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
exit $serviceExitCode
