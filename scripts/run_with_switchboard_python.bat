@echo off
REM Run Operator Board with the Python environment that ships with Unreal's Switchboard.
REM Usage: run_with_switchboard_python.bat "C:\Program Files\Epic Games\UE_5.6"
setlocal
set ENGINE=%~1
if "%ENGINE%"=="" (
  echo Usage: %~nx0 "path\to\UE_5.x"
  exit /b 1
)
set SBPY=%ENGINE%\Engine\Extras\ThirdPartyNotUE\SwitchboardThirdParty\Python\Scripts\python.exe
if not exist "%SBPY%" set SBPY=%ENGINE%\Engine\Extras\ThirdPartyNotUE\SwitchboardThirdParty\Python\python.exe
if not exist "%SBPY%" (
  echo Switchboard Python not found. Launch Switchboard once so it creates its environment, or use run.bat.
  exit /b 1
)
cd /d "%~dp0.."
"%SBPY%" -m operator_board %2 %3 %4
