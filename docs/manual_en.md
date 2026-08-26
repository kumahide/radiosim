# RadioSim Pro 2.9

![RadioSim Pro](../logo.png)

> **Intended reader**: users of the **Windows binary (`RadioSimPro.exe`)**. This is also the document that Help → Open Documentation shows inside the app.
> To run it from source or work on the code, see [developer_en.md](developer_en.md).

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
9. [Usage — Relay Path](#usage--relay-path)
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
| **Relay Path** | Bridges a link that will not close directly. Up to 7 relay points, a link budget per section, and **the overall verdict decided by the tightest section** (regenerative relay — each relay receives and transmits again) |

#### Map

- **Map** (4 modes): pick coordinates by clicking the map / continuously add paths from the map into Multiple Paths / place waypoints for a relay route / visualize, prefetch, and delete DEM cache

#### Calculation models

- Automatic terrain profile generation from GSI DEM PNG tiles (5 m / 10 m mesh)
- Earth curvature correction (standard atmosphere K = 4/3, fixed)
- Diffraction loss calculation using Bullington / Fresnel-Kirchhoff methods
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

The horizontal resolution of the DEM is 5–10 m, giving a practical accuracy of **±5–15 dB** for diffraction loss. ⚠️ **That range does not cover paths where several obstacles overlap** — the combined loss has not been checked against measurements or a reference implementation (described below).
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
| Language           | English / 日本語 (plus your own) | UI language (requires restart) → [Adding your own UI language](#adding-your-own-ui-language) |
| Coordinate Display       | Decimal Degrees (DD) / Degrees Minutes Seconds (DMS) | How coordinates are **displayed** → see the note below |
| Proxy Settings...  | URL entry                        | Explicit HTTP proxy URL (blank = use OS proxy settings) → see below  |
| Load App Settings... | —                              | Imports **only** theme, language and proxy from a settings file      |
| Delete All Cache... | —                               | Deletes all downloaded DEM / map tiles (with confirmation)           |

### Help

| Item         | Description                          |
| ------------ | -------------------------------------- |
| Open Documentation | Opens this document in a browser       |
| About        | Shows the installed version            |

> **"Load Parameters" vs "Load App Settings"** — the former imports **simulation parameters** (coordinates, frequency, antenna heights, …); the latter imports **how the app looks and connects** (theme, language, proxy). **Neither touches the other's territory**, so opening a file you received from someone else will never silently change your display language or network settings.

> **The Map is not in the menus** — open it from the **"Map" button** at the bottom of the launcher (→ [Map](#map)).

### Adding your own UI language

Besides Japanese and English you can **add a translation of your own**. Create a `lang` folder next to the app, put `<language-code>.json` in it, and restart — the language then appears under Settings > Language.

```json
{
  "_name": "Français",
  "btn_run": "Exécuter",
  "menu_help": "Aide"
}
```

- `_name` is **the name shown in the language menu**. Write it in the language's own script — someone looking for their language is reading a screen they cannot read yet. Without it the file's code is shown instead.
- **You do not have to translate everything.** Only the keys you write are replaced; **the rest stay in English**. New keys added in later versions will not break your file.
- The key list lives in the source file `core/i18n.py` (<https://github.com/kumahide/radiosim>). ⚠️ **It is not part of the binary (exe) distribution** — that file is not bundled, so read it in the repository above.
- **Keep the placeholders — `{n}`, `{dir}` and friends — exactly as they appear in the English text.** A key whose placeholders differ **is not applied** (it stays English), and at startup you are told what was skipped **grouped by reason** (how many per reason, with up to three examples). ⚠️ **Nothing appears when everything was applied** — silence means "no problems", not "the file was ignored" (whether it loaded is visible in **Settings > Language**). ⚠️ Applying such a translation would crash the app the moment that screen opens; **skipping it is what prevents that**. ⚠️ **The format spec inside a placeholder (the `.2f` in `{n:.2f}`) must match English too** — only the **position** in the sentence is yours to move.
- **Words that appear in artifacts are out of scope.** This covers reports (HTML) and KML, and also **the wording inside the terrain profile and comparison graphs** (axis names, legends, titles, the way units are bracketed) and **the environment types** (Urban / Suburban / Rural / LoS). Artifact wording is a contract with whatever reads the files, so it does not move for the screen's convenience. ⚠️ **These also show on screen with the same wording**: a figure becomes an artifact as soon as you save it, and the environment wording is written into reports and profiles, so the on-screen side cannot be translated on its own.
- The bundled `ja` / `en` cannot be overridden (a `ja.json` is ignored).

> ⚠️ **Layout is not guaranteed for languages you add.** Window sizes are verified against the length of the Japanese and English wording; longer wording may be clipped. Treat it as an **unofficial translation**.

### Coordinate Format (DD / DMS)

This selects **how coordinates are displayed**. It is not an input mode switch.

- **Input fields accept either notation.** With DMS selected you can still type `34.8, 132.6` (DD), and with DD selected you can still type `34°48'00.0"N, 132°36'00.0"E`. Everything is normalised to decimal degrees before the calculation runs.
- **Fields are reformatted into the selected notation when you switch the format, or when settings/a project are loaded.** Committing an entry does not reformat it, so **what you just typed stays in the form you typed it** — this is not a fault.

### Proxy Settings

If DEM tile retrieval requires an HTTP proxy (e.g. on a corporate network), open **Settings > Proxy Settings** and enter the proxy URL (e.g. `http://proxy.example.com:8080`). Changes take effect immediately (no restart needed). Leave blank and click OK to revert to OS proxy settings.

> If no proxy is configured and the elevation server is completely unreachable, the run **aborts after a dozen seconds or so** and a dialog asks you to check the proxy settings — it will not quietly finish and hand you flat terrain.

---

## Map

![Map view with the transmit point, the receive point and the path between them; clicking the map picks up coordinates](images/shot_map.png)

The **"Map" button** at the bottom of the launcher opens an auxiliary window over the GSI pale map. The map is a single app-wide instance (owned by the launcher), and a **mode selector** at the top switches between four modes (Pick Coordinates / Continuous Add / Waypoints / Cache). The core simulation works without ever opening the map; the map is a convenience layer.

> On opening, it auto-zooms and centers to fit the path length of the currently set TX/RX.

### Pick Coordinates mode (default)

Click the map to fill **whichever site is still empty** (TX first, then RX); the picked points are written back to the launcher's TX / RX coordinate fields (the numeric fields are always the source of truth). Once both are set, a plain click no longer writes anything — select the marker you want to move (see below) so that re-placing one site never wipes the other.

- Shows cyan endpoint markers (TX filled / RX hollow), a path line, and a distance label at the midpoint.
- Dragging pans the map (coordinates update only on a committed click).
- **Clearing or editing the launcher's coordinate fields updates the map straight away** (the numeric fields are the source of truth). Empty a field and a plain click places that site again.
- **To place a site somewhere far away, right-click the map** ("Place TX here" / "Place RX here"). It works even when you have panned so far that the markers are off-screen, because you name the site instead of picking its marker.

### Continuous Add mode

A mode for stacking paths into Multiple Paths straight from the map. Selecting the **Append to Multiple Paths** mode opens (and raises) the Multiple Paths window; every time you place a **TX → RX** pair on the map, one row is appended to the batch and the map auto-resets for the next entry (no need to press "+ Add row" in the batch).

- Each row's RF settings (frequency, antenna gains, antenna heights) are **frozen from the launcher values at the moment of adding**. The workflow is to fix your conditions in the launcher first, then stack paths.
- All paths in the batch are drawn on the map, **drawn exactly as in Pick Coordinates mode**: a filled marker for TX, a hollow one for RX, the path line, and a distance label at its midpoint. The label carries the path ID, so you can tell which row of the batch each path belongs to. ⚠️ **Where TX and RX sit at nearly the same place the two markers overlap and cannot be told apart** — use the Relay Path window for those.
- Row changes on the batch side (delete, clear all, CSV import, add, duplicate, committing a coordinate-cell edit) are reflected on the map in real time.
- Closing the Multiple Paths window returns the map to Pick Coordinates mode.

### Waypoints mode

A mode for placing the points of a relay route from the map. Open the map from the Relay Path window and select the **Add waypoints** mode; each click appends **one point to the end** of the list (this is not the alternating TX → RX pick of Pick Coordinates mode).

- The map draws the polyline through the points and a **horizontal-distance badge for every hop**.
- ⚠️ **That horizontal distance appears on the map only.** The hop table has no distance column, and the report carries the **slant distance** (a different quantity).
- The map is a mirror of the Relay Path window. Adding and removing points is driven from that window, and the map follows it.

### Moving a point you have already placed

In all three input modes you can adjust a point on the map: **click its marker to select it** — it gets an amber ring and the status bar names it ("waypoint R1 selected", "RX of path p1 selected") — then **click the map where it should go**. The selection is used up by that one move, and **Esc** cancels it.

- The rule is one line: **a plain click adds a point, a click after a selection moves the selected one.**
- In Pick Coordinates and Append modes you can also **right-click the map** ("Place TX here" / "Place RX here") — the way in when the marker is off-screen.
- Dragging is still the map pan, on purpose: if dragging moved points, grabbing the map to scroll would silently rewrite your input.
- The window remains the source of truth. If the point you selected has been deleted or reordered in the window meanwhile, the map refuses the move and asks you to select again — it never moves a different point instead.
- ⚠️ **Terrain is sampled on a 5–10 m mesh.** If you move a point less than that, the status bar says so: the marker moves but the calculation can sample exactly the same ground and return the same result.

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

![Terrain profile chart: the line of sight and the first Fresnel zone drawn over the terrain, with the obstructed span, the RX level and the margin shown](images/shot_profile.png)

### 1. Launcher Window

An input form is displayed on startup.

#### Site Info

| Field                   | Description                                                   |
| ----------------------- | ------------------------------------------------------------- |
| TX Coords (Lat, Lon)    | TX station latitude and longitude (e.g.`34.5429, 132.4118`) |
| RX Coords (Lat, Lon)    | RX station latitude and longitude                             |
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
| Terrain Resolution    | One of High (approx. 5 m) / Medium (approx. 10 m) / Low (approx. 20 m). **You pick the level; the number of points follows from the path length** (the resolved count and the effective spacing are shown right below it). Default: Medium |

#### Project Info (optional)

| Field   | Description                                                             |
| ------- | ---------------------------------------------------------------------- |
| Project | Project name shown in the report header (applies to both Single and Multiple Paths reports) |
| Note    | Free note. Shown on the **Single Mode report and the Multiple Paths summary page** (`summary.html` / `report_all.html`). **Not shown on per-path reports** — it describes the survey as a whole, so it is not repeated per path |

> Project Info is entered in the **launcher, which is the single source of truth**. Both Single Mode's saved report and Multiple Paths inherit these values. In Multiple Paths they are shown read-only (🔒) and pulled in with **"↻ From Launcher"**.

### 2. The "Run" Button (Single Mode)

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

#### The "SAVE PACKAGE" Button

Saves the current display state to `results/YYYYMMDD_HHMMSS/` (see [Save Package](#save-package)).

### 4. Automatic Saving of Input Values

Each time you run a Single Mode simulation, the input values are saved to `radiosim_conf.json` and restored on the next launch. **No explicit save action is needed.**

To pull past conditions in from a file, or to carry a whole input set around, use the [File menu](#file) (**Load Parameters** / **Save Project As** / **Open Project**).

---

## Usage — Multiple Paths

![Multiple Paths input table, one path per row, with the verdict (OK / NG) and the horizontal distance returned to each row after the run](images/shot_batch.png)

Click the **Multiple Paths** button in the launcher to open the dedicated window.

### Design — refine in Single, commit in Multiple Paths

**Single (the launcher) is where you refine conditions; Multiple Paths is where you turn committed conditions into deliverables.** The launcher is the single source of truth, and each batch row is a **committed link frozen by copying the launcher fields at the moment the row was added**.

### Input Methods

**Manual entry**: Type IDs, coordinates, antenna heights, frequencies, and TX/RX gains directly into the table. Rows can be added, deleted, reordered by drag and drop, and edited cell by cell.

The **Dist (m)** column on the right is read-only and computed from the TX/RX coordinates (updated whenever a coordinate is committed). A mistyped coordinate shows up as an absurd distance, so it can be caught before running.

- **+ Add row**: Adds a row that freezes a copy of the current launcher fields (coordinates, frequency, gains, antenna heights).
- **Pick on map**: Opens the map in "Append to Multiple Paths" mode; every **TX → RX** pair you place adds one row (opening the map from this window is new in 2.7 — previously the flow could only be started from the map side).
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

The **Common Settings** panel at the top defines default values used whenever a per-path override is not specified. It is **read-only**, shown as a snapshot of the launcher (the source of truth). Use the **↻ From Launcher** button to pull in the launcher's current values.

### Running and Results

Click **Run** to process paths sequentially. The bar shows progress (done / total), and **each verdict is returned to the row that produced it**.

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

![Condition Explorer comparison view: one path under four conditions of differing frequency and gain, with the RX level and margin side by side and only the last condition NG](images/shot_scenario.png)

A screen for **taking one fixed path and digging into it under different conditions**. Open it with the "Condition Explorer" button in the launcher. The path (coordinates) and the terrain resolution are fixed to the launcher values, so **terrain is fetched only once** and the N conditions are then computed on top of it (repeat runs on the same path do not re-fetch DEM).

The toggle at the top switches between two modes. After changing coordinates or parameters in the launcher, press **"↻ From Launcher"** at the top right to pull them in (**until you do, the run uses the values shown on screen** — what you see is what is computed).

### Compare (base + N conditions)

- The leftmost **Base** column is the launcher's current values (read-only) — the reference never moves.
- In columns **Cond 1 ... Cond 5**, change only the fields you care about (they start as copies of the base). "+ Condition" adds up to 5 columns.
- You can vary frequency, TX power, TX/RX antenna gain, RX sensitivity, TX/RX antenna height, vegetation height, rain rate, environment and diffraction model. **Coordinates and terrain resolution cannot be varied** (comparing different paths is what Multiple Paths is for).
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

## Usage — Relay Path

![Relay Path window: three waypoints (transmit, relay, receive) with the RX level, margin and verdict returned per section in the table](images/shot_multihop.png)

For **one link carried over relay points**. Open it with the **Relay Path** button in the launcher. Use it when two points cannot see each other directly and a ridge (or another site) in between can carry the traffic.

### Assumption — regenerative relay

Each relay point **receives and then transmits again**. That means:

- Every section gets its own independent link budget — **section losses are never added together**.
- **The overall verdict is decided by the tightest section.** The report shows the overall verdict **and** the per-section breakdown, because "it fails" is not actionable unless you can see which section is short.
- **Passive reflectors are out of scope.** Their losses combine differently and are not part of this tool's calculation.

### Waypoints and sections

- The **waypoint table** is the input surface. **The first point is the transmitter and the last is the receiver**; "Add point" inserts a relay **before the receiver**, so the order on screen is the order of the route. Each row shows its role (TX / relay / RX).
- **The `＋` on a row inserts a relay directly below it** — to add one point in the middle you no longer have to delete the ones after it and retype them. The receiver row has no `＋` because the last point is fixed as the receiver. ⚠️ **Default names are not renumbered** (that would overwrite the entry of anyone who renamed `R1` to something meaningful); the order is carried by the rows and the role shown on each of them.
- Each waypoint has **coordinates and an antenna height**. **Relay rows carry an `×` so you can delete any single point** (TX and RX cannot be deleted, so they have no `×`). Deleting a relay also drops the settings of the section leaving that point; inserting one adds an empty section leaving it, so inserting and deleting at the same position returns you to where you were.
- Coordinates accept **either decimal degrees or degrees/minutes/seconds**; committing an entry reformats it to the notation chosen in Settings > Coordinate Display. **Height belongs to the point, not to the section** — a relay has one antenna, so it must not be possible to enter one height as "section 1 RX" and a different one as "section 2 TX".
- The **section table** lets you override **frequency and TX/RX gain per section** (blank = use the common settings from the launcher), because the two antennas at a relay are often different.
- The **Common Settings** panel at the top lists the **11 items** that feed the run (frequency, TX power, TX gain, RX gain, RX sensitivity / vegetation height, k-factor, terrain resolution, rain rate, environment type, diffraction model) — the same items, shown the same way, as in the Multiple Paths window. It is **read-only**, shown as a snapshot of the launcher (the source of truth), and **↻ From Launcher** pulls in the current values. ⚠️ Frequency and TX/RX gain can be overridden per section as described above, so what you see here is **the value inherited when the cell is blank**.
- **Pick on map** switches the map into waypoint mode; each click fills the next waypoint. The map draws a polyline through the points in order and puts the **horizontal distance of each section** at the midpoint of its line — the section table has no distance column, so this is where you read how long a section is.
- TX and RX start from **the launcher's coordinates as they were when the window opened** (relay points start empty).
- Up to 7 relay points (8 sections).

> **⚠️ Relay points are meant to be placed, not dragged around to explore.** Each section fetches its own terrain data, so moving a point triggers a new download. Use the [Condition Explorer](#usage--condition-explorer-compare--sweep) to explore heights and conditions.

### Running the route

**Run** processes the sections in order, filling the result list section by section (received level, margin, verdict), and finishes with the overall verdict (the tightest section).

⚠️ **The overall verdict has the same three values as a section: OK / NG / ERR.** If even one section could not be judged (ERR), the overall verdict is **ERR** as well — which is not the same thing as "computed, but the link does not close" (NG). In that case the overall margin is shown as `—` rather than a number, because a path containing a section that could not be judged has no headroom to speak of. The section table shows which section is ERR.

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
| Relay path waypoints and sections | |

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
| Terrain Resolution | High / Medium / Low           | —       | —      |
| Rain Rate         | 0                              | 200     | mm/h   |
| Env Type          | Urban / Suburban / Rural / LoS | —      |        |
| Diff Method       | bullington / single            | —      |        |

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

#### Bullington Method (default, ITU-R P.526 4.5.1)

Replaces several obstacles with a single **equivalent knife edge**: two tangent lines are drawn from each end to the terrain point of maximum elevation angle, and their intersection becomes the obstacle. The standard correction term `(1 - exp(-Luc/6)) x (10 + 0.02 x d[km])` is then applied.

🔑 **Version 3.0 replaced the previous custom Deygout implementation with this** (history under Known Limitations). The reason was not accuracy but **continuity**: the old implementation counted obstacles, so **changing vegetation height or antenna height by 1 m could change the count and move the result by 84 dB** (raising an antenna by 1 m made the link 84 dB worse). The equivalent edge moves continuously as the terrain moves, so that jump cannot occur by construction.

⚠️ **This does not claim compliance with ITU-R P.526 or P.452.** Only this one method from P.526 4.5.1 is implemented; the **spherical-earth term** added by the full method of P.452 4.2.1 (Delta-Bullington), clutter, and ducting are not included.

#### Fresnel-Kirchhoff Loss J(ν)

```
J(ν) = 6.9 + 20 × log₁₀(√((ν - 0.1)² + 1) + ν - 0.1)  [dB]  (ν > -0.8)
J(ν) = 0                                                          (ν ≤ -0.8)
```

#### Single Method

Applies J(ν) only to the maximum ν across all sample points. Fast, but it does not represent the combined loss of multiple ridges.

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


### The "How to read this result" section in the reports

**Every report carries this section at the bottom** — the four HTML reports (single path, the Multiple Paths ledger, relay path, condition explorer) and `report.txt` — from 3.0 onwards. A report gets printed and handed on, and **the person who receives it reads neither this manual nor the notes on screen**. So the assumptions, and how far the formulas actually used reach, are written into the report itself.

- **Always present**: elevations come from a **bare-earth model** (no buildings, no trees); vegetation height is **the single value entered**, applied along the whole path; environment loss is an **empirical figure taken from the area class**; **ground reflection (two-ray interference) is not modelled**; Earth curvature uses the **standard-atmosphere factor K = 1.33, fixed**, so periods when refraction departs from it (sub-refraction, ducting) are not covered.
- **Present when they apply**: the `Bullington` model reads low where two or more ridges are well separated, and the spherical-earth term is not included; rain or gaseous attenuation was **out of range and taken as 0 dB** (below 1 GHz); the vegetation coefficient was evaluated **outside its 1–6 GHz definition**. ⚠️ **A link with no rain assumed gets no rain note** — limits of things that were not calculated are not listed.
- **Reports that hold several links on one sheet** (Multiple Paths, relay, explorer) show every note that applies to **at least one** of them.
- A **calibration profile** field is shown. It always reads **not applied** — results have never been compared against measurements.

⚠️ **No number changes because of this section** — it states what was already true. ⚠️ The Multiple Paths ledger now fits **26 rows on one A4 page** (four fewer than before; beyond that it flows onto a second page).

### Output column specification (the output contract) and its change policy

Spreadsheet formulas and roll-up scripts reference **column names and their order directly**. RadioSim therefore treats the **file name, column names, column order and value format of every machine-readable artifact as a contract**, governed by the following policy (written down in 3.0).

- **New columns are appended at the end only.** Existing columns never move (some readers address them by position).
- **Removing, renaming or redefining a column is announced one version ahead.** The announcement goes into both the CHANGELOG and this document (one alone does not reach a reader who only has the binary).
- **The value format (unit, decimals, clamping) is part of the contract too.** When a unit changes, the column name changes with it (e.g. `slant_km` → `slant_m` in 2.5), so an old formula fails loudly instead of reading the wrong number.
- **The file name is part of the contract.** When "what one row means" changes, we add a **new file** rather than new columns (e.g. the relay path writes `hops.csv` instead of pushing per-section columns into `summary.csv`).

⚠️ **This policy is not a promise never to change anything** — it is the road a change has to travel.

> 🔴 **Format change in 3.0 (policy 3 applied to itself)**: **dB values now carry one decimal instead of two** (`-93.20` → `-93.2`). This covers `rx_dbm`, `margin_db`, `fspl_db`, `diff_db`, `veg_db`, `env_db`, `rain_db`, `gas_db`, `total_loss_db` and the two gain columns, in **all three CSVs** (`summary.csv` / `hops.csv` / `scenario.csv`). **No column or file name changes** — `-93.2` is the same number as `-93.20`, so **anything reading the value as a number keeps working**. ⚠️ **Fix anything that relies on the digit count of the text.** The reason: 0.01 dB was a precision this calculation does not have (the elevation grid is 5–10 m wide and carries metres of error of its own, vegetation height is a single assumed value, environment loss is an empirical figure per area class). **The maths is unchanged — only what is shown was coarsened.**

⚠️ **Free-text columns (`note` / `error` / `label`) are prefixed with `'` when the value starts with `=` `+` `@` and similar**, so spreadsheets do not evaluate them as formulas. Values that read as numbers (a negative margin, for instance) are written as they are.

#### `summary.csv` (Multiple Paths — **one row per path**)

| Column | Unit | Meaning |
| --- | --- | --- |
| `id` | — | Path ID (the `id` of the input CSV) |
| `status` | — | Status (`OK` / `NG` / `ERROR`) |
| `freq_mhz` | MHz | Frequency |
| `gain_tx_dbi` | dBi | TX antenna gain |
| `gain_rx_dbi` | dBi | RX antenna gain |
| `h_tx` | m | TX antenna height |
| `h_rx` | m | RX antenna height |
| `rx_dbm` | dBm | RX level |
| `margin_db` | dB | Margin (RX level − threshold) |
| `fspl_db` | dB | Free-space path loss |
| `diff_db` | dB | Diffraction loss |
| `veg_db` | dB | Vegetation attenuation |
| `env_db` | dB | Environmental loss |
| `rain_db` | dB | Rain attenuation |
| `gas_db` | dB | Gaseous attenuation |
| `total_loss_db` | dB | Total loss |
| `slant_m` | m | Slant distance (integer) |
| `f1_pct` | % | F1 obstruction (**clamped at 100%**) |
| `note` | — | The note from the input CSV (free text) |
| `error` | — | Why it failed; empty for a path that succeeded |
| `f1_depth_x` | ×F1 | F1 intrusion depth — how many F1 radii the obstruction reaches into the zone (**not capped**). When `f1_pct` reads 100, `1.00` means *exactly* full obstruction while `2.50` means it reaches 2.5 F1 radii past the line of sight |
| `samples` | points | How many terrain samples were taken for this link. It is **derived per row** from the resolution level and the path length, so it differs from row to row within one CSV (the effective spacing is roughly `slant_m` / (samples − 1)) |

⚠️ **A row may carry numbers even when `status` is `ERROR`** — the calculation went through and only the artifacts (graph, report) failed to be written. The `error` column says what is missing.

#### `hops.csv` (Relay Path — **one row per section**, N rows per path)

| Column | Unit | Meaning |
| --- | --- | --- |
| `group_id` | — | Path ID (the same value on every section of that path) |
| `hop_index` | — | Section number (starts at 1) |
| `hop_id` | — | Section ID |
| `from` | — | Name of the waypoint the section starts at |
| `to` | — | Name of the waypoint the section ends at |
| `status` | — | Status of the section (`OK` / `NG` / `ERROR`) |
| `freq_mhz` | MHz | Frequency |
| `gain_tx_dbi` | dBi | TX antenna gain |
| `gain_rx_dbi` | dBi | RX antenna gain |
| `h_tx` | m | TX antenna height |
| `h_rx` | m | RX antenna height |
| `rx_dbm` | dBm | RX level |
| `margin_db` | dB | Margin |
| `slant_m` | m | Slant distance (integer) |
| `f1_pct` | % | F1 obstruction (**clamped at 100%**) |
| `error` | — | Why it failed; empty for a section that succeeded |
| `f1_depth_x` | ×F1 | F1 intrusion depth — how many F1 radii the obstruction reaches into the zone (**not capped**). When `f1_pct` reads 100, `1.00` means *exactly* full obstruction while `2.50` means it reaches 2.5 F1 radii past the line of sight |
| `samples` | points | How many terrain samples were taken for this section. It is **derived per section** from the resolution level and the section length, so it differs between sections of one route |

⚠️ **Losses are never chained across sections** (a regenerative relay receives and transmits anew). The overall status is that of the section with the smallest margin, and it is **ERR whenever any section could not be judged (ERR)**.

#### `scenario.csv` (Condition Explorer — **one row per condition**, one per point in a sweep)

| Column | Unit | Meaning |
| --- | --- | --- |
| `label` | — | Condition name (the axis value in a sweep) |
| `condition` | — | ⚠️ **The second column is the only one whose name changes**: in a sweep it becomes the axis name (`freq_mhz` and so on) and carries that axis value. In compare mode it stays `condition` and carries the condition name |
| `status` | — | Status (`OK` / `NG` / `ERROR`) |
| `rx_dbm` | dBm | RX level |
| `margin_db` | dB | Margin |
| `total_loss_db` | dB | Total loss |
| `fspl_db` | dB | Free-space path loss |
| `diff_db` | dB | Diffraction loss |
| `veg_db` | dB | Vegetation attenuation |
| `env_db` | dB | Environmental loss |
| `rain_db` | dB | Rain attenuation |
| `gas_db` | dB | Gaseous attenuation |
| `f1_pct` | % | F1 obstruction (**clamped at 100%**) |
| `slant_m` | m | Slant distance (integer) |
| `freq_mhz` | MHz | Frequency used for this condition |
| `p_tx_dbm` | dBm | TX power |
| `gain_tx_dbi` | dBi | TX antenna gain |
| `gain_rx_dbi` | dBi | RX antenna gain |
| `sens_dbm` | dBm | Threshold |
| `h_tx` | m | TX antenna height |
| `h_rx` | m | RX antenna height |
| `veg_h` | m | Vegetation height |
| `rain_mmh` | mm/h | Rain rate |
| `env_type` | — | Env type |
| `diff_method` | — | Diffraction model |
| `f1_depth_x` | ×F1 | F1 intrusion depth — how many F1 radii the obstruction reaches into the zone (**not capped**). When `f1_pct` reads 100, `1.00` means *exactly* full obstruction while `2.50` means it reaches 2.5 F1 radii past the line of sight |

⚠️ **Sweeping `freq_mhz` / `h_tx` / `h_rx` / `veg_h` produces two columns with the same name** (the second column is the axis, the later one is the value used for that condition). A reader that addresses columns by name keeps the later one, so **read the second column by position**.

> 📣 **Announcement (this is fixed in 3.1)**: the duplicate is our fault, not the reader's, so **3.1 gives the second column a fixed name and stops emitting the same name twice** (the axis name will be carried elsewhere). The new column name will be given in the 3.1 CHANGELOG.
> - **If you read the second column by position, nothing changes for you.**
> - **If you address columns by name, you will need one pass of maintenance in 3.1** — including code that currently looks for `condition` in compare mode.
> - ⚠️ **Nothing changes in 3.0**; this version only announces it, as required by change policy 2 (*removing, renaming or redefining a column is announced one version ahead*).

#### `terrain_profile.csv` (Single Mode — **one row per terrain sample**)

| Column | Unit | Meaning |
| --- | --- | --- |
| `Distance_m` | m | Horizontal distance from the TX site (0.1 m resolution) |
| `Elevation_m` | m | Elevation, raw — before the earth-curvature correction (0.01 m resolution) |

⚠️ **The row count is exactly the number of terrain samples taken for that run** (derived from the resolution level and the path length).

---

## Uninstall

1. Confirm the app is not running.
2. Delete the entire extracted folder.

The app writes no data to the registry or AppData. Deleting the folder is a complete uninstall.

---

## Known Limitations

### Accuracy

- DEM horizontal resolution (5–10 m) is the hard ceiling for accuracy; individual building obstructions are not modeled
- The Bullington method is an approximation; **on single-obstacle paths**, errors of ±5–15 dB relative to measurements are expected (**the range says nothing about the combined loss of several overlapping obstacles**)
- 🔴 **Diffraction loss can come out too high or too low, and there is no way to tell in advance which paths are trustworthy.** Version 3.0 replaced the diffraction model with the **Bullington equivalent knife edge (ITU-R P.526 4.5.1)**, removing two defects: the divergence that drove mountain paths to hundreds or thousands of dB, and a **discontinuity that moved the result by 84 dB when vegetation height or antenna height changed by 1 m** (raising an antenna by 1 m made the diffraction loss 84 dB worse). **The possibility of being wrong has not gone away**: (1) the combined loss has only been placed alongside two structurally different methods (Epstein-Peterson and the previous custom implementation) to check its order of magnitude — it has **not been checked against measurements or a reference implementation**; (2) **where two or more ridges are well separated the result reads low** (a known property of this method, accepted by the standard that defines it); (3) on a single hill it now reads **higher** than before, by the standard correction term; (4) on deeply obstructed paths, **raising the terrain sample count raises the diffraction loss** (up to +14.8% going from 20 m to 5 m effective spacing); (5) the **spherical-earth term is not included**, so a **long, flat path with low antennas beyond the radio horizon** (over sea or tidal flats) can read low — none of the 26 representative paths met that condition, but **the product does not check for it**. ⚠️ **Neither the amount of relief nor the Fresnel blockage tells you which paths are affected** — relief is not a predictor. ⚠️ **Fresnel blockage is capped at 100% everywhere it is shown** (screen, report, CSV) even though the internal raw value goes above it, so the percentage will not warn you.
- 🛡 **What you can do about it**: switch "Diffraction Model" to `Single`, run the path again, and compare the two numbers. **Where they differ substantially, do not trust the default (Bullington) value** — the verdict may read NG, but **you cannot tell from these numbers whether that NG is correct**. ⚠️ **This does not tell you which one is right.** A large gap means the result **depends heavily on the choice of model** — that is the diagnosis. **A small gap does not guarantee accuracy either**, since neither value has been checked against measurements or a reference implementation. ⚠️ `Single` does not represent the combined loss of several obstacles (it looks at one point only) and does not apply the standard correction term, so it **comes out lower than Bullington**. ⚠️ How that relates to the true value is equally unknown — it does not mean `Single` is the safer side
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
- 🔴 **Restart RadioSim after changing the display scale** (if you change Windows "Scale and layout" **while the app is running**). Without a restart, dragging **the launcher** makes **its size drift continuously while you drag** — shrinking after you raise the scale, growing after you lower it. ⚠️ **Restarting restores the correct size.** ⚠️ **Only the launcher is affected** — we verified by measurement that resizable windows (map, batch, scenario, multi-hop, graph) do not drift. **Leave the launcher alone and you can keep working.** ⚠️ This does not happen if you have not changed the scale since launch (using several monitors is fine by itself). ⚠️ The cause is in the underlying GUI toolkit (Tk 8.6), not in the application: we verified by measurement that it cannot be worked around from our side (more than ten candidate workarounds were tried, none of them worked). ⚠️ **We verified by measurement that the newer Tk (9.0) does not have this problem**, but moving to it depends on other conditions (support in the Python runtime we build on), so there is no date yet
- When monitors with different display scales are in use, moving a window to another monitor resizes the text of the whole application to match that scale. Only the menu bar strip (File / Settings / Help) is drawn by Windows, so it keeps the scale of the monitor its own window sits on and may look smaller (or larger) than the rest. This affects appearance only, not operation

---

## Copyright

© 2026 BearValley AI Craftworks. All rights reserved.

This software is distributed under the **MIT License**, commercial use included. The license text ships as `LICENSE`, next to `RadioSimPro.exe`.
