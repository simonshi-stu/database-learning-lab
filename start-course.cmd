@echo off
cd /d "%~dp0"
if not exist tmp mkdir tmp
if not exist runs mkdir runs
set "TEMP=%CD%\tmp"
set "TMP=%CD%\tmp"
set "TMPDIR=%CD%\tmp"
set PYTHONDONTWRITEBYTECODE=1
.venv\Scripts\python.exe -B -m uvicorn lab.course_server:app --host 127.0.0.1 --port 8022
