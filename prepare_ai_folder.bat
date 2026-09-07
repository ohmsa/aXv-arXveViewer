@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "AI_DIR=%~dp0ai_upscale"

if not exist "%AI_DIR%\" mkdir "%AI_DIR%"
echo AI folder: %AI_DIR%
echo.

set "FOUND=0"
for %%F in (
  realesrgan-ncnn-vulkan.exe
  realcugan-ncnn-vulkan.exe
  waifu2x-ncnn-vulkan.exe
  openvino_models\RealESRGAN_x4.onnx
  openvino_models\RealESRGAN_x4_fp16.onnx
) do (
  if exist "%AI_DIR%\%%F" (
    echo [OK] %%F
    set "FOUND=1"
  ) else (
    echo [--] %%F
  )
)
echo.
if "%FOUND%"=="1" (
  echo At least one supported engine or model was found.
  exit /b 0
)
echo No supported engine or model was found.
echo Obtain assets from their official distributors, review their licenses,
echo and place them under the ai_upscale folder.
exit /b 1
