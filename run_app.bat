@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    if not exist "venv\Scripts\python.exe" (
        echo Chua co moi truong chay. Dang khoi tao lan dau...
        call setup_env.bat
    )
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" main.py %*
)
