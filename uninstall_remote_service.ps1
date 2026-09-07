param(
    [string]$TaskName = "Attendance Automation Hub Web"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Remote service task is not installed: $TaskName"
    exit 0
}
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Remote service task removed: $TaskName"
