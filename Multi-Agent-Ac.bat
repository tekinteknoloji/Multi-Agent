@echo off
cd /d "%~dp0"
python "%~dp0web_app.py" --open
if errorlevel 1 pause
