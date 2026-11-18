Option Explicit
Dim shell, fs, root, python, supervisor
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = fs.BuildPath(root, "venv\Scripts\pythonw.exe")
supervisor = fs.BuildPath(root, "supervisor.py")
shell.CurrentDirectory = root
' supervisor.py acquires the exclusive OS lock before launching a worker.
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & supervisor & Chr(34), 0, False
