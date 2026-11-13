@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo PySide6 설치에 실패했습니다. Python과 네트워크 연결을 확인해주세요.
  pause
  exit /b 1
)
py main.py
if errorlevel 1 pause
