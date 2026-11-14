@echo off
rem Windows: double-click to run. First run creates .venv and installs PyMuPDF.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist ".venv\Scripts\python.exe" (
    echo Installing for the first time...
    %PY% -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install -q --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail
)
start "" ".venv\Scripts\pythonw.exe" pdf_editor.py %*
exit /b 0
:fail
echo Install failed. Install Python 3.10+ from https://www.python.org/downloads/ (tick "Add python.exe to PATH")
pause
