@echo off
cd /d "%~dp0"
python -m restim_tcode_proxy
if errorlevel 1 pause
