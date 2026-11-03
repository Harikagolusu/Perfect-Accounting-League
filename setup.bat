@echo off
REM One-shot Windows setup for PAL. Run from the repository root.
REM Installs backend + frontend dependencies, writes your DeepSeek key, sets up
REM the database, then starts both servers. Safe to re-run.
setlocal enabledelayedexpansion
cd /d "%~dp0"
title PAL setup

echo === PAL setup ===
echo.

REM --- 0. Required tools must already be installed (see SETUP.md section 1) ---
set "MISSING="
for %%T in (git node npm python psql) do (
  where %%T >nul 2>&1 || set "MISSING=!MISSING! %%T"
)
if not "!MISSING!"=="" (
  echo Missing tools:!MISSING!
  echo Install them ^(SETUP.md section 1^), open a NEW terminal, then run this file again.
  pause
  exit /b 1
)

echo [1/4] Backend virtualenv and dependencies...
pushd backend
if not exist .venv (
  python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt || goto :fail

if not exist .env (
  copy .env.example .env >nul
  set /p "DSKEY=Enter your DeepSeek API key: "
  powershell -NoProfile -Command "(Get-Content .env) -replace '^DEEPSEEK_API_KEY=.*','DEEPSEEK_API_KEY=!DSKEY!' | Set-Content .env -Encoding utf8"
  echo Saved your key to backend\.env
)

echo [2/4] Database (createdb may ask for your postgres password; ignore "already exists")...
createdb pal_db
alembic upgrade head || goto :fail
popd

echo [3/4] Frontend dependencies...
call npm install || goto :fail

echo [4/4] Starting servers in two new windows...
start "PAL backend" cmd /k "cd backend && call .venv\Scripts\activate.bat && uvicorn app.main:app --reload"
start "PAL frontend" cmd /k "npm run dev"

echo.
echo Done.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:8080
exit /b 0

:fail
echo.
echo Setup failed - see the error above.
popd 2>nul
pause
exit /b 1
