Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$SourceDir = Join-Path $ProjectDir "src"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourceDir
}
else {
    "$SourceDir;$env:PYTHONPATH"
}
$Failures = 0
$Warnings = 0

function Write-Result {
    param(
        [ValidateSet("PASS", "WARN", "FAIL")]
        [string]$Level,
        [string]$Message
    )

    if ($Level -eq "FAIL") { $script:Failures++ }
    if ($Level -eq "WARN") { $script:Warnings++ }
    Write-Host "[$Level] $Message"
}

function Test-Tool {
    param([string]$Command, [string[]]$Arguments)

    $resolved = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $resolved) {
        Write-Result "FAIL" "$Command is not available in PATH."
        return $false
    }
    try {
        $text = (& $Command @Arguments 2>&1 | Out-String).Trim()
        $firstLine = ($text -split "`r?`n" | Select-Object -First 1)
        Write-Result "PASS" "$Command - $firstLine"
        return $true
    }
    catch {
        Write-Result "FAIL" "$Command failed: $($_.Exception.Message)"
        return $false
    }
}

Write-Host "Feishu automation environment diagnostic"
Write-Host "Project: $ProjectDir"
Write-Host ""

$requiredFiles = @(
    "hub.ps1",
    "Attendance Hub.vbs",
    "src\attendance_hub\automation\feishu_flow.py",
    "src\attendance_hub\core\app_config.py",
    "src\attendance_hub\core\attendance_history.py",
    "src\attendance_hub\core\daily_plans.py",
    "src\attendance_hub\core\holiday_sync.py",
    "src\attendance_hub\desktop\control_panel.py",
    "scripts\runtime\run_morning.ps1",
    "scripts\runtime\run_clock_out.ps1",
    "scripts\runtime\run_web.ps1",
    "scripts\runtime\run_web_hidden.vbs",
    "scripts\operations\schedule_clock_out.ps1",
    "scripts\operations\sync_daily_plan.ps1",
    "scripts\setup\install_daily_task.ps1",
    "config\calendar_overrides.json",
    "config\daily_plans.json",
    "config\official_holidays.json"
)
$missingFiles = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $ProjectDir $_) -PathType Leaf)
})
if ($missingFiles.Count -eq 0) {
    Write-Result "PASS" "All required project files are present."
}
else {
    Write-Result "FAIL" ("Missing files: " + ($missingFiles -join ", "))
}

$pythonOk = Test-Tool "python" @("--version")
$adbOk = Test-Tool "adb" @("version")
Test-Tool "scrcpy" @("--version") | Out-Null
Test-Tool "java" @("-version") | Out-Null
Test-Tool "node" @("--version") | Out-Null
$appiumOk = Test-Tool "appium.cmd" @("--version")

if ([string]::IsNullOrWhiteSpace($env:JAVA_HOME)) {
    Write-Result "FAIL" "JAVA_HOME is not set."
}
else {
    Write-Result "PASS" "JAVA_HOME=$env:JAVA_HOME"
}
if ([string]::IsNullOrWhiteSpace($env:ANDROID_HOME)) {
    Write-Result "FAIL" "ANDROID_HOME is not set."
}
else {
    Write-Result "PASS" "ANDROID_HOME=$env:ANDROID_HOME"
}

if ($adbOk) {
    $deviceOutput = (& adb devices -l 2>&1 | Out-String)
    $connected = @($deviceOutput -split "`r?`n" | Where-Object {
        $_ -match "^\S+\s+device(?:\s|$)"
    })
    if ($connected.Count -gt 0) {
        Write-Result "PASS" ("Authorized Android device: " + ($connected -join "; "))
    }
    elseif ($deviceOutput -match "unauthorized") {
        Write-Result "FAIL" "Android device is unauthorized; unlock it and accept USB debugging."
    }
    else {
        Write-Result "FAIL" "No authorized Android device was found."
    }
}

if ($appiumOk) {
    $drivers = (& appium.cmd driver list --installed 2>&1 | Out-String)
    if ($drivers -match "uiautomator2") {
        Write-Result "PASS" "UiAutomator2 driver is installed."
    }
    else {
        Write-Result "FAIL" "UiAutomator2 is missing; run: appium.cmd driver install uiautomator2"
    }
}

$venvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    & $venvPython -c "import appium, selenium, attendance_hub.server" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Result "PASS" "Python virtual environment and packages are ready."
    }
    else {
        Write-Result "FAIL" "Python packages are incomplete; install requirements.txt."
    }
}
else {
    Write-Result "FAIL" ".venv is missing; create it as described in docs/MIGRATION.md."
}

Write-Host ""
Write-Host "Result: $Failures failure(s), $Warnings warning(s)."
if ($Failures -gt 0) { exit 1 }
exit 0
