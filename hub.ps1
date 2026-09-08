param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "desktop",
        "web",
        "diagnose",
        "clock-in",
        "clock-out",
        "configure",
        "install-web",
        "install-daily",
        "tailscale",
        "migrate",
        "cutover",
        "rollback",
        "build"
    )]
    [string]$Command = "desktop",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [object[]]$CommandArguments = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$SourceDir = Join-Path $ProjectDir "src"
$PythonwExe = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceDir
}
else {
    "$SourceDir;$env:PYTHONPATH"
}
$scripts = @{
    web = "scripts\runtime\run_web.ps1"
    diagnose = "scripts\operations\diagnose_environment.ps1"
    configure = "scripts\setup\configure_remote.ps1"
    "install-web" = "scripts\setup\install_remote_service.ps1"
    "install-daily" = "scripts\setup\install_daily_task.ps1"
    tailscale = "scripts\setup\setup_tailscale_serve.ps1"
    migrate = "scripts\migration\migrate_from_legacy.ps1"
    cutover = "scripts\migration\cutover_to_hub.ps1"
    rollback = "scripts\migration\rollback_to_legacy.ps1"
    build = "scripts\release\build_release.ps1"
}

switch ($Command) {
    "desktop" {
        if (-not (Test-Path -LiteralPath $PythonwExe -PathType Leaf)) {
            throw "Python environment not found: $PythonwExe"
        }
        Start-Process `
            -FilePath $PythonwExe `
            -ArgumentList @("-m", "attendance_hub.desktop.control_panel") `
            -WorkingDirectory $ProjectDir `
            -WindowStyle Hidden | Out-Null
    }
    "clock-in" {
        & (Join-Path $ProjectDir "scripts\runtime\run_morning.ps1") `
            -ManualClockIn @CommandArguments
    }
    "clock-out" {
        & (Join-Path $ProjectDir "scripts\runtime\run_clock_out.ps1") `
            -Source ui @CommandArguments
    }
    default {
        $scriptPath = Join-Path $ProjectDir $scripts[$Command]
        & $scriptPath @CommandArguments
    }
}
