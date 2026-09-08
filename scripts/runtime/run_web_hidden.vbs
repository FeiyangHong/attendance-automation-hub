Option Explicit

Dim shell, fileSystem, scriptDir, projectDir, powershellExe, runner, command, exitCode

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

scriptDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
projectDir = fileSystem.GetParentFolderName(fileSystem.GetParentFolderName(scriptDir))
powershellExe = shell.ExpandEnvironmentStrings("%SystemRoot%") & _
    "\System32\WindowsPowerShell\v1.0\powershell.exe"
runner = scriptDir & "\run_web.ps1"

shell.CurrentDirectory = projectDir
command = Chr(34) & powershellExe & Chr(34) & _
    " -NoProfile -NonInteractive -ExecutionPolicy Bypass -File " & _
    Chr(34) & runner & Chr(34)
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
