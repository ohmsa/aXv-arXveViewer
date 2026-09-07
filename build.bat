@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "BUILD_MODE=fast"
if /I "%~1"=="debug" set "BUILD_MODE=debug"
if /I "%~1"=="final" set "BUILD_MODE=final"

for %%F in (viewer.py ai_upscale.py tlg_decoder.py requirements-build.txt LICENSE NOTICE THIRD_PARTY_NOTICES.md) do (
  if not exist "%%F" (
    echo [ERROR] Required file is missing: %%F
    exit /b 1
  )
)

set "PY_VERSION=3.11.9"
set "PY_ZIP=python-%PY_VERSION%-embed-amd64.zip"
set "PYEXE=%~dp0python_embed\python.exe"

if not exist "%PYEXE%" (
  echo [1/4] Downloading the local Python runtime...
  if not exist "python_embed" mkdir "python_embed"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PY_VERSION%/%PY_ZIP%' -OutFile 'python_embed\%PY_ZIP%'"
  if errorlevel 1 exit /b 1
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath 'python_embed\%PY_ZIP%' -DestinationPath 'python_embed' -Force"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem 'python_embed\python3*._pth' | ForEach-Object { (Get-Content -LiteralPath $_.FullName) -replace '#import site','import site' | Set-Content -LiteralPath $_.FullName }"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'python_embed\get-pip.py'"
  "%PYEXE%" "python_embed\get-pip.py" --no-warn-script-location --no-cache-dir
  if errorlevel 1 exit /b 1
) else (
  echo [1/4] Local Python is ready.
)

echo [2/4] Installing build dependencies...
"%PYEXE%" -m pip install --no-cache-dir -r requirements-build.txt
if errorlevel 1 exit /b 1

echo [3/4] Building ArchiveViewer...
if /I "%BUILD_MODE%"=="final" (
  "%PYEXE%" -m PyInstaller --noconfirm --clean --onefile --windowed --name ArchiveViewer viewer.py
) else if /I "%BUILD_MODE%"=="debug" (
  "%PYEXE%" -m PyInstaller --noconfirm --clean --onedir --console --name ArchiveViewer viewer.py
) else (
  "%PYEXE%" -m PyInstaller --noconfirm --clean --onedir --windowed --name ArchiveViewer viewer.py
)
if errorlevel 1 exit /b 1

if /I "%BUILD_MODE%"=="final" (
  set "OUT_DIR=dist"
) else (
  set "OUT_DIR=dist\ArchiveViewer"
)

echo [4/4] Copying notices and optional local assets...
for %%F in (LICENSE NOTICE THIRD_PARTY_NOTICES.md) do copy /Y "%%F" "%OUT_DIR%\%%F" >nul
if exist "ai_upscale\" xcopy /Y /E /I "ai_upscale" "%OUT_DIR%\ai_upscale" >nul
if exist "unrar.exe" copy /Y "unrar.exe" "%OUT_DIR%\unrar.exe" >nul
if exist "tlg6_native.dll" copy /Y "tlg6_native.dll" "%OUT_DIR%\tlg6_native.dll" >nul

echo.
echo Build complete: %OUT_DIR%
echo AI engines, models, UnRAR, and the optional native TLG decoder are not downloaded by this script.
pause
