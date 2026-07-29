@echo off
setlocal
title FFVII HiCloud Dynamic Packing DP6 + Palette64

if "%~1"=="" (
  echo Drag a clean NTSC-U FFVII CUE onto this BAT file.
  echo.
  pause
  exit /b 2
)

if /I not "%~x1"==".cue" (
  echo ERROR: Drag the CUE file, not the BIN.
  echo.
  pause
  exit /b 2
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0patch_hicloud_dp6.py" "%~1"
) else (
  python "%~dp0patch_hicloud_dp6.py" "%~1"
)

echo.
if errorlevel 1 (
  echo Patch failed. The original BIN/CUE were not modified.
) else (
  echo Patch complete. Load the new *_HiCloud_DP6_Palette64.cue.
)
pause
