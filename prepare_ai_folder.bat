@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "AI_DIR=%~dp0ai_upscale"

if not exist "%AI_DIR%\" mkdir "%AI_DIR%"
for %%D in (models models-se models-cunet openvino_models) do (
  if not exist "%AI_DIR%\%%D\" mkdir "%AI_DIR%\%%D"
)

echo AI folder prepared:
echo   %AI_DIR%
echo.
echo Expected engines and models:

set "FOUND=0"
for %%F in (
  realesrgan-ncnn-vulkan.exe
  models\realesrgan-x4plus-anime.param
  models\realesrgan-x4plus-anime.bin
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
echo Official release pages:
echo   Real-ESRGAN: https://github.com/xinntao/Real-ESRGAN/releases
echo   Real-CUGAN:  https://github.com/nihui/realcugan-ncnn-vulkan/releases
echo   waifu2x:     https://github.com/nihui/waifu2x-ncnn-vulkan/releases
echo   OpenVINO:    https://github.com/openvinotoolkit/openvino
echo.
echo Download assets from their official distributors, review the included
echo licenses, and copy the engine executable and model files into this folder.

if "%FOUND%"=="1" (
  echo.
  echo At least one supported engine or model was found.
  exit /b 0
)
echo.
echo No supported engine or model was found yet.
exit /b 1
