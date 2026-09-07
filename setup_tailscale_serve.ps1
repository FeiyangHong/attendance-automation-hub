param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,
    [switch]$Disable
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
if ($null -eq $tailscale) {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Tailscale\tailscale.exe"),
        (Join-Path $env:LOCALAPPDATA "Tailscale\tailscale.exe")
    )
    $service = Get-CimInstance Win32_Service -Filter "Name='Tailscale'" -ErrorAction SilentlyContinue
    if ($null -ne $service) {
        $match = [regex]::Match([string]$service.PathName, '^(?:"([^"]+)"|(\S+))')
        $serviceExe = if ($match.Groups[1].Success) {
            $match.Groups[1].Value
        } else {
            $match.Groups[2].Value
        }
        if (-not [string]::IsNullOrWhiteSpace($serviceExe)) {
            $candidates = @((Join-Path (Split-Path -Parent $serviceExe) "tailscale.exe")) + $candidates
        }
    }
    $installed = $candidates | Where-Object {
        $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
    } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace([string]$installed)) {
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
