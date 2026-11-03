@echo off
cd /d "%~dp0"
python -m pip install -r requirements-ai.txt
if errorlevel 1 (
  echo Paket AI tidak bisa dipasang. Mencoba mode audio...
  python -m pip install -r requirements.txt
)
echo.
echo Buka http://localhost:8000 di browser Chrome.
python app.py
pause
