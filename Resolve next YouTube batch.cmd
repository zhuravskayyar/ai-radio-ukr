@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found.
    pause
    exit /b 1
)

echo Resolving the next quota-safe batch of playlist tracks...
".venv\Scripts\python.exe" -u "scripts\resolve_playlist.py" --limit 80
echo.
echo Finished. You can run this file again after the YouTube daily quota resets.
pause
endlocal
