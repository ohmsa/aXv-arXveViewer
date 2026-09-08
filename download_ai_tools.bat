@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_ai_tools.ps1"
set "SETUP_RESULT=%ERRORLEVEL%"
if "%SETUP_RESULT%"=="2" exit /b 0
if not "%SETUP_RESULT%"=="0" exit /b %SETUP_RESULT%
call prepare_ai_folder.bat
