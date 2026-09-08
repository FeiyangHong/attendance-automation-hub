param(
    [string]$TaskName = "Attendance Hub Morning Clock-In"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$RunnerScript = Join-Path $ProjectDir "scripts\runtime\run_morning.ps1"
$PowerShellExe = Join-Path $env:SystemRoot `
    "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $RunnerScript -PathType Leaf)) {
    throw "Scheduler script not found: $RunnerScript"
}

$actionArguments = @(
    "-NoProfile"
    "-NonInteractive"
    "-ExecutionPolicy Bypass"
    "-File `"$RunnerScript`""
) -join " "

$action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument $actionArguments `
    -WorkingDirectory $ProjectDir

# Start daily at 09:00 and catch up after shutdown or sleep.
$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At "09:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

# Appium/Android automation requires the current interactive desktop session.
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Feishu morning clock-in randomized over the remaining 09:00-09:30 window"

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $task `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Run a safe timing test first with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$RunnerScript`" -TestMode"
