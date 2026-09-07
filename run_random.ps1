param(
    [switch]$TestMode,
    [switch]$Immediate,
    [switch]$ManualClockIn,
    [switch]$PlannedClockIn,
    [string]$PlannedDate = "",
    [string]$PlannedTime = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ScriptFile = Join-Path $ProjectDir "04_feishu_flow.py"
$CalendarFile = Join-Path $ProjectDir "config\calendar_overrides.json"
$OfficialCalendarFile = Join-Path $ProjectDir "config\official_holidays.json"
$MorningPlanFile = Join-Path $ProjectDir "config\morning_current_plan.json"
$DailyPlansFile = Join-Path $ProjectDir "config\daily_plans.json"
$HolidaySyncScript = Join-Path $ProjectDir "attendance_hub\core\holiday_sync.py"
$AppConfigFile = Join-Path $ProjectDir "config\app_config.json"
$AppiumCmd = Join-Path $env:APPDATA "npm\appium.cmd"
$AppiumHost = "127.0.0.1"
$AppiumPort = 4723

$WindowStartHour = 9
$WindowStartMinute = 0
$WindowEndHour = 9
$WindowEndMinute = 30
$RetryDelaySeconds = 15
$MaxAttempts = 3

$startedAt = Get-Date
$monthName = $startedAt.ToString("yyyy-MM")
$dayName = $startedAt.ToString("yyyy-MM-dd")
$LogDir = Join-Path (Join-Path $ProjectDir "logs") $monthName
$LogFile = Join-Path $LogDir "$dayName.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$timestamp $Message"

    # Log content is ASCII-only. Retry short-lived locks from readers,
    # antivirus scanners, or another process flushing the same daily file.
    $writeSucceeded = $false

    for ($writeAttempt = 1; $writeAttempt -le 20; $writeAttempt += 1) {
        try {
            Add-Content -LiteralPath $LogFile -Value $line -Encoding ASCII
            $writeSucceeded = $true
            break
        }
        catch [System.IO.IOException] {
            if ($writeAttempt -eq 20) {
                throw
            }

            Start-Sleep -Milliseconds 250
        }
    }

    if (-not $writeSucceeded) {
        throw "Failed to append to log file: $LogFile"
    }

    Write-Host $line
}

function Write-MorningPlan {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("waiting", "running", "success", "failed", "skipped")]
        [string]$Status,
        [AllowNull()]
        [Nullable[datetime]]$TargetTime = $null,
        [int]$Attempts = 0,
        [AllowNull()]
        [Nullable[int]]$ExitCode = $null,
        [string]$Message = ""
    )

    # Manual clock-in and safe-test runs must not replace the daily task's
    # randomly selected target shown in the control panel.
    if ($TestMode -or $ManualClockIn) {
        return
    }

    $record = [ordered]@{
        date = $startedAt.ToString("yyyy-MM-dd")
        target_time = if ($null -ne $TargetTime) {
            $TargetTime.ToString("yyyy-MM-dd HH:mm:ss")
        } else {
            ""
        }
        status = $Status
        attempts = $Attempts
        exit_code = if ($null -ne $ExitCode) { $ExitCode } else { $null }
        message = $Message
        updated_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    } | ConvertTo-Json

    $planDirectory = Split-Path -Parent $MorningPlanFile
    New-Item -ItemType Directory -Force -Path $planDirectory | Out-Null
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText(
        $MorningPlanFile,
        $record + [Environment]::NewLine,
        $utf8WithoutBom
    )
}

function Test-AutomationSafety {
    if (-not (Test-Path -LiteralPath $AppConfigFile -PathType Leaf)) {
        Write-Log "SAFETY: config/app_config.json is missing; device access is blocked."
        return $false
    }
    try {
        $safety = Get-Content -LiteralPath $AppConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-Log "SAFETY: app configuration is invalid; device access is blocked."
        return $false
    }
    if (-not [bool]$safety.device_access_enabled) {
        Write-Log "SAFETY: device access is disabled."
        return $false
    }
    if ((-not $TestMode) -and (-not [bool]$safety.real_actions_enabled)) {
        Write-Log "SAFETY: real attendance actions are disabled."
        return $false
    }
    return $true
}

