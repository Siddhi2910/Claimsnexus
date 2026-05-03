@echo off
echo Creating venv...
C:\python3.13\python.exe -m venv .venv
echo Activating and installing requirements...
call .\.venv\Scripts\activate.bat
C:\python3.13\python.exe -m pip install --upgrade pip
pip install -r requirements.txt
echo Done!
