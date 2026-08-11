@echo off
chcp 65001 >nul
python -m pip install -r requirements.txt
if errorlevel 1 pause & exit /b 1
python robot_blocks.py
if errorlevel 1 pause
