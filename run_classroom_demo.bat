@echo off
title VIGIL AI Classroom Cheating Surveillance Demo
echo ===================================================
echo   Starting VIGIL AI Classroom Surveillance Demo...
echo ===================================================
call .\.venv\Scripts\activate.bat
python -m classroom_monitor --mock-demo
pause
