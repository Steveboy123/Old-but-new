Option Explicit
Dim shell, fso, appDir, pythonw, launcher, installer, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = appDir & "\.venv\Scripts\pythonw.exe"
launcher = appDir & "\control_nexus_launcher.py"
installer = appDir & "\install_windows.bat"

If Not fso.FileExists(pythonw) Then
    shell.Run Chr(34) & installer & Chr(34), 1, True
End If

If fso.FileExists(pythonw) Then
    cmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & launcher & Chr(34)
    shell.Run cmd, 0, False
Else
    MsgBox "Control Nexus setup did not finish. Run install_windows.bat and try again.", 48, "Control Nexus"
End If
