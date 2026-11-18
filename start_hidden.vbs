Option Explicit

Dim objWMIService, colProcesses, WshShell
Set objWMIService = GetObject("winmgmts:\\.\root\cimv2")
Set colProcesses = objWMIService.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE (Name = 'python.exe' OR Name = 'pythonw.exe') AND CommandLine LIKE '%supervisor.py%'")

If colProcesses.Count > 0 Then
    WScript.Quit 0
End If

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\marketplace_agent"
WshShell.Run """C:\marketplace_agent\venv\Scripts\pythonw.exe"" ""C:\marketplace_agent\supervisor.py""", 0, False
