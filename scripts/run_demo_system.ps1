# =====================================================================
# VIGIL AI -- UNIFIED COMPETITION DEMO SYSTEM LAUNCHER
# SRS v2 PIPELINE + WEB MONITOR + REVIEW QUEUE
# =====================================================================

param (
    [ValidateSet("india", "student")]
    [string]$Preset = "india",

    [ValidateSet("replay", "live", "REPLAY", "LIVE")]
    [string]$Mode = "replay",

    [switch]$DebugOverlay = $false,
    [switch]$SkipTests = $false,
    [switch]$NoBrowser = $false,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host " VIGIL AI -- UNIFIED COMPETITION DEMO SYSTEM (SRS v2.0)" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan

# 1. Locate Virtual Environment Python
$PythonExe = ""
if (Test-Path "$ProjectRoot\venv\Scripts\python.exe") {
    $PythonExe = "$ProjectRoot\venv\Scripts\python.exe"
} elseif (Test-Path "$ProjectRoot\.venv\Scripts\python.exe") {
    $PythonExe = "$ProjectRoot\.venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

Write-Host "[+] Using Python interpreter: $PythonExe" -ForegroundColor Green

# 2. Run Test Suite unless skipped
if (-not $SkipTests) {
    Write-Host "[*] Running regression test suite..." -ForegroundColor Yellow
    & $PythonExe -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[-] Test suite failed. Fix failures or pass -SkipTests." -ForegroundColor Red
        exit 1
    }
    Write-Host "[+] All regression tests passed." -ForegroundColor Green
}

# 3. Check Demo Videos
$VideoPath = "$ProjectRoot\demo_video\${Preset}_classroom.mp4"
if (-not (Test-Path $VideoPath)) {
    Write-Host "[-] Demo video not found at: $VideoPath" -ForegroundColor Red
    exit 1
}

# 4. Initialize Database Schema & Seed Data
Write-Host "[*] Initializing SQLite database schema & demo records..." -ForegroundColor Yellow
& $PythonExe -c "from storage.database import init_db; from server import seed_initial_demo_data; init_db(); seed_initial_demo_data(); print('Database schema and demonstration data ready.')"

$DemoUrl = "http://localhost:$Port/demo"
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host " >> DEMO WEB INTERFACE: $DemoUrl" -ForegroundColor Green
Write-Host " >> REST API DOCS:      http://localhost:$Port/docs" -ForegroundColor Gray
Write-Host " >> MULTI-ROOM MONITOR: http://localhost:$Port/" -ForegroundColor Gray
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host " Starting VIGIL AI Server on port $Port..." -ForegroundColor Yellow
Write-Host " Press Ctrl+C in this terminal to gracefully shutdown." -ForegroundColor Cyan

# 5. Open Browser
if (-not $NoBrowser) {
    Start-Process $DemoUrl
}

# 6. Run Server in Foreground
& $PythonExe server.py --host 0.0.0.0 --port $Port

