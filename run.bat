@echo off
cd /d "%~dp0"
echo Directory: %CD%
echo.
if exist ".venv\Scripts\python.exe" (
  echo Using .venv Python
  ".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude ".venv" --reload-exclude "**/site-packages/**" --reload-exclude "__pycache__"
) else (
  echo Using system python
  python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude ".venv" --reload-exclude "**/site-packages/**" --reload-exclude "__pycache__"
)
pause
