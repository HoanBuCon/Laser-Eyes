@echo off
setlocal
cd /d "%~dp0"

echo [VIGIL AI] Kiem tra moi truong Python...

set "PYTHON_CMD="

:: 1. Thu tim py launcher voi 3.12, 3.11, 3.10, -3
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3.12 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=py -3.12"
        goto :found_python
    )
    py -3.11 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=py -3.11"
        goto :found_python
    )
    py -3.10 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=py -3.10"
        goto :found_python
    )
    py -3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=py -3"
        goto :found_python
    )
)

:: 2. Thu tim lenh python trong PATH
where python >nul 2>nul
if %errorlevel% equ 0 (
    python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if %errorlevel% equ 0 (
        set "PYTHON_CMD=python"
        goto :found_python
    )
)

echo [LOI] Khong tim thay Python >= 3.10 phu hop tren may (yeu cau Python 3.10, 3.11 hoac 3.12).
echo Vui long cai dat Python 3.11 hoac 3.12 tu python.org va tich vao 'Add python.exe to PATH'.
pause
exit /b 1

:found_python
echo [VIGIL AI] Su dung trinh thuc thi: %PYTHON_CMD%

if not exist ".venv\Scripts\python.exe" (
    echo [VIGIL AI] Dang tao moi truong ao .venv...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong the tao moi truong ao .venv.
        pause
        exit /b 1
    )
)

echo [VIGIL AI] Dang nang cap pip va cai dat cac goi thu vien tu requirements.txt...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [LOI] Cai dat thu vien that bai.
    pause
    exit /b 1
)

echo [VIGIL AI] Cai dat moi truong hoan tat thanh cong!
endlocal
