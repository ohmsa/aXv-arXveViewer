@echo off
setlocal
cd /d "%~dp0"

where cargo >nul 2>nul
if errorlevel 1 (
    echo Rust toolchain was not found.
    echo Install rustup from https://rustup.rs/ and restart this terminal.
    exit /b 1
)

cargo build --release
if errorlevel 1 exit /b 1

echo.
echo Built: target\release\axv.exe
