@echo off
REM ScoutCam Single-Camera Viewer launcher (uses rtsp_ingest_debug.py)
REM Usage:
REM   view_cam.bat scoutcam-blue.local
REM   view_cam.bat 10.0.0.11
REM   view_cam.bat 10.0.0.13 --path /cam0      (one camera of a dual Pi 5)
REM   view_cam.bat 10.0.0.13 --path /cam1 --probe

setlocal

REM Find the script directory
set "SCRIPT_DIR=%~dp0"

if "%~1"=="" (
    echo Usage: view_cam.bat ^<hostname-or-ip^>
    echo.
    echo Examples:
    echo   view_cam.bat scoutcam-blue.local
    echo   view_cam.bat 10.0.0.11
    exit /b 1
)

REM Check if Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ and ensure it is in your PATH.
    pause
    exit /b 1
)

echo Starting single-camera viewer for: %1
python "%SCRIPT_DIR%rtsp_ingest_debug.py" --pi %1 %2 %3 %4 %5 %6

if errorlevel 1 (
    echo.
    echo Viewer exited with an error.
    pause
)
