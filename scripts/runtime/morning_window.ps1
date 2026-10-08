function Get-MorningWindow {
    param([Parameter(Mandatory = $true)][string]$ProjectDir)

    $windowFile = Join-Path $ProjectDir "config\morning_window.json"
    $startText = "09:00"
    $endText = "09:30"
    if (Test-Path -LiteralPath $windowFile -PathType Leaf) {
        $data = Get-Content -LiteralPath $windowFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $startText = [string]$data.start
        $endText = [string]$data.end
    }
    foreach ($value in @($startText, $endText)) {
        if ($value -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
            throw "Invalid morning window: use HH:mm in 24-hour format."
        }
    }
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $start = [datetime]::ParseExact($startText, 'HH:mm', $culture)
    $end = [datetime]::ParseExact($endText, 'HH:mm', $culture)
    if ($start -ge $end -or $endText -ge "23:55") {
        throw "Morning window must be within one day and end before 23:55."
    }
    return [pscustomobject]@{
        StartText = $startText
        EndText = $endText
        StartHour = $start.Hour
        StartMinute = $start.Minute
        EndHour = $end.Hour
        EndMinute = $end.Minute
        DurationMinutes = ($end - $start).TotalMinutes
    }
}
