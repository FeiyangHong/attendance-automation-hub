param(
    [string]$Username = "admin",
    [string]$DeviceUdid = "",
    [string[]]$TailscaleUser = @(),
    [int]$Port = 8765,
    [switch]$CookieSecure,
    [switch]$EnableDevice,
    [switch]$EnableRealActions,
    [string]$ConfirmProduction = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Create the project virtual environment first: $PythonExe"
}

$arguments = @(
    "-m", "attendance_hub.configure",
    "--username", $Username,
    "--device-udid", $DeviceUdid,
    "--port", $Port.ToString()
)
foreach ($user in $TailscaleUser) {
    $arguments += @("--tailscale-user", $user)
}
if ($CookieSecure) { $arguments += "--cookie-secure" }
if ($EnableDevice) { $arguments += "--enable-device" }
if ($EnableRealActions) { $arguments += "--enable-real-actions" }
if ($ConfirmProduction) {
    $arguments += @("--confirm-production", $ConfirmProduction)
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
