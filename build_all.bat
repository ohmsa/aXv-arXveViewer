@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_all.ps1" %*
exit /b %errorlevel%
