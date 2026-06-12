@echo off
setlocal

echo ============================================================
echo RadioSim Pro - Build Script
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Add Python to PATH.
    pause
    exit /b 1
)
echo [OK] Python:
python --version

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing PyInstaller...
    pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause
        exit /b 1
    )
)
echo [OK] PyInstaller:
python -m PyInstaller --version

echo.
echo [INFO] Checking dependencies...
pip install numpy matplotlib requests Pillow sv-ttk darkdetect markdown truststore --quiet
echo [OK] Dependencies OK

echo.
echo [INFO] Cleaning old build...
if exist build\RadioSimPro rmdir /s /q build\RadioSimPro
if exist dist\RadioSimPro  rmdir /s /q dist\RadioSimPro
echo [OK] Clean done

echo.
echo [INFO] Building... (this may take a few minutes)
echo.
python -m PyInstaller radiosim.spec --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the error messages above.
    pause
    exit /b 1
)

echo.
echo [INFO] Creating runtime directories...
if not exist dist\RadioSimPro\terrain_cache mkdir dist\RadioSimPro\terrain_cache
if not exist dist\RadioSimPro\results       mkdir dist\RadioSimPro\results

echo.
echo ============================================================
echo [SUCCESS] Build complete!
echo.
echo Output : dist\RadioSimPro\
echo Exe    : dist\RadioSimPro\RadioSimPro.exe
echo.
echo Zip the dist\RadioSimPro\ folder for distribution.
echo ============================================================
echo.
endlocal
