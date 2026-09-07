param(
    [string]$OutputFile = "attendance-automation-hub.zip"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$OutputPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir $OutputFile))
if ([System.IO.Path]::GetDirectoryName($OutputPath) -ne $ProjectDir) {
    throw "Release archive must be written directly inside the project directory."
}
if ([System.IO.Path]::GetExtension($OutputPath) -ne ".zip") {
    throw "Release archive must use a .zip extension."
}
$tracked = & git -C $ProjectDir ls-files
if ($LASTEXITCODE -ne 0 -or -not $tracked) {
    throw "Unable to read the Git release manifest."
}
if (Test-Path -LiteralPath $OutputPath -PathType Leaf) {
    Remove-Item -LiteralPath $OutputPath -Force
}
& git -C $ProjectDir archive --format=zip --output=$OutputPath HEAD
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
    throw "Release archive creation failed."
}
Write-Host "Release archive created: $OutputPath"
