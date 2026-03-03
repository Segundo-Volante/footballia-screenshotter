@echo off
echo.
echo Footballia Screenshotter — Setup
echo ====================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python 3.10+ is required but not found.
    echo    Install from: https://python.org
    exit /b 1
)
echo Python found

REM Create virtual environment
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate
echo Virtual environment activated

REM Install dependencies
echo Installing Python packages...
pip install -q -r requirements.txt

REM Install Playwright
echo Installing Chromium for Playwright...
python -m playwright install chromium
echo Chromium installed

REM Create .env if missing
if not exist ".env" (
    echo.
    echo Creating .env file...
    (
        echo # OpenAI ^(required for GPT-4o-mini classification^)
        echo OPENAI_API_KEY=
        echo.
        echo # Google Gemini ^(optional^)
        echo GEMINI_API_KEY=
    ) > .env
    echo .env created — add your API keys there
)

echo.
echo ====================================
echo Setup complete!
echo.
echo To start:
echo   venv\Scripts\activate
echo   python main.py
echo.
echo Then open http://localhost:8000 in your browser.
echo.
pause
