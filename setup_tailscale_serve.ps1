param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$Disable
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if ($null -eq $tailscale) {
    $installed = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (-not (Test-Path -LiteralPath $installed -PathType Leaf)) {
        throw "Tailscale is not installed. Install it and complete account login first."
    }
    $tailscaleExe = $installed
}
else {
    $tailscaleExe = $tailscale.Source
}

if ($Disable) {
    & $tailscaleExe serve reset
    exit $LASTEXITCODE
}

$status = & $tailscaleExe status --json 2>$null | ConvertFrom-Json
if (-not [bool]$status.Self.Online) {
    throw "Tailscale is installed but this computer is not online/logged in."
}
& $tailscaleExe serve --bg "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to configure Tailscale Serve. Run this script from an Administrator PowerShell window."
}
& $tailscaleExe serve status
