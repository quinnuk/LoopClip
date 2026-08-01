' LoopClip - silent launcher
' Double-click this (or point a desktop shortcut at it) to start the app
' with no console/terminal window flashing up - it opens the same way any
' normal Windows app does.
'
' Requires pythonw.exe to be available (it ships alongside python.exe in
' every standard Python install, including the Microsoft Store version).

Set objShell = CreateObject("WScript.Shell")
strPath = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
objShell.CurrentDirectory = strPath

' 0 = hidden window, False = don't wait for it to exit
objShell.Run "pythonw main.py", 0, False
