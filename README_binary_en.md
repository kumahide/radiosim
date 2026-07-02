# RadioSim Pro 2.3

![RadioSim Pro](logo.png)

A desktop simulator for screening radio link propagation characteristics before field surveys.
Automatically retrieves DEM (Digital Elevation Model) data from the Geospatial Information Authority of Japan (GSI) and visualizes terrain profiles, diffraction loss, vegetation attenuation, and link budgets in real time.

---

## Table of Contents

1. [Overview](#overview)
2. [Requirements](#requirements)
3. [Installation &amp; Launch](#installation--launch)
4. [UI Settings](#ui-settings)
5. [Usage — Single Mode](#usage--single-mode)
6. [Usage — Batch Mode](#usage--batch-mode)
7. [Input Parameters](#input-parameters)
8. [Calculation Models](#calculation-models)
9. [DEM Retrieval Logic](#dem-retrieval-logic)
10. [Save Package](#save-package)
11. [Uninstall](#uninstall)
12. [Known Limitations](#known-limitations)
13. [Copyright](#copyright)

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
- Map Window — pick coordinates by clicking the map / continuously add paths from the map into Batch Mode / visualize, prefetch, and delete DEM cache
- Automatic path map in HTML reports (TX/RX, path, and distance overlaid on a map)
- Save results as a package (PNG / CSV / JSON / HTML / KML)
- Japanese / English UI — switchable from the menu bar
- System-aware dark mode (Light / Dark / System auto)

### Accuracy Statement

The horizontal resolution of the DEM is 5–10 m, giving a practical accuracy of **±5–15 dB** for diffraction loss.
This tool is intended solely for screening purposes — determining whether a field survey is necessary — and must not be used as the basis for final link design decisions.

---

## Requirements

| Item     | Requirement                                                   |
| -------- | ------------------------------------------------------------- |
| OS       | Windows 10 / 11 (64-bit)                                      |
| Internet | Required for DEM retrieval (fetched tiles are cached locally) |
| Python   | Not required (bundled in the binary)                          |

---

## Installation & Launch

### Installation

1. Extract the distribution ZIP file to any folder.
2. Place the extracted folder wherever you like. The app can be moved freely.

> **Note**: Do not modify the folder structure. `RadioSimPro.exe` cannot run as a standalone file.

### Launch

Double-click `RadioSimPro.exe`.

On first launch, the following directories and files are created automatically in the same folder as the exe:

| Path                   | Contents                                            |
| ---------------------- | --------------------------------------------------- |
| `terrain_cache/`     | Disk cache for DEM tiles (persists across sessions) |
| `results/`           | Output destination for saved packages               |
| `radiosim_conf.json` | UI settings and last-used input values              |

---

## UI Settings

The menu bar provides the following options. Settings are saved to `radiosim_conf.json`.

| Menu                 | Item                  | Description                                                       |
| -------------------- | --------------------- | ----------------------------------------------------------------- |
| Settings > Theme     | System / Light / Dark | Window color theme                                                |
| Settings > Language  | English / 日本語      | UI language (requires restart)                                    |
| Settings > Proxy     | URL entry             | Explicit HTTP proxy URL (blank = use OS proxy settings)           |
| Settings > Load App Settings | —             | Imports only theme/language/proxy from a settings file (leaves simulation parameters unchanged)|
| Settings > Delete All Cache | —              | Deletes all downloaded DEM/map tiles (with confirmation)          |
| Help > Open README   | —                    | Opens this document in a browser                                  |

> The **Map Window** is opened from the **"Map Window" button** at the bottom of the launcher (not the menu) — see below.

### Proxy Settings

If DEM tile retrieval requires an HTTP proxy (e.g. on a corporate network), open **Settings > Proxy Settings** and enter the proxy URL (e.g. `http://proxy.example.com:8080`). Changes take effect immediately. Leave blank and click OK to revert to OS proxy settings.

### Map Window

The **"Map Window" button** at the bottom of the launcher opens an auxiliary window over the GSI pale map. The map is a single app-wide instance (owned by the launcher), and a **mode selector** at the top switches between three modes. The core simulation works without ever opening the map; the Map Window is a convenience layer.

> On opening, it auto-zooms and centers to fit the path length of the currently set TX/RX.

#### Pick Coordinates mode (default)

Click the map to set **TX → RX** alternately; the picked points are written back to the launcher's start/end coordinate fields (the numeric fields are always the source of truth). Click again at any time to re-place a point.

- Shows UISP-style markers (TX filled / RX hollow), a path line, and a distance label at the midpoint.
- Dragging pans the map (coordinates update only on a committed click).

#### Continuous Add mode

A mode for stacking paths into Batch Mode straight from the map. Selecting it opens (and raises) the Batch Mode window; every time you place a **TX → RX** pair on the map, one row is appended to the batch and the map auto-resets for the next entry (no need to press "+ Add row" in the batch).

- Each row's RF settings (frequency, antenna gains, antenna heights) are **frozen from the launcher values at the moment of adding**. The workflow is to fix your conditions in the launcher first, then stack paths.
- All paths in the batch are drawn on the map. Committed paths use **TX = filled dot / RX = bearing arrowhead** (pointing along TX → RX) plus a distance label, so TX and RX stay distinguishable even when close together or at the same coordinates.
- Row changes on the batch side (delete, clear all, CSV import, add, duplicate, committing a coordinate-cell edit) are reflected on the map in real time.
- Closing the Batch Mode window returns the map to Pick Coordinates mode.

#### Cache Management mode

Review the DEM tile cache and prefetch or delete tiles for any area — intended for downloading what you need for offline use before heading to a site with poor connectivity. Normal simulations already cache the tiles around each path automatically, so **you do not need to open this for everyday use**.

**Coverage display (automatic)** — As you pan or zoom, cached areas are continuously shaded. The color reflects the highest accuracy already cached.

| Color  | Accuracy                        |
| ------ | ------------------------------- |
| Green  | 5 m mesh (from airborne LiDAR)  |
| Yellow | 5 m mesh (from photogrammetry)  |
| Cyan   | 10 m mesh                       |

Unshaded areas are not yet cached.

**Controls (mouse gestures)**

| Gesture                       | Action                                  |
| ----------------------------- | --------------------------------------- |
| Drag                          | Pan the map                             |
| Ctrl + drag                   | Select an area and download             |
| Ctrl + Alt + drag             | Force re-download an area (re-fetch all)|
| Shift + Ctrl + drag           | Delete the cache for an area            |

Downloads and deletions show a confirmation dialog with the estimated number of areas and size. Progress and results appear in the status bar. Use **Settings > Delete All Cache** to clear the entire cache.

> **Be considerate of the tile server**: Tiles are fetched from GSI's public servers. Tiles already cached are never re-downloaded. Use force re-download over wide areas only when necessary.

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

### Design — refine in Single, commit in Batch

**Single (the launcher) is where you refine conditions; Batch is where you turn committed conditions into deliverables.** The launcher is the single source of truth, and each batch row is a **committed link frozen by copying the launcher fields at the moment the row was added**.

### Input Methods

**Manual entry**: Type IDs, coordinates, antenna heights, frequencies, and TX/RX gains directly into the table. Rows can be added, deleted, reordered by drag and drop, and edited cell by cell.

- **+ Add row**: Adds a row that freezes a copy of the current launcher fields (coordinates, frequency, gains, antenna heights).
- **Right-click a row**: Opens a per-row menu.
  - **→ Send to Single**: Loads that row's coordinates + RF into the launcher for adjustment.
  - **⟳ Update RF from Single**: Writes the launcher's current RF back into that row (**coordinates are kept**).
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
- `freq` / `gain_tx` / `gain_rx` fall back to the Common Settings values when omitted (they are **per-link identifying attributes** that may differ per path). Env type, rain rate, and diffraction model are set globally in Common Settings
- Legacy CSVs without `gain_tx` / `gain_rx` columns still load (backward compatible; gains inherit Common Settings)

### Common Settings (a snapshot of the launcher)

The **Common Settings** panel at the top defines default values used whenever a per-path override is not specified. It is **read-only**, shown as a snapshot of the launcher (the source of truth). Use the **↻ Update from launcher** button to pull in the launcher's current values.

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

Layers are tried in order: `dem5a_png` → `dem5b_png` → `dem_png`. If a higher-priority layer returns 404 or a missing-data pixel, the next layer is used.

### Caching Strategy

- **Tile prefetch**: At simulation start, all tiles within the TX/RX bounding box are pre-downloaded to the disk cache (supports offline use and speeds up batch processing)
- **Disk cache**: Tiles saved to `terrain_cache/`, persists across sessions
- **Terrain cache**: If TX/RX coordinates and sample count match a previous run, DEM retrieval is skipped entirely (cleared on app restart)

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

> **Path map**: `report.html` embeds a static map with TX/RX, the path, and the distance overlaid on the GSI pale map. It auto-rotates so the path is horizontal (TX left / RX right) with a north arrow. Where map tiles cannot be fetched, the map is omitted with a short note and the report is still produced.

### Batch Mode

Saves to `results/batch_YYYYMMDD_HHMMSS/`:

| File             | Contents                                         |
| ---------------- | ------------------------------------------------ |
| `summary.html` | All-path summary with thumbnails                 |
| `summary.csv`  | Numerical results for all paths                  |
| `summary.kml`  | Google Earth KML for all paths                   |
| `{id}/`        | Per-path package (same structure as Single Mode) |

---

## Uninstall

1. Confirm the app is not running.
2. Delete the entire extracted folder.

The app writes no data to the registry or AppData. Deleting the folder is a complete uninstall.

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

---

## Copyright

© 2026 BearValley Corp. All rights reserved.
