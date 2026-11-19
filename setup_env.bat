@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
echo === marketplace_agent: установка окружения ===
if not exist "venv\Scripts\python.exe" (
    echo Создаю venv на Python 3.11 или 3.12...
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.11 -m venv venv 2>nul || py -3.12 -m venv venv 2>nul || goto :need_python
    ) else (
        python -m venv venv || goto :need_python
    )
)
"venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12)) else 1)"
if errorlevel 1 goto :bad_python
echo Обновляю pip...
"venv\Scripts\python.exe" -m pip install --upgrade pip || goto :failed
echo Устанавливаю зависимости...
"venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
echo Устанавливаю Chromium для Playwright (нужен для отзывов и витрин)...
"venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 echo [WARN] Chromium не установился. Бот запустится, браузерные функции будут недоступны.
if not exist ".env" (
    if exist ".env.example" copy /Y ".env.example" ".env" >nul
    echo [ACTION] Заполните .env своими ключами: notepad .env
)
echo Запускаю диагностику...
"venv\Scripts\python.exe" doctor.py
echo.
echo Готово. Запуск в тихом режиме: start_hidden.vbs
pause
exit /b 0

:need_python
echo [ERROR] Не найден Python 3.11 или 3.12.
echo Установите Python 3.11 (x64) с python.org, отметьте "Add python.exe to PATH",
echo затем запустите setup_env.bat снова.
pause
exit /b 1

:bad_python
echo [ERROR] Окружение создано на неподдерживаемой версии Python.
echo Удалите папку venv и установите Python 3.11 или 3.12, затем повторите setup_env.bat.
pause
exit /b 1

:failed
echo [ERROR] Установка не удалась. Проверьте интернет, права на папку и версию Python.
pause
exit /b 1
