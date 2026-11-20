@echo off
setlocal
REM Stage 5C: preserve the existing 7AM Task Scheduler task and Stage 5B scan.
REM Run from JobPilot's own folder, regardless of Task Scheduler start directory.
cd /d "%~dp0"
python -u "%~dp0scheduled_scan.py" --run --max-age-days 45 --min-score 45
set "SCAN_EXIT=%ERRORLEVEL%"
REM Archived relevant leads stay queued if mail fails and can be retried later.
REM Also retry unsent archives after a failed scan. Does not submit applications.
python -u "%~dp0email_notifications.py" --deliver
set "EMAIL_EXIT=%ERRORLEVEL%"
if not "%SCAN_EXIT%"=="0" exit /b %SCAN_EXIT%
exit /b %EMAIL_EXIT%
