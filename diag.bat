@echo off
chcp 65001 > nul
cd /d "%~dp0"
where py > nul 2>&1
if errorlevel 1 (
  echo Python이 없습니다. run.bat 안내를 먼저 보세요.
  pause
  exit /b 1
)
py -m pip install --user -q -r requirements.txt
py screen_recall.py --diag
pause
