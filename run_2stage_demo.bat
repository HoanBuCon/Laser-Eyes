@echo off
echo =====================================================================
echo    VIGIL AI -- 2-STAGE POSE & BEHAVIOR HIGH-LOAD PIPELINE DEMO
echo =====================================================================
echo.
echo Running Single-Pass Multi-Person Pose (HD 1280px) on:
echo   demo_video\india_classroom.mp4
echo.
echo Hotkeys:
echo   [N]     Toggle No-Cheating BBox (ON/OFF)
echo   [B]     Toggle All BBoxes (ON/OFF)
echo   [SPACE] Pause / Resume
echo   [Q/ESC] Quit
echo.

.\venv\Scripts\python.exe scripts\evaluate_2stage_pose.py --video demo_video\india_classroom.mp4 --output data\output_demo\india_classroom_2stage_annotated.mp4 --pose-model yolo11n-pose.pt --imgsz 1280 --conf 0.20 --ai-fps 10 --show --metrics data\output_demo\india_classroom_2stage_metrics.json

echo.
echo =====================================================================
echo 2-Stage Evaluation Completed!
echo Output Video : data\output_demo\india_classroom_2stage_annotated.mp4
echo Metrics JSON : data\output_demo\india_classroom_2stage_metrics.json
echo =====================================================================
pause
