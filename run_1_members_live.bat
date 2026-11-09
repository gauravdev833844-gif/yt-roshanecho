@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "1_members_live_downloader.py"
) else (
    python "1_members_live_downloader.py"
)
echo.
pause
