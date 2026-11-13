@echo off
setlocal
cd /d "%~dp0"

set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%APP_PYTHON%" (
    echo Creating the app's Python environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv ".venv"
    ) else (
        where python >nul 2>nul
        if errorlevel 1 goto setup_failed
        python -m venv ".venv"
    )
    if errorlevel 1 goto setup_failed
)

if not exist "%APP_PYTHON%" goto setup_failed

"%APP_PYTHON%" -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo Installing app dependencies...
    "%APP_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto setup_failed
)

"%APP_PYTHON%" -m streamlit run app.py --server.headless=true
if errorlevel 1 (
    echo The app stopped with an error.
    pause
    exit /b 1
)
exit /b 0

:setup_failed
echo Could not prepare the app environment.
echo Make sure Python 3 and an internet connection are available, then try again.
pause
exit /b 1
