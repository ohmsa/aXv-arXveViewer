@echo off
setlocal
cd /d "%~dp0"

set "AXV_RUSTUP_HOME=%CD%\.rustup"
set "AXV_CARGO_HOME=%CD%\.cargo"
set "RUSTUP_HOME=%AXV_RUSTUP_HOME%"
set "CARGO_HOME=%AXV_CARGO_HOME%"
set "PATH=%CARGO_HOME%\bin;%PATH%"
set "RUSTUP_INIT=%CD%\.build-tools\rustup-init.exe"

if exist "%CARGO_HOME%\bin\cargo.exe" goto build

if not exist "%CD%\.build-tools" mkdir "%CD%\.build-tools"
echo Rustup is distributed under the MIT or Apache-2.0 license.
echo License: https://github.com/rust-lang/rustup#license
choice /M "Download and install the official Rust toolchain into this project"
if errorlevel 2 exit /b 0
echo Downloading the official Rust installer...
curl.exe -fL --retry 3 "https://win.rustup.rs/x86_64" -o "%RUSTUP_INIT%"
if errorlevel 1 (
    echo Failed to download rustup-init.exe.
    exit /b 1
)

echo Preparing a project-local Rust toolchain...
"%RUSTUP_INIT%" -y --no-modify-path --profile minimal --default-toolchain stable
if errorlevel 1 exit /b 1

:build
call ensure_msvc_build_tools.bat
if errorlevel 2 (
    echo Build cancelled by user.
    exit /b 0
)
if errorlevel 1 exit /b 1

echo Building aXv...
cargo build --release
if errorlevel 1 (
    echo.
    echo If link.exe or a Windows SDK was not found, install the Visual Studio
    echo Build Tools workload named "Desktop development with C++".
    exit /b 1
)

echo.
echo Built: target\release\axv.exe
echo Rust remains inside .rustup and .cargo in this project.
