@echo off
setlocal

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_INSTALL=%%I"
)
if defined VS_INSTALL exit /b 0

where link.exe >nul 2>nul
if not errorlevel 1 exit /b 0

echo.
echo Microsoft C++ Build Tools are required for the Rust MSVC target.
echo The installer is provided by Microsoft and is not bundled with this project.
echo License: https://visualstudio.microsoft.com/license-terms/
echo Components: Desktop development with C++ and a Windows SDK
echo.
choice /M "Install Microsoft Visual Studio Build Tools using winget"
if errorlevel 2 exit /b 2

where winget.exe >nul 2>nul
if errorlevel 1 (
    echo winget.exe was not found.
    echo Install Build Tools manually from:
    echo https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio
    exit /b 1
)

winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --source winget --override "--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
if errorlevel 1 exit /b 1

echo Microsoft C++ Build Tools installation completed.
exit /b 0

