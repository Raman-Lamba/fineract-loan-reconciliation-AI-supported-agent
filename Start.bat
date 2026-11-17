@echo off
rem Double-click to start the sandbox and open the page in your browser. Close this window to stop the page.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Ui
if errorlevel 1 (
  echo.
  echo Something stopped the start. The red STOP line above says what to do; fix that and double-click Start.bat again.
  echo If it says Docker is not running: start Docker Desktop, wait until it says "Engine running", then try again.
  echo.
  pause
)
