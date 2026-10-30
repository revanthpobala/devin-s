@echo off
setlocal
cd /d "%~dp0\..\.."

echo ===============================================================================
echo               Stock Market Repository Archival ^& Maintenance
echo ===============================================================================
echo.
echo Running archival scan in safe DRY RUN mode first...
python scripts\maintenance\archive_repo_data.py %*

echo.
echo To execute archival and reclaim disk space:
echo    python scripts\maintenance\archive_repo_data.py --execute
echo.
pause

