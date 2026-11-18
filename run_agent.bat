@echo off
setlocal
cd /d "%~dp0" || exit /b 1
"venv\Scripts\python.exe" "supervisor.py"
exit /b %errorlevel%