function Get-CalendarDecision {
    param(
        [Parameter(Mandatory = $true)]
        [datetime]$Date
    )

    $dateText = $Date.ToString("yyyy-MM-dd")
    $forceWorkdays = @()
    $forceHolidays = @()
    $officialWorkdays = @()
    $officialHolidays = @()

    if (Test-Path -LiteralPath $CalendarFile -PathType Leaf) {
        try {
            $calendar = Get-Content -LiteralPath $CalendarFile -Raw -Encoding UTF8 |
                ConvertFrom-Json

            if ($calendar.PSObject.Properties.Name -contains "force_workdays") {
                $forceWorkdays = @($calendar.force_workdays)
            }

            if ($calendar.PSObject.Properties.Name -contains "force_holidays") {
                $forceHolidays = @($calendar.force_holidays)
            }
        }
        catch {
            throw "Unable to read calendar overrides: $($_.Exception.Message)"
        }
    }

    if (Test-Path -LiteralPath $OfficialCalendarFile -PathType Leaf) {
        try {
            $officialCalendar = Get-Content -LiteralPath $OfficialCalendarFile -Raw -Encoding UTF8 |
                ConvertFrom-Json
            $yearText = $Date.ToString("yyyy")
            $yearProperty = $officialCalendar.years.PSObject.Properties |
                Where-Object { $_.Name -eq $yearText } |
                Select-Object -First 1

            if ($null -ne $yearProperty) {
                $officialWorkdays = @($yearProperty.Value.workdays)
                $officialHolidays = @($yearProperty.Value.holidays)
            }
        }
        catch {
            throw "Unable to read official calendar cache: $($_.Exception.Message)"
        }
    }

    if ($forceWorkdays -contains $dateText) {
        return [pscustomobject]@{
            ShouldRun = $true
            Reason = "manual workday override"
        }
    }

    if ($forceHolidays -contains $dateText) {
        return [pscustomobject]@{
            ShouldRun = $false
            Reason = "manual holiday override"
        }
    }

    if ($officialWorkdays -contains $dateText) {
        return [pscustomobject]@{
            ShouldRun = $true
            Reason = "official adjusted workday"
        }
    }

    if ($officialHolidays -contains $dateText) {
        return [pscustomobject]@{
            ShouldRun = $false
            Reason = "official holiday"
        }
    }

    $isWeekend = $Date.DayOfWeek -in @(
        [System.DayOfWeek]::Saturday,
        [System.DayOfWeek]::Sunday
    )

    if ($isWeekend) {
        return [pscustomobject]@{
            ShouldRun = $false
            Reason = "default weekend rule"
        }
    }

    return [pscustomobject]@{
        ShouldRun = $true
        Reason = "default weekday rule"
    }
}

function Get-CustomClockInTime {
    param(
        [Parameter(Mandatory = $true)]
        [datetime]$Date
    )

    if (-not (Test-Path -LiteralPath $DailyPlansFile -PathType Leaf)) {
        return ""
    }
    try {
        $data = Get-Content -LiteralPath $DailyPlansFile -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $dateText = $Date.ToString("yyyy-MM-dd")
        $dateProperty = $data.plans.PSObject.Properties |
            Where-Object { $_.Name -eq $dateText } |
            Select-Object -First 1
        if ($null -eq $dateProperty) {
            return ""
        }
        return [string]$dateProperty.Value.clock_in
    }
    catch {
        throw "Unable to read daily exact plans: $($_.Exception.Message)"
    }
}

function Test-TodayMorningAlreadySucceeded {
    if (-not (Test-Path -LiteralPath $MorningPlanFile -PathType Leaf)) {
        return $false
    }
    try {
        $record = Get-Content -LiteralPath $MorningPlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
        return (
            [string]$record.date -eq (Get-Date).ToString("yyyy-MM-dd") -and
            [string]$record.status -eq "success"
        )
    }
    catch {
        return $false
    }
}

