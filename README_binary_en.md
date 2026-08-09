# RadioSim Pro 2.7

![RadioSim Pro](logo.png)

A desktop simulator for screening radio link propagation characteristics before field surveys.
Automatically retrieves DEM (Digital Elevation Model) data from the Geospatial Information Authority of Japan (GSI) and visualizes terrain profiles, diffraction loss, vegetation attenuation, and link budgets in real time.

---

## Table of Contents

1. [Overview](#overview)
2. [Requirements](#requirements)
3. [Installation &amp; Launch](#installation--launch)
4. [Menus](#menus)
5. [Map](#map)
6. [Usage — Single Mode](#usage--single-mode)
7. [Usage — Multiple Paths](#usage--multiple-paths)
8. [Usage — Condition Explorer](#usage--condition-explorer-compare--sweep)
9. [Usage — Relay Route](#usage--relay-route)
10. [Project Files (.rsproj)](#project-files-rsproj)
11. [Input Parameters](#input-parameters)
12. [Calculation Models](#calculation-models)
13. [DEM Retrieval Logic](#dem-retrieval-logic)
14. [Save Package](#save-package)
15. [Uninstall](#uninstall)
16. [Known Limitations](#known-limitations)
17. [Copyright](#copyright)

---

## Overview

RadioSim Pro is a tool designed specifically for **pre-survey screening** in radio link design.
Enter the coordinates, antenna heights, and radio settings for the TX (transmitter) and RX (receiver) stations, and the tool automatically retrieves GSI elevation data, draws a terrain cross-section, and determines link budget viability within seconds.

### Key Features

#### Workflows — four, by what you want to find out

| Workflow | What it answers |
| --- | --- |
| **Single Mode** | Does this one link close? Watch the terrain profile while you move antenna heights and rain rate on the spot |
| **Multiple Paths** | Many links at once, with CSV in and out |
| **Condition Explorer (compare / sweep)** | **Digs into one path.** Line up results under different conditions (base + up to 5), or sweep one axis over N points to see where the link starts to close — chart plus table. Terrain is fetched once |
| **Relay Route** | Bridges a link that will not close directly. Up to 7 relay points, a link budget per section, and **the overall verdict decided by the tightest section** (regenerative relay — each relay receives and transmits again) |

#### Map

- **Map** (3 modes): pick coordinates by clicking the map / continuously add paths from the map into Multiple Paths / visualize, prefetch, and delete DEM cache

#### Calculation models

- Automatic terrain profile generation from GSI DEM PNG tiles (5 m / 10 m mesh)
- Earth curvature correction (standard atmosphere K = 4/3, fixed)
- Diffraction loss calculation using Deygout / Fresnel-Kirchhoff methods
- Vegetation attenuation (LoS intrusion depth model)
- Environmental loss (4 categories: Urban / Suburban / Rural / LoS)
- Rain attenuation (ITU-R P.838-3) and gaseous attenuation (ITU-R P.676-13 Annex 2)

#### Output and reports

- **A4 reports (v2)**: per-path / summary rendered as a single print-ready A4 page. Export to PDF straight from the browser with Ctrl+P (no extra software). Self-identifying header/footer carrying the project name, timestamp, and ID
- **Antenna initial aim (AZ/EL)**: true azimuth and elevation to point at the far end, shown for both ends in per-path reports (initial values; do the final tuning on-site by maximizing RSSI)
- **Automatic path map** in HTML reports (TX/RX, path, and distance overlaid on a map)
- **All-paths overview map** in the summary report (color-coded by verdict)
- **Print the whole batch at once**: `report_all.html` concatenates the summary and every path into one document — a single Ctrl+P produces the PDF for all pages
- Save results as a package (PNG / CSV / JSON / HTML / KML)

#### Reusing your input

- **Project files (`.rsproj`)**: bundle coordinates, all parameters, project info, batch rows, explorer conditions and the relay path into **one file** and pick the work up later
- **Project info (name + free note)**: entered in the launcher and inherited by both Single and Multiple Paths reports

#### Interface

- Real-time antenna height and rain rate sliders in the graph window
- Switchable coordinate notation (Decimal Degrees / Degrees Minutes Seconds)
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

> **You do not need to install Python.** Everything required to run the app is bundled.

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

## Menus

The menu bar has three menus — **File / Settings / Help**. Every item is listed below.

### File

Operations that reach out to files or to the OS.

| Item                | Description                                                                                     |
| ------------------- | ------------------------------------------------------------------------------------------------- |
| Open Project...     | Loads a saved `.rsproj` and restores the whole input set → [Project Files (.rsproj)](#project-files-rsproj) |
| Save Project As...  | Writes the current input set to a `.rsproj` → [Project Files (.rsproj)](#project-files-rsproj)   |
| Load Parameters...  | Imports **simulation parameters only** from a settings file into the input form                 |
| Open Results Folder | Opens the `results/` folder in Explorer                                                         |

### Settings

Your choices are saved to `radiosim_conf.json` and persist across restarts.

| Item               | Options                          | Description                                                        |
| ------------------ | -------------------------------- | -------------------------------------------------------------------- |
| Theme              | System / Light / Dark            | Window color theme                                                   |
| Language           | English / 日本語                 | UI language (requires restart)                                       |
| Coordinate Display       | Decimal Degrees (DD) / Degrees Minutes Seconds (DMS) | How coordinates are **displayed** → see the note below |
| Proxy Settings...  | URL entry                        | Explicit HTTP proxy URL (blank = use OS proxy settings) → see below  |
| Load App Settings... | —                              | Imports **only** theme, language and proxy from a settings file      |
| Delete All Cache... | —                               | Deletes all downloaded DEM / map tiles (with confirmation)           |

### Help

| Item         | Description                          |
| ------------ | -------------------------------------- |
| Open README  | Opens this document in a browser       |
| About        | Shows the installed version            |

> **"Load Parameters" vs "Load App Settings"** — the former imports **simulation parameters** (coordinates, frequency, antenna heights, …); the latter imports **how the app looks and connects** (theme, language, proxy). **Neither touches the other's territory**, so opening a file you received from someone else will never silently change your display language or network settings.

> **The Map is not in the menus** — open it from the **"Map" button** at the bottom of the launcher (→ [Map](#map)).

### Coordinate Format (DD / DMS)

This selects **how coordinates are displayed**. It is not an input mode switch.

- **Input fields accept either notation.** With DMS selected you can still type `34.8, 132.6` (DD), and with DD selected you can still type `34°48'00.0"N, 132°36'00.0"E`. Everything is normalised to decimal degrees before the calculation runs.
- **Fields are reformatted into the selected notation when you switch the format, or when settings/a project are loaded.** Committing an entry does not reformat it, so **what you just typed stays in the form you typed it** — this is not a fault.

### Proxy Settings

If DEM tile retrieval requires an HTTP proxy (e.g. on a corporate network), open **Settings > Proxy Settings** and enter the proxy URL (e.g. `http://proxy.example.com:8080`). Changes take effect immediately (no restart needed). Leave blank and click OK to revert to OS proxy settings.

> If no proxy is configured and the elevation server is completely unreachable, the run **aborts after a dozen seconds or so** and a dialog asks you to check the proxy settings — it will not quietly finish and hand you flat terrain.

---

## Map

![地図上に送信点・受信点と経路が表示された画面。地図をクリックして座標を拾える](docs/images/shot_map.png)

The **"Map" button** at the bottom of the launcher opens an auxiliary window over the GSI pale map. The map is a single app-wide instance (owned by the launcher), and a **mode selector** at the top switches between three modes. The core simulation works without ever opening the map; the map is a convenience layer.

> On opening, it auto-zooms and centers to fit the path length of the currently set TX/RX.

### Pick Coordinates mode (default)

Click the map to set **TX → RX** alternately; the picked points are written back to the launcher's start/end coordinate fields (the numeric fields are always the source of truth). Click again at any time to re-place a point.

- Shows UISP-style markers (TX filled / RX hollow), a path line, and a distance label at the midpoint.
- Dragging pans the map (coordinates update only on a committed click).

### Continuous Add mode

A mode for stacking paths into Multiple Paths straight from the map. Selecting the **Append to Multiple Paths** mode opens (and raises) the Multiple Paths window; every time you place a **TX → RX** pair on the map, one row is appended to the batch and the map auto-resets for the next entry (no need to press "+ Add row" in the batch).

- Each row's RF settings (frequency, antenna gains, antenna heights) are **frozen from the launcher values at the moment of adding**. The workflow is to fix your conditions in the launcher first, then stack paths.
- All paths in the batch are drawn on the map. Committed paths use **TX = filled dot / RX = bearing arrowhead** (pointing along TX → RX) plus a distance label, so TX and RX stay distinguishable even when close together or at the same coordinates.
- Row changes on the batch side (delete, clear all, CSV import, add, duplicate, committing a coordinate-cell edit) are reflected on the map in real time.
- Closing the Multiple Paths window returns the map to Pick Coordinates mode.

### Cache Management mode

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

![地形断面グラフ。送受信点を結ぶ見通し線とフレネル第 1 ゾーンが地形に重ねて描かれ、遮蔽区間と受信レベル・マージンが表示されている](docs/images/shot_profile.png)

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

#### Project Info (optional)

| Field   | Description                                                             |
| ------- | ---------------------------------------------------------------------- |
| Project | Project name shown in the report header (applies to both Single and Multiple Paths reports) |
| Note    | Free note. Shown on the **Single Mode report and the Multiple Paths summary page** (`summary.html` / `report_all.html`). **Not shown on per-path reports** — it describes the survey as a whole, so it is not repeated per path |

> Project Info is entered in the **launcher, which is the single source of truth**. Both Single Mode's saved report and Multiple Paths inherit these values. In Multiple Paths they are shown read-only (🔒) and pulled in with **"↻ Refresh from launcher"**.

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

### 4. Automatic Saving of Input Values

Each time you run a Single Mode simulation, the input values are saved to `radiosim_conf.json` and restored on the next launch. **No explicit save action is needed.**

To pull past conditions in from a file, or to carry a whole input set around, use the [File menu](#file) (**Load Parameters** / **Save Project As** / **Open Project**).

---

## Usage — Multiple Paths

![複数経路の入力表。1 行 1 経路で、実行後の判定（OK / NG）と水平距離が各行に返っている](docs/images/shot_batch.png)

Click the **Multiple Paths** button in the launcher to open the dedicated window.

### Design — refine in Single, commit in Multiple Paths

**Single (the launcher) is where you refine conditions; Multiple Paths is where you turn committed conditions into deliverables.** The launcher is the single source of truth, and each batch row is a **committed link frozen by copying the launcher fields at the moment the row was added**.

### Input Methods

**Manual entry**: Type IDs, coordinates, antenna heights, frequencies, and TX/RX gains directly into the table. Rows can be added, deleted, reordered by drag and drop, and edited cell by cell.

The **Dist (m)** column on the right is read-only and computed from the TX/RX coordinates (updated whenever a coordinate is committed). A mistyped coordinate shows up as an absurd distance, so it can be caught before running.

- **+ Add row**: Adds a row that freezes a copy of the current launcher fields (coordinates, frequency, gains, antenna heights).
- **Pick from map**: Opens the map in "Append to Multiple Paths" mode; every **TX → RX** pair you place adds one row (opening the map from this window is new in 2.7 — previously the flow could only be started from the map side).
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
- Column names are **case-insensitive and ignore surrounding spaces** (`ID,Start,…` loads fine). `id` values must be unique **case-insensitively** (`p01` and `P01` would map to the same output folder, so they are rejected as duplicates)

### Common Settings (a snapshot of the launcher)

The **Common Settings** panel at the top defines default values used whenever a per-path override is not specified. It is **read-only**, shown as a snapshot of the launcher (the source of truth). Use the **↻ Update from launcher** button to pull in the launcher's current values.

### Running and Results

Click **▶ Run** to process paths sequentially. OK / NG / ERR counts update in real time.

On completion, the following are saved to `results/batch_YYYYMMDD_HHMMSS/`:

| File                         | Contents                                                         |
| ---------------------------- | ---------------------------------------------------------------- |
| `summary.html`             | Summary report for all paths (with graph thumbnails)             |
| `summary.csv`              | Numerical results for all paths (spreadsheet-compatible; distances in metres) |
| `summary.kml`              | Google Earth KML with OK / NG / Error color coding               |
| `report_all.html`          | Summary + every path in one document (Ctrl+P prints them all)    |
| `{id}/report.html`         | Per-path detailed report (terrain graph + path map embedded)     |
| `{id}/profile.png`         | Terrain cross-section graph                                      |
| `{id}/path.kml`            | 3D KML with terrain, LoS, Fresnel zone, and obstruction segments |
| `{id}/settings.json`       | Per-path input parameters                                        |
| `{id}/terrain_profile.csv` | Terrain profile data                                             |
| `{id}/report.txt`          | Text-format link budget report                                   |

---

## Usage — Condition Explorer (Compare / Sweep)

![条件探索の比較画面。1 本の経路に対し周波数と利得を変えた 4 条件の受信レベルとマージンが並び、最後の条件だけ NG になっている](docs/images/shot_scenario.png)

A screen for **taking one fixed path and digging into it under different conditions**. Open it with the "Condition Explorer" button in the launcher. The path (coordinates) and sample count are fixed to the launcher values, so **terrain is fetched only once** and the N conditions are then computed on top of it (repeat runs on the same path do not re-fetch DEM).

The toggle at the top switches between two modes. After changing coordinates or parameters in the launcher, press **"↻ From Launcher"** at the top right to pull them in (**until you do, the run uses the values shown on screen** — what you see is what is computed).

### Compare (base + N conditions)

- The leftmost **Base** column is the launcher's current values (read-only) — the reference never moves.
- In columns **Cond 1 ... Cond 5**, change only the fields you care about (they start as copies of the base). "+ Condition" adds up to 5 columns.
- You can vary frequency, TX power, TX/RX antenna gain, RX sensitivity, TX/RX antenna height, vegetation height, rain rate, environment and diffraction model. **Coordinates and sample count cannot be varied** (comparing different paths is what Multiple Paths is for).
- The report is a difference table with **the delta from the base shown inside each cell** (e.g. `-76.44 (+13.56)`). Rows that differ are tinted.

### Sweep (one axis, N points)

- Pick an **axis** (antenna height, frequency, rain rate, ...), a range, and **Points** (2-41).
- The range can be entered two ways: **From / To** (absolute values) or **Base ±** (swing around the current value). **Base ±** matches how the question is usually asked — "what happens within ±10 m of the current 30 m?". Switching between them keeps the span, and the range is always normalised to From / To internally (that is also what a project file stores).
- The report contains **a line chart and a numeric table**. The chart draws the verdict threshold (0 dB margin) and colours each point by verdict (OK = green / NG = orange); the colour change is where the link starts to close. Even at the maximum 41 points the report stays on **a single A4 page** — the table stays a single column (rows are simply tightened), so the progression can be read straight down.
- When the margin spans a huge range (deeply obstructed paths), the vertical axis switches to a log-like scale so the region around the threshold stays readable.

### Running and results

**Run** proceeds through terrain fetch -> condition calculation -> report generation, with the progress bar covering all three. On completion a dialog reports the output directory and offers to open the report (same flow as Single and Multiple Paths). To look at it later, use "Open Results" in the launcher.

The following are saved to `results/scenario_YYYYMMDD_HHMMSS/`:

| File              | Contents                                                          |
| ----------------- | ----------------------------------------------------------------- |
| `scenario.html` | Single-page A4 report (compare = difference table / sweep = chart + table) |
| `scenario.csv`  | Numbers for every condition / point (spreadsheet-compatible)      |

---

## Usage — Relay Route

![中継経路の画面。送信点・中継点・受信点の 3 地点と、区間ごとの受信レベル・マージン・判定が表に返っている](docs/images/shot_multihop.png)

For **one link carried over relay points**. Open it with the **Relay Route** button in the launcher. Use it when two points cannot see each other directly and a ridge (or another site) in between can carry the traffic.

### Assumption — regenerative relay

Each relay point **receives and then transmits again**. That means:

- Every section gets its own independent link budget — **section losses are never added together**.
- **The overall verdict is decided by the tightest section.** The report shows the overall verdict **and** the per-section breakdown, because "it fails" is not actionable unless you can see which section is short.
- **Passive reflectors are out of scope.** Their losses combine differently and are not part of this tool's calculation.

### Waypoints and sections

- The **waypoint table** is the input surface. **The first point is the transmitter and the last is the receiver**; "Add point" inserts a relay **before the receiver**, so the order on screen is the order of the route. Each row shows its role (TX / relay / RX).
- Each waypoint has **coordinates and an antenna height**. **Relay rows carry an `×` so you can delete any single point** (TX and RX cannot be deleted, so they have no `×`). Deleting a relay also drops the settings of the section leaving that point.
- Coordinates accept **either decimal degrees or degrees/minutes/seconds**; committing an entry reformats it to the notation chosen in Settings > Coordinate Display. **Height belongs to the point, not to the section** — a relay has one antenna, so it must not be possible to enter one height as "section 1 RX" and a different one as "section 2 TX".
- The **section table** lets you override **frequency and TX/RX gain per section** (blank = use the common settings from the launcher), because the two antennas at a relay are often different.
- **Pick from map** switches the map into waypoint mode; each click fills the next waypoint.
- TX and RX start from **the launcher's coordinates as they were when the window opened** (relay points start empty).
- Up to 7 relay points (8 sections).

> **⚠️ Relay points are meant to be placed, not dragged around to explore.** Each section fetches its own terrain data, so moving a point triggers a new download. Use the [Condition Explorer](#usage--condition-explorer-compare--sweep) to explore heights and conditions.

### Running the route

**Run** processes the sections in order, filling the result list section by section (received level, margin, verdict), and finishes with the overall verdict (the tightest section).

The following are saved to `results/multihop_YYYYMMDD_HHMMSS/`:

| File | Contents |
| --- | --- |
| `route.html` | Combined sheet: overall verdict, per-section breakdown, overview map |
| `report_all.html` | Combined sheet + every section in one document (Ctrl+P for a single PDF) |
| `hops.csv` | One row per section (spreadsheet-compatible) |
| `{id}/` | Per-section package (same structure as Single Mode) |

---

## Project Files (.rsproj)

Bundles **the whole input set into one file** so you can pick the work up later. Use **File → Save Project / Open Project**.

### What is included

| Included | Not included |
| --- | --- |
| Coordinates and all parameters (the launcher inputs) | **Results** (those belong to `results/`) |
| Project info (name and free note) | **App settings** (theme, language, proxy) |
| Multiple Paths rows | Window positions, sizes, open/closed state |
| Explorer conditions (compare columns / sweep axis and range) | |
| Relay route waypoints and sections | |

**Leaving out theme, language and proxy is deliberate** — opening a project you received from someone else must not switch your display language or your network settings.

### Saving

- Open windows (batch / explorer / relay path) contribute their **current** values.
- **Closed windows keep their contents too** — the previous values are carried forward, so closing the batch window before saving does not delete your rows.
- Anything that is not a readable number is not written; the dialog tells you which part was skipped (so a broken file is never produced silently).

### Opening

- **Open windows are not closed.** A bar appears at the top of each of them, and the window's contents are replaced **only when you press "Apply to this window"** (nothing changes until you do). Dismiss the bar with `×`.
- The launcher fields and project info are replaced immediately.
- **Contents for closed windows appear when you open them** — this app freezes a window's inputs when it opens, and loading follows the same rule.
- If you close a window without applying, **what you saw on screen wins** (that is what gets written on the next save).
- A file saved by a newer version of RadioSim will not be opened (you are told, rather than having it silently mangled).

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
| `report.html`         | Detailed report (single A4 page) with the terrain graph, a path map, and antenna initial aim AZ/EL embedded |
| `path.kml`            | 3D KML for Google Earth                                  |
| `settings.json`       | Complete input parameters (reloadable via File > Load Parameters) |
| `terrain_profile.csv` | Terrain profile data                                     |
| `report.txt`          | Text-format link budget report                           |

> **A4 reports (v2)**: `report.html` / `summary.html` are rendered as a single print-ready A4 page (`@page A4` / `@media print`). Open in a browser and use **Ctrl+P → "Save as PDF"** to get an A4 PDF with no extra software. Turn **"Headers and footers" off** in the print dialog (the report carries its own self-identifying header/footer with the project name, timestamp, and ID). The project name and free note come from the launcher's Project Info fields.
>
> **Print all at once (`report_all.html`)**: choose **"Open all pages"** in the completion dialog to open it ("Open summary" opens `summary.html` as before). Multiple Paths runs also save `report_all.html`, which concatenates the summary ledger and every per-path report into one document. **Open it and press Ctrl+P to get the PDF for all pages at once** (page 1 = the summary ledger, pages 2+ = one A4 page per path). Clicking a thumbnail in the ledger jumps to that path inside the same document. `summary.html` and `{id}/report.html` are still written separately, so use those when you only need to share one path. The combined file gets large with many paths (each embeds its terrain profile).
>
> **Path map**: `report.html` (single) embeds a static map with TX/RX, the path, and the distance on the GSI pale map; `summary.html` (batch) embeds an **all-paths overview map** (north-up, color-coded by verdict). Where map tiles cannot be fetched, the map is omitted with a short note and the report is still produced.
>
> **Antenna initial aim (AZ/EL)**: the Site Info of `report.html` shows the true azimuth AZ and elevation EL to point at the far end, for both ends (geometry from existing data = initial values; do the final tuning on-site by maximizing RSSI). AZ is a **true** azimuth — to aim with a magnetic compass, correct for local declination (in Japan magnetic north is ~7-9° west of true north, varies by region).

### Multiple Paths

Saves to `results/batch_YYYYMMDD_HHMMSS/`:

| File             | Contents                                         |
| ---------------- | ------------------------------------------------ |
| `summary.html` | All-path summary with thumbnails                 |
| `summary.csv`  | Numerical results for all paths                  |
| `summary.kml`  | Google Earth KML for all paths                   |
| `report_all.html` | Summary + every path in one printable document |
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
- 🔴 **Over rugged terrain (mountain paths) the diffraction loss diverges and the result is unusable** (known defect, fix planned). A broad ridge is counted as many independent knife edges, so diffraction loss comes out at **hundreds to thousands of dB**. ⚠️ **Fresnel-zone blockage is capped at 100% everywhere it is shown** (screen, report, CSV) even though the internal raw value goes far above it, so a diverging path still just reads "100%" — the percentage will not warn you. The verdict will read NG, but **you cannot tell from these numbers whether that NG is correct**. ⇒ **Trust the results only on flat or coastal paths with roughly 25 m of relief or less** (measured). The ±5–15 dB figure above applies within that range
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

© 2026 BearValley AI Craftworks. All rights reserved.

This software is distributed under the **MIT License**, commercial use included. The license text ships as `LICENSE`, next to `RadioSimPro.exe`.
