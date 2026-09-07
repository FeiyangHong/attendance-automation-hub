param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = [System.IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot "..\..")
)
$AppConfigFile = Join-Path $ProjectDir "config\app_config.json"
if (-not (Test-Path -LiteralPath $AppConfigFile -PathType Leaf)) {
    throw "Device configuration is missing."
}
$appConfig = Get-Content -LiteralPath $AppConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not [bool]$appConfig.device_access_enabled) {
    throw "Device access is disabled."
}
$deviceUdid = [string]$appConfig.device_udid
if ([string]::IsNullOrWhiteSpace($deviceUdid)) {
    throw "Device UDID is not configured."
}

$scrcpyCommand = Get-Command scrcpy.exe -ErrorAction SilentlyContinue
if ($null -ne $scrcpyCommand) {
    $scrcpyExe = $scrcpyCommand.Source
}
else {
    $candidate = Get-ChildItem `
        -LiteralPath (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages") `
        -Filter "scrcpy.exe" `
        -File `
        -Recurse `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*Genymobile.scrcpy*" } |
        Select-Object -First 1
    if ($null -eq $candidate) {
        throw "scrcpy.exe was not found in PATH or the WinGet package directory."
    }
    $scrcpyExe = $candidate.FullName
}

$mutex = New-Object System.Threading.Mutex(
    $false,
    "Local\AttendanceAutomationHubDevice"
)
$acquired = $false
try {
    try {
        $acquired = $mutex.WaitOne(5000, $false)
    }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        throw "The phone is busy with another attendance or control action."
    }
    $process = Start-Process `
        -FilePath $scrcpyExe `
        -ArgumentList @("-s", $deviceUdid) `
        -WorkingDirectory $ProjectDir `
        -PassThru
    Write-Output "scrcpy started. pid=$($process.Id); device=$deviceUdid"
}
finally {
    if ($acquired) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
