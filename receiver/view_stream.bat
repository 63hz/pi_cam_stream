@echo off
REM ScoutCam Stream Viewer
REM Uses ffplay for low-latency video playback
REM Requires ffmpeg to be installed and in PATH

setlocal

REM Default Pi address - change this or pass as argument
set PI_IP=%1
if "%PI_IP%"=="" set PI_IP=scoutcam.local

set RTSP_URL=rtsp://%PI_IP%:8554/cam

echo ========================================
echo   ScoutCam Stream Viewer
echo ========================================
echo.
echo Connecting to: %RTSP_URL%
echo.
echo Controls:
echo   q     - Quit
echo   f     - Toggle fullscreen
echo   p     - Pause
echo   s     - Step frame (when paused)
echo   left/right - Seek (if recording)
echo.

REM Low-latency playback settings
ffplay -fflags nobuffer -flags low_delay -framedrop ^
    -strict experimental ^
    -rtsp_transport tcp ^
    -i %RTSP_URL%

if %ERRORLEVEL% neq 0 (
    echo.
    echo Failed to connect to stream!
    echo.
    echo Troubleshooting:
    echo   1. Is the Pi powered on and connected to the network?
    echo   2. Is scoutcam service running? SSH and run: scoutcam status
    echo   3. Can you ping the Pi? ping %PI_IP%
    echo   4. Is ffmpeg installed? Download from https://ffmpeg.org/download.html
    echo.
    pause
)
