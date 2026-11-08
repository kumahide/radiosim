@echo off
setlocal enabledelayedexpansion

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
rem  setting up the development environment, in buildtools\build_link.py, and
rem  in ISSUES.md B-020. Keep the prose there, not here.
rem ============================================================

rem ============================================================
rem  Environment declaration (2.6a1 / B-020)
rem ------------------------------------------------------------
rem  RADIOSIM_PYTHON     = the one python.exe used for BOTH verification and
rem                        the shipped build. REQUIRED.
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

set "DIST_DIR=%~dp0dist"
set "WORK_DIR=%~dp0build"

rem ---- "clean" subcommand: wipe regenerable artifacts, caches, logs, zips ----
rem Kept: the venv and the REPO-ROOT terrain_cache / results / basemap_pale /
rem tools / .qa (source-run data; expensive to refetch or user-owned).
rem
rem NOT kept: everything under the build output root (DIST_DIR / WORK_DIR),
rem INCLUDING the terrain_cache and results that the built app created next to
rem itself. Those belong to the build output and a normal build discards them
rem too. Do not describe them as preserved - a 2026-08-04 review found the
rem README promising exactly that while this block deleted them.
rem Usage: build.bat clean
if /i "%~1"=="clean" (
    echo [INFO] Cleaning build artifacts, caches, logs, and distribution zips...
    if exist "%WORK_DIR%"      rmdir /s /q "%WORK_DIR%"
    if exist "%DIST_DIR%"      rmdir /s /q "%DIST_DIR%"
    if exist .pytest_cache     rmdir /s /q .pytest_cache
    if exist .ruff_cache       rmdir /s /q .ruff_cache
    rem __pycache__: every one under the repo, found recursively (B-344 - a list of
    rem named folders went stale whenever a folder was added). Virtualenvs,
    rem node_modules and .git are not ours to clean. The wildcard is required:
    rem without it, dir lists the CONTENTS of .\__pycache__ instead of searching.
    for /f "delims=" %%D in ('dir /s /b /ad *__pycache__ 2^>nul ^| findstr /v /i /c:"\.venv" /c:"\node_modules\\" /c:"\.git\\"') do rmdir /s /q "%%D"
    del /q build_log.txt 2>nul
    rem build_*.log: stray captures from before the log moved to build\build.log
    del /q build_*.log 2>nul
    del /q radiosim.log 2>nul
    del /q radiosim_profile.log 2>nul
    del /q .coverage 2>nul
    echo [OK] Clean complete.
    echo      kept    : venv / repo-root terrain_cache, results, basemap_pale / tools
    echo      removed : build output incl. the app's own terrain_cache and results
    endlocal
    exit /b 0
)

rem ---- Build (4.0 stage 8d / stage 11a, ISSUES.md I-144): the app ships on
rem      the python.org embeddable Python, not PyInstaller. This is only a thin
rem      door: every check and every step lives in buildtools\build_link.py
rem      (Japanese rationale there). The PyInstaller build of the old UI was
rem      removed with that UI in stage 11a.
rem      Usage: build.bat                  (portable zip; same as "link")
rem             build.bat installer        (Inno Setup installer; same as "link-installer")
rem      An unknown argument stops instead of falling back to the zip, so a typo
rem      never builds the wrong kind of package.
set "LINK_MODE="
if "%~1"=="" set "LINK_MODE=zip"
if /i "%~1"=="link" set "LINK_MODE=zip"
if /i "%~1"=="installer" set "LINK_MODE=installer"
if /i "%~1"=="link-installer" set "LINK_MODE=installer"
if not defined LINK_MODE (
    echo [ERROR] Unknown subcommand: %~1
    echo         Use one of: ^(none^) / link / installer / link-installer / clean
    pause
    exit /b 1
)
"%PY%" "%~dp0buildtools\build_link.py" --mode %LINK_MODE%
if errorlevel 1 (
    echo [ERROR] Link build failed. Check the error messages above.
    pause
    exit /b 1
)

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
