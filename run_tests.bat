@echo off
title VIGIL AI Test Suite
echo ===================================================
echo   Executing VIGIL AI Comprehensive Test Suite...
echo ===================================================
call .\.venv\Scripts\activate.bat
pytest -v tests/
pause
