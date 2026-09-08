param(
    [string]$TaskName = "Attendance Automation Hub Web"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$Runner = Join-Path $ProjectDir "scripts\runtime\run_web.ps1"
$HiddenRunner = Join-Path $ProjectDir "scripts\runtime\run_web_hidden.vbs"
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$RemoteConfig = Join-Path $ProjectDir "config\remote_config.json"
$WscriptExe = Join-Path $env:SystemRoot "System32\wscript.exe"

foreach ($required in @($Runner, $HiddenRunner, $PythonExe, $RemoteConfig, $WscriptExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file not found: $required"
    }
}

$arguments = "//B //NoLogo `"$HiddenRunner`""
$action = New-ScheduledTaskAction `
    -Execute $WscriptExe `
    -Argument $arguments `
    -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Hidden loopback-only Attendance Automation Hub web service"
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Write-Host "Remote service task installed but not started: $TaskName"
