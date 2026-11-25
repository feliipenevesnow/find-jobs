@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Prepare o ambiente conforme a primeira instalacao no README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py --diaria --abrir
if errorlevel 1 pause
