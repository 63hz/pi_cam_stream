@echo off
REM RF-DETR vs YOLO26 pose comparison demo (side-by-side, live).
REM Pose tracks PEOPLE - aim the source camera at a person.
REM Usage:
REM   pose_demo.bat                  Default source: pi5-cam0
REM   pose_demo.bat 0                Laptop webcam (index 0)
REM   pose_demo.bat blue             scoutcam-blue stream
REM   pose_demo.bat pi5-cam0 --imgsz 512 --rfdetr-every-n 2
REM Keys in the window: q quit, s save snapshot.

setlocal
set "SCRIPT_DIR=%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    pause
    exit /b 1
)

set "SRC=%~1"
if "%SRC%"=="" set "SRC=pi5-cam0"

echo Starting RF-DETR vs YOLO26 pose demo on %SRC% ...
python "%SCRIPT_DIR%pose_compare.py" --source %SRC% %2 %3 %4 %5 %6 %7 %8

if errorlevel 1 (
    echo.
    echo Demo exited with an error.
    pause
)
