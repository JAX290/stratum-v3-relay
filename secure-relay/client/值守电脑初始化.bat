@echo off
rem Double-click this file. It asks for administrator rights and runs the .ps1 beside it.
rem Kept ASCII-only on purpose: a batch file with Chinese literals breaks when the console
rem codepage differs from the file encoding, so the script is located by pattern instead.
setlocal
set "SCRIPT="
for %%I in ("%~dp0*.ps1") do set "SCRIPT=%%~fI"
if not defined SCRIPT (
  echo.
  echo   Missing the setup .ps1 next to this file.
  echo   Put both files in the same folder, then run this again.
  echo.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
