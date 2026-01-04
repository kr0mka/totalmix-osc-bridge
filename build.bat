@echo off
echo ========================================
echo  Building TotalMix OSC Bridge
echo ========================================
echo.

python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
    echo.
)

python -c "import pythonosc" >nul 2>&1
if errorlevel 1 (
    echo python-osc not found. Installing...
    pip install python-osc
    echo.
)

echo Building executable...
python -m PyInstaller --onefile --name totalmix-bridge --console bridge.py

echo.
echo ========================================
if exist dist\totalmix-bridge.exe (
    echo  SUCCESS: dist\totalmix-bridge.exe
) else (
    echo  ERROR: Build failed
)
echo ========================================
echo.
pause
