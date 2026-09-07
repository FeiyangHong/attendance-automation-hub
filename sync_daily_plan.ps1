param(
    [Parameter(Mandatory = $true)]
    [string]$PlanDate,
    [AllowEmptyString()]
    [string]$ClockIn = "",
    [AllowEmptyString()]
    [string]$ClockOut = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$MorningRunner = Join-Path $ProjectDir "run_random.ps1"
$ClockOutRunner = Join-Path $ProjectDir "run_clock_out.ps1"
$PowerShellExe = Join-Path $env:SystemRoot `
    "System32\WindowsPowerShell\v1.0\powershell.exe"
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$planDay = [datetime]::ParseExact(
    $PlanDate,
    "yyyy-MM-dd",
    $culture,
    [System.Globalization.DateTimeStyles]::None
)

foreach ($requiredFile in @($MorningRunner, $ClockOutRunner)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Daily plan runner not found: $requiredFile"
    }
}

function Sync-PlannedTask {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("Clock-In", "Clock-Out")][string]$Kind,
        [AllowEmptyString()][string]$TimeText
    )

    $taskName = "Feishu Planned $Kind $PlanDate"
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ([string]::IsNullOrWhiteSpace($TimeText)) {
        if ($null -ne $existingTask) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        Write-Output "$taskName removed."
        return
    }

    $runTime = [datetime]::ParseExact(
        "$PlanDate $TimeText",
        "yyyy-MM-dd HH:mm",
        $culture,
        [System.Globalization.DateTimeStyles]::None
    )
    if ($runTime -le (Get-Date).AddSeconds(10)) {
        if ($null -ne $existingTask) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        Write-Output "$taskName retained in the plan file but is already in the past."
        return
    }

    if ($Kind -eq "Clock-In") {
        $runner = $MorningRunner
        $runnerArguments = @(
            "-PlannedClockIn"
            "-PlannedDate `"$PlanDate`""
            "-PlannedTime `"$TimeText`""
        )
        $description = "Exact Feishu clock-in from the per-date calendar plan"
        $limit = New-TimeSpan -Minutes 30
    }
    else {
        $runner = $ClockOutRunner
        $runnerArguments = @(
            "-NotifyOnFailure"
            "-Source scheduled"
            "-PlannedDate `"$PlanDate`""
        )
        $description = "Exact Feishu clock-out from the per-date calendar plan"
        $limit = New-TimeSpan -Minutes 30
    }

    $actionArguments = @(
        "-NoProfile"
        "-NonInteractive"
        "-ExecutionPolicy Bypass"
        "-File `"$runner`""
        $runnerArguments
    ) -join " "
    $action = New-ScheduledTaskAction `
        -Execute $PowerShellExe `
        -Argument $actionArguments `
        -WorkingDirectory $ProjectDir
    $trigger = New-ScheduledTaskTrigger -Once -At $runTime
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit $limit `
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
        -Description $description
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    Write-Output "$taskName scheduled for $($runTime.ToString('yyyy-MM-dd HH:mm:ss'))."
}

Sync-PlannedTask -Kind "Clock-In" -TimeText $ClockIn
Sync-PlannedTask -Kind "Clock-Out" -TimeText $ClockOut

if (-not [string]::IsNullOrWhiteSpace($ClockOut)) {
    $legacyTaskName = "Feishu One-Time Clock-Out"
    $legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
    if ($null -ne $legacyTask) {
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
        Write-Output "Legacy one-time clock-out task removed after plan migration."
    }
}
