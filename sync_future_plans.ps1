param(
    [datetime]$From = (Get-Date)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectDir = $PSScriptRoot
$PlanFile = Join-Path $ProjectDir "config\daily_plans.json"
$SyncScript = Join-Path $ProjectDir "sync_daily_plan.ps1"
if (-not (Test-Path -LiteralPath $PlanFile -PathType Leaf)) { exit 0 }
$data = Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($property in $data.plans.PSObject.Properties) {
    $planDate = [datetime]::ParseExact(
        $property.Name,
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    if ($planDate.Date -lt $From.Date) { continue }
    $clockIn = [string]$property.Value.clock_in
    $clockOut = [string]$property.Value.clock_out
    & $SyncScript -PlanDate $property.Name -ClockIn $clockIn -ClockOut $clockOut
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to synchronize plan for $($property.Name)."
    }
}
