param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("c2", "d", "e")]
    [string]$IconId,
    # Override only for isolated tests or a deliberately chosen shortcut folder.
    [string]$ShortcutDirectory = [Environment]::GetFolderPath("Desktop")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$Launcher = Join-Path $ProjectDir "Attendance Hub.vbs"
$IconFile = Join-Path $ProjectDir "src\attendance_hub\desktop\assets\icons\$IconId.ico"
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) { throw "Launcher is missing." }
if (-not (Test-Path -LiteralPath $IconFile -PathType Leaf)) { throw "Icon asset is missing." }
if ([string]::IsNullOrWhiteSpace($ShortcutDirectory)) { throw "Desktop folder is unavailable." }
$ShortcutDirectory = (Resolve-Path -LiteralPath $ShortcutDirectory).ProviderPath
if (-not (Test-Path -LiteralPath $ShortcutDirectory -PathType Container)) {
    throw "Shortcut folder does not exist."
}
$Shell = New-Object -ComObject WScript.Shell
$Matches = @(
    Get-ChildItem -LiteralPath $ShortcutDirectory -File -Filter "*.lnk" |
        ForEach-Object {
            try {
                $Link = $Shell.CreateShortcut($_.FullName)
                if ($Link.TargetPath -and [IO.Path]::GetFullPath($Link.TargetPath) -ieq $Launcher) {
                    [pscustomobject]@{ Link = $Link; PreviousIcon = $Link.IconLocation }
                }
            }
            catch { # Ignore unreadable unrelated shortcuts; never modify them.
            }
        }
)
if ($Matches.Count -eq 0) {
    $NewLink = Join-Path $ShortcutDirectory "Attendance Hub.lnk"
    if (Test-Path -LiteralPath $NewLink) {
        throw "Attendance Hub.lnk already exists but points elsewhere; it was not changed."
    }
    $Link = $Shell.CreateShortcut($NewLink)
    $Link.TargetPath = $Launcher
    $Link.WorkingDirectory = $ProjectDir
    $Link.Description = "Attendance Automation Hub"
    $Matches = @([pscustomobject]@{ Link = $Link; PreviousIcon = $Link.IconLocation })
}
$Updated = @()
try {
    foreach ($Item in $Matches) {
        $Updated += $Item
        $Item.Link.IconLocation = "$IconFile,0"
        $Item.Link.Save()
    }
}
catch {
    foreach ($Item in $Updated) {
        $Item.Link.IconLocation = $Item.PreviousIcon
        $Item.Link.Save()
    }
    throw
}
# Ask Explorer to refresh icons without restarting it or clearing its caches.
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AttendanceHubIconRefresh {
    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
'@
    [AttendanceHubIconRefresh]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
}
catch { # Saving the shortcuts succeeded; Explorer may refresh them slightly later.
}
Write-Output "Updated $($Matches.Count) Attendance Hub shortcut(s)."
