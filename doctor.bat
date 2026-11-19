@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Нет venv. Сначала запустите setup_env.bat
    pause
    exit /b 1
)
"venv\Scripts\python.exe" doctor.py %*
echo.
pause
