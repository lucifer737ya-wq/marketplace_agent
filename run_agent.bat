@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Не найдено виртуальное окружение venv.
    echo Запустите один раз setup_env.bat - он создаст venv и поставит зависимости.
    pause
    exit /b 1
)
"venv\Scripts\python.exe" "supervisor.py"
exit /b %errorlevel%
