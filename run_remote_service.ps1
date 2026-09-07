param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$RemoteConfig = Join-Path $ProjectDir "config\remote_config.json"
$LogDir = Join-Path $ProjectDir "logs\remote_service"

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python environment not found: $PythonExe"
}
if (-not (Test-Path -LiteralPath $RemoteConfig -PathType Leaf)) {
    throw "Remote configuration not found. Run configure_remote.ps1 first."
}
$config = Get-Content -LiteralPath $RemoteConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]$config.bind_host -notin @("127.0.0.1", "::1", "localhost")) {
    throw "Remote service must remain bound to loopback."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:PYTHONUNBUFFERED = "1"
& $PythonExe -m attendance_hub.server 2>&1 |
    Tee-Object -FilePath (Join-Path $LogDir "$((Get-Date).ToString('yyyy-MM-dd')).log") -Append
exit $LASTEXITCODE
