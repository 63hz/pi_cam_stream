@echo off
REM ScoutCam calibration launcher - forwards all args to calibrate_camera.py
REM Examples:
REM   calibrate.bat make-board                          Print a checkerboard
REM   calibrate.bat capture --camera blue               Capture views (live)
REM   calibrate.bat calibrate --camera blue             Compute intrinsics
REM   calibrate.bat calibrate --camera pi5-cam0 --rational   (wide lens)
REM   calibrate.bat verify --camera blue                Raw vs undistorted
REM   calibrate.bat list                                Calibration status

setlocal
set "SCRIPT_DIR=%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ and ensure it is in your PATH.
    pause
    exit /b 1
)

python "%SCRIPT_DIR%calibrate_camera.py" %*

if errorlevel 1 (
    echo.
    echo Command exited with an error.
    pause
)
