# RadioSim Pro 2.6

A desktop simulator for screening radio link propagation characteristics before field surveys.
Automatically retrieves DEM (Digital Elevation Model) data from the Geospatial Information Authority of Japan (GSI) and visualizes terrain profiles, diffraction loss, vegetation attenuation, and link budgets in real time.

---

## Table of Contents

1. [Overview](#overview)
2. [Building the Windows Binary](#building-the-windows-binary)
3. [Requirements](#requirements)
4. [File Structure](#file-structure)
5. [Installation &amp; Launch (from source)](#installation--launch-from-source)
6. [Menus](#menus)
7. [Map Window](#map-window)
8. [Usage — Single Mode](#usage--single-mode)
9. [Usage — Batch Mode](#usage--batch-mode)
10. [Input Parameters](#input-parameters)
11. [Calculation Models](#calculation-models)
12. [DEM Retrieval Logic](#dem-retrieval-logic)
13. [Save Package](#save-package)
14. [Project Files (.rsproj)](#project-files-rsproj)
15. [Architecture](#architecture)
16. [Development Environment](#development-environment)
17. [Testing](#testing)
18. [Known Limitations](#known-limitations)

---

## Overview

RadioSim Pro is a tool designed specifically for **pre-survey screening** in radio link design.
Enter the coordinates, antenna heights, and radio settings for the TX (transmitter) and RX (receiver) stations, and the tool automatically retrieves GSI elevation data, draws a terrain cross-section, and determines link budget viability within seconds.

### Key Features

#### Execution flows (four)

Also **the unit of class review** — when fixing a defect, always ask whether it applies to all four plus the map (five faces).

| Flow | Implementation | Source of truth for input |
| --- | --- | --- |
| **Single Mode** | `simulation.py` | The launcher's input fields |
| **Batch Mode** | `batch.py` | The batch table (CSV I/O) |
| **Condition Explorer (compare / sweep)** | `scenario.py` | A pinned path plus a condition list. **One DEM fetch + N pure computations** (leaning on the pipeline being two-phase) |
| **Relay Route** | `multihop.py` | The waypoint list; sections are derived. Regenerative relay model, so **the overall verdict is the min** (the tightest section), reported alongside the per-section breakdown |

#### Map

- **Map Window** (`views/map_window.py`, a single app-wide instance, 3 modes): pick coordinates / continuously add paths into Batch Mode / visualize, prefetch, and delete DEM cache

#### Calculation models (`models.py` — pure functions, zero side effects)

- Automatic terrain profile generation from GSI DEM PNG tiles (5 m / 10 m mesh)
- Earth curvature correction (standard atmosphere K = 4/3, fixed)
- Diffraction loss calculation using Deygout / Fresnel-Kirchhoff methods
- Vegetation attenuation (LoS intrusion depth model)
- Environmental loss (4 categories: Urban / Suburban / Rural / LoS)
- Rain attenuation (ITU-R P.838-3) and gaseous attenuation (ITU-R P.676-13 Annex 2)

#### Output and reports (`report_*.py` — all headless)

- **A4 reports (v2)**: per-path / summary as a single print-ready A4 page (`@page A4` + Ctrl+P for zero-dependency PDF; self-identifying header/footer)
- **Antenna initial aim (AZ/EL)**: true azimuth/elevation to the far end, shown for both ends in per-path reports (geometry from existing data = initial values)
- Automatic path map in HTML reports (TX/RX, path, and distance overlaid on a map)
- **All-paths overview map** in the summary report (color-coded by verdict)
- **Everything concatenated**: `report_all.html` carries the summary plus every path (for relays, every section)
- Save results as a package (PNG / CSV / JSON / HTML / KML)

#### Reusing input

- **Project files (`.rsproj`)**: coordinates, all parameters, project info, batch rows, explorer conditions and the waypoint list bundled into one file (**never results, never app settings**)
- **Project info (name + free note)**: entered in the launcher and inherited by both Single and Batch reports

#### Interface

- Real-time antenna height and rain rate sliders in the graph window
- Switchable coordinate notation (DD / DMS; `coords.py` is pure)
- Japanese / English UI — switchable from the menu bar (`i18n.py` is the single source)
- System-aware dark mode (Light / Dark / System auto)

### Accuracy Statement

The horizontal resolution of the DEM is 5–10 m, giving a practical accuracy of **±5–15 dB** for diffraction loss.
This tool is intended solely for screening purposes — determining whether a field survey is necessary — and must not be used as the basis for final link design decisions.

---

## Building the Windows Binary

Uses PyInstaller to produce a self-contained EXE folder (onedir mode) that requires no Python installation on the target machine.

### Prerequisites

- Python 3.11 or later (developed and CI-tested on 3.14)
- **`RADIOSIM_PYTHON`** must point at the `python.exe` of the same virtual environment the tests run in (**required**). → [Development Environment](#development-environment)
- PyInstaller and all dependencies are installed at their pinned versions by `build.bat`

> **Why declare it instead of discovering it**: `build.bat` used to build with whatever Python was on `PATH`, so the binary shipped with transitive dependencies that differed from the ones pytest had verified — including the TLS CA bundle. Any search-or-fallback logic would make that mismatch succeed silently, so a missing `RADIOSIM_PYTHON` **stops the build**.

### Build Steps

```bat
build.bat
```

`build.bat` performs the following steps automatically:

| Step | Action |
| --- | --- |
| 1 | Validate `RADIOSIM_PYTHON` (abort if unset or missing) and report the Python version |
| 2 | Install pinned dependencies (`pip install -r requirements.txt -r requirements-dev.txt` — PyInstaller is pinned there too) |
| 3 | Remove old build artifacts (`build/RadioSimPro/` and `dist/RadioSimPro/` under the output root) |
| 4 | Run `python -m PyInstaller radiosim.spec --noconfirm --distpath … --workpath …` |
| 5 | **Check the bundle for missing imports** (`buildtools/check_bundle_imports.py`). **Aborts the build** if the exe excludes a module that bundled code imports unconditionally |
| 6 | Create `terrain_cache/` and `results/` in the output folder |

`build.bat clean` removes regenerable artifacts, caches, logs, and distribution zips.

- **Kept**: the virtual environment and the **repo-root** `terrain_cache/`, `results/`, `basemap_pale/` (data from source runs — expensive to refetch, or user-owned)
- **Removed**: everything under the build output root. ⚠️ **That includes the `terrain_cache/` and `results/` the built exe created next to itself** — they are part of the build output and a normal build recreates them anyway.

### Output

Defaults to `dist/` next to the sources. Set **`RADIOSIM_BUILD_ROOT`** to place `dist/` and `build/` elsewhere — useful when the repository lives inside a cloud-synced folder and you do not want every rebuild to re-upload.

```
dist/
└── RadioSimPro/
    ├── RadioSimPro.exe   ← launch this
    ├── _internal/        ← Python runtime and dependencies
    ├── terrain_cache/
    └── results/
```

### Creating a Distribution Package

ZIP the `RadioSimPro/` folder from the build output:

```powershell
# Honour RADIOSIM_BUILD_ROOT (falls back to dist/ next to the sources)
$dist = if ($env:RADIOSIM_BUILD_ROOT) { "$env:RADIOSIM_BUILD_ROOT\dist" } else { "dist" }
Compress-Archive -Path "$dist\RadioSimPro" -DestinationPath "$dist\RadioSimPro.zip" -Force
```

> ⚠️ **Do not hard-code `dist\RadioSimPro`** — where `RADIOSIM_BUILD_ROOT` is set it either fails or silently zips a stale artifact from the repository.

### Key `radiosim.spec` Settings

| Setting | Details |
| --- | --- |
| `icon.png` → `icon.ico` | Auto-converted at build time; skipped if `icon.png` is absent |
| EXE file properties | Auto-generated from `APP_VERSION` / `COPYRIGHT` in `version.py` |
| `console=False` | No console window shown to the user |
| UPX compression | Enabled only when UPX is installed |
| `README_binary_*.md` / `logo.png` | Bundled into the binary; accessed via `sys._MEIPASS` |

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError` on launch | Remove the module from `excludes`, or add it to `hiddenimports` in `radiosim.spec`, then rebuild |
| Error messages not visible | Change `console=False` to `console=True` in `radiosim.spec` and rebuild |
| SmartScreen warning on target machine | Expected for unsigned executables — click "More info" → "Run anyway" |

---

## Requirements

| Item     | Requirement                                                   |
| -------- | ------------------------------------------------------------- |
| OS       | Windows 10/11 (macOS / Linux may work but are untested)       |
| Python   | 3.11 or later — required by pinned numpy 2.4 (tested on 3.14) |
| Internet | Required for DEM retrieval (fetched tiles are cached locally) |

### Dependencies

```
python -m pip install -r requirements.txt
```

**[`requirements.txt`](requirements.txt) is the single source of versions** (pinned, so a source run gets what the binary ships). Installing by name leaves the versions floating and duplicates the table below.

| Library    | Purpose                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------- |
| numpy      | Vector computation for terrain and propagation calculations                                 |
| matplotlib | Terrain profile graph and slider rendering                                                  |
| requests   | HTTP retrieval of GSI DEM tiles                                                             |
| Pillow     | PNG tile image decoding                                                                     |
| sv-ttk     | Windows 11-style UI theme                                                                   |
| darkdetect | System dark mode detection                                                                  |
| markdown   | README viewer (optional — the app works without it)                                        |
| truststore | SSL certificate verification in corporate proxy environments (optional — works without it) |
| tkintermapview | Map window tile display (GSI pale map; the map feature degrades gracefully if absent) |

---

## File Structure

```
radiosim/
├── main.py               # Entry point
├── models.py             # Pure calculation logic (no side effects)
├── simulation.py         # ViewModel / orchestrator
├── config.py             # App config I/O, input validation, logging (minimal external deps)
├── dem.py                # DEM/pale tile fetch, elevation decode, cache, proxy (external deps confined)
├── batch.py              # Batch execution engine (CSV I/O, validation, run)
├── scenario.py           # Condition explorer runner (A-1 compare / A-2 sweep; phases; headless)
├── report_scenario.py    # Condition explorer output (A4 line chart + table, CSV; headless)
├── multihop.py           # Relay path engine (A-3; waypoints to hops, min aggregation; headless)
├── report_multihop.py    # Relay path output (route sheet + per-hop sheets, hops.csv; headless)
├── project.py            # Project file (.rsproj) I/O — bundles the whole input set (headless)
├── report_common.py      # Shared report parts (A4 skeleton CSS, header/footer, document shell; pure)
├── report_path.py        # Per-path output generation (PNG/HTML/KML; headless)
├── report_summary.py     # Batch summary output generation (CSV/HTML/KML, all-pages document; headless)
├── report_map.py         # Headless path-overlay map generation for reports
├── map_graphics.py       # Pure-PIL map overlay drawing (shared by UI and reports)
├── coords.py             # Coordinate notation conversion (DD <-> DMS, pure functions)
├── units.py              # Distance display formatting (internal km -> displayed m, pure functions)
├── mpl_fonts.py          # matplotlib Japanese font application (headless; shared by graph/report)
├── i18n.py               # Multilingual string table
├── version.py            # Version information
├── views/
│   ├── launcher.py       # Launcher window
│   ├── graph.py          # Graph window (matplotlib + tkinter)
│   ├── map_window.py     # Map window (Pick Coordinates / Append to Batch / Cache Management modes)
│   ├── dialogs.py        # Shared modal dialogs centered on the parent window
│   ├── errors.py         # Single sink that routes unhandled GUI exceptions to the log and a dialog
│   ├── progress.py       # Progress transport (worker thread -> main thread)
│   ├── theme.py          # Theme colors and UI fonts for plain tk widgets (sourced from sv_ttk)
│   ├── window_fit.py     # Single implementation of fit-window-to-content (clipping guard)
│   ├── scenario.py       # Condition explorer window (compare / sweep)
│   ├── multihop.py       # Relay route window (waypoints are the input surface; hops are derived)
│   └── batch_builder.py  # Batch Mode window
├── docs/
│   └── glossary.md       # Glossary of on-screen terms (enforced by tests/test_i18n_glossary.py)
├── README_ja.md          # Japanese README
├── README_en.md          # This file
└── tests/
    ├── test_models.py
    ├── test_golden_links.py   # Regression corpus (golden link-budget values + purity invariants)
    ├── golden_corpus_gen.py  # Corpus generator (run manually; fetches real DEM)
    ├── test_simulation.py
    ├── test_config.py
    ├── test_dem.py
    ├── test_batch.py
    ├── test_report.py
    ├── test_scenario.py
    ├── test_multihop.py
    ├── test_project.py
    ├── test_report_map.py
    ├── test_map_window.py
    ├── test_coords.py
    ├── test_units.py
    ├── test_mpl_fonts.py
    ├── test_progress.py
    ├── test_runner_logging.py
    ├── test_theme.py
    ├── test_window_fit.py
    ├── test_errors.py
    ├── test_bundle_imports.py
    ├── test_ui_consistency.py
    ├── test_i18n_glossary.py
    ├── test_paths.py
    ├── test_smoke.py
    ├── test_docs_consistency.py
    ├── test_env_consistency.py
    ├── test_repo_hygiene.py
    └── test_claude_hooks.py
```

---

## Installation & Launch (from source)

```bash
# Install dependencies (requirements.txt is the single source of versions —
# pinned so that a source run matches what ships in the binary)
python -m pip install -r requirements.txt

# For running the tests or building, add the development set
python -m pip install -r requirements-dev.txt

# Launch
cd radiosim
python main.py
```

The following directories are created automatically in the project root on first launch:

| Directory          | Contents                              |
| ------------------ | ------------------------------------- |
| `terrain_cache/` | Disk cache for DEM tiles              |
| `results/`       | Output destination for saved packages |

---

## Menus

Three menus — **File / Settings / Help** (built in `_build_menu`, [views/launcher.py](views/launcher.py)). Labels come from the `i18n.py` dictionaries as the single source. Every item is listed below.

### File

Home for operations that **reach out of the app** (moved here from the launcher button row). ⚠️ Do not add buttons here — the button row already spends the FHD 100% (1080px screen) height budget and is a recurring source of clipping.

| Item                | Description                                                              |
| ------------------- | -------------------------------------------------------------------------- |
| Open Project...     | Loads a `.rsproj` and restores the whole input set → [Project Files (.rsproj)](#project-files-rsproj) |
| Save Project As...  | Writes the current input set to a `.rsproj` → same                        |
| Load Parameters...  | Imports **simulation parameters only** from a settings file              |
| Open Results Folder | Opens `results/` in Explorer                                             |

### Settings

Selections are persisted to `radiosim_conf.json`.

| Item                 | Options                     | Description                                                     |
| -------------------- | --------------------------- | ----------------------------------------------------------------- |
| Theme                | System / Light / Dark       | Window color theme                                                |
| Language             | English / 日本語            | UI language (requires restart)                                    |
| Coord Format         | Decimal Degrees (DD) / Degrees Minutes Seconds (DMS) | How coordinates are **displayed** (`coords.py`) → below |
| Proxy Settings...    | URL entry                   | Explicit HTTP proxy URL (blank = OS proxy settings) → below       |
| Load App Settings... | —                           | Imports **only** theme, language and proxy from a settings file   |
| Delete All Cache...  | —                           | Deletes all downloaded DEM / map tiles (with confirmation)        |

### Help

| Item        | Description                          |
| ----------- | -------------------------------------- |
| Open README | Opens this document in a browser       |
| About       | Shows the version from `version.py`    |

> **"Load Parameters" and "Load App Settings" are mutually exclusive in scope** — the former covers simulation parameters, the latter theme/language/proxy. **Neither writes the other's territory** (so opening someone else's file never flips your display language or network settings).

> **The Map Window is not in the menus** — it opens from the **"Map Window" button** at the bottom of the launcher (→ [Map Window](#map-window)).

### Coordinate Format (DD / DMS)

**A notation (display) setting, not an input mode.** `coords.parse_pair` accepts both DD and DMS leniently and everything is normalised to DD before the calculation (values reaching `simulation.SimParams` are always DD).

⚠️ Fields are reformatted in only three places — startup, notation switch, and settings/project load — **never on entry commit** (Enter / focus-out). So typing DD while DMS is selected leaves the text as typed: **not a fault**, but the screen gives no signal that the value was accepted (a known rough edge).

### Proxy Settings

If DEM tile retrieval requires an HTTP proxy (e.g. on a corporate network), open **Settings > Proxy Settings** and enter the proxy URL:

```
http://proxy.example.com:8080
```

- Changes take effect immediately — no restart required
- Leaving the field blank and clicking OK reverts to OS proxy settings (system settings / environment variables)
- `truststore` integration with the Windows certificate store is also active to handle corporate SSL inspection
- If the elevation server is entirely unreachable, the run **aborts on consecutive failures** and asks the user, instead of quietly finishing on flat terrain

---

## Map Window

The **"Map Window" button** in the launcher (`views/map_window.py`) opens an auxiliary window over the GSI pale map. The **map is a single app-wide instance owned by the launcher**, with a three-mode selector at the top (the batch window does not open its own map — the launcher is the main line and the batch is a subordinate sink). The core simulation works without the map; the Map Window is a convenience layer. On opening it auto-zooms/centers to fit the path length of the current TX/RX.

- **Pick Coordinates mode (default)**: click the map to set TX→RX alternately and write them back to the launcher's start/end fields (the numeric fields are the source of truth). Shows UISP-style markers, a path line, and a distance label. Wired via `apply_map_pick` / `current_path_coords`.
- **Append to Batch mode**: selecting it opens (or raises) the batch window; each TX→RX pair placed on the map appends one batch row and auto-resets (no "add row" needed). RF (frequency, gains, antenna heights) is frozen from the launcher at the moment of adding. Committed paths render as **TX = filled dot / RX = bearing arrowhead** plus distance (so TX/RX stay distinguishable even when near/identical). Batch row edits (delete, edit-commit, import, etc.) reflect on the map in real time. Wired via `append_path` / `existing_paths`.
- **Cache Management mode**: follows pan/zoom and shades cached areas by highest accuracy (green = 5 m LiDAR / yellow = 5 m photogrammetry / cyan = 10 m). Gestures: drag = pan / Ctrl + drag = download / Ctrl + Alt + drag = force re-download / Shift + Ctrl + drag = delete area, each with a confirmation dialog. Built on `dem.prefetch_tiles` and related public APIs; tiles are never re-downloaded once present. Clear everything via **Settings > Delete All Cache**.

---

## Usage — Single Mode

### 1. Launcher Window

An input form is displayed on startup.

#### Site Info

| Field                   | Description                                                   |
| ----------------------- | ------------------------------------------------------------- |
| Start Coords (Lat, Lon) | TX station latitude and longitude (e.g.`34.5429, 132.4118`) |
| End Coords (Lat, Lon)   | RX station latitude and longitude                             |
| TX Antenna Height (m)   | TX antenna height above ground                                |
| RX Antenna Height (m)   | RX antenna height above ground                                |

#### Radio Settings

| Field                    | Description                                  |
| ------------------------ | -------------------------------------------- |
| Frequency (MHz)          | Frequency (1–100,000 MHz)                   |
| TX Power (dBm)           | Transmit power                               |
| TX/RX Antenna Gain (dBi) | Antenna gain                                 |
| Sensitivity (dBm)        | Receiver sensitivity (minimum receive level) |

#### Environment

| Field                 | Description                                                                             |
| --------------------- | --------------------------------------------------------------------------------------- |
| Env Type              | Environment category (Urban / Suburban / Rural / LoS)                                   |
| Vegetation Height (m) | Average height of vegetation or buildings along the path                                |
| Rician K-Factor (initial) | LOS/scatter power ratio. Display only — does not affect link budget calculation (default = 10.0) |
| Sampling Points       | Number of terrain sample points (10–2000; more = higher accuracy but longer retrieval) |

### 2. Single Mode Button

Clicking the button runs data retrieval in two phases.

1. **DEM tile prefetch**: All tiles within the TX/RX bounding box are downloaded to the disk cache (up to 8 threads). Already-cached tiles are skipped, so subsequent runs complete instantly.
2. **Terrain elevation fetch**: Elevation is retrieved in parallel for each sample point (up to 8 threads). If the same TX/RX coordinates and sample count were used previously, cached data is loaded instantly.

### 3. Graph Window

After retrieval completes, the terrain cross-section graph is displayed.

#### Reading the Graph

| Element             | Description                                              |
| ------------------- | -------------------------------------------------------- |
| Brown fill          | Terrain (with earth curvature correction applied)        |
| Green fill          | Vegetation layer (terrain elevation + vegetation height) |
| Red dashed line     | Line of Sight (LoS)                                      |
| Cyan band           | 1st Fresnel Zone                                         |
| Black vertical bars | TX / RX antennas                                         |

#### Sliders

| Slider    | Range       | Description                           |
| --------- | ----------- | ------------------------------------- |
| TX Height | 0–150 m    | Adjust TX antenna height in real time |
| RX Height | 0–150 m    | Adjust RX antenna height in real time |
| Rain Rate | 0–100 mm/h | Adjust rain rate in real time         |

Moving a slider triggers automatic recalculation after a 50 ms debounce delay.

#### Save Button

Saves the current display state to `results/YYYYMMDD_HHMMSS/` (see [Save Package](#save-package)).

### 4. Saving and Loading Settings

- Input values are automatically saved to `radiosim_conf.json` each time Single Mode is run
- **Load Settings**: Loads a previous `settings.json` and restores it to the input form
- **Open Results**: Opens the `results/` folder in Explorer

---

## Usage — Batch Mode

Click the **Batch Mode** button in the launcher to open the dedicated window.

### Design: refine in Single, finalize in Batch

**Single (the launcher) is where you refine conditions; Batch is where you produce deliverables from finalized conditions.** The launcher is the single source of truth, and each batch row is a **finalized link frozen by copying the launcher fields at the moment the row is added**.

### Input Methods

**Manual entry**: Type IDs, coordinates, antenna heights, frequencies, and TX/RX gains directly into the table. Rows can be added, deleted, reordered by drag and drop, and cells edited in place.

The **Dist(m)** column on the right is read-only and is computed from the TX/RX coordinates (refreshed whenever coordinates are committed). A mistyped coordinate shows up as an absurd distance, so the error is visible before you run the batch.

- **+ Add Row**: adds a row frozen from the current launcher fields (coordinates, frequency, gains, antenna heights).
- **Right-click a row**: opens the per-row menu.
  - **→ Send to Single**: loads that row's coordinates + RF into the launcher for adjustment.
  - **⟳ Update RF from Launcher**: writes the launcher's current RF back into that row (**coordinates are preserved**).
  - Duplicate / Delete.

**CSV import**: Click the Template button to save a sample CSV, edit it, then import.

#### CSV Format

Required columns: `id, start, end, h_tx, h_rx`

Optional columns: `freq, gain_tx, gain_rx, note`

```csv
id,start,end,h_tx,h_rx,freq,gain_tx,gain_rx,note
path01,"34.54, 132.41","34.53, 132.40",30.0,10.0,2400,12.5,8.0,Main link
path02,"34.55, 132.42","34.52, 132.39",20.0,15.0,,,,Sub link
```

- `start` / `end` must be quoted because they contain a comma
- `freq` / `gain_tx` / `gain_rx` fall back to the Common Settings value when omitted (they are **per-link identifying attributes** that may differ per path). Env type, rain rate, and diffraction model are set globally in Common Settings and apply to all paths
- Legacy CSVs without `gain_tx` / `gain_rx` columns still load (backward compatible; gains inherit Common Settings)
- Column names are **case-insensitive and ignore surrounding spaces** (`ID,Start,…` loads fine). `id` values must be unique **case-insensitively** (`p01` and `P01` would map to the same output folder, so they are rejected as duplicates)

### Common Settings (a snapshot of the launcher)

The **Common Settings** panel at the top defines default values used whenever a per-path override is not specified. It is **read-only** — a snapshot of the launcher (the source of truth). Use the **↻ From Launcher** button to pull in the launcher's current values.

### Running and Results

Click **▶ Run** to process paths sequentially. OK / NG / ERR counts update in real time.

On completion, the following are saved to `results/batch_YYYYMMDD_HHMMSS/`:

| File                         | Contents                                                         |
| ---------------------------- | ---------------------------------------------------------------- |
| `summary.html`             | Summary report for all paths (with graph thumbnails)             |
| `summary.csv`              | Numerical results for all paths (spreadsheet-compatible)         |
| `summary.kml`              | Google Earth KML with OK / NG / Error color coding               |
| `report_all.html`          | Summary + every path in one document (Ctrl+P prints them all)    |
| `{id}/report.html`         | Per-path detailed report (terrain graph + path map embedded)     |
| `{id}/profile.png`         | Terrain cross-section graph                                      |
| `{id}/path.kml`            | 3D KML with terrain, LoS, Fresnel zone, and obstruction segments |
| `{id}/settings.json`       | Per-path input parameters                                        |
| `{id}/terrain_profile.csv` | Terrain profile data                                             |
| `{id}/report.txt`          | Text-format link budget report                                   |

---

## Input Parameters

### Validation Ranges

| Parameter         | Min                            | Max     | Unit   |
| ----------------- | ------------------------------ | ------- | ------ |
| Frequency         | 1                              | 100,000 | MHz    |
| TX Power          | -30                            | 60      | dBm    |
| TX/RX Gain        | 0                              | 60      | dBi    |
| Sensitivity       | -130                           | -20     | dBm    |
| TX/RX Height      | 0                              | 500     | m      |
| Vegetation Height | 0                              | 100     | m      |
| Rician K-Factor   | 0                              | 30      | —     |
| Sampling Points   | 10                             | 2,000   | points |
| Rain Rate         | 0                              | 200     | mm/h   |
| Env Type          | Urban / Suburban / Rural / LoS | —      |        |
| Diff Method       | deygout / single               | —      |        |

---

## Calculation Models

### Earth Curvature Correction

Radio waves are refracted by the atmosphere and bend more than the Earth's curvature alone. This is modeled using the effective Earth radius factor K. This tool uses the standard atmosphere value (K = 4/3 ≈ 1.333) as a fixed internal constant.

```
Effective Earth radius  Re = R_earth × K  (K = 4/3, fixed)
Curvature correction   Δh(d) = d × (D - d) / (2 × Re)  [m]
```

| K value      | Meaning                                     |
| ------------ | ------------------------------------------- |
| 4/3 ≈ 1.333 | Standard atmosphere (value used by this tool) |
| K > 4/3      | Atmospheric duct (waves bend more strongly) |
| K < 4/3      | Sub-refractive conditions                   |

### Fresnel Zone

The 1st Fresnel zone radius (ITU-R P.526):

```
r₁(d) = √(λ × d₁ × d₂ / (d₁ + d₂))
```

When the 1st Fresnel zone is obstructed by terrain or vegetation, diffraction loss occurs.

### Diffraction Loss

#### Deygout Method (default, ITU-R P.526)

A recursive model that handles multiple diffraction edges. Appropriate for real terrain with overlapping ridges.

#### Fresnel-Kirchhoff Loss J(ν)

```
J(ν) = 6.9 + 20 × log₁₀(√((ν - 0.1)² + 1) + ν - 0.1)  [dB]  (ν > -0.8)
J(ν) = 0                                                          (ν ≤ -0.8)
```

#### Single Method

Applies J(ν) only to the maximum ν across all sample points. Fast, but may underestimate loss with multiple ridges.

### Vegetation Attenuation

```
Intrusion depth(d) = max(0, veg_top(d) - LoS(d))
Weight(d)          = clip(intrusion depth / r₁(d), 0, 1)
Effective length   = Σ[weight(d)] × sample spacing
Veg Loss           = min(effective length × coeff, 45 dB)
```

| Frequency band | coeff         |
| -------------- | ------------- |
| Below 1 GHz    | 0.12 × f^0.5 |
| 1–6 GHz       | 0.20 × f^0.7 |
| Above 6 GHz    | 0.35 × f^0.9 |

### Environmental Loss

```
Env Loss = base + blocked_ratio × blk_c + slant_dist × dist_c + diff_loss × diff_c
```

| Environment | base | blk_c | dist_c | diff_c | min | max  |
| ----------- | ---- | ----- | ------ | ------ | --- | ---- |
| Urban       | 10.0 | 0.08  | 1.20   | 0.15   | 6.0 | 30.0 |
| Suburban    | 6.0  | 0.05  | 0.80   | 0.10   | 3.0 | 30.0 |
| Rural       | 4.0  | 0.03  | 0.50   | 0.08   | 2.0 | 25.0 |
| LoS         | 2.0  | 0.01  | 0.30   | 0.05   | 1.0 | 15.0 |

### Rain Attenuation (ITU-R P.838-3)

```
γ_R = k × R^α  [dB/km]
Rain Loss = γ_R × d_slant
```

Rain sensitivity at 2.4 GHz is very low (≈ 0.1 dB/km at 100 mm/h). Practical impact begins above 10 GHz.

### Gaseous Attenuation (ITU-R P.676-13 Annex 2)

```
γ_total = γ_O₂ + γ_H₂O  [dB/km]
Gas Loss = γ_total × d_slant
```

### Link Budget

```
EIRP       = P_tx + G_tx                             [dBm]
FSPL       = 20×log₁₀(d) + 20×log₁₀(f) - 147.55   [dB]
Total Loss = FSPL + Diff + Veg + Env + Rain + Gas   [dB]
P_rx       = EIRP + G_rx - Total Loss               [dBm]
Act Margin = P_rx - Sensitivity                     [dB]
Status     = OK (≥ 0 dB) / NG (< 0 dB)
```

---

## DEM Retrieval Logic

### Data Sources

| Layer ID      | Resolution           | Zoom | Coverage                      |
| ------------- | -------------------- | ---- | ----------------------------- |
| `dem5a_png` | 5 m (airborne LiDAR) | 15   | Urban areas, mountain regions |
| `dem5b_png` | 5 m (photogrammetry) | 15   | Wider coverage than dem5a     |
| `dem_png`   | 10 m (base map)      | 14   | Nationwide                    |

Layers are tried in order: `dem5a_png` → `dem5b_png` → `dem_png`. If a higher-priority layer returns 404 or a missing-data pixel `(128, 0, 0)`, the next layer is used.

### Caching Strategy

- **Tile prefetch**: At simulation start, all tiles within the TX/RX bounding box are pre-downloaded to the disk cache (supports offline use and speeds up batch processing)
- **Memory cache**: Tiles stored in process memory (key: `(layer_id, xtile, ytile)`)
- **Disk cache**: Tiles saved to `terrain_cache/{layer_id}/{xtile}/{ytile}.png`, persists across sessions
- **Terrain cache**: If TX/RX coordinates and sample count match a previous run, DEM retrieval is skipped entirely

---

## Save Package

### Single Mode

Saves to `results/YYYYMMDD_HHMMSS/`:

| File                    | Contents                                                 |
| ----------------------- | -------------------------------------------------------- |
| `profile.png`         | Terrain cross-section graph (150 dpi)                    |
| `report.html`         | Detailed report with the terrain graph and a path map embedded |
| `path.kml`            | 3D KML for Google Earth                                  |
| `settings.json`       | Complete input parameters (reloadable via File > Load Parameters) |
| `terrain_profile.csv` | Terrain profile data                                     |
| `report.txt`          | Text-format link budget report                           |

### Batch Mode

Saves to `results/batch_YYYYMMDD_HHMMSS/`:

| File             | Contents                                         |
| ---------------- | ------------------------------------------------ |
| `summary.html` | All-path summary with thumbnails                 |
| `summary.csv`  | Numerical results for all paths                  |
| `summary.kml`  | Google Earth KML for all paths                   |
| `report_all.html` | Summary + every path in one printable document |

### Condition Explorer (compare / sweep)

Saves to `results/scenario_YYYYMMDD_HHMMSS/`:

| File              | Contents                                                     |
| ----------------- | ------------------------------------------------------------ |
| `scenario.html` | Single-page A4 (compare = difference table with in-cell deltas / sweep = chart + table) |
| `scenario.csv`  | Numbers for every condition / point (machine-readable)        |
| `{id}/`        | Per-path package (same structure as Single Mode) |

### Relay Route

Saves to `results/multihop_YYYYMMDD_HHMMSS/`:

| File | Contents |
| --- | --- |
| `route.html` | Combined sheet (overall verdict = min, per-section breakdown, overview map) |
| `report_all.html` | Combined sheet + every section in one printable document |
| `hops.csv` | **One row per section** (a separate contract from the batch `summary.csv`; the file name and column names stay `hop*`) |
| `{id}/` | Per-section package (same structure as Single Mode) |

---

## Project Files (.rsproj)

A single JSON file that bundles **the whole input set**. Read and written from **File → Open Project / Save Project** in the launcher (implemented in [`project.py`](project.py) — headless, never imports tkinter).

### What it bundles

| Section | Contents |
| --- | --- |
| `meta` | Project name and free note |
| `params` | Launcher sim parameters (coordinates always DD) |
| `batch` | Batch rows, keyed by `PathRow` attribute names (not CSV column names) |
| `scenario` | Explorer conditions, **kept as the on-screen strings** |
| `multihop` | Relay waypoints and per-section RF |

**Never included**: results (that is what `results/` is for), app settings (theme / language / proxy), window geometry or open/closed state. App settings are excluded so that opening someone else's project cannot silently switch your language or proxy — both the reader and the writer go through `config.select_sim`.

### Compatibility rules

- `schema_version` is **a single integer for the whole document** (no per-section versions).
- **Unknown keys are ignored, missing keys fall back to defaults, and a newer version is rejected** (the same style as `config.load_config`).
- `app_version` / `saved_at` are provenance stamps and are **never used to decide how to read the file**.
- **A missing section means "this window's state is unknown"**, not "the window is empty". The reader leaves it alone and the writer carries the previous value forward, so closing the batch window never produces a saved file with the rows deleted.
- Values that are not readable numbers (NaN / Inf) are never written: that section is skipped and the UI says so, rather than emitting non-standard JSON.

### How loading works

Loading closes the open windows (batch / explorer / relay route) after asking for confirmation, then they pick the new state up when reopened. This matches the app-wide rule that a window freezes its inputs when it opens, so no window needs a separate injection path.

---

## Architecture

### Layer Structure

```
[View layer]
  views/launcher.py       Launcher window
  views/graph.py          Graph window
  views/map_window.py     Map window (Pick Coordinates / Append to Batch / Cache Management)
  views/batch_builder.py  Batch Mode window
  views/dialogs.py        Shared modal dialogs centered on the parent
  views/errors.py         Unhandled Tk callback exceptions -> log + dialog (one sink for every window)
  views/progress.py       Progress transport (queue + polling, shared by single/batch)
  views/theme.py          Single source of theme colors and UI fonts (for tk.Menu / tk.Canvas outside ttk)
  views/window_fit.py     Single implementation of window sizing (shared by every window)
  views/scenario.py       Condition explorer window (compare / sweep)
  -> Has side effects. Delegates calculation and I/O downward.

          |
          v

[Orchestrator layer]
  simulation.py   DEM fetch management, terrain cache, calculation calls
  batch.py        CSV I/O, validation, batch execution engine
  scenario.py     Condition explorer runner (fixed terrain, N conditions, phases)
  multihop.py     Relay path engine (waypoints are the source of truth; overall verdict is the weakest section)
  project.py      Project files (.rsproj) — bundles the input set (never results, never app settings)
  report_scenario.py Condition explorer output (line chart + table, CSV; headless)
  report_common.py  Shared report parts (A4 skeleton, header/footer, document shell)
  report_path.py    Per-path output generation (PNG/HTML/KML; headless)
  report_summary.py Summary output generation (CSV/HTML/KML, all-pages document; headless)
  report_map.py   Headless path-overlay map generation (tile fetch + compositing)

          |
          +---> [Pure calc. layer]  models.py
          |     Propagation calc. (no side effects)
          |
          +---> [Pure rendering layer]  map_graphics.py
          |     PIL drawing of markers/distance/north arrow (shared by UI and reports)
          |
          +---> [Pure conversion layer]  coords.py / units.py
          |     Coordinate notation (DD <-> DMS) and distance display formatting (km -> m), no side effects
          |
          +---> [Config & validation layer]  config.py
          |     App config I/O, input validation, logging
          |
          +---> [External dependency layer]  dem.py
                DEM/pale tile HTTP fetch, elevation decode, cache, proxy
```

---

## Development Environment

Dependencies are declared in **two files**:

| File | Contents | Ships in the binary? |
| --- | --- | --- |
| `requirements.txt` | Runtime dependencies (numpy / matplotlib / requests …) | **Yes** |
| `requirements-dev.txt` | Testing, static analysis, packaging (pytest / pytest-cov / pyright / ruff / bandit / PyInstaller) | **No** |

They are separate because mixing development tooling into the runtime dependency list risks bundling it into the EXE. **Both are pinned** — the PyInstaller pin matters most, since it decides the bootloader inside the shipped binary; leaving it unpinned means the release was built by whatever version was newest that day.

```bat
rem 1. Create the virtual environment (preferably outside any cloud-synced folder:
rem    a venv bakes in absolute paths, so syncing it is useless on another machine)
python -m venv D:\dev\radiosim\venv

rem 2. Install both dependency sets at their pinned versions
D:\dev\radiosim\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt

rem 3. Declare that interpreter as the single environment used for both
rem    verification and builds (reopen the shell afterwards)
setx RADIOSIM_PYTHON D:\dev\radiosim\venv\Scripts\python.exe

rem 4. Optional: keep dist / build out of the synced folder as well
setx RADIOSIM_BUILD_ROOT D:\dev\radiosim
```

`tests/test_env_consistency.py` verifies that the versions actually installed in the interpreter running pytest match the pins in `requirements.txt`, so drift fails the suite the moment it appears.

## Testing

**Run tests with the declared interpreter (`RADIOSIM_PYTHON`).**

```powershell
& "$env:RADIOSIM_PYTHON" -m pytest tests/ -v
& "$env:RADIOSIM_PYTHON" -m pytest tests/ --cov
```

> ⚠️ **Do not use a bare `python -m pytest`.** A different interpreter drifts from
> the pins in `requirements.txt`, so **the versions you verified stop matching the
> ones that go into the exe** (`build.bat` always builds with the declared
> interpreter). If `RADIOSIM_PYTHON` is set and you start pytest with another
> Python, `tests/conftest.py` stops the run and explains why. **Nothing happens
> where the variable is unset** (CI, a fresh clone), so `python -m pytest` is fine there.

### Test Suite

| File                       | Coverage                                                                        |
| -------------------------- | ------------------------------------------------------------------------------- |
| `test_models.py`         | Terrain profile, diffraction, vegetation, rain, gas, link budget                |
| `test_scenario.py`       | Condition explorer (single DEM fetch + N conditions, phase progress, non-mutating overrides, A4 sheet/CSV output) |
| `test_multihop.py`       | Relay paths (waypoint-to-hop derivation, shared relay height, losses never chained, min aggregation, hops.csv / route sheet) |
| `test_project.py`        | Project files (`.rsproj` round-trip, app settings never imported, missing section means "not held", newer schema rejected, corrupt files) |
| `test_golden_links.py`   | Regression corpus: freezes every `LinkBudgetResult` field for 26 representative links (recomputed from stored real-DEM elevations, no network) plus the purity invariants A-1/A-2 rely on |
| `test_simulation.py`     | DEM fetch (parallel, cache, error handling), calculation, save (report coords)  |
| `test_config.py`         | Input validation, config I/O (app/sim split), i18n key coverage                 |
| `test_dem.py`            | DEM decoding, tile fetch/prefetch, proxy/session, cache deletion/stats, coverage outline |
| `test_batch.py`          | CSV parse, validation, _make_params, execution engine (run_batch/_process_one/_fetch_sync), HTML coords |
| `test_report.py`         | KML generation (per-path/summary, lon-lat order, obstruction, XML escaping), PNG/HTML smoke, combined report (sheet CSS scoping, in-document anchors) |
| `test_report_map.py`     | Report path-overlay map generation (zoom fit, tile stitch, rotation, crop)      |
| `test_map_window.py`     | Map window safe teardown (after-loop stop invariants) + static guard that all teardown paths go through close_map_safely |
| `test_coords.py`         | Coordinate conversion (DD/DMS parse, format, roundtrip, hemisphere sign, errors)|
| `test_units.py`          | Distance display formatting (km -> m, digit grouping, raw values for CSV, arrays)|
| `test_mpl_fonts.py`      | matplotlib Japanese font application (language-aware, priority, no-font fallback)|
| `test_progress.py`       | Progress transport (start/stop lifecycle, stale poll after stop, latest-only delivery, thread safety) |
| `test_runner_logging.py` | Background runs (batch / explorer / relay) log their failures **with a traceback** |
| `test_theme.py`          | Plain tk widget colors (color source from sv_ttk, fg/bg contrast, applied to every menu and re-applied on theme switch) and UI fonts (labels match entries, dynamically created widgets, no hardcoded font families) |
| `test_ui_consistency.py` | Cross-window consistency gate (run button at the right end of the progress bar, Accent only on "run", verdict colors sourced from theme, **no bold on screen**) |
| `test_i18n_glossary.py`  | On-screen wording gate (checks the glossary in [docs/glossary.md](docs/glossary.md) against every i18n string: no avoided synonym reaches the screen, every listed term is actually in use, and the table does not contradict itself) |
| `test_window_fit.py`     | Cross-window clipping gate (every window fits its content, still fits after content grows, and **unregistered new windows** are caught statically) |
| `test_errors.py`         | Unhandled exceptions in GUI callbacks are logged **with a traceback** and surfaced in a dialog naming the log file (no stacked modals when errors repeat; the log survives even if the dialog cannot be shown) |
| `test_bundle_imports.py` | Gate for the bundle-import gate itself (real warn lines from the failing `2.6RC1` build as fixtures; `(conditional)`, `missing module` and allowlisted pairs must stay silent; a missing report must not count as a pass) |
| `test_paths.py`          | Write-target path resolution (config, results, log and DEM cache do not depend on the current directory; normal startup resolves to the legacy locations; static guard that the resolver is not re-implemented elsewhere) **plus test-run isolation** (tests never read the developer's real settings nor write into the repository: constants, default arguments and the open log handler) |
| `test_smoke.py`          | Import smoke for all modules, core headless purity (no tkinter leak) + tkinter root construction (skipped when headless) + network-block gate self-check + static guard on thread creation rules (no ThreadPoolExecutor, daemon=True) |
| `test_docs_consistency.py` | Docs vs code consistency (section-level module/test/dependency enumeration)     |
| `test_env_consistency.py` | Runtime environment vs requirements.txt pins (all lines pinned, installed versions match) |
| `test_repo_hygiene.py`   | Guard against files that must never be tracked (OneDrive sync-conflict copies, non-publishable classes, runtime logs, oversized files). Shares one decision path with `.git/hooks/pre-commit`, so commit time and CI enforce the same rule |
| `test_claude_hooks.py`   | Local dev hook (`.claude/`) issue-ledger parsing: state annotations, ID 000, archive placement, and done-item evidence (commit refs). **Skipped in CI** because the target is git-ignored (local pytest only) |

---

## Known Limitations

### Accuracy

- DEM horizontal resolution (5–10 m) is the hard ceiling for accuracy; individual building obstructions are not modeled
- The Deygout method is an approximation; errors of ±5–15 dB relative to measurements are expected
- The vegetation model is empirical; species, density, and seasonal variation are not accounted for
- Environmental loss coefficients are empirical; suitability for specific regions is not guaranteed

### Data Coverage

- **DEM coverage is Japan only.** GSI tiles do not cover areas outside Japan; coordinates outside Japan will return elevation 0 m
- `dem5a_png` / `dem5b_png` (5 m) do not cover the entire country; missing areas fall back to `dem_png` (10 m)
- Ocean, lakes, and missing data areas are treated as elevation 0 m

### Path Length

- Paths up to 20 km are recommended for screening purposes
- Longer distances exceed the practical accuracy limits of this tool

### Operation

- Parameters cannot be changed while the graph window is open; close it first, then re-run
- The terrain cache is cleared on restart; the disk cache persists across sessions
