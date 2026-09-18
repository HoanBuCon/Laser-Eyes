<#
.SYNOPSIS
    One-click launcher for VIGIL AI ICTU 2026 Working Prototype Demonstration.

.DESCRIPTION
    Executes the latest Actor-Centric Temporal Architecture on both india_classroom.mp4
    and student_classroom.mp4 with 6DRepNet GPU Tensor batching, per-seat baseline subtraction,
    and automatic evidence generation into data/prototype_final/.

.EXAMPLE
    .\scripts\run_prototype.ps1
#>

Write-Host "`n==========================================================================" -ForegroundColor Cyan
Write-Host " VIGIL AI - AI-Assisted Exam Monitoring Co-Pilot (ICTU 2026)" -ForegroundColor Green
Write-Host " Master Working Prototype Demonstration Launcher" -ForegroundColor Cyan
Write-Host "==========================================================================`n" -ForegroundColor Cyan

$PythonExe = ".\venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

# 1. Run unit test suite
Write-Host "[1/3] Running Pytest Unit & Regression Suite..." -ForegroundColor Yellow
& $PythonExe -m pytest tests/ -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Test suite failed! Please review errors." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "[PASS] All 112+ Unit Tests Passed Successfully!`n" -ForegroundColor Green

# 2. Run multi-video demo pipeline
Write-Host "[2/3] Running Master Multi-Video Demo Pipeline..." -ForegroundColor Yellow
& $PythonExe scripts/run_demo_all_videos.py --video all --output-dir data/prototype_final
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Pipeline execution encountered an issue!" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Final summary
Write-Host "`n[3/3] Output Artifacts Successfully Generated at data/prototype_final/:" -ForegroundColor Yellow
Write-Host " - Video 1 (India):   data/prototype_final/india/india_classroom_result.mp4" -ForegroundColor Cyan
Write-Host " - Video 2 (Student): data/prototype_final/student/student_classroom_result.mp4" -ForegroundColor Cyan
Write-Host " - Ground Truth CSV:  data/prototype_final/india/gt_comparison.csv" -ForegroundColor Cyan
Write-Host " - Runtime Profile:   data/prototype_final/india/runtime_profile.json" -ForegroundColor Cyan
Write-Host " - Alert Diagnosis:   data/prototype_final/india/alert_diagnosis.json" -ForegroundColor Cyan
Write-Host " - 10s Evidence MP4s: data/prototype_final/india/evidence/`n" -ForegroundColor Cyan

Write-Host "==========================================================================" -ForegroundColor Green
Write-Host " VIGIL AI PROTOTYPE READY FOR DEMONSTRATION & REVIEW" -ForegroundColor Green
Write-Host " To launch Web Dashboard: $PythonExe server.py" -ForegroundColor Yellow
Write-Host "==========================================================================`n" -ForegroundColor Green