function Test-OfficialCalendarYear {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Year
    )

    if (-not (Test-Path -LiteralPath $OfficialCalendarFile -PathType Leaf)) {
        return $false
    }

    try {
        $cache = Get-Content -LiteralPath $OfficialCalendarFile -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $yearText = $Year.ToString()
        $yearProperty = $cache.years.PSObject.Properties |
            Where-Object { $_.Name -eq $yearText } |
            Select-Object -First 1
        return ($null -ne $yearProperty) -and (@($yearProperty.Value.holidays).Count -gt 0)
    }
    catch {
        return $false
    }
}

function Ensure-OfficialCalendarYear {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Year
    )

    if (Test-OfficialCalendarYear -Year $Year) {
        Write-Log "Official calendar cache is ready for year=$Year."
        return $true
    }

    if (-not (Test-Path -LiteralPath $HolidaySyncScript -PathType Leaf)) {
        Write-Log "ERROR: Holiday sync script not found: $HolidaySyncScript"
        return $false
    }

    Write-Log "Official calendar for year=$Year is missing; syncing from gov.cn."
    $previousErrorActionPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"
        $syncOutput = & $PythonExe $HolidaySyncScript `
            --year $Year `
            --cache $OfficialCalendarFile 2>&1
        $syncExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    foreach ($outputLine in $syncOutput) {
        Write-Log "[CALENDAR SYNC] $($outputLine.ToString())"
    }

    if (($syncExitCode -ne 0) -or (-not (Test-OfficialCalendarYear -Year $Year))) {
        Write-Log "ERROR: Official calendar sync failed; normal clock-in is stopped for safety."
        return $false
    }

    Write-Log "Official calendar sync completed for year=$Year."
    return $true
}

function Test-AppiumServer {
    $tcpClient = New-Object System.Net.Sockets.TcpClient

    try {
        $connectTask = $tcpClient.ConnectAsync($AppiumHost, $AppiumPort)
        return $connectTask.Wait(1000) -and $tcpClient.Connected
    }
    catch {
        return $false
    }
    finally {
        $tcpClient.Dispose()
    }
}

function Ensure-AppiumServer {
    if (Test-AppiumServer) {
        Write-Log "Appium is already listening on ${AppiumHost}:$AppiumPort."
        return $true
    }

    if (-not (Test-Path -LiteralPath $AppiumCmd -PathType Leaf)) {
        Write-Log "ERROR: Appium launcher not found: $AppiumCmd"
        return $false
    }

    Write-Log "Appium is not listening; starting it in the background."

    Start-Process `
        -FilePath $AppiumCmd `
        -ArgumentList @("--address", $AppiumHost, "--port", $AppiumPort) `
        -WorkingDirectory $ProjectDir `
        -WindowStyle Hidden | Out-Null

    $appiumDeadline = (Get-Date).AddSeconds(30)

    while ((Get-Date) -lt $appiumDeadline) {
        if (Test-AppiumServer) {
            Write-Log "Appium started successfully."
            return $true
        }

        Start-Sleep -Seconds 1
    }

    Write-Log "ERROR: Appium did not become ready within 30 seconds."
    return $false
}

