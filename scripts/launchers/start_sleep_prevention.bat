@echo off
title Prevent Sleep Before Orchestrator
echo ======================================================
echo Setting Sleep Prevention for Market Hours
echo ======================================================
echo.
echo This will prevent your PC from sleeping during market hours.
echo.

REM Prevent sleep completely (AC power - plugged in)
echo [1/2] Preventing sleep on AC power...
powercfg -change -standby-timeout-ac 0
if %ERRORLEVEL% EQU 0 (
    echo     ^> Sleep timeout set to Never
) else (
    echo     ^> Warning: Failed to set sleep timeout
)

REM Prevent sleep for DC power (laptop battery)
echo [2/2] Preventing sleep on DC power...
powercfg -change -standby-timeout-dc 0
if %ERRORLEVEL% EQU 0 (
    echo     ^> Sleep timeout set to Never
) else (
    echo     ^> Warning: Failed to set sleep timeout
)

REM Prevent display from turning off (AC)
echo [3/3] Preventing display sleep on AC power...
powercfg -change -monitor-timeout-ac 0
if %ERRORLEVEL% EQU 0 (
    echo     ^> Monitor timeout set to Never
) else (
    echo     ^> Warning: Failed to set monitor timeout
)

REM Prevent display from turning off (DC)
echo [4/4] Preventing display sleep on DC power...
powercfg -change -monitor-timeout-dc 0
if %ERRORLEVEL% EQU 0 (
    echo     ^> Monitor timeout set to Never
) else (
    echo     ^> Warning: Failed to set monitor timeout
)

echo.
echo ======================================================
echo Sleep prevention settings applied successfully!
echo ======================================================
echo.
echo Next: Run start_orchestrator.bat to start the market pipeline
echo.
pause
