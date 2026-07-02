@echo off
setlocal

rem ---- clean サブコマンド：再生成可能な生成物・キャッシュ・ログ・配布zipを一掃 ----
rem 残すもの（再取得コスト大 or ユーザーデータ）：.venv / terrain_cache / results /
rem basemap_pale / tools / .qa。使い方：build.bat clean
if /i "%~1"=="clean" (
    echo [INFO] Cleaning build artifacts, caches, logs, and distribution zips...
    if exist build             rmdir /s /q build
    if exist dist              rmdir /s /q dist
    if exist .pytest_cache     rmdir /s /q .pytest_cache
    if exist .ruff_cache       rmdir /s /q .ruff_cache
    if exist __pycache__       rmdir /s /q __pycache__
    if exist views\__pycache__ rmdir /s /q views\__pycache__
    if exist tests\__pycache__ rmdir /s /q tests\__pycache__
    del /q RadioSimPro-*.zip 2>nul
    del /q build_log.txt 2>nul
    del /q radiosim.log 2>nul
    del /q .coverage 2>nul
    echo [OK] Clean complete. ^(kept: .venv / terrain_cache / results / basemap_pale / tools^)
    endlocal
    exit /b 0
)

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
