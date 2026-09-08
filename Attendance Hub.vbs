Option Explicit

Dim shell, fileSystem, baseDir, launcher, command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

baseDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
launcher = baseDir & "\hub.ps1"
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " _
    & Chr(34) & launcher & Chr(34) & " desktop"

shell.Run command, 0, False
