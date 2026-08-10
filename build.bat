@echo off
setlocal

rem ============================================================
rem  ASCII ONLY - DO NOT ADD JAPANESE (OR ANY NON-ASCII) TO THIS FILE.
rem ------------------------------------------------------------
rem  cmd.exe reads .bat files byte-wise under the OEM codepage. A UTF-8
rem  multibyte character can land across its internal read boundary and shift
rem  the parser, at which point lines are silently truncated ("'cho' is not
rem  recognized") and the build falls through WITHOUT building anything while
rem  still exiting 0. This happened on 2026-07-30: the previous version of this
rem  file had Japanese comments and worked only because its byte layout
rem  happened to be safe - editing anything above a comment could break it.
rem  The Japanese rationale for everything below lives in the README section on
rem  setting up the development environment, and in ISSUES.md B-020. Keep the
rem  prose there, not here.
rem ============================================================

rem ============================================================
rem  Environment declaration (2.6a1 / B-020)
rem ------------------------------------------------------------
rem  RADIOSIM_PYTHON     = the one python.exe used for BOTH verification and
rem                        the shipped build. REQUIRED.
rem  RADIOSIM_BUILD_ROOT = where dist / build go. Optional; defaults to the
rem                        repository root.
rem
rem  Why declared, not discovered: this script used to build with whatever
rem  python was on PATH, so the binary shipped with 8 transitive dependencies
rem  that differed from the ones pytest had verified - including certifi's CA
rem  bundle, i.e. the TLS trust store used to fetch GSI tiles. Any search or
rem  fallback makes that mismatch succeed silently, so a missing declaration
rem  must STOP the build.
rem ============================================================
if not defined RADIOSIM_PYTHON (
    echo [ERROR] RADIOSIM_PYTHON is not set.
    echo         Point it at the project venv's python.exe, then reopen the shell:
    echo           setx RADIOSIM_PYTHON D:\dev\radiosim\venv\Scripts\python.exe
    echo         Create it first - see the README section on setting up the
    echo         development environment.
    pause
    exit /b 1
)
if not exist "%RADIOSIM_PYTHON%" (
    echo [ERROR] RADIOSIM_PYTHON points at a file that does not exist:
    echo           %RADIOSIM_PYTHON%
    pause
    exit /b 1
)
set "PY=%RADIOSIM_PYTHON%"

if defined RADIOSIM_BUILD_ROOT (
    set "DIST_DIR=%RADIOSIM_BUILD_ROOT%\dist"
    set "WORK_DIR=%RADIOSIM_BUILD_ROOT%\build"
) else (
    set "DIST_DIR=%~dp0dist"
    set "WORK_DIR=%~dp0build"
)
set "APP_DIR=%DIST_DIR%\RadioSimPro"

rem ---- "clean" subcommand: wipe regenerable artifacts, caches, logs, zips ----
rem Kept: the venv and the REPO-ROOT terrain_cache / results / basemap_pale /
rem tools / .qa (source-run data; expensive to refetch or user-owned).
rem
rem NOT kept: everything under the build output root (DIST_DIR / WORK_DIR),
rem INCLUDING the terrain_cache and results that the built exe created next to
rem itself. Those belong to the build output and a normal build discards them
rem too (see "Cleaning old build" below, which removes APP_DIR wholesale).
rem Do not describe them as preserved - a 2026-08-04 review found the README
rem promising exactly that while this block deleted them.
rem Usage: build.bat clean
if /i "%~1"=="clean" (
    echo [INFO] Cleaning build artifacts, caches, logs, and distribution zips...
    if exist "%WORK_DIR%"      rmdir /s /q "%WORK_DIR%"
    if exist "%DIST_DIR%"      rmdir /s /q "%DIST_DIR%"
    rem Also sweep any leftovers from before dist/build moved out of the repo.
    if exist "%~dp0build"      rmdir /s /q "%~dp0build"
    if exist "%~dp0dist"       rmdir /s /q "%~dp0dist"
    if exist .pytest_cache     rmdir /s /q .pytest_cache
    if exist .ruff_cache       rmdir /s /q .ruff_cache
    if exist __pycache__       rmdir /s /q __pycache__
    if exist views\__pycache__ rmdir /s /q views\__pycache__
    if exist tests\__pycache__ rmdir /s /q tests\__pycache__
    rem Distribution zips live next to DIST_DIR once RADIOSIM_BUILD_ROOT is set,
    rem so sweeping only the repo root silently kept them (same 2026-08-04 review).
    rem 2026-08-11 (independent review): the follow-up line used to look INSIDE
    rem DIST_DIR *after* DIST_DIR was already removed above - dead either way,
    rem and one level off from where the zips actually sit. Sweep the build root.
    del /q RadioSimPro-*.zip 2>nul
    if defined RADIOSIM_BUILD_ROOT del /q "%RADIOSIM_BUILD_ROOT%\RadioSimPro-*.zip" 2>nul
    del /q build_log.txt 2>nul
    del /q radiosim.log 2>nul
    del /q .coverage 2>nul
    echo [OK] Clean complete.
    echo      kept    : venv / repo-root terrain_cache, results, basemap_pale / tools
    echo      removed : build output incl. the exe's own terrain_cache and results
    endlocal
    exit /b 0
)

echo ============================================================
echo RadioSim Pro - Build Script
echo ============================================================
echo.

echo [OK] Python ^(RADIOSIM_PYTHON^):
"%PY%" --version
echo      %PY%

echo.
echo [INFO] Checking dependencies (pinned via requirements.txt / requirements-dev.txt)...
rem Install both sets at their pins. PyInstaller is pinned in
rem requirements-dev.txt because its bootloader ends up inside the shipped exe:
rem an unpinned bundler means the release was built by whatever was newest.
"%PY%" -m pip install -r requirements.txt -r requirements-dev.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install pinned dependencies.
    pause
    exit /b 1
)
echo [OK] Dependencies OK
echo [OK] PyInstaller:
"%PY%" -m PyInstaller --version

