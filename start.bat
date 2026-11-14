@echo off
rem Double-click to open the Spotify Jam Bot menu.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No .venv folder found. Follow "Install" in README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py %*
