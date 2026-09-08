param(
    [switch]$TestMode,
    [switch]$NotifyOnFailure,
    [ValidateSet("ui", "remote", "scheduled")]
    [string]$Source = "ui",
    [ValidateRange(1, 900)]
    [int]$AttemptTimeoutSeconds = 120,
    [string]$PlannedDate = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$SourceDir = Join-Path $ProjectDir "src"
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ScriptFile = Join-Path $SourceDir "attendance_hub\automation\feishu_flow.py"
$FlowModule = "attendance_hub.automation.feishu_flow"
$AppiumCmd = Join-Path $env:APPDATA "npm\appium.cmd"
$AppiumHost = "127.0.0.1"
$AppiumPort = 4723
$MaxAttempts = 3
$RetryDelaySeconds = 15
$ResultFile = Join-Path $ProjectDir "config\clock_out_last_result.json"
$AppConfigFile = Join-Path $ProjectDir "config\app_config.json"
$RunId = [guid]::NewGuid().ToString("N")

$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceDir
}
else {
    "$SourceDir;$env:PYTHONPATH"
}

$startedAt = Get-Date
$LogDir = Join-Path (Join-Path $ProjectDir "logs") $startedAt.ToString("yyyy-MM")
$LogFile = Join-Path $LogDir "$($startedAt.ToString('yyyy-MM-dd')).log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$timestamp [CLOCK-OUT] $Message"
    for ($attempt = 1; $attempt -le 20; $attempt += 1) {
        try {
            Add-Content -LiteralPath $LogFile -Value $line -Encoding ASCII
            Write-Host $line
            return
        }
        catch [System.IO.IOException] {
            if ($attempt -eq 20) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Write-ClockOutResult {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message,
        [int]$Attempts = 0,
        [int]$ExitCode = 0
    )

    $result = [ordered]@{
        id = $RunId
        status = $Status
        message = $Message
        attempts = $Attempts
        exit_code = $ExitCode
        source = $Source
        test_mode = [bool]$TestMode
        acknowledged = $false
        timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
    } | ConvertTo-Json

    $resultDirectory = Split-Path -Parent $ResultFile
    New-Item -ItemType Directory -Force -Path $resultDirectory | Out-Null
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($ResultFile, $result + [Environment]::NewLine, $utf8WithoutBom)
}

function Send-FailureNotification {
    param([Parameter(Mandatory = $true)][string]$Message)

    if (-not $NotifyOnFailure) {
        return
    }

    $messageExe = Join-Path $env:SystemRoot "System32\msg.exe"
    if (-not (Test-Path -LiteralPath $messageExe -PathType Leaf)) {
        Write-Log "WARN: Windows message utility is unavailable."
        return
    }

    try {
        & $messageExe $env:USERNAME $Message 2>&1 | Out-Null
        Write-Log "Failure notification sent to the interactive user."
    }
    catch {
        Write-Log "WARN: Unable to display failure notification: $($_.Exception.Message)"
    }
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

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-AppiumServer) {
            Write-Log "Appium started successfully."
            return $true
        }
        Start-Sleep -Seconds 1
    }

    Write-Log "ERROR: Appium did not become ready within 30 seconds."
    return $false
}

