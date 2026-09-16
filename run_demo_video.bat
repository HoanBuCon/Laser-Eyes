@echo off
echo =====================================================================
echo    VIGIL AI -- CLASSROOM SURVEILLANCE DEMO VIDEO EVALUATION
echo =====================================================================
echo.
echo Running End-to-End Enterprise Proctoring Pipeline with Live GUI Window on:
echo   demo_video\india_classroom.mp4
echo.
echo Note: Press 'q' or 'ESC' on the video window to stop early.
echo       Press 'SPACE' to pause/resume playback.
echo.

.\venv\Scripts\python.exe scripts\evaluate_demo_video.py --video demo_video\india_classroom.mp4 --output data\output_demo\india_classroom_annotated.mp4 --conf 0.15 --show --metrics data\output_demo\india_classroom_metrics.json

echo.
echo =====================================================================
echo Evaluation Completed!
echo Output Video : data\output_demo\india_classroom_annotated.mp4
echo Metrics JSON : data\output_demo\india_classroom_metrics.json
echo Evidence Dir : data\evidence\ and data\evidence_clips\
echo =====================================================================
pause
