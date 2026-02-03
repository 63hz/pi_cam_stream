@echo off
REM ScoutCam Stream Recorder
REM Records RTSP stream to local MP4 file
REM Requires ffmpeg to be installed and in PATH

setlocal

REM Default Pi address - change this or pass as argument
set PI_IP=%1
if "%PI_IP%"=="" set PI_IP=scoutcam.local

set RTSP_URL=rtsp://%PI_IP%:8554/cam

REM Generate timestamp for filename
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set datetime=%%I
set TIMESTAMP=%datetime:~0,8%_%datetime:~8,6%
set OUTPUT=scoutcam_%TIMESTAMP%.mp4

echo ========================================
echo   ScoutCam Stream Recorder
echo ========================================
echo.
echo Recording from: %RTSP_URL%
echo Output file: %OUTPUT%
echo.
echo Press Ctrl+C to stop recording
echo.

REM Record with copy codec (no re-encoding, minimal CPU usage)
ffmpeg -rtsp_transport tcp ^
    -i %RTSP_URL% ^
    -c copy ^
    -movflags +faststart ^
    %OUTPUT%

if %ERRORLEVEL% neq 0 (
    echo.
    echo Recording stopped or failed!
    echo.
)

echo.
echo Recording saved to: %OUTPUT%
pause