function Invoke-ClockOutFlow {
    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $PythonExe
    $processInfo.Arguments = "-m $FlowModule --mode clock-out"
    if ($TestMode) {
        $processInfo.Arguments += " --dry-run"
    }
    $processInfo.WorkingDirectory = $ProjectDir
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $processInfo.EnvironmentVariables["PYTHONUNBUFFERED"] = "1"

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    try {
        if (-not $process.Start()) {
            throw "Unable to start the Python clock-out flow."
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        if (-not $process.WaitForExit($AttemptTimeoutSeconds * 1000)) {
            Write-Log "Attempt exceeded $AttemptTimeoutSeconds seconds; terminating Python pid=$($process.Id)."
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
            $timedOut = $true
            $exitCode = 124
        }
        else {
            $timedOut = $false
            $exitCode = $process.ExitCode
        }

        $output = @($stdoutTask.Result, $stderrTask.Result) -join [Environment]::NewLine
        foreach ($line in ($output -split "`r?`n")) {
            if ($line) {
                Write-Log "[PYTHON] $line"
            }
        }

        return [pscustomobject]@{
            ExitCode = $exitCode
            TimedOut = $timedOut
        }
    }
    finally {
        $process.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Log "ERROR: Python executable not found: $PythonExe"
    Write-ClockOutResult -Status "failed" -Message "Python executable was not found." -ExitCode 1
    Send-FailureNotification "Feishu clock-out failed: Python is unavailable."
    exit 1
}
if (-not (Test-Path -LiteralPath $ScriptFile -PathType Leaf)) {
    Write-Log "ERROR: Python flow not found: $ScriptFile"
    Write-ClockOutResult -Status "failed" -Message "Clock-out flow script was not found." -ExitCode 1
    Send-FailureNotification "Feishu clock-out failed: the flow script is missing."
    exit 1
}

if (-not (Test-AutomationSafety)) {
    Write-ClockOutResult -Status "failed" -Message "Automation safety lock blocked this run." -ExitCode 4
    exit 4
}

if ((-not $TestMode) -and ((Get-Date).ToString("HH:mm") -ge "23:55")) {
    Write-Log "CROSS-DAY SAFETY: real clock-out cannot start from 23:55 onward."
    Write-ClockOutResult -Status "failed" -Message "Cross-day safety blocked a late clock-out run." -ExitCode 5
    exit 5
}
$attendanceRunDate = (Get-Date).Date

if (-not [string]::IsNullOrWhiteSpace($PlannedDate)) {
    try {
        $culture = [System.Globalization.CultureInfo]::InvariantCulture
        $plannedDay = [datetime]::ParseExact(
            $PlannedDate,
            "yyyy-MM-dd",
            $culture,
            [System.Globalization.DateTimeStyles]::None
        )
    }
    catch {
        Write-Log "ERROR: Invalid planned date: $PlannedDate"
        exit 1
    }
    if ((Get-Date).Date -ne $plannedDay.Date) {
        Write-Log "PLANNED MODE: target date $PlannedDate is not today; stale catch-up skipped."
        exit 0
    }
}

$mutex = New-Object System.Threading.Mutex(
    $false,
    "Local\AttendanceAutomationHubDevice"
)
$mutexAcquired = $false
$attempt = 0

try {
    try {
        $mutexAcquired = $mutex.WaitOne(0, $false)
    }
    catch [System.Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }

    if (-not $mutexAcquired) {
        Write-Log "DEVICE BUSY: another attendance or phone-control action is active; exiting."
        Write-ClockOutResult -Status "busy" -Message "Another device action was already running." -ExitCode 3
        Send-FailureNotification "Feishu clock-out did not start because the device was busy."
        exit 3
    }

    Write-Log "Clock-out runner started. testMode=$TestMode"
    if ($TestMode) {
        Write-Log "TEST MODE: the real clock-out click is disabled."
    }

    if (-not (Ensure-AppiumServer)) {
        Write-ClockOutResult -Status "failed" -Message "Appium could not be started." -ExitCode 1
        Send-FailureNotification "Feishu clock-out failed because Appium could not start."
        exit 1
    }

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt += 1) {
        if ((-not $TestMode) -and ((Get-Date).Date -ne $attendanceRunDate)) {
            Write-Log "CROSS-DAY SAFETY: the date changed before retry; stopping."
            Write-ClockOutResult -Status "failed" -Message "The date changed before retry." -Attempts ($attempt - 1) -ExitCode 5
            exit 5
        }
        Write-Log "Starting attempt #$attempt."
        $flowResult = Invoke-ClockOutFlow
        $exitCode = $flowResult.ExitCode
        if ($exitCode -eq 0) {
            Write-Log "Clock-out runner completed successfully."
            Write-ClockOutResult `
                -Status "success" `
                -Message "Clock-out or update-clock flow completed successfully." `
                -Attempts $attempt `
                -ExitCode 0
            exit 0
        }
        if ($TestMode -or $attempt -eq $MaxAttempts) {
            $failureMessage = if ($flowResult.TimedOut) {
                "Clock-out attempt timed out and was terminated."
            }
            else {
                "Clock-out flow failed after $attempt attempt(s)."
            }
            Write-Log "$failureMessage exitCode=$exitCode"
            Write-ClockOutResult `
                -Status "failed" `
                -Message $failureMessage `
                -Attempts $attempt `
                -ExitCode $exitCode
            Send-FailureNotification `
                "Feishu clock-out failed after $attempt attempt(s). Open the control panel and check the log."
            exit $exitCode
        }
        Write-Log "Clock-out flow failed; retrying in $RetryDelaySeconds seconds."
        Start-Sleep -Seconds $RetryDelaySeconds
    }
}
catch {
    Write-Log "FATAL: $($_.Exception.Message)"
    Write-ClockOutResult `
        -Status "failed" `
        -Message "Fatal clock-out runner error: $($_.Exception.Message)" `
        -Attempts $attempt `
        -ExitCode 1
    Send-FailureNotification "Feishu clock-out stopped because of a fatal runner error."
    exit 1
}
finally {
    if ($mutexAcquired) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
