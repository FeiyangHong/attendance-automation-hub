param(
    [string]$LegacyTaskName = "Feishu Morning Clock-In",
    [switch]$Preview,
    [string]$ConfirmCutover = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ((-not $Preview) -and $ConfirmCutover -cne "CUTOVER") {
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
$RemoteTaskName = "Attendance Automation Hub Web"

function Get-NormalizedPath {
    param([AllowEmptyString()][string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    try {
        return [System.IO.Path]::GetFullPath($Value).TrimEnd("\")
    }
    catch {
        return ""
    }
}

function Test-TaskUsesProject {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][string]$RepositoryPath
    )

    $needle = $RepositoryPath.TrimEnd("\") + "\"
    foreach ($action in @($Task.Actions)) {
        $workingDirectoryProperty = $action.PSObject.Properties["WorkingDirectory"]
        $workingDirectory = if ($null -ne $workingDirectoryProperty) {
            Get-NormalizedPath ([string]$workingDirectoryProperty.Value)
        }
        else {
            ""
        }
        if ($workingDirectory -and $workingDirectory.Equals(
            $RepositoryPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            return $true
        }
        $argumentsProperty = $action.PSObject.Properties["Arguments"]
        $arguments = if ($null -ne $argumentsProperty) {
            [string]$argumentsProperty.Value
        }
        else {
            ""
        }
        if ($arguments.IndexOf(
            $needle,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -ge 0) {
            return $true
        }
    }
    return $false
}

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
$legacyProjectDir = if ($null -ne $legacy) {
    Get-NormalizedPath ([string]$legacy.Actions[0].WorkingDirectory)
}
else {
    ""
}
if (-not $legacyProjectDir -and $null -ne $legacy) {
    $match = [regex]::Match(
        [string]$legacy.Actions[0].Arguments,
        '(?i)-File\s+"?([^"\s]*run_random\.ps1)"?'
    )
    if ($match.Success) {
        $legacyProjectDir = Get-NormalizedPath (Split-Path -Parent $match.Groups[1].Value)
    }
}
if (-not $legacyProjectDir) {
    throw "Unable to determine the legacy repository from task '$LegacyTaskName'."
}
if ($legacyProjectDir.Equals($ProjectDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Legacy and successor repository paths unexpectedly match."
}
$legacyTasks = @(
    Get-ScheduledTask -ErrorAction Stop |
        Where-Object { Test-TaskUsesProject -Task $_ -RepositoryPath $legacyProjectDir }
)
if ($legacyTasks.Count -eq 0) {
    throw "No scheduled tasks referencing the legacy repository were found."
}
if ($Preview) {
    Write-Host "Cutover preview only; no system state was changed."
    Write-Host "Successor repository: $ProjectDir"
    Write-Host "Legacy repository: $legacyProjectDir"
    Write-Host "Tasks that will be disabled after successor validation:"
    $legacyTasks |
        Sort-Object TaskName |
        Select-Object TaskName, State, @{Name = "Enabled"; Expression = {
            [bool]$_.Settings.Enabled
        }} |
        Format-Table -AutoSize
    return
}
$snapshot = [ordered]@{
    captured_at = (Get-Date).ToString("o")
    legacy_project_dir = $legacyProjectDir
    legacy_task_name = $LegacyTaskName
    legacy_exists = ($null -ne $legacy)
    legacy_enabled = if ($null -ne $legacy) { [bool]$legacy.Settings.Enabled } else { $false }
    legacy_action = if ($null -ne $legacy) { [string]$legacy.Actions[0].Arguments } else { "" }
    legacy_tasks = @(
        $legacyTasks | ForEach-Object {
            [ordered]@{
                task_name = $_.TaskName
                enabled = [bool]$_.Settings.Enabled
                state = [string]$_.State
                action = [string]$_.Actions[0].Arguments
            }
        }
    )
    new_task_name = $NewTaskName
}
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $BackupFile -Encoding UTF8

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
    $successorTasks = @(
        Get-ScheduledTask -ErrorAction Stop |
            Where-Object {
                $_.TaskName -eq $NewTaskName -or
                $_.TaskName -like "Attendance Hub Planned *"
            }
    )
    foreach ($task in $successorTasks) {
        if (Test-TaskUsesProject -Task $task -RepositoryPath $legacyProjectDir) {
            throw "Successor task still references the legacy repository: $($task.TaskName)"
        }
    }

    Start-ScheduledTask -TaskName $RemoteTaskName
    $healthReady = $false
    $healthDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $healthDeadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/healthz" -TimeoutSec 2
            if ([string]$health.status -eq "ok") {
                $healthReady = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthReady) {
        throw "The successor Web service did not pass its local health check."
    }

    foreach ($task in $legacyTasks) {
        if ([string]$task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $task.TaskName -ErrorAction Stop
        }
        Disable-ScheduledTask -TaskName $task.TaskName -ErrorAction Stop | Out-Null
    }
    Write-Host "Cutover completed. $($legacyTasks.Count) old task(s) were retained but disabled."
    Write-Host "Rollback state: $BackupFile"
}
catch {
    Disable-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue | Out-Null
    Get-ScheduledTask -ErrorAction SilentlyContinue |
        Where-Object { $_.TaskName -like "Attendance Hub Planned *" } |
        Disable-ScheduledTask -ErrorAction SilentlyContinue | Out-Null
    foreach ($entry in @($snapshot.legacy_tasks)) {
        if ([bool]$entry.enabled) {
            Enable-ScheduledTask -TaskName ([string]$entry.task_name) `
                -ErrorAction SilentlyContinue | Out-Null
        }
        else {
            Disable-ScheduledTask -TaskName ([string]$entry.task_name) `
                -ErrorAction SilentlyContinue | Out-Null
        }
    }
    throw
}