echo.
echo [INFO] Cleaning old build...
if exist "%APP_DIR%"              rmdir /s /q "%APP_DIR%"
if exist "%WORK_DIR%\RadioSimPro" rmdir /s /q "%WORK_DIR%\RadioSimPro"
echo [OK] Clean done

echo.
echo [INFO] Building... (this may take a few minutes)
echo      dist : %DIST_DIR%
echo      work : %WORK_DIR%
echo.
"%PY%" -m PyInstaller radiosim.spec --noconfirm --distpath "%DIST_DIR%" --workpath "%WORK_DIR%"

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the error messages above.
    pause
    exit /b 1
)

rem ---- Gate: does the exe ship without a module that bundled code always
rem      imports? Source-run QA cannot see this (the dev machine has the whole
rem      stdlib), so this report is the only place it is visible. It was already
rem      visible at 2.6RC1 build time and nobody read it - hence a gate, not a
rem      note. Rationale in Japanese: buildtools\check_bundle_imports.py / ISSUES B-036.
echo.
echo [INFO] Checking the bundle for unconditionally-imported missing modules...
"%PY%" buildtools\check_bundle_imports.py "%WORK_DIR%\radiosim\warn-radiosim.txt"
if errorlevel 1 (
    echo.
    echo [ERROR] Build stopped: the exe would crash at run time. See above.
    pause
    exit /b 1
)

echo.
echo [INFO] Creating runtime directories...
if not exist "%APP_DIR%\terrain_cache" mkdir "%APP_DIR%\terrain_cache"
if not exist "%APP_DIR%\results"       mkdir "%APP_DIR%\results"

echo.
echo ============================================================
echo [SUCCESS] Build complete!
echo.
echo Output : %APP_DIR%\
echo Exe    : %APP_DIR%\RadioSimPro.exe
echo.
echo Zip the output folder for distribution.
echo ============================================================
echo.

rem ---- Version-boundary advisory: RC/final builds create their tag server-side
rem      via "gh release create", so they never pass through the pre-push hook.
rem      Firing here (a decision point RC/final always cross) is the backstop.
rem      Advisory only - never affects the build result. Skips quietly if the
rem      tool or node is absent.
if exist tools\qa-hook\release-check.mjs (
    where node >nul 2>&1
    if not errorlevel 1 (
        node tools\qa-hook\release-check.mjs
    ) else (
        echo [INFO] node not found - skipping release advisory.
    )
)

endlocal
