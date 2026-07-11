# RadioSim Pro 2.4

A desktop simulator for screening radio link propagation characteristics before field surveys.
Automatically retrieves DEM (Digital Elevation Model) data from the Geospatial Information Authority of Japan (GSI) and visualizes terrain profiles, diffraction loss, vegetation attenuation, and link budgets in real time.

---

## Table of Contents

1. [Overview](#overview)
2. [Building the Windows Binary](#building-the-windows-binary)
3. [Requirements](#requirements)
4. [File Structure](#file-structure)
5. [Installation &amp; Launch (from source)](#installation--launch-from-source)
6. [Usage — Single Mode](#usage--single-mode)
7. [Usage — Batch Mode](#usage--batch-mode)
8. [Input Parameters](#input-parameters)
9. [Calculation Models](#calculation-models)
10. [DEM Retrieval Logic](#dem-retrieval-logic)
11. [Save Package](#save-package)
12. [Architecture](#architecture)
13. [Testing](#testing)
14. [Known Limitations](#known-limitations)

---

## Overview

RadioSim Pro is a tool designed specifically for **pre-survey screening** in radio link design.
Enter the coordinates, antenna heights, and radio settings for the TX (transmitter) and RX (receiver) stations, and the tool automatically retrieves GSI elevation data, draws a terrain cross-section, and determines link budget viability within seconds.

### Key Features

- Automatic terrain profile generation from GSI DEM PNG tiles (5 m / 10 m mesh)
- Earth curvature correction (standard atmosphere K = 4/3, fixed)
- Diffraction loss calculation using Deygout / Fresnel-Kirchhoff methods
- Vegetation attenuation (LoS intrusion depth model)
- Environmental loss (4 categories: Urban / Suburban / Rural / LoS)
- Rain attenuation (ITU-R P.838-3) and gaseous attenuation (ITU-R P.676-13 Annex 2)
- Real-time antenna height and rain rate sliders in the graph window
- Batch Mode — process multiple paths from a CSV file
- Map Window — pick coordinates by clicking the map / visualize, prefetch, and delete DEM cache
- Automatic path map in HTML reports (TX/RX, path, and distance overlaid on a map)
- **A4 reports (v2)**: per-path / summary as a single print-ready A4 page (`@page A4` + Ctrl+P for zero-dependency PDF; self-identifying header/footer)
- **Antenna initial aim (AZ/EL)**: true azimuth/elevation to the far end, shown for both ends in per-path reports (geometry from existing data = initial values)
- **All-paths overview map** in the summary report (color-coded by verdict)
- **Project info (name + free note)**: entered in the launcher and inherited by both Single and Batch reports
- Save results as a package (PNG / CSV / JSON / HTML / KML)
- Japanese / English UI — switchable from the menu bar
- System-aware dark mode (Light / Dark / System auto)

### Accuracy Statement

The horizontal resolution of the DEM is 5–10 m, giving a practical accuracy of **±5–15 dB** for diffraction loss.
This tool is intended solely for screening purposes — determining whether a field survey is necessary — and must not be used as the basis for final link design decisions.

---

## Building the Windows Binary

Uses PyInstaller to produce a self-contained EXE folder (onedir mode) that requires no Python installation on the target machine.

### Prerequisites

- Python 3.11 or later must be on `PATH` (developed and CI-tested on 3.14)
- PyInstaller and all dependencies are installed automatically by `build.bat`

### Build Steps

```bat
build.bat
```

`build.bat` performs the following steps automatically:

| Step | Action |
| --- | --- |
| 1 | Verify Python / PyInstaller are available; install if missing |
| 2 | Install pinned dependencies (`pip install -r requirements.txt`) |
| 3 | Remove old build artifacts (`build/RadioSimPro/` and `dist/RadioSimPro/`) |
| 4 | Run `python -m PyInstaller radiosim.spec --noconfirm` |
| 5 | Create `terrain_cache/` and `results/` in the output folder |

### Output

```
dist/
└── RadioSimPro/
    ├── RadioSimPro.exe   ← launch this
    ├── _internal/        ← Python runtime and dependencies
    ├── terrain_cache/
    └── results/
```

### Creating a Distribution Package

ZIP the entire `dist/RadioSimPro/` folder:

```bat
powershell Compress-Archive -Path dist\RadioSimPro -DestinationPath dist\RadioSimPro.zip -Force
```

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
pip install numpy matplotlib requests Pillow sv-ttk darkdetect markdown truststore tkintermapview
```

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
├── report.py             # Batch result output generation (PNG/HTML/KML, summaries; headless)
├── report_map.py         # Headless path-overlay map generation for reports
├── map_graphics.py       # Pure-PIL map overlay drawing (shared by UI and reports)
├── coords.py             # Coordinate notation conversion (DD <-> DMS, pure functions)
├── mpl_fonts.py          # matplotlib Japanese font application (headless; shared by graph/report)
├── i18n.py               # Multilingual string table
├── version.py            # Version information
├── views/
│   ├── launcher.py       # Launcher window
│   ├── graph.py          # Graph window (matplotlib + tkinter)
│   ├── map_window.py     # Map window (Pick Coordinates / Append to Batch / Cache Management modes)
│   ├── dialogs.py        # Shared modal dialogs centered on the parent window
│   └── batch_builder.py  # Batch Mode window
├── README_ja.md          # Japanese README
├── README_en.md          # This file
└── tests/
    ├── test_models.py
    ├── test_simulation.py
    ├── test_config.py
    ├── test_dem.py
    ├── test_batch.py
    ├── test_report.py
    ├── test_report_map.py
    ├── test_map_window.py
    ├── test_coords.py
    ├── test_mpl_fonts.py
    ├── test_smoke.py
    ├── test_docs_consistency.py
    └── test_env_consistency.py
```

---

## Installation & Launch (from source)

```bash
# Install dependencies
pip install numpy matplotlib requests Pillow sv-ttk darkdetect markdown truststore tkintermapview

# Launch
cd radiosim
python main.py
```

The following directories are created automatically in the project root on first launch:

| Directory          | Contents                              |
| ------------------ | ------------------------------------- |
| `terrain_cache/` | Disk cache for DEM tiles              |
| `results/`       | Output destination for saved packages |

### UI Settings

The menu bar provides the following options. Settings are saved to `radiosim_conf.json`.

| Menu                    | Item                  | Description                                                              |
| ----------------------- | --------------------- | ------------------------------------------------------------------------ |
| Settings > Theme        | System / Light / Dark | Window color theme                                                       |
| Settings > Language     | English / 日本語      | UI language (requires restart)                                           |
| Settings > Proxy        | URL entry             | Explicit HTTP proxy URL (leave blank to use OS proxy settings)           |
| Settings > Load App Settings | —               | Imports only app settings (theme/language/proxy) from a settings file   |
| Settings > Delete All Cache | —                | Deletes all downloaded DEM/map tiles (with confirmation)                 |
| Help > Open README      | —                    | Opens this document in a browser                                         |

The Map Window is opened from the **"Map Window" button** at the bottom of the launcher (not the menu).

#### Proxy Settings

If DEM tile retrieval requires an HTTP proxy (e.g. on a corporate network), open **Settings > Proxy Settings** and enter the proxy URL:

```
http://proxy.example.com:8080
```

- Changes take effect immediately — no restart required
- Leaving the field blank and clicking OK reverts to OS proxy settings (system settings / environment variables)
- `truststore` integration with the Windows certificate store is also active to handle corporate SSL inspection

#### Map Window

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

#### Diffraction Model Button

Toggles between Deygout (multiple diffraction) and Single (single obstacle). Deygout is the default (more conservative and realistic).

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
| K-Factor          | 0                              | 30      | —     |
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
| `settings.json`       | Complete input parameters (reloadable via Load Settings) |
| `terrain_profile.csv` | Terrain profile data                                     |
| `report.txt`          | Text-format link budget report                           |

### Batch Mode

Saves to `results/batch_YYYYMMDD_HHMMSS/`:

| File             | Contents                                         |
| ---------------- | ------------------------------------------------ |
| `summary.html` | All-path summary with thumbnails                 |
| `summary.csv`  | Numerical results for all paths                  |
| `summary.kml`  | Google Earth KML for all paths                   |
| `{id}/`        | Per-path package (same structure as Single Mode) |

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
  -> Has side effects. Delegates calculation and I/O downward.

          |
          v

[Orchestrator layer]
  simulation.py   DEM fetch management, terrain cache, calculation calls
  batch.py        CSV I/O, validation, batch execution engine
  report.py       Batch result output generation (PNG/HTML/KML, summaries; headless)
  report_map.py   Headless path-overlay map generation (tile fetch + compositing)

          |
          +---> [Pure calc. layer]  models.py
          |     Propagation calc. (no side effects)
          |
          +---> [Pure rendering layer]  map_graphics.py
          |     PIL drawing of markers/distance/north arrow (shared by UI and reports)
          |
          +---> [Pure conversion layer]  coords.py
          |     Coordinate notation conversion (DD <-> DMS, no side effects)
          |
          +---> [Config & validation layer]  config.py
          |     App config I/O, input validation, logging
          |
          +---> [External dependency layer]  dem.py
                DEM/pale tile HTTP fetch, elevation decode, cache, proxy
```

---

## Testing

```bash
python -m pytest tests/ -v
python -m pytest tests/ --cov
```

### Test Suite (429 tests)

| File                       | Count | Coverage                                                                        |
| -------------------------- | ----- | ------------------------------------------------------------------------------- |
| `test_models.py`         | 82    | Terrain profile, diffraction, vegetation, rain, gas, link budget                |
| `test_simulation.py`     | 38    | DEM fetch (parallel, cache, error handling), calculation, save (report coords)  |
| `test_config.py`         | 36    | Input validation, config I/O (app/sim split), i18n key coverage                 |
| `test_dem.py`            | 64    | DEM decoding, tile fetch/prefetch, proxy/session, cache deletion/stats, coverage outline |
| `test_batch.py`          | 78    | CSV parse, validation, _make_params, execution engine (run_batch/_process_one/_fetch_sync), HTML coords |
| `test_report.py`         | 20    | KML generation (per-path/summary, lon-lat order, obstruction, XML escaping), PNG/HTML smoke |
| `test_report_map.py`     | 25    | Report path-overlay map generation (zoom fit, tile stitch, rotation, crop)      |
| `test_map_window.py`     | 4     | Map window safe teardown (after-loop stop invariants)                           |
| `test_coords.py`         | 24    | Coordinate conversion (DD/DMS parse, format, roundtrip, hemisphere sign, errors)|
| `test_mpl_fonts.py`      | 4     | matplotlib Japanese font application (language-aware, priority, no-font fallback)|
| `test_smoke.py`          | 19    | Import smoke for all modules, core headless purity (no tkinter leak) + tkinter root construction (skipped when headless) |
| `test_docs_consistency.py` | 9   | Docs vs code consistency (section-level module/test/dependency enumeration)     |
| `test_env_consistency.py` | 10   | Runtime environment vs requirements.txt pins (all lines pinned, installed versions match) |

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
