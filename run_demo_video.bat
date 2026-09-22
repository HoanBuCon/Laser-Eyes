@echo off
title VIGIL AI Classroom Surveillance Demo
echo =====================================================================
echo    VIGIL AI -- CLASSROOM SURVEILLANCE DEMO VIDEO EVALUATION
echo =====================================================================
echo.
echo Running Single-Video Demo Pipeline on india_classroom.mp4...
echo.

if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe scripts\run_demo_video.py --video india --show
) else if exist ".\.venv\Scripts\python.exe" (
    .\.venv\Scripts\python.exe scripts\run_demo_video.py --video india --show
) else (
    python scripts\run_demo_video.py --video india --show
)

pause
