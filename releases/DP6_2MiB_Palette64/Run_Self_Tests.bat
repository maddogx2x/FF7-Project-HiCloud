@echo off
setlocal
title FFVII HiCloud DP6 Package Tests

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0test_dp6.py"
) else (
  python "%~dp0test_dp6.py"
)

echo.
if errorlevel 1 (
  echo One or more tests failed.
) else (
  echo Package tests passed.
)
pause
