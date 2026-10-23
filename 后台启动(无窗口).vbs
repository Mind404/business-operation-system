' Background startup script (no console window)
' Double-click to run system in background without black console window
' To stop: open Task Manager, end python.exe process

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' Check Python
Set exec = WshShell.Exec("python --version")
If exec.Status <> 0 Then
    MsgBox "Python not found! Please install Python 3.8+ and check 'Add Python to PATH'.", vbCritical, "Startup Failed"
    WScript.Quit
End If

' Check dependencies
Set exec = WshShell.Exec("python -c ""import flask""")
If exec.Status <> 0 Then
    WshShell.Run "pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple", 0, True
End If

' Check database
If Not fso.FileExists(scriptDir & "\business.db") Then
    WshShell.Run "python init_db.py", 0, True
End If

' Start app in background
WshShell.Run "python app.py --no-debug", 0, False

' Wait 3 seconds then open browser
WScript.Sleep 3000
WshShell.Run "http://127.0.0.1:5000", 1, False

MsgBox "System started in background!" & vbCrLf & vbCrLf & _
       "URL: http://127.0.0.1:5000" & vbCrLf & _
       "Browser opened automatically." & vbCrLf & vbCrLf & _
       "To stop: open Task Manager, end python.exe process", vbInformation, "Started"
