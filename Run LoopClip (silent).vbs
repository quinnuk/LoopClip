' Launches LoopClip with no console window - not even a brief flash - by
' asking pythonw.exe to run main.py directly via the Windows shell instead
' of through a cmd.exe/batch window.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = scriptDir
shell.Run """pythonw"" ""main.py""", 1, False