function Invoke-PythonFlow {
    param(
        [switch]$DryRun
    )

    if (-not (Ensure-AppiumServer)) {
        return 1
    }

    $pythonArguments = @($ScriptFile)

    if ($DryRun) {
        $pythonArguments += "--dry-run"
    }

    Write-Log "Starting Python flow. dryRun=$DryRun"

    # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecord
    # objects. Temporarily allow those records so the native exit code can
    # drive the retry loop instead of bypassing it as a PowerShell exception.
    $previousErrorActionPreference = $ErrorActionPreference

    try {
        $ErrorActionPreference = "Continue"
        $pythonOutput = & $PythonExe @pythonArguments 2>&1
        $pythonExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    foreach ($outputLine in $pythonOutput) {
        Write-Log "[PYTHON] $($outputLine.ToString())"
    }

    Write-Log "Python flow finished. exitCode=$pythonExitCode"
    return $pythonExitCode
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Log "ERROR: Python executable not found: $PythonExe"
    exit 1
}

if (-not (Test-Path -LiteralPath $ScriptFile -PathType Leaf)) {
    Write-Log "ERROR: Python script not found: $ScriptFile"
    exit 1
}

if (-not (Test-AutomationSafety)) {
    exit 4
}

if ($Immediate -and (-not $TestMode)) {
    Write-Log "ERROR: -Immediate is allowed only together with -TestMode."
    exit 1
}

if ($ManualClockIn -and ($TestMode -or $Immediate)) {
    Write-Log "ERROR: -ManualClockIn cannot be combined with test-mode switches."
    exit 1
}

if ($PlannedClockIn -and ($TestMode -or $Immediate -or $ManualClockIn)) {
    Write-Log "ERROR: -PlannedClockIn cannot be combined with manual or test switches."
    exit 1
}

if ($PlannedClockIn -and (
    [string]::IsNullOrWhiteSpace($PlannedDate) -or
    [string]::IsNullOrWhiteSpace($PlannedTime)
)) {
    Write-Log "ERROR: -PlannedClockIn requires -PlannedDate and -PlannedTime."
    exit 1
}

$isExplicitClockIn = $ManualClockIn -or $PlannedClockIn
$customTarget = $null
$customDailyClockIn = $false

# The morning scheduler lock prevents the daily task and its exact-plan backup
# from choosing two targets. Manual clock-in deliberately bypasses this lock;
# all actual device work is protected later by the shared device lock.
$mutexName = "Local\AttendanceAutomationHubMorningScheduler"
$mutex = New-Object System.Threading.Mutex($false, $mutexName)
$mutexAcquired = $false
$targetTime = $null
$attempt = 0

try {
    try {
        if ($ManualClockIn) {
            $mutexAcquired = $true
        }
        else {
            $mutexAcquired = $mutex.WaitOne(0, $false)
        }
    }
    catch [System.Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }

    if (-not $mutexAcquired) {
        Write-Log "Another scheduler instance is already running; exiting."
        if ($ManualClockIn) {
            exit 3
        }
        exit 0
    }

    Write-Log "Scheduler started. testMode=$TestMode; manualClockIn=$ManualClockIn; plannedClockIn=$PlannedClockIn; maxAttempts=$MaxAttempts"

    $now = Get-Date
    $windowStart = $now.Date.AddHours($WindowStartHour).AddMinutes($WindowStartMinute)
    $windowEnd = $now.Date.AddHours($WindowEndHour).AddMinutes($WindowEndMinute)

    if ($PlannedClockIn) {
        $culture = [System.Globalization.CultureInfo]::InvariantCulture
        $plannedTarget = [datetime]::ParseExact(
            "$PlannedDate $PlannedTime",
            "yyyy-MM-dd HH:mm",
            $culture,
            [System.Globalization.DateTimeStyles]::None
        )
        if ($now.Date -ne $plannedTarget.Date) {
            Write-Log "PLANNED MODE: target date $PlannedDate is not today; stale catch-up skipped."
            exit 0
        }
    }

    if ((-not $isExplicitClockIn) -and (-not $TestMode)) {
        $customClockIn = Get-CustomClockInTime -Date $now
        if (-not [string]::IsNullOrWhiteSpace($customClockIn)) {
            $culture = [System.Globalization.CultureInfo]::InvariantCulture
            $customTarget = [datetime]::ParseExact(
                "$($now.ToString('yyyy-MM-dd')) $customClockIn",
                "yyyy-MM-dd HH:mm",
                $culture,
                [System.Globalization.DateTimeStyles]::None
            )
            $customDailyClockIn = $true
            Write-Log "DAILY PLAN: exact clock-in $customClockIn replaces today's random target."
            if (Test-TodayMorningAlreadySucceeded) {
                Write-Log "DAILY PLAN: today's exact-plan flow already succeeded; duplicate run skipped."
                exit 0
            }
        }
    }

    if ($ManualClockIn) {
        Write-Log "CALENDAR: bypassed for an explicit manual clock-in request."
    }
    elseif ($PlannedClockIn) {
        Write-Log "CALENDAR: bypassed for the explicit per-date clock-in plan."
    }
    elseif ($customDailyClockIn) {
        Write-Log "CALENDAR: bypassed because the per-date clock-in plan has priority."
    }
    elseif (-not $TestMode) {
        if (-not (Ensure-OfficialCalendarYear -Year $now.Year)) {
            Write-MorningPlan -Status "failed" -Message "Official calendar sync failed."
            exit 1
        }

        try {
            $calendarDecision = Get-CalendarDecision -Date $now
        }
        catch {
            Write-Log "CALENDAR ERROR: $($_.Exception.Message)"
            Write-MorningPlan -Status "failed" -Message "Calendar configuration could not be read."
            exit 1
        }

        Write-Log "CALENDAR: date=$($now.ToString('yyyy-MM-dd')); reason=$($calendarDecision.Reason)."

        if (-not $calendarDecision.ShouldRun) {
            Write-Log "CALENDAR: today is not a workday; morning flow skipped."
            Write-MorningPlan -Status "skipped" -Message $calendarDecision.Reason
            exit 0
        }

        Write-Log "CALENDAR: today is a workday; scheduler may proceed."
    }
    else {
        Write-Log "CALENDAR: bypassed in test mode."
    }

    if ($ManualClockIn) {
        $delaySeconds = 0
        $targetTime = $now
        Write-Log "MANUAL MODE: real clock-in is requested immediately."
    }
    elseif ($PlannedClockIn) {
        $delaySeconds = 0
        $targetTime = $plannedTarget
        Write-Log "PLANNED MODE: exact clock-in target=$($targetTime.ToString('yyyy-MM-dd HH:mm'))."
    }
    elseif ($customDailyClockIn) {
        $targetTime = $customTarget
        $delaySeconds = [Math]::Max(
            0,
            [Math]::Ceiling(($targetTime - (Get-Date)).TotalSeconds)
        )
        Write-Log "DAILY PLAN: waiting for exact target=$($targetTime.ToString('yyyy-MM-dd HH:mm'))."
        Write-MorningPlan `
            -Status "waiting" `
            -TargetTime $targetTime `
            -Message "Waiting for the exact per-date clock-in plan."
    }
    elseif ($TestMode) {
        # Fast timer test: wait 5-15 seconds and never perform the real click.
        if ($Immediate) {
            $delaySeconds = 0
        }
        else {
            $delaySeconds = Get-Random -Minimum 5 -Maximum 16
        }
        $targetTime = $now.AddSeconds($delaySeconds)

        Write-Log "TEST MODE: real clock-in click is disabled."
        Write-Log "Test delay=$delaySeconds seconds; target=$($targetTime.ToString('HH:mm:ss'))."
    }
    else {
        if ($now -gt $windowEnd) {
            Write-Log "Current time is after 09:30; today's morning flow is skipped."
            Write-MorningPlan -Status "skipped" -Message "Started after the daily window."
            exit 0
        }

        # Before 09:00, randomize across the full 09:00-09:30 window.
        # After 09:00, randomize only across now-09:30.
        if ($now -lt $windowStart) {
            $randomRangeStart = $windowStart
        }
        else {
            $randomRangeStart = $now
        }

        $maxDelaySeconds = [Math]::Max(
            0,
            [Math]::Floor(($windowEnd - $randomRangeStart).TotalSeconds)
        )

        if ($maxDelaySeconds -eq 0) {
            $randomOffsetSeconds = 0
        }
        else {
            # Get-Random excludes Maximum, so add one to include the endpoint.
            $randomOffsetSeconds = Get-Random `
                -Minimum 0 `
                -Maximum ($maxDelaySeconds + 1)
        }

        $targetTime = $randomRangeStart.AddSeconds($randomOffsetSeconds)
        $delaySeconds = [Math]::Max(
            0,
            [Math]::Ceiling(($targetTime - (Get-Date)).TotalSeconds)
        )

        Write-Log "Window=$($windowStart.ToString('HH:mm:ss'))~$($windowEnd.ToString('HH:mm:ss'))."
        Write-Log "Remaining-window start=$($randomRangeStart.ToString('HH:mm:ss'))."
        Write-Log "Random delay=$delaySeconds seconds; target=$($targetTime.ToString('HH:mm:ss'))."
        Write-MorningPlan `
            -Status "waiting" `
            -TargetTime $targetTime `
            -Message "Waiting for the randomly selected target time."
    }

    if ($delaySeconds -gt 0) {
        Start-Sleep -Seconds $delaySeconds
    }

    if ((-not $TestMode) -and ((Get-Date).ToString("HH:mm") -ge "23:55")) {
        Write-Log "CROSS-DAY SAFETY: real clock-in cannot start from 23:55 onward."
        exit 5
    }

    # Do not clock in if the machine resumes after 09:30.
    if ((-not $TestMode) -and (-not $isExplicitClockIn) -and (-not $customDailyClockIn) -and ((Get-Date) -gt $windowEnd)) {
        Write-Log "The machine resumed after 09:30; today's morning flow is skipped."
        Write-MorningPlan `
            -Status "skipped" `
            -TargetTime $targetTime `
            -Message "The machine resumed after the daily window."
        exit 0
    }

    $attempt = 0

    while ($true) {
        $attempt += 1
        Write-Log "Starting attempt #$attempt."
        Write-MorningPlan `
            -Status "running" `
            -TargetTime $targetTime `
            -Attempts $attempt `
            -Message "Attendance flow is running."

        $deviceMutex = New-Object System.Threading.Mutex(
            $false,
            "Local\AttendanceAutomationHubDevice"
        )
        $deviceMutexAcquired = $false
        try {
            try {
                $deviceMutexAcquired = $deviceMutex.WaitOne(5000, $false)
            }
            catch [System.Threading.AbandonedMutexException] {
                $deviceMutexAcquired = $true
            }
            if (-not $deviceMutexAcquired) {
                Write-Log "DEVICE BUSY: another attendance or phone-control action is active."
                $exitCode = 3
            }
            else {
                $exitCode = Invoke-PythonFlow -DryRun:$TestMode
            }
        }
        finally {
            if ($deviceMutexAcquired) {
                $deviceMutex.ReleaseMutex()
            }
            $deviceMutex.Dispose()
        }

        if ($exitCode -eq 0) {
            Write-Log "Scheduler completed successfully."
            Write-MorningPlan `
                -Status "success" `
                -TargetTime $targetTime `
                -Attempts $attempt `
                -ExitCode 0 `
                -Message "Morning clock-in flow completed successfully."
            exit 0
        }

        if ($TestMode) {
            Write-Log "Test flow failed; test mode does not retry."
            exit $exitCode
        }

        if ($attempt -ge $MaxAttempts) {
            Write-Log "Flow failed after $attempt attempt(s); stopping at the retry limit."
            Write-MorningPlan `
                -Status "failed" `
                -TargetTime $targetTime `
                -Attempts $attempt `
                -ExitCode $exitCode `
                -Message "Morning flow stopped at the retry limit."
            exit $exitCode
        }

        if ((-not $isExplicitClockIn) -and (-not $customDailyClockIn) -and ((Get-Date) -ge $windowEnd)) {
            Write-Log "Flow failed and the 09:30 deadline has been reached; stopping retries."
            Write-MorningPlan `
                -Status "failed" `
                -TargetTime $targetTime `
                -Attempts $attempt `
                -ExitCode $exitCode `
                -Message "Morning flow reached the daily deadline."
            exit $exitCode
        }

        # Retry transient Python/Appium failures without another random wait.
        Write-Log "Flow failed; retrying in $RetryDelaySeconds seconds."
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    Write-MorningPlan `
        -Status "failed" `
        -TargetTime $targetTime `
        -Attempts $attempt `
        -ExitCode 1 `
        -Message "Fatal scheduler error: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($mutexAcquired -and (-not $ManualClockIn)) {
        $mutex.ReleaseMutex()
    }

    $mutex.Dispose()
}
