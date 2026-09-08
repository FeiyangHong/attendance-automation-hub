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
$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$BackupDir = Join-Path $ProjectDir "data\cutover"
$legacy = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
$snapshotFile = Get-ChildItem -LiteralPath $BackupDir -Filter "task-state-*.json" `
    -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$snapshot = if ($null -ne $snapshotFile) {
    Get-Content -LiteralPath $snapshotFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
}
else {
    $null
}

if ($null -ne $snapshot -and $null -ne $snapshot.legacy_tasks) {
    foreach ($entry in @($snapshot.legacy_tasks)) {
        $task = Get-ScheduledTask -TaskName ([string]$entry.task_name) `
            -ErrorAction SilentlyContinue
        if ($null -eq $task) { continue }
        if ([bool]$entry.enabled) {
            Enable-ScheduledTask -TaskName $task.TaskName | Out-Null
        }
        else {
            Disable-ScheduledTask -TaskName $task.TaskName | Out-Null
        }
    }
}
elseif ($null -ne $legacy) {
    Enable-ScheduledTask -TaskName $LegacyTaskName | Out-Null
}
else {
    throw "Neither a cutover snapshot nor the legacy daily task was found."
}
Disable-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue | Out-Null
Stop-ScheduledTask -TaskName $RemoteTaskName -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName $RemoteTaskName -ErrorAction SilentlyContinue | Out-Null
Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like "Attendance Hub Planned *" } |
    Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null
Write-Host "Rollback completed. Legacy task states restored; successor tasks retained but disabled."
