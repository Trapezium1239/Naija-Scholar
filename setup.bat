@echo off
echo ===================================================
echo NAIJA SCHOLAR ENGINE - LIGHTHOUSE INTEL ACADEMY
echo ===================================================
echo Cleaning up zombie processes on port 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /f /pid %%a 2>nul

if not exist .venv (
    echo Creating Python Virtual Environment...
    python -m venv .venv
)
call .venv\Scripts\activate.bat
echo Installing dependencies...
pip install -r requirements.txt

echo Running System Diagnostics...
python test_all.py
if %errorlevel% neq 0 (
    echo System Tests Failed. Halting boot sequence.
    pause
    exit /b %errorlevel%
)

echo Tests passed. Booting Master Architecture...
start "Naija Scholar FastAPI Server" cmd /k "python main.py"
start "Naija Scholar Autonomous Seeder" cmd /k "python autonomous_seeder.py"

pause