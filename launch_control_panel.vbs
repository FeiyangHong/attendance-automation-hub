Option Explicit

Dim shell, fileSystem, baseDir, pythonwExe, appFile, command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

baseDir = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonwExe = baseDir & "\.venv\Scripts\pythonw.exe"
appFile = baseDir & "\feishu_control_panel.pyw"
command = Chr(34) & pythonwExe & Chr(34) & " " & Chr(34) & appFile & Chr(34)

shell.Run command, 0, False
