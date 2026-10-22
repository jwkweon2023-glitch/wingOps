@echo off
cd /d "%~dp0"
if not exist .env.local.bat (
 echo Missing .env.local.bat -- create it using LOCAL_SETUP.txt
 pause
 exit /b 1
)
call .env.local.bat
python -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
