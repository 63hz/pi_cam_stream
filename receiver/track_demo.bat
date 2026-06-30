@echo off
REM YOLO26 pose tracking demo - unique per-person skeletons, fading motion
REM trails, and a cumulative floor-usage heatmap (press h). Aim at people.
REM Usage:
REM   track_demo.bat                        Default source: pi5-cam0
REM   track_demo.bat 0                       Laptop webcam
REM   track_demo.bat pi5-cam0 --anchor feet  Floor-contact anchor (for mapping)
REM Keys in the window: q quit, s snapshot, h heatmap, t trails, c clear, b boxes.

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

echo Starting YOLO26 tracking demo on %SRC% ...
python "%SCRIPT_DIR%pose_tracks.py" --source %SRC% %2 %3 %4 %5 %6 %7 %8

if errorlevel 1 (
    echo.
    echo Demo exited with an error.
    pause
)
