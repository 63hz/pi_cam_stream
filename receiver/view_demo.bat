@echo off
REM ScoutCam 4-Camera Demo Viewer
REM Shows all four cameras across the three Pis in one auto-sizing grid:
REM   blue     = Pi 4B  10.0.0.11 /cam   (1080p30)
REM   red      = Pi 4B  10.0.0.12 /cam   (1080p30)
REM   pi5-cam0 = Pi 5   10.0.0.13 /cam0  (720p60)
REM   pi5-cam1 = Pi 5   10.0.0.13 /cam1  (720p60)
REM Mixed resolutions/framerates are fine - each tile captures at its own native
REM rate and is scaled aspect-preserving into the grid. Offline cameras show
REM "NO SIGNAL" and reconnect automatically. Press q to quit, f for fullscreen.

setlocal
set "SCRIPT_DIR=%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ and ensure it is in your PATH.
    pause
    exit /b 1
)

echo Starting 4-camera demo viewer (blue, red, pi5-cam0, pi5-cam1)...
python "%SCRIPT_DIR%multicam_viewer.py" --no-discover --urls ^
  blue=rtsp://10.0.0.11:8554/cam ^
  red=rtsp://10.0.0.12:8554/cam ^
  pi5-cam0=rtsp://10.0.0.13:8554/cam0 ^
  pi5-cam1=rtsp://10.0.0.13:8554/cam1

if errorlevel 1 (
    echo.
    echo Viewer exited with an error.
    pause
)
