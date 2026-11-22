@echo off
cd /d "%~dp0"
set "TEMP=%CD%\tmp"
set "TMP=%CD%\tmp"
set "TMPDIR=%CD%\tmp"
set PYTHONDONTWRITEBYTECODE=1
.venv\Scripts\python.exe -B -m uvicorn lab.course_server:app --host 127.0.0.1 --port 8022
