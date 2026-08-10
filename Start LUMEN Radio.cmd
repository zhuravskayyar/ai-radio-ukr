@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv311\Scripts\pythonw.exe" (
    start "" ".venv311\Scripts\pythonw.exe" "main.py"
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
) else if exist ".venv311\Scripts\python.exe" (
    powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -FilePath '%~dp0.venv311\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0'"
) else if exist ".venv\Scripts\python.exe" (
    powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -FilePath '%~dp0.venv\Scripts\python.exe' -ArgumentList 'main.py' -WorkingDirectory '%~dp0'"
) else (
    powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -WindowStyle Hidden -FilePath 'python' -ArgumentList 'main.py' -WorkingDirectory '%~dp0'"
)

endlocal
