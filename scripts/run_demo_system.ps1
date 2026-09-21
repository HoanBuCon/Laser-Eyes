# =====================================================================
# VIGIL AI — UNIFIED COMPETITION DEMO SYSTEM LAUNCHER
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
Write-Host " VIGIL AI — UNIFIED COMPETITION DEMO SYSTEM (SRS v2.0)" -ForegroundColor Cyan
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

# 4. Initialize Database Schema
Write-Host "[*] Initializing SQLite database schema..." -ForegroundColor Yellow
& $PythonExe -c "from storage.database import init_db; init_db(); print('Database schema ready.')"

# 5. Start FastAPI / Uvicorn Server in Background Job or Process
$NormalizedMode = $Mode.ToUpper()
Write-Host "[*] Starting VIGIL AI Server on http://localhost:$Port ..." -ForegroundColor Yellow

$ServerProc = Start-Process -FilePath $PythonExe -ArgumentList "-m uvicorn api.main:app --host 0.0.0.0 --port $Port" -PassThru -NoNewWindow

# Wait for server readiness
$Ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:$Port/ready" -Method Get -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($resp.status -eq "ready") {
            $Ready = $true
            break
        }
    } catch {}
}

if (-not $Ready) {
    Write-Host "[-] Server failed to respond on http://localhost:$Port/ready" -ForegroundColor Red
    Stop-Process -Id $ServerProc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "[+] Server is ONLINE & READY." -ForegroundColor Green

# 6. Auto-start chosen Preset & Mode
Write-Host "[*] Initializing Demo Preset: $Preset ($NormalizedMode mode, Debug: $DebugOverlay)..." -ForegroundColor Yellow
$StartBody = @{
    preset = $Preset
    mode = $NormalizedMode
    debug_overlay = [bool]$DebugOverlay
} | ConvertTo-Json

try {
    $startResp = Invoke-RestMethod -Uri "http://localhost:$Port/api/v1/demo/start" -Method Post -Body $StartBody -ContentType "application/json"
    Write-Host "[+] Demo Engine started successfully: Run ID $($startResp.run_id)" -ForegroundColor Green
} catch {
    Write-Host "[!] Warning: Auto-start demo endpoint returned: $_" -ForegroundColor Yellow
}

$DemoUrl = "http://localhost:$Port/demo"
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host " >> DEMO WEB INTERFACE: $DemoUrl" -ForegroundColor Green
Write-Host " >> REST API DOCS:      http://localhost:$Port/docs" -ForegroundColor Gray
Write-Host " >> MULTI-ROOM MONITOR: http://localhost:$Port/" -ForegroundColor Gray
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host " Press Ctrl+C in this terminal to gracefully shutdown." -ForegroundColor Cyan

# 7. Open Browser
if (-not $NoBrowser) {
    Start-Process $DemoUrl
}

# 8. Keep process alive until Ctrl+C
try {
    while ($true) {
        if ($ServerProc.HasExited) {
            Write-Host "[-] Server process terminated unexpectedly." -ForegroundColor Red
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host "`n[*] Shutting down VIGIL AI Demo Server..." -ForegroundColor Yellow
    if ($ServerProc -and -not $ServerProc.HasExited) {
        Stop-Process -Id $ServerProc.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[+] Clean shutdown complete." -ForegroundColor Green
}
