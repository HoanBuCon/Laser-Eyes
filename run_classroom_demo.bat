@echo off
title VIGIL AI Classroom Cheating Surveillance Demo
echo ===================================================
echo   Starting VIGIL AI Classroom Surveillance Demo...
echo ===================================================

if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe scripts\run_demo_video.py --video india
) else if exist ".\.venv\Scripts\python.exe" (
    .\.venv\Scripts\python.exe scripts\run_demo_video.py --video india
) else (
    python scripts\run_demo_video.py --video india
)

pause
