@echo off
REM ScoutCam Multi-Camera Viewer launcher
REM Usage:
REM   view_all.bat                                    Auto-discover all cameras
REM   view_all.bat scoutcam-blue.local                Specific camera(s)
REM   view_all.bat scoutcam-blue.local scoutcam-red.local

setlocal

REM Find the script directory
set "SCRIPT_DIR=%~dp0"

REM Check if Python is available
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Install Python 3.10+ and ensure it is in your PATH.
    pause
    exit /b 1
)

REM Build the command
if "%~1"=="" (
    REM No arguments — auto-discover
    echo Starting multi-camera viewer with auto-discovery...
    python "%SCRIPT_DIR%multicam_viewer.py"
) else (
    REM Pass all arguments as --hosts
    echo Starting multi-camera viewer for: %*
    python "%SCRIPT_DIR%multicam_viewer.py" --hosts %*
)

if errorlevel 1 (
    echo.
    echo Viewer exited with an error.
    pause
)
