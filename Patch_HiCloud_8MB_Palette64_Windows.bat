@echo off
setlocal

if "%~1"=="" (
  echo Drag your original NTSC-U CUE file onto this BAT file.
  pause
  exit /b 1
)

python "%~dp0Patch_HiCloud.py" "%~1"
if errorlevel 1 (
  echo.
  echo Patch failed. No original disc files were modified.
  pause
  exit /b 1
)

echo.
echo Enable 8 MiB PSX RAM before booting the new CUE.
pause
