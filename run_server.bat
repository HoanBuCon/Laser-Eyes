@echo off
title VIGIL AI Enterprise Server
echo ===================================================
echo   Starting VIGIL AI Enterprise Proctoring Server...
echo ===================================================
call .\.venv\Scripts\activate.bat
python server.py --host 0.0.0.0 --port 8000
pause
