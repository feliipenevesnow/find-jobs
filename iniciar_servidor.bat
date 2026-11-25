@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Find Jobs - Servidor Web (Porta 8090)

if exist ".venv\Scripts\python.exe" goto run_server

echo [Setup] Criando ambiente virtual (.venv) e instalando dependencias...
python -m venv .venv
if errorlevel 1 goto setup_error

".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto setup_error

if not exist "curriculo.local.md" copy /Y "curriculo.md" "curriculo.local.md" >nul 2>&1
if not exist ".env" copy /Y ".env.example" ".env" >nul 2>&1

:run_server
".venv\Scripts\python.exe" -u server.py --host 0.0.0.0 --port 8090
if errorlevel 1 goto run_error
goto :eof

:setup_error
echo [ERRO] Falha ao preparar o ambiente Python.
pause
exit /b 1

:run_error
echo.
echo [ERRO] O servidor Find Jobs foi encerrado com erro.
pause
exit /b 1
