@echo off
setlocal
cd /d "%~dp0"

where cargo >nul 2>nul
if errorlevel 1 (
    call build_rust_portable.bat
    exit /b
)

call ensure_msvc_build_tools.bat
if errorlevel 2 (
    echo Build cancelled by user.
    exit /b 0
)
if errorlevel 1 exit /b 1

cargo build --release
if errorlevel 1 exit /b 1

echo.
echo Built: target\release\axv.exe
