@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (set "PYTHON_CMD=py -3") else (set "PYTHON_CMD=python")
if not exist .venv\Scripts\python.exe (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo 请安装 Python 3.10 或更新版本，并加入 PATH。
    pause
    exit /b 1
  )
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo 依赖安装未完成，请检查网络后重试。
  pause
  exit /b 1
)
.venv\Scripts\python.exe scripts\launch.py
pause
