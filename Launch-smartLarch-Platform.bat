@echo off
REM smartLarch Platform launcher - no console for GUI (pythonw via PS1)
cd /d "%~dp0"
set "SL_LOG=%~dp0smartLarch-launch.log"
echo ===== %date% %time% Launch-smartLarch-Platform.bat =====>> "%SL_LOG%"
echo Folder: %~dp0>> "%SL_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Launch-smartLarch-Platform.ps1" %* 2>> "%SL_LOG%"
if errorlevel 1 (
    echo.
    echo [smartLarch] Launch failed.
    echo.
    echo The log is in THIS folder, next to the launcher:
    echo   %SL_LOG%
    echo Runtime install log (first-run only):
    echo   %~dp0smartLarch-runtime-install.log
    echo.
    echo Send that log file together with a photo of this window.
    echo.
    echo If you double-clicked from INSIDE the zip preview,
    echo extract the whole folder first, then run this .bat from the extracted folder.
    echo.
    pause
)
