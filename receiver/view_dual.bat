@echo off
REM ScoutCam Dual-Camera Viewer launcher (two cameras on one Pi 5)
REM Opens both /cam0 and /cam1 from a single host in the grid viewer.
REM Usage:
REM   view_dual.bat                 Defaults to 10.0.0.13 (Pi 5)
REM   view_dual.bat 10.0.0.13
REM   view_dual.bat scoutcam.local

setlocal

set "SCRIPT_DIR=%~dp0"

set "HOST=%~1"
if "%HOST%"=="" set "HOST=10.0.0.13"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ and ensure it is in your PATH.
    pause
    exit /b 1
)

echo Starting dual-camera viewer for %HOST% (/cam0 + /cam1)...
python "%SCRIPT_DIR%multicam_viewer.py" --no-discover --hosts %HOST% --paths /cam0 /cam1

if errorlevel 1 (
    echo.
    echo Viewer exited with an error.
    pause
)
