@echo off
title Reset Sleep Settings
echo ======================================================
echo Resetting Sleep Settings to Defaults
echo ======================================================
echo.
echo This will restore normal sleep behavior.
echo.

REM Restore default sleep timeout (AC power)
echo [1/2] Restoring sleep timeout on AC power...
powercfg -change -standby-timeout-ac 30
if %ERRORLEVEL% EQU 0 (
    echo     ^> Sleep timeout restored to 30 minutes
) else (
    echo     ^> Warning: Failed to restore sleep timeout
)

REM Restore default sleep timeout (DC power)
echo [2/2] Restoring sleep timeout on DC power...
powercfg -change -standby-timeout-dc 30
if %ERRORLEVEL% EQU 0 (
    echo     ^> Sleep timeout restored to 30 minutes
) else (
    echo     ^> Warning: Failed to restore sleep timeout
)

REM Restore default monitor timeout (AC)
echo [3/3] Restoring monitor timeout on AC power...
powercfg -change -monitor-timeout-ac 15
if %ERRORLEVEL% EQU 0 (
    echo     ^> Monitor timeout restored to 15 minutes
) else (
    echo     ^> Warning: Failed to restore monitor timeout
)

REM Restore default monitor timeout (DC)
echo [4/4] Restoring monitor timeout on DC power...
powercfg -change -monitor-timeout-dc 15
if %ERRORLEVEL% EQU 0 (
    echo     ^> Monitor timeout restored to 15 minutes
) else (
    echo     ^> Warning: Failed to restore monitor timeout
)

echo.
echo ======================================================
echo Sleep settings have been reset to defaults!
echo ======================================================
echo.
echo You can now let your PC sleep normally.
echo.
pause
