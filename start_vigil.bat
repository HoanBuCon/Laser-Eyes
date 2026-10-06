@echo off
:: =====================================================================
::  VIGIL AI CLASSROOM - the single way to start the system.
::
::  Double-click this file, or run from a terminal:
::      start_vigil.bat
::      start_vigil.bat --port 8080
::      start_vigil.bat --host 0.0.0.0 --demo-token "choose-a-secret"   (LAN)
::
::  Everything else - seat calibration, live analysis, replay, review and
::  export - is done in the website this command opens.
:: =====================================================================
setlocal
cd /d "%~dp0"
title VIGIL AI Classroom

set "PYTHON_EXE="
if exist "venv\Scripts\python.exe" set "PYTHON_EXE=venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

if not defined PYTHON_EXE (
    echo [VIGIL] No Python environment found. Creating it now ^(first run only^)...
    call setup_env.bat
    if errorlevel 1 exit /b 1
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    ".venv\Scripts\python.exe" -m pip install -r requirements-hpe.txt
)

"%PYTHON_EXE%" server.py --open-browser %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [VIGIL] The server stopped with exit code %EXIT_CODE%.
    pause
)
endlocal & exit /b %EXIT_CODE%
