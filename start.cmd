@echo off
cd /d %~dp0
if not exist tmp mkdir tmp
if not exist runs mkdir runs
.venv\Scripts\python.exe -m uvicorn lab.server:app --host 127.0.0.1 --port 8021