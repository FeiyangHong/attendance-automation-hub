param(
    [string]$TaskName = "Attendance Hub Morning Clock-In",
    [switch]$UpdateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$RunnerScript = Join-Path $ProjectDir "scripts\runtime\run_morning.ps1"
. (Join-Path $ProjectDir "scripts\runtime\morning_window.ps1")
$window = Get-MorningWindow -ProjectDir $ProjectDir
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

# Start at the configured window opening and catch up within the remaining window.
$trigger = New-ScheduledTaskTrigger `
    -Daily `
    -At $window.StartText

$executionLimit = New-TimeSpan -Minutes ([Math]::Max(60, $window.DurationMinutes + 30))
$description = "Feishu clock-in randomized over the remaining $($window.StartText)-$($window.EndText) window"
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($UpdateOnly) {
    if ($null -eq $existingTask) {
        Write-Output "Window saved; daily task is not installed."
        exit 0
    }
    $existingTask.Settings.ExecutionTimeLimit = [System.Xml.XmlConvert]::ToString($executionLimit)
    $existingTask.Triggers = @($trigger)
    $existingTask.Description = $description
    Set-ScheduledTask -InputObject $existingTask | Out-Null
    Write-Output "Daily trigger updated to $($window.StartText); enabled state preserved."
    exit 0
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit $executionLimit `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

if ($null -ne $existingTask -and -not $existingTask.Settings.Enabled) {
    $settings.Enabled = $false
}

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
    -Description $description

Register-ScheduledTask `
    -TaskName $TaskName `
    -InputObject $task `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Run a safe timing test first with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$RunnerScript`" -TestMode"
