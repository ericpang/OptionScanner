@echo off
setlocal
cd /d "%~dp0"
if not defined PORT set PORT=8000

rem Use the project's virtual environment if there is one
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"

python --version >nul 2>&1 || (echo Python was not found on PATH. Install Python 3.11+ first. & pause & exit /b 1)

rem Install dependencies on first run
python -c "import fastapi, uvicorn, numpy, httpx" >nul 2>&1 || (
  echo Installing dependencies...
  python -m pip install -r requirements.txt || (echo Dependency install failed. & pause & exit /b 1)
)

if not exist ".env" if exist ".env.example" copy /y ".env.example" ".env" >nul

rem Already running?
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul && (
  echo The app is already running on port %PORT%. Opening it in your browser.
  if not defined NO_BROWSER start "" "http://127.0.0.1:%PORT%"
  exit /b 0
)

echo Starting Stock Deep-Dive on http://127.0.0.1:%PORT% ...
start "Stock Deep-Dive server" /min cmd /c "python -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%"

rem Wait up to ~20 seconds for the server to come up, then open the browser
powershell -NoProfile -Command "for($i=0;$i -lt 20;$i++){ try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 3 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if errorlevel 1 (
  echo The server did not respond in time. Check the "Stock Deep-Dive server" window for errors.
  pause
  exit /b 1
)
if not defined NO_BROWSER start "" "http://127.0.0.1:%PORT%"
echo Running. Use stop.bat to stop it.
endlocal
