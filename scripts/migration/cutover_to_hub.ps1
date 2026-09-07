param(
    [string]$LegacyTaskName = "Feishu Morning Clock-In",
    [string]$ConfirmCutover = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($ConfirmCutover -cne "CUTOVER") {
    throw "Cutover was not confirmed. Re-run with -ConfirmCutover CUTOVER."
}

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$AppConfigFile = Join-Path $ProjectDir "config\app_config.json"
$RemoteConfigFile = Join-Path $ProjectDir "config\remote_config.json"
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$NewTaskName = "Attendance Hub Morning Clock-In"
$BackupDir = Join-Path $ProjectDir "data\cutover"
$BackupFile = Join-Path $BackupDir "task-state-$((Get-Date).ToString('yyyyMMdd-HHmmss')).json"

foreach ($required in @($AppConfigFile, $RemoteConfigFile, $PythonExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Cutover prerequisite missing: $required"
    }
}
$appConfig = Get-Content -LiteralPath $AppConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not [bool]$appConfig.device_access_enabled -or -not [bool]$appConfig.real_actions_enabled) {
    throw "Both device_access_enabled and real_actions_enabled must be true before cutover."
}
$remoteConfig = Get-Content -LiteralPath $RemoteConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$remoteConfig.password_hash)) {
    throw "Remote authentication is not configured."
}

$legacy = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
$snapshot = [ordered]@{
    captured_at = (Get-Date).ToString("o")
    legacy_task_name = $LegacyTaskName
    legacy_exists = ($null -ne $legacy)
    legacy_enabled = if ($null -ne $legacy) { [bool]$legacy.Settings.Enabled } else { $false }
    legacy_action = if ($null -ne $legacy) { [string]$legacy.Actions[0].Arguments } else { "" }
    new_task_name = $NewTaskName
}
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$snapshot | ConvertTo-Json | Set-Content -LiteralPath $BackupFile -Encoding UTF8

try {
    & (Join-Path $ProjectDir "scripts\setup\install_daily_task.ps1") `
        -TaskName $NewTaskName
    & (Join-Path $ProjectDir "scripts\setup\install_remote_service.ps1")
    & (Join-Path $ProjectDir "scripts\operations\sync_future_plans.ps1")

    $newTask = Get-ScheduledTask -TaskName $NewTaskName -ErrorAction Stop
    $newAction = [string]$newTask.Actions[0].Arguments
    if ($newAction -notlike "*$ProjectDir*") {
        throw "The new daily task does not point to the successor repository."
    }
    if ($null -ne $legacy) {
        Disable-ScheduledTask -TaskName $LegacyTaskName | Out-Null
    }
    Write-Host "Cutover completed. Old task was retained but disabled."
    Write-Host "Rollback state: $BackupFile"
}
catch {
    Disable-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue | Out-Null
    if ($null -ne $legacy -and [bool]$snapshot.legacy_enabled) {
        Enable-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue | Out-Null
    }
    throw
}
