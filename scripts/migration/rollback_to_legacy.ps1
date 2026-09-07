param(
    [string]$LegacyTaskName = "Feishu Morning Clock-In",
    [string]$ConfirmRollback = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($ConfirmRollback -cne "ROLLBACK") {
    throw "Rollback was not confirmed. Re-run with -ConfirmRollback ROLLBACK."
}

$NewTaskName = "Attendance Hub Morning Clock-In"
$RemoteTaskName = "Attendance Automation Hub Web"
$legacy = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
if ($null -eq $legacy) {
    throw "Legacy task was not found; refusing to disable the successor."
}
Enable-ScheduledTask -TaskName $LegacyTaskName | Out-Null
Disable-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue | Out-Null
Stop-ScheduledTask -TaskName $RemoteTaskName -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName $RemoteTaskName -ErrorAction SilentlyContinue | Out-Null
Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like "Attendance Hub Planned *" } |
    Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null
Write-Host "Rollback completed. Legacy task enabled; successor tasks retained but disabled."
