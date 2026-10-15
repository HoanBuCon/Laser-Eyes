@echo off
:: =====================================================================
::  VIGIL AI - double-click shortcut for:   python vigil.py
::
::  vigil.py is the one way to start the system (Hub, website, Classroom,
::  VIGIL One). Options are passed through, e.g.:
::      start_vigil.bat --demo
::      start_vigil.bat --hub-only --lan --demo-token "choose-a-secret"
:: =====================================================================
setlocal
cd /d "%~dp0"
title VIGIL AI

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

"%PYTHON_EXE%" vigil.py %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo [VIGIL] VIGIL stopped with exit code %EXIT_CODE%.
    pause
)
endlocal & exit /b %EXIT_CODE%
