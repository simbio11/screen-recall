@echo off
chcp 65001 > nul
cd /d "%~dp0"
where py > nul 2>&1
if errorlevel 1 (
  echo Python이 없습니다. https://www.python.org/downloads/ 에서 설치하세요. 설치할 때 "Add python.exe to PATH" 체크.
  pause
  exit /b 1
)
py -m pip install --user -q -r requirements.txt
py screen_recall.py
pause
