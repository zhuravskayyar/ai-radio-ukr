@echo off
setlocal
cd /d "%~dp0"
set "VECTOR_PYTHON=runtime\python.exe"
if not exist "%VECTOR_PYTHON%" set "VECTOR_PYTHON=.venv311\Scripts\python.exe"
if not exist "%VECTOR_PYTHON%" set "VECTOR_PYTHON=.venv\Scripts\python.exe"
if not exist "%VECTOR_PYTHON%" set "VECTOR_PYTHON=python"
"%VECTOR_PYTHON%" online.py --host 0.0.0.0 --port 8080 --public-listen --allowed-origin https://zhuravskayyar.github.io
pause
