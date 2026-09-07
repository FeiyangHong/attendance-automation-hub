param(
    [Parameter(Mandatory = $true)]
    [string]$RunAt,
    [string]$TaskName = "Attendance Hub One-Time Clock-Out"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$RunnerScript = Join-Path $ProjectDir "run_clock_out.ps1"
$PowerShellExe = Join-Path $env:SystemRoot `
    "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $RunnerScript -PathType Leaf)) {
    throw "Clock-out runner not found: $RunnerScript"
}

$culture = [System.Globalization.CultureInfo]::InvariantCulture
$runTime = [datetime]::ParseExact(
    $RunAt,
    "yyyy-MM-dd HH:mm",
    $culture,
    [System.Globalization.DateTimeStyles]::None
)
if ($runTime -le (Get-Date).AddSeconds(10)) {
    throw "The scheduled clock-out time must be in the future."
}

$arguments = @(
    "-NoProfile"
    "-NonInteractive"
    "-ExecutionPolicy Bypass"
    "-File `"$RunnerScript`""
    "-NotifyOnFailure"
    "-Source scheduled"
) -join " "

$action = New-ScheduledTaskAction `
    -Execute $PowerShellExe `
    -Argument $arguments `
    -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Once -At $runTime
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
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
    -Description "One-time Feishu clock-out requested from the control panel"

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Write-Host "One-time clock-out scheduled: $($runTime.ToString('yyyy-MM-dd HH:mm:ss'))"
