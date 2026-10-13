@echo off
REM ---------------------------------------------------------------------------
REM Job Agent - single command, single server, single port.
REM
REM FastAPI serves both the API and the built React UI, so there is no second
REM process and no proxy. A .cmd file is used rather than .ps1 because
REM PowerShell's default execution policy blocks unsigned scripts.
REM ---------------------------------------------------------------------------

setlocal
set ROOT=%~dp0
set PORT=8000
if not "%~1"=="" set PORT=%~1

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo [ERROR] No virtualenv at %ROOT%.venv
  echo         python -m venv .venv
  echo         .venv\Scripts\python -m pip install -r backend\requirements.txt
  pause & exit /b 1
)

if not exist "%ROOT%.env" (
  echo [ERROR] No .env file. Copy .env.example to .env and add your GROQ_API_KEY.
  pause & exit /b 1
)

REM --- free the port if a previous run is still holding it --------------------
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr /c:":%PORT% "') do (
  echo [info] Port %PORT% is in use by PID %%P - stopping it.
  taskkill /PID %%P /F >nul 2>&1
)

REM --- build the UI if it is missing or out of date ---------------------------
if not exist "%ROOT%frontend\node_modules" (
  echo [setup] Installing frontend dependencies...
  pushd "%ROOT%frontend" && call npm install --silent && popd
)
if not exist "%ROOT%frontend\dist\index.html" (
  echo [setup] Building the UI...
  pushd "%ROOT%frontend" && call npm run build && popd
)

echo.
echo   Job Agent  ->  http://localhost:%PORT%
echo   Ctrl+C to stop.
echo.

start "" "http://localhost:%PORT%"

pushd "%ROOT%backend"
"%ROOT%.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
popd

endlocal
