<#
.SYNOPSIS
    One-click launcher for VIGIL AI ICTU 2026 Working Prototype Demonstration.

.DESCRIPTION
    Performs pre-flight environment checks, executes unit test verification, and runs
    the consolidated SRS v2.0 pipeline across both classroom demo videos (India & Student).
    Generates standardized demo artifacts into data/demo_final/.

.EXAMPLE
    .\scripts\run_prototype.ps1
    .\scripts\run_prototype.ps1 -Show
    .\scripts\run_prototype.ps1 -DebugOverlay
#>

param (
    [switch]$Show,
    [switch]$DebugOverlay,
    [switch]$SkipTests,
    [string]$HeadProvider = "sixdrepnet",
    [string]$OutputRoot = "data/demo_final"
)

$ErrorActionPreference = "Stop"

Write-Host "`n==========================================================================" -ForegroundColor Cyan
Write-Host " VIGIL AI - AI-Assisted Exam Monitoring Co-Pilot (ICTU 2026)" -ForegroundColor Green
Write-Host " Master Working Prototype Demonstration Launcher" -ForegroundColor Cyan
Write-Host "==========================================================================`n" -ForegroundColor Cyan

# 0. Pre-Flight Environment Checks
Write-Host "[0/3] Performing Pre-Flight Environment Checks..." -ForegroundColor Yellow

$PythonExe = $null
if (Test-Path ".\venv\Scripts\python.exe") {
    $PythonExe = ".\venv\Scripts\python.exe"
} elseif (Test-Path ".\.venv\Scripts\python.exe") {
    $PythonExe = ".\.venv\Scripts\python.exe"
} else {
    $PythonExe = "python"
}

Write-Host " - Python Interpreter: $PythonExe" -ForegroundColor DarkGray

# Check CUDA support
$CudaCheck = & $PythonExe -c "import torch; is_cuda = torch.cuda.is_available(); name = torch.cuda.get_device_name(0) if is_cuda else 'CPU'; print(str(is_cuda) + '|' + str(name))"

$HasCuda = $false
$GpuName = "CPU"

if ($CudaCheck -and ($CudaCheck -match '\|')) {
    $CudaParts = $CudaCheck -split '\|'
    $HasCuda = ($CudaParts[0].Trim() -eq "True")
    if ($CudaParts.Length -gt 1) {
        $GpuName = $CudaParts[1].Trim()
    }
}

if ($HasCuda) {
    Write-Host " - Hardware Acceleration: CUDA Available ($GpuName)" -ForegroundColor Green
} else {
    Write-Host " - Hardware Acceleration: CPU Mode (CUDA not detected)" -ForegroundColor Yellow
}

# Check Demo Videos
$IndiaVideo = (Test-Path "demo_video\india_classroom.mp4") -or (Test-Path "video\india_classroom.mp4")
$StudentVideo = (Test-Path "demo_video\student_classroom.mp4") -or (Test-Path "video\student_classroom.mp4")

if (-not $IndiaVideo -or -not $StudentVideo) {
    Write-Host "[WARNING] One or more demo videos not found in demo_video/ or video/." -ForegroundColor Yellow
} else {
    Write-Host " - Video Feeds: Both india_classroom.mp4 and student_classroom.mp4 Verified" -ForegroundColor Green
}

# 1. Run Unit Tests (unless skipped)
if (-not $SkipTests) {
    Write-Host "`n[1/3] Running Pytest Unit & Regression Suite..." -ForegroundColor Yellow
    & $PythonExe -m pytest tests/ -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[ERROR] Test suite failed! Aborting prototype run." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "[PASS] All Unit & Infrastructure Tests Passed Successfully!`n" -ForegroundColor Green
} else {
    Write-Host "`n[1/3] Skipping Unit Tests as requested." -ForegroundColor DarkGray
}

# 2. Run Multi-Video Demo Pipeline
Write-Host "[2/3] Executing Master Multi-Video Demo Pipeline..." -ForegroundColor Yellow

$DemoArgs = @("scripts/run_demo_all_videos.py", "--output-root", $OutputRoot, "--head-provider", $HeadProvider)
if ($Show) {
    $DemoArgs += "--show"
}
if ($DebugOverlay) {
    $DemoArgs += "--debug-overlay"
}

& $PythonExe $DemoArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Demo pipeline execution encountered an issue!" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Verify Generated Output Artifacts
Write-Host "`n[3/3] Verifying Generated Artifacts in $OutputRoot/:" -ForegroundColor Yellow

$IndiaDir = Join-Path $OutputRoot "india"
$StudentDir = Join-Path $OutputRoot "student"

Write-Host " - Video 1 (India):" -ForegroundColor Cyan
Write-Host "     Video:     $(Join-Path $IndiaDir 'result.mp4')" -ForegroundColor DarkGray
Write-Host "     Episodes:  $(Join-Path $IndiaDir 'episodes.json')" -ForegroundColor DarkGray
Write-Host "     Events:    $(Join-Path $IndiaDir 'events.json')" -ForegroundColor DarkGray
Write-Host "     Summary:   $(Join-Path $IndiaDir 'demo_summary.json')" -ForegroundColor DarkGray
Write-Host "     Runtime:   $(Join-Path $IndiaDir 'runtime_profile.json')" -ForegroundColor DarkGray

Write-Host " - Video 2 (Student):" -ForegroundColor Cyan
Write-Host "     Video:     $(Join-Path $StudentDir 'result.mp4')" -ForegroundColor DarkGray
Write-Host "     Episodes:  $(Join-Path $StudentDir 'episodes.json')" -ForegroundColor DarkGray
Write-Host "     Events:    $(Join-Path $StudentDir 'events.json')" -ForegroundColor DarkGray
Write-Host "     Summary:   $(Join-Path $StudentDir 'demo_summary.json')" -ForegroundColor DarkGray
Write-Host "     Runtime:   $(Join-Path $StudentDir 'runtime_profile.json')" -ForegroundColor DarkGray

Write-Host "`n==========================================================================" -ForegroundColor Green
Write-Host " VIGIL AI PROTOTYPE DEMONSTRATION COMPLETE" -ForegroundColor Green
Write-Host " Status: DEMO_RUNNER_READY_FOR_HUMAN_REVIEW" -ForegroundColor Cyan
Write-Host "==========================================================================`n" -ForegroundColor Green
