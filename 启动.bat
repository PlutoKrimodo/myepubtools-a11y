@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title MyEpubTools Launcher

echo ==========================================
echo   MyEpubTools Accessibility Desktop
echo ==========================================
echo.

rem 1) Locate Python: prefer 3.12, then any 3.x
set "PY="
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.12"
) else (
    py -3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PY=py -3"
    ) else (
        python --version >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    echo [ERROR] Python was not found.
    echo Please install Python 3.10+ ^(3.12 recommended^)
    echo and enable "Add python.exe to PATH" during installation.
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

rem 2) Version check (3.10+)
%PY% -c "import sys; sys.exit(1 if sys.version_info < (3, 10) else 0)"
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    %PY% --version
    echo.
    pause
    exit /b 1
)

rem 3) Create virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

rem 4) Install dependencies only when missing
"%VENV_PY%" -c "import PyQt6, flask, bs4, opencc" >nul 2>&1
if errorlevel 1 (
    echo [2/3] Installing dependencies, first run may take 1-2 minutes...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [INFO] Default index failed, retrying with Tsinghua mirror...
        "%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    )
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed. Please check your network.
        pause
        exit /b 1
    )
) else (
    echo [2/3] Dependencies are ready.
)

rem 5) Launch (--smoke is for self-check only)
echo [3/3] Starting MyEpubTools...
echo.
if /i "%~1"=="--smoke" (
    "%VENV_PY%" -m desktop.main --smoke
) else (
    "%VENV_PY%" -m desktop.main
)
if errorlevel 1 (
    echo.
    echo [ERROR] The application exited with an error.
    echo Please screenshot the messages above.
    pause
)
endlocal
