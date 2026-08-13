"""
i18n.py
=======
UI 文字列翻訳テーブル。t(key) でロケール対応文字列を取得する。

**利用者が言語を足せる**（I-074 と並ぶ 2.8 の芯）＝`lang/<コード>.json` を置くと
言語メニューに現れる。仕組みは軽い＝`t()` は前からキー単位で英語へフォールバック
するので、**外部の表は「上書きできた分だけ」効き、未訳のキーは自動で英語**になる。
⛔ **プラグイン機構は作らない**（設計哲学⑤）＝読むのは JSON 1 種類だけ。

⚠️ **開くのは画面の語だけ**（下の `_ARTIFACT_PREFIXES` で成果物を除く）。レポートや
KML の語まで開くと、**出力契約が利用者ごとに変わる**＝それは `+0.1` ではなく `+1.0`
の話になる（`docs/glossary.md` が「成果物の語は画面の都合で動かさない」と決めた線）。
"""

import json
import os
import re

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # ===== Common =====
        "html_lang":            "en",
        "menu_theme":           "Theme",
        "menu_system":          "System",
        "menu_light":           "Light",
        "menu_dark":            "Dark",
        "menu_language":        "Language",
        "lang_en":              "English",
        "lang_ja":              "日本語",
        "lang_changed_msg":     "Restart the app to apply the language change.",
        "menu_settings":        "Settings",
        "menu_proxy_settings":  "Proxy Settings...",
        "menu_load_app_settings":"Load App Settings...",
        "menu_delete_all_cache":"Delete All Cache...",
        "dlg_proxy_title":      "Proxy Settings",
        "dlg_proxy_url_label":  "Proxy URL",
        "dlg_proxy_hint":       "Leave blank to use system proxy settings.",
        "btn_clear":            "Clear",
        "btn_cancel":           "Cancel",
        "menu_help":            "Help",
        "menu_open_readme":     "Open Documentation",
        "readme_filename":        "docs/developer_en.md",
        "readme_binary_filename": "docs/manual_en.md",
        "dlg_readme_missing":   "Documentation file not found.",
        "dlg_doc_title":        "Documentation",
        "lang_ext_title":       "External language files",
        "lang_ext_rejected":    "Some translations were not applied and are shown in "
                                "English instead. A translation is skipped when its "
                                "placeholders (such as {n}) differ from the English "
                                "text, or when the key is unknown.",
        "lang_ext_rejected_line": "  {lang}: {n} skipped ({keys} …)",
        "menu_about":           "About",
        "dlg_about_msg":        "{app}\n\nVersion: {ver}\n{copy}",
        "dlg_error":            "Error",
        "dlg_unexpected_error": "Unexpected error",
        "dlg_unexpected_error_msg":
            "The operation stopped because of an unexpected error.\n\n"
            "{error}\n\n"
            "The full details were written to:\n{log}",
        "dlg_ok":               "OK",
        "dlg_close":            "Close",
        "dlg_open_report":      "Open report",
        "dlg_open_folder":      "Open folder",
        "dlg_open_summary":     "Open summary",
        "dlg_open_all":         "Open all pages",
        "dlg_yes":              "Yes",
        "dlg_no":               "No",

        # ===== Launcher =====
        "grp_site_info":        " Site Info ",
        "grp_radio_settings":   " Radio Settings ",
        "grp_environment":      " Environment ",
        "lbl_start":            "TX Coords (Lat, Lon)",
        "lbl_end":              "RX Coords (Lat, Lon)",
        "lbl_h_tx":             "TX Antenna Height (m)",
        "lbl_h_rx":             "RX Antenna Height (m)",
        "lbl_freq":             "Frequency (MHz)",
        "lbl_p_tx":             "TX Power (dBm)",
        "lbl_gain_tx":          "TX Antenna Gain (dBi)",
        "lbl_gain_rx":          "RX Antenna Gain (dBi)",
        "lbl_sens":             "Sensitivity (dBm)",
        "lbl_env_type":         "Env Type",
        "lbl_rain":             "Rain Rate (mm/h)",
        "lbl_diff_method":      "Diffraction Model",
        "diff_opt_deygout":     "Deygout",
        "diff_opt_single":      "Single",
        "lbl_coord_format":     "Coordinate Display",
        "coord_fmt_dd":         "Decimal Degrees (DD)",
        "coord_fmt_dms":        "Degrees Minutes Seconds (DMS)",
        "lbl_veg_h":            "Vegetation Height (m)",
        "lbl_k_factor":         "Rician K-Factor (initial)",
        "lbl_samples":          "Sampling Points",
        "tip_start":            'lat, lon   e.g. "34.54, 132.41"',
        "tip_end":              'lat, lon   e.g. "34.53, 132.41"',
        "tip_rain_rate":        "Rain rate   0 – 200 mm/h",
        "tip_diff_method":      "Diffraction model: Deygout (multi-knife) or Single",
        "tip_h_tx":             "TX antenna height   0 – 500 m",
        "tip_h_rx":             "RX antenna height   0 – 500 m",
        "tip_freq":             "Frequency   1 – 100 000 MHz",
        "tip_p_tx":             "TX power   −30 – 60 dBm",
        "tip_gain_tx":          "TX antenna gain   0 – 60 dBi",
        "tip_gain_rx":          "RX antenna gain   0 – 60 dBi",
        "tip_sens":             "Receiver sensitivity   −130 – −20 dBm",
        "tip_veg_h":            "Vegetation height   0 – 100 m",
        "tip_k_factor":         "Rician K-factor (LOS/scatter power ratio)   0 – 30",
        "tip_samples":          "Terrain sample points   10 – 2000",
        "status_ready":         "Ready",
        "status_prefetch":      "Prefetching DEM tiles…",
        "status_prefetch_pct":  "Prefetching DEM tiles… {pct}%",
        "status_fetching":      "Fetching terrain…",
        "status_fetching_pct":  "Fetching terrain… {pct}%",
        "status_rendering":     "Preparing graph…",
        "btn_run":              "Run",
        "menu_file":            "File",
        "menu_load_settings":   "Load Parameters...",
        "menu_open_results":    "Open Results Folder",
        "dlg_input_error":      "Input Error",
        "dlg_settings_ok":      "Settings imported.",
        "dlg_app_settings_ok":  "App settings imported.",
        "dlg_app_settings_none_title": "No App Settings",
        "dlg_app_settings_none": "The selected file contains no app settings (theme/language/proxy). Nothing was changed.",
        "dlg_select_settings":  "Select settings.json",
        "menu_open_project":    "Open Project...",
        "menu_save_project":    "Save Project As...",
        "dlg_select_project":   "Open project file",
        "dlg_save_project":     "Save project file",
        "proj_filetype":        "RadioSim project",
        "proj_saved":           "Project saved:\n{path}",
        "proj_loaded":          "Project loaded.\nWindows that are already open keep what you see — use \"Apply to this window\" in the bar at the top to pull the project in. Closed windows pick it up when you open them.",
        # 読み込んだあとに各ウィンドウの上へ出す帯（I-061）＝**押したときだけ**差し替える。
        "proj_notice":          "A project was loaded. You can replace what this window holds.",
        "proj_notice_take":     "Apply to this window",
        "proj_warn_multihop":   "\n\n⚠️ The relay path was not saved because it has values that cannot be read: {reason}",
        "proj_warn_batch":      "\n\n⚠️ The path rows were not saved because row \"{id}\" has values that cannot be read.",
        "proj_err_not_project": "This is not a RadioSim project file.",
        "proj_err_newer":       "Saved by a newer version of RadioSim (schema {ver}, supported {cur}). Update RadioSim to open it.",
        "proj_err_broken":      "Cannot read the project file: {reason}",
        "dlg_select_app_settings": "Select a settings file (app settings only)",
        "dlg_success":          "Success",

        # ===== Batch Builder =====
        "batch_window_title":   "Multiple Paths",
        "batch_common_cfg":     " Common Settings ",
        "batch_case_info":      " Case Info ",
        "batch_project_name":   "Project",
        "batch_memo":           "Note",
        "col_id":               "ID",
        "col_start":            "Start (lat, lon)",
        "col_end":              "End (lat, lon)",
        "col_h_tx":             "h_tx",
        "col_h_rx":             "h_rx",
        "col_freq":             "Freq(MHz)",
        "col_gain_tx":          "Gtx(dBi)",
        "col_gain_rx":          "Grx(dBi)",
        "col_note":             "Note",
        "col_dist":             "Dist(m)",
        "lbl_b_freq":           "Freq (MHz)",
        "lbl_b_gain_tx":        "TX Gain (dBi)",
        "lbl_b_gain_rx":        "RX Gain (dBi)",
        "lbl_b_sens":           "Sens (dBm)",
        "lbl_b_veg_h":          "Veg H (m)",
        "lbl_b_k_factor":       "Rician K-Factor",
        "lbl_b_samples":        "Samples",
        "lbl_b_rain":           "Rain (mm/h)",
        "lbl_b_diff_model":     "Diff Model",
        "btn_add_row":          "+ Add Row",
        "btn_import_csv":       "Import CSV",
        "btn_export_csv":       "Export CSV",
        "btn_template":         "Template",
        "btn_clear_all":        "Clear All",
        "btn_refresh_common":   "↻ From Launcher",
        "hint_common_readonly": "🔒 from Launcher",
        "menu_send_to_single":  "→ Send to Single",
        "menu_update_rf":       "⟳ Update RF from Launcher",
        "menu_dup":             "⧉ Duplicate",
        "menu_del":             "× Delete",
        "batch_starting":       "Starting…",
        "batch_stage_render":   "   Generating report…",
        "batch_stage_summary":  "   Generating summary…",
        "batch_done":           "Done: {dir}",
        "batch_error_msg":      "An error occurred",
        "dlg_clear_msg":        "Remove all {n} row(s)?",
        "dlg_import_confirm":   "This will replace {n} existing row(s).\nContinue?",
        "dlg_import_success":   "{n} path(s) imported.",
        "dlg_import_error":     "Import Error",
        "dlg_export_empty":     "No paths to export.",
        "dlg_export_saved":     "Saved:\n{path}",
        "dlg_export_title":     "Export",
        "dlg_export_error":     "Export Error",
        "dlg_template_saved":   "Template saved:\n{path}",
        "dlg_validation_error": "Validation Error",
        "dlg_common_cfg_error": "Common Settings Error",
        "dlg_batch_complete_msg": "Multiple Paths complete.\nSaved to: {dir}\n\n\"Open all pages\" opens report_all.html (Ctrl+P prints every page).",
        "dlg_batch_error":      "Multiple Paths Error",
        "select_batch_csv":     "Select the paths CSV",

        # ===== Graph =====
        "graph_dist_axis":      "Distance [m]",
        "graph_alt_axis":       "Altitude [m]",
        "graph_curve_note":     "Terrain shown with equivalent-earth (k={k:.2f}) curvature: not true elevation (mid-path bulge +{bulge:.0f} m; vertical exaggeration approx x{vexag:.0f})",
        "legend_terrain":       "Terrain",
        "legend_vegetation":    "Vegetation",
        "legend_los":           "Line of Sight",
        "legend_fresnel":       "1st Fresnel Zone",
        "slider_htx":           "TX Height [m]",
        "slider_hrx":           "RX Height [m]",
        "slider_rain":          "Rain Rate [mm/h]",
        "btn_save_pkg":         "SAVE PACKAGE",
        "dlg_not_ready_title":  "Not Ready",
        "dlg_not_ready_msg":    "No simulation result yet.\nPlease run RUN SIMULATION first.",
        "dlg_saved_title":      "Saved",
        "dlg_saved_msg":        "Package saved:\n{dir}\n\nOpen report.html?",
        "dlg_save_error":       "Save Error",
        "panel_link_budget":    "Link Budget",
        "panel_environment":    "Environment",
        "pl_eirp":              "EIRP",
        "pl_fspl":              "FSPL",
        "pl_diff_loss":         "Diff Loss",
        "pl_veg_loss":          "Veg Loss",
        "pl_env_loss":          "Env Loss",
        "pl_rain_loss":         "Rain Loss",
        "pl_gas_loss":          "Gas Loss",
        "pl_total_loss":        "Total Loss",
        "pl_rx_ant_g":          "RX Ant.G",
        "pl_rx_level":          "RX Level",
        "pl_threshold":         "Threshold",
        "pl_act_margin":        "Act Margin",
        "pl_status":            "Status",
        "pl_env_type":          "Env Type",
        "pl_diff_model":        "Diff Model",
        "pl_k_factor":          "K-Factor (est.)",
        "pl_f1_obs":            "F1 Obs",
        "pl_slant_dist":        "Slant Dist",

        # ===== Env types =====
        "env_urban":            "Urban",
        "env_suburban":         "Suburban",
        "env_rural":            "Rural",
        "env_los":              "LoS",

        # ===== HTML per-path report =====
        "html_path_title":      "Radio Link Report",
        "html_generated":       "Generated",
        "html_batch_mode":      "Batch Mode",  # HTML footer (summary)
        "html_single_mode":     "Single Mode",  # HTML footer (per-path)
        "html_report_memo":     "Note:",

        "html_status":          "Status",
        "html_rx_level":        "RX Level",
        "html_act_margin":      "Act Margin",
        "html_total_loss":      "Total Loss",
        "html_site_info":       "Site Info",
        "html_tx_coords":       "TX Coords",
        "html_rx_coords":       "RX Coords",
        "html_tx_height":       "TX Height",
        "html_rx_height":       "RX Height",
        "html_slant_dist":      "Slant Dist",
        "html_horiz_dist":      "Horiz Dist",
        "html_aim_tx":          "TX aim (AZ/EL)",
        "html_aim_rx":          "RX aim (AZ/EL)",
        "html_map_title":       "Path Map",
        "html_map_unavailable": "Map unavailable (tiles could not be fetched).",
        # 成果物（PNG / report.html / KML）の生成に失敗した経路の印（I-010）。
        "html_artifact_missing": "no output",
        "html_radio_settings":  "Radio Settings",
        "html_frequency":       "Frequency",
        "html_tx_power":        "TX Power",
        "html_tx_gain":         "TX Gain",
        "html_rx_gain":         "RX Gain",
        "html_link_budget":     "Link Budget",
        "html_eirp":            "EIRP",
        "html_fspl":            "FSPL",
        "html_diff_loss":       "Diff Loss",
        "html_veg_loss":        "Veg Loss",
        "html_env_loss":        "Env Loss",
        "html_rain_loss":       "Rain Loss",
        "html_gas_loss":        "Gas Loss",
        "html_rx_ant_gain":     "RX Ant. Gain",
        "html_threshold":       "Threshold",
        "html_environment":     "Environment",
        "html_env_type":        "Env Type",
        "html_diff_model":      "Diff Model",
        "html_rain_rate":       "Rain Rate",
        "html_model_deygout":   "Deygout",
        "html_model_single":    "Single",

        # ===== Scenario explorer (A-1 compare / A-2 sweep) =====
        "scn_window_title":     "Condition Explorer",
        "scn_mode":             "Condition Explorer",
        "scn_tab_compare":      "Compare (base + N)",
        "scn_tab_sweep":        "Sweep (1 axis, N points)",
        "scn_compare_title":    "Condition Comparison",
        "scn_sweep_title":      "Parameter Sweep",
        # 枠名は `Path`＝「固定」は凍結方式の言い方で、🔒 のヒントが既に言っている
        # （同じ意味を 2 通りで言わない・I-048）。中身は col_start / col_end を
        # **共有**する＝バッチと同じ語で座標を呼ぶことを i18n の側で保証する。
        "scn_fixed_group":      " Path ",
        "scn_fixed_path":       "Fixed path",   # レポート文中の表記＝据え置き
        # 凍結帯のラベル＝バッチの列と**同じ語**（`col_start` / `col_end`）。
        # 表記の注記「(lat, lon)」は付けない＝編集できない欄に入力の作法を書いても
        # 使い道が無く、帯の幅だけが伸びる（実測 +270px）。
        "scn_tx_coord":         "Start",
        "scn_rx_coord":         "End",
        "scn_samples":          "Samples",
        "scn_margin_axis":      "Margin (dB)",
        "scn_base":             "Base",
        "scn_cond_n":           "Cond {n}",
        "scn_add_cond":         "+ Condition",
        "scn_del_cond":         "− Condition",
        "scn_result":           "Result",
        "scn_col_label":        "Condition / value",
        "scn_saved_msg":        "Saved:\n{dir}",
        "scn_axis":             "Axis",
        "scn_from":             "From",
        "scn_to":               "To",
        "scn_points":           "Points",
        "scn_delta":            "±",
        "scn_range_how":        "Range:",
        "scn_range_from_to":    "From / To",
        "scn_range_delta":      "Base ±",
        "scn_phase_fetch":      "Fetching terrain...",
        "scn_phase_calc":       "Calculating conditions...",
        "scn_phase_render":     "Writing report...",
        "scn_err_points":       "Points must be between 2 and {max}.",
        "scn_err_range":        "From and To must differ.",
        "scn_err_no_result":    "Run a scenario first.",
        "scn_err_override":     "Not an overridable parameter: {keys}",
        "scn_err_axis":         "Not a sweepable axis: {axis}",
        "scn_err_value":        "{label}: {reason} (value: {value})",
        "scn_axis_freq_mhz":    "Frequency",
        "scn_axis_p_tx":        "TX power",
        "scn_axis_gain_tx":     "TX antenna gain",
        "scn_axis_gain_rx":     "RX antenna gain",
        "scn_axis_sens":        "RX sensitivity",
        "scn_axis_h_tx":        "TX antenna height",
        "scn_axis_h_rx":        "RX antenna height",
        "scn_axis_veg_h":       "Vegetation height",
        "scn_axis_k_factor":    "Rician K-Factor",
        "scn_axis_rain_rate":   "Rain rate",
        "scn_axis_env_type":    "Environment",
        "scn_axis_diff_method": "Diffraction model",

        # ===== HTML summary report =====
        "html_batch_title":     "Batch Simulation Report",
        # report_all.html（summary + all per-path sheets in one document）
        "html_all_title":       "Batch Report (All Pages)",
        "html_all_link":        "Print all pages at once (report_all.html)",
        "html_total":           "Total",
        "html_ok":              "OK",
        "html_ng":              "NG",
        "html_error":           "Error",
        "html_col_id":          "ID",
        "html_col_status":      "Status",
        "html_col_freq":        "Freq (MHz)",
        "html_col_gain_tx":     "TX Gain (dBi)",
        "html_col_gain_rx":     "RX Gain (dBi)",
        "html_col_h_tx":        "h_tx (m)",
        "html_col_h_rx":        "h_rx (m)",
        "html_col_rx":          "RX (dBm)",
        "html_col_margin":      "Margin (dB)",
        "html_col_fspl":        "FSPL",
        "html_col_diff":        "Diff",
        "html_col_veg":         "Veg",
        "html_col_env":         "Env",
        "html_col_rain":        "Rain",
        "html_col_gas":         "Gas",
        "html_col_total_loss":  "Total Loss",
        "html_col_slant":       "Slant (m)",
        "html_col_f1":          "F1 (%)",
        "html_col_note":        "Note",
        "html_col_graph":       "Graph",

        # ===== Tile Manager =====
        "map_window_title":     "Map",
        "map_mode_label":       "Mode:",
        "map_mode_cache":       "Cache Management",
        "map_mode_coords":      "Pick Coordinates",
        "map_mode_append":      "Append to Multiple Paths",
        "map_coords_hint_tx":   "Coordinate pick: click the map to set the TX site.",
        "map_coords_hint_rx":   "Coordinate pick: click the map to set the RX site.",
        "map_append_hint_tx":   "Append paths: click the map to set the TX site (a row is added once RX is set).",
        "map_append_hint_rx":   "Append paths: click the map to set the RX site, then a path row is added.",
        "map_append_added":     "Added path: {pid}",
        "map_marker_tx":        "TX",
        "map_marker_rx":        "RX",
        "tm_hint":              "Ctrl+drag: download  |  Ctrl+Alt+drag: force re-download  |  Shift+Ctrl+drag: delete  |  drag: pan",
        "tm_dl_title":          "Download",
        "tm_dl_confirm":        "Download DEM for {n} areas?",
        "tm_dl_force_title":    "Force Re-download",
        "tm_dl_force_confirm":  "Force re-download DEM for {n} areas (ignore cache)?",
        "tm_dl_size_hint":      "Estimated size: ~{mb} MB (varies by resolution)",
        "tm_downloading":       "Downloading…",
        "tm_dl_progress":       "Downloading… {done}/{total} ({pct}%)",
        "tm_dl_done":           "Done: 5m-aerial {dl5a}, 5m-photo {dl5b}, 10m {dl_dem} downloaded  /  skipped {skipped}, failed {failed}",
        "tm_delete_title":      "Delete Range",
        "tm_delete_confirm":    "Delete cached tiles for {n} areas? This cannot be undone.",
        "tm_delete_done":       "{deleted} tiles deleted.",
        "tm_delete_all_title":  "Delete All Cache",
        "tm_delete_all_confirm":"Delete ALL cached tiles, including both elevation (DEM) tiles and report map tiles? This cannot be undone.",
        "tm_delete_all_done":   "All cache deleted: {deleted} tiles.",
        "tm_stats":             "Total cache: {count} tiles / {mb} MB",
        "tm_attr_pale":         "Source: GSI Tiles (Pale map)",
        "tm_attr_photo":        "Source: GSI Tiles (Aerial photo)",
        "map_layer_label":      "Basemap",
        "map_layer_pale":       "Pale map",
        "map_layer_photo":      "Aerial photo",

        # ===== Terrain fetch =====
        "err_dem_unreachable":  (
            "Could not download terrain data (DEM). The run was stopped so that "
            "a flat 0 m terrain is not reported as a real result.\n\n"
            "Check your network connection, and set a proxy if your site requires "
            "one (Settings > Proxy Settings)."
        ),

        # ===== Multi-hop (relay) =====
        "mh_window_title":      "Relay Path",
        "mh_role_tx":           "(TX)",
        "mh_role_rx":           "(RX)",
        "mh_role_relay":        "(relay)",
        "mh_route_id":          "Path ID",
        "mh_waypoints":         " Waypoints ",
        "mh_hops_group":        " Section Settings ",
        "mh_result":            " Result ",
        "mh_col_no":            "#",
        "mh_col_name":          "Name",
        "mh_col_coord":         "Coords (Lat, Lon)",
        "mh_col_height":        "Height (m)",
        "mh_col_section":       "Section",
        "mh_add_point":         "+ Add point",
        "mh_del_point":         "- Remove relay",
        "mh_from_map":          "Pick on map",
        "mh_hint_order":        "Points are used in this order (TX first, RX last).",
        "mh_hint_inherit":      "Leave blank to inherit the common settings.",
        "mh_running":           "Section {i} / {n}…",
        "mh_summary":           "Overall: {status}   {label}: {margin} dB   ({worst})",
        "mh_err_coord":         "Point {no} ({name}): coordinates must be \"lat, lon\" and the height a number.",
        "mh_err_no_map":        "The map is not available from here.",
        "map_mode_waypoints":   "Add waypoints",
        "map_status_waypoint":  "Waypoints: click the map to append a point to the relay path.",
        "mh_report_title":      "Relay Path Report",
        "mh_mode_label":        "Relay path",
        "mh_overall":           "Overall",
        # 集約カードの語は**判定で変わる**（I-052）。同じ数字が答えている問いが
        # OK と NG で別物なので、語のほうを切り替える（値の出所は変えない）。
        "mh_overall_margin":    "Overall margin (worst)",
        "mh_overall_shortfall": "Largest shortfall",
        "mh_hops":              "Sections",
        "mh_worst_hop":         "Weakest section",
        "mh_section":           "Section",
        "mh_heights":           "Heights (TX / RX)",
        "mh_regenerative_note": "Each section is an independent link budget (regenerative relay): losses are not added across sections, and the overall verdict is the weakest section. Passive reflectors are out of scope.",
        "mh_err_too_few":       "A relay path needs at least 2 waypoints (TX and RX).",
        "mh_err_too_many":      "Too many sections (max {max}).",
        "mh_err_rf_count":      "Radio settings do not match the sections ({hops} section(s) / {rf} setting(s)).",
        "mh_err_id_too_long":   "Path ID {pid} is too long (max {max}, got {n}).",
        "mh_err_bad_height":    "{name}: antenna height is not a number.",
        "mh_err_height_range":  "{name}: antenna height {val} is out of range (0 - 500 m).",

        # ===== Validation (config) =====
        "err_freq":             "Frequency must be between 1 and 100000 MHz",
        "err_p_tx":             "TX Power must be between -30 and 60 dBm",
        "err_gain_tx":          "TX Antenna Gain must be between 0 and 60 dBi",
        "err_gain_rx":          "RX Antenna Gain must be between 0 and 60 dBi",
        "err_sens":             "Sensitivity must be between -130 and -20 dBm",
        "err_h_tx":             "TX Antenna Height must be between 0 and 500 m",
        "err_h_rx":             "RX Antenna Height must be between 0 and 500 m",
        "err_veg_h":            "Vegetation Height must be between 0 and 100 m",
        "err_k_factor":         "Rician K-Factor must be between 0 and 30",
        "err_samples":          "Sampling Points must be between 10 and 2000",
        "err_rain_rate":        "Rain Rate must be between 0 and 200 mm/h",
        "err_coord_format":     'must be in "lat, lon" format',
        "err_coord_invalid":    "contains an invalid numeric value",
        "err_lat_range":        "Latitude must be between -85.05 and 85.05 (Web Mercator limit)",
        "err_lon_range":        "Longitude must be between -180 and 180",
        "err_coord_identical":  "Start and End coordinates are identical. Please specify different locations.",
        "err_numeric":          "A numeric value is required",
        "err_not_finite":       "A finite numeric value is required (NaN / Inf are not allowed)",
        "err_env_type":         "env_type must be one of",
        "err_diff_method":      "diff_method must be one of",
        "err_label_start":      "Start",
        "err_label_end":        "End",

        # ===== Validation (batch) =====
        "verr_empty":           "Path list is empty.",
        "verr_duplicate_id":    "Duplicate ID: '{pid}'",
        "verr_invalid_id":      "[{pid}] ID must contain only letters, digits, '_', or '-'",
        "verr_id_too_long":     "[{pid}] ID too long (max {max} chars): {n}",
        "verr_note_too_long":   "[{pid}] Note too long (max {max} chars): {n}",
        "verr_invalid_coord":   "[{pid}] Invalid coordinate or height value.",
        "verr_tx_lat":          "[{pid}] TX latitude out of range: {val}",
        "verr_tx_lon":          "[{pid}] TX longitude out of range: {val}",
        "verr_rx_lat":          "[{pid}] RX latitude out of range: {val}",
        "verr_rx_lon":          "[{pid}] RX longitude out of range: {val}",
        "verr_identical":       "[{pid}] TX and RX coordinates are identical.",
        "verr_h_tx":            "[{pid}] h_tx out of range (0–500): {val}",
        "verr_h_rx":            "[{pid}] h_rx out of range (0–500): {val}",
        "verr_freq":            "[{pid}] freq out of range (1–100000): {val}",
        "verr_gain_tx":         "[{pid}] TX gain out of range (0–60): {val}",
        "verr_gain_rx":         "[{pid}] RX gain out of range (0–60): {val}",
    },

    "ja": {
        # ===== Common =====
        "html_lang":            "ja",
        "menu_theme":           "テーマ",
        "menu_system":          "システム",
        "menu_light":           "ライト",
        "menu_dark":            "ダーク",
        "menu_language":        "言語",
        "lang_en":              "English",
        "lang_ja":              "日本語",
        "lang_changed_msg":     "アプリを再起動すると言語が切り替わります。",
        "menu_settings":        "設定",
        "menu_proxy_settings":  "プロキシ設定...",
        "menu_load_app_settings":"アプリ設定を読込む...",
        "menu_delete_all_cache":"全キャッシュ削除...",
        "dlg_proxy_title":      "プロキシ設定",
        "dlg_proxy_url_label":  "プロキシ URL",
        "dlg_proxy_hint":       "空白にするとシステム設定を使用します",
        "btn_clear":            "クリア",
        "btn_cancel":           "キャンセル",
        "menu_help":            "ヘルプ",
        "menu_open_readme":     "ドキュメントを開く",
        "readme_filename":        "docs/developer_ja.md",
        "readme_binary_filename": "docs/manual_ja.md",
        "dlg_readme_missing":   "ドキュメントファイルが見つかりません。",
        "dlg_doc_title":        "ドキュメント",
        "lang_ext_title":       "外部の言語ファイル",
        "lang_ext_rejected":    "一部の訳を採用しなかったので、そのキーは英語のまま"
                                "表示します。差し込み（{n} など）が英語の文と違う"
                                "場合と、キーが存在しない場合に採用しません。",
        "lang_ext_rejected_line": "  {lang}: {n} 件（{keys} …）",
        "menu_about":           "バージョン情報",
        "dlg_about_msg":        "{app}\n\nバージョン: {ver}\n{copy}",
        "dlg_error":            "エラー",
        "dlg_unexpected_error": "予期しないエラー",
        "dlg_unexpected_error_msg":
            "予期しないエラーが起きたため、処理を中断しました。\n\n"
            "{error}\n\n"
            "詳しい内容を次の場所に書き出しました:\n{log}",
        "dlg_ok":               "OK",
        "dlg_close":            "閉じる",
        "dlg_open_report":      "レポートを開く",
        "dlg_open_folder":      "保存先を開く",
        "dlg_open_summary":     "サマリを開く",
        "dlg_open_all":         "全ページを開く",
        "dlg_yes":              "はい",
        "dlg_no":               "いいえ",

        # ===== Launcher =====
        "grp_site_info":        " サイト情報 ",
        "grp_radio_settings":   " 無線設定 ",
        "grp_environment":      " 伝搬環境 ",
        "lbl_start":            "送信座標（緯度, 経度）",
        "lbl_end":              "受信座標（緯度, 経度）",
        "lbl_h_tx":             "送信アンテナ高（m）",
        "lbl_h_rx":             "受信アンテナ高（m）",
        "lbl_freq":             "周波数（MHz）",
        "lbl_p_tx":             "送信電力（dBm）",
        "lbl_gain_tx":          "送信アンテナ利得（dBi）",
        "lbl_gain_rx":          "受信アンテナ利得（dBi）",
        "lbl_sens":             "受信感度（dBm）",
        "lbl_env_type":         "環境区分",
        "lbl_rain":             "降雨強度（mm/h）",
        "lbl_diff_method":      "回折モデル",
        "diff_opt_deygout":     "Deygout",
        "diff_opt_single":      "Single",
        "lbl_coord_format":     "座標の表示形式",
        "coord_fmt_dd":         "十進度（DD）",
        "coord_fmt_dms":        "度分秒（DMS）",
        "lbl_veg_h":            "植生高（m）",
        "lbl_k_factor":         "初期Kファクター（ライス）",
        "lbl_samples":          "地形サンプル数",
        "tip_start":            '緯度, 経度   例: "34.54, 132.41"',
        "tip_end":              '緯度, 経度   例: "34.53, 132.41"',
        "tip_rain_rate":        "降雨強度   0〜200 mm/h",
        "tip_diff_method":      "回折モデル: Deygout（多重ナイフエッジ）/ Single",
        "tip_h_tx":             "送信アンテナ高   0〜500 m",
        "tip_h_rx":             "受信アンテナ高   0〜500 m",
        "tip_freq":             "周波数   1〜100000 MHz",
        "tip_p_tx":             "送信電力   −30〜60 dBm",
        "tip_gain_tx":          "送信アンテナ利得   0〜60 dBi",
        "tip_gain_rx":          "受信アンテナ利得   0〜60 dBi",
        "tip_sens":             "受信感度   −130〜−20 dBm",
        "tip_veg_h":            "植生高   0〜100 m",
        "tip_k_factor":         "ライスKファクター（見通し/散乱電力比）   0〜30",
        "tip_samples":          "地形サンプル数   10〜2000",
        "status_ready":         "待機中",
        "status_prefetch":      "DEMタイル取得中…",
        "status_prefetch_pct":  "DEMタイル取得中… {pct}%",
        "status_fetching":      "地形データ取得中…",
        "status_fetching_pct":  "地形データ取得中… {pct}%",
        "status_rendering":     "グラフを準備中…",
        "btn_run":              "実行",
        "menu_file":            "ファイル",
        "menu_load_settings":   "パラメータ読込...",
        "menu_open_results":    "結果フォルダを開く",
        "dlg_input_error":      "入力エラー",
        "dlg_settings_ok":      "設定を読み込みました。",
        "dlg_app_settings_ok":  "アプリ設定を読み込みました。",
        "dlg_app_settings_none_title": "アプリ設定なし",
        "dlg_app_settings_none": "選択したファイルにアプリ設定（テーマ／言語／プロキシ）がありません。変更はありません。",
        "dlg_select_settings":  "settings.json を選択",
        "menu_open_project":    "プロジェクトを開く...",
        "menu_save_project":    "プロジェクトを保存...",
        "dlg_select_project":   "プロジェクトファイルを開く",
        "dlg_save_project":     "プロジェクトファイルを保存",
        "proj_filetype":        "RadioSim プロジェクト",
        "proj_saved":           "プロジェクトを保存しました:\n{path}",
        "proj_loaded":          "プロジェクトを読み込みました。\n開いているウィンドウはそのままです（上の帯の「内容を取り込む」で差し替えられます）。閉じているウィンドウは、開いたときに反映されます。",
        # 読み込んだあとに各ウィンドウの上へ出す帯（I-061）＝**押したときだけ**差し替える。
        "proj_notice":          "プロジェクトを読み込みました。このウィンドウの内容を差し替えられます。",
        "proj_notice_take":     "内容を取り込む",
        "proj_warn_multihop":   "\n\n⚠️ 中継経路は読めない値があるため保存していません: {reason}",
        "proj_warn_batch":      "\n\n⚠️ 複数経路の行は「{id}」に読めない値があるため保存していません。",
        "proj_err_not_project": "RadioSim のプロジェクトファイルではありません。",
        "proj_err_newer":       "より新しい版の RadioSim で保存されたファイルです（schema {ver} / 対応 {cur}）。RadioSim を更新してください。",
        "proj_err_broken":      "プロジェクトファイルを読めません: {reason}",
        "dlg_select_app_settings": "設定ファイルを選択（アプリ設定のみ）",
        "dlg_success":          "完了",

        # ===== Batch Builder =====
        "batch_window_title":   "複数経路",
        "batch_common_cfg":     " 共通設定 ",
        "batch_case_info":      " 案件情報 ",
        "batch_project_name":   "案件名",
        "batch_memo":           "メモ",
        "col_id":               "ID",
        "col_start":            "送信座標（緯度, 経度）",
        "col_end":              "受信座標（緯度, 経度）",
        "col_h_tx":             "送信高",
        "col_h_rx":             "受信高",
        "col_freq":             "周波数(MHz)",
        "col_gain_tx":          "送信利得(dBi)",
        "col_gain_rx":          "受信利得(dBi)",
        "col_note":             "備考",
        "col_dist":             "水平距離(m)",
        "lbl_b_freq":           "周波数（MHz）",
        "lbl_b_gain_tx":        "送信アンテナ利得（dBi）",
        "lbl_b_gain_rx":        "受信アンテナ利得（dBi）",
        "lbl_b_sens":           "受信感度（dBm）",
        "lbl_b_veg_h":          "植生高（m）",
        "lbl_b_k_factor":       "Kファクター（ライス）",
        "lbl_b_samples":        "サンプル数",
        "lbl_b_rain":           "降雨量（mm/h）",
        "lbl_b_diff_model":     "回折モデル",
        "btn_add_row":          "+ 行を追加",
        "btn_import_csv":       "CSVインポート",
        "btn_export_csv":       "CSVエクスポート",
        "btn_template":         "テンプレート",
        "btn_clear_all":        "全削除",
        "btn_refresh_common":   "↻ ランチャーから更新",
        "hint_common_readonly": "🔒 ランチャーの値",
        "menu_send_to_single":  "→ シングルへ送る",
        "menu_update_rf":       "⟳ RF をランチャーで更新",
        "menu_dup":             "⧉ 複製",
        "menu_del":             "× 削除",
        "batch_starting":       "開始中…",
        "batch_stage_render":   "   レポート生成中…",
        "batch_stage_summary":  "   サマリ生成中…",
        "batch_done":           "完了: {dir}",
        "batch_error_msg":      "エラーが発生しました",
        "dlg_clear_msg":        "全 {n} 行を削除しますか？",
        "dlg_import_confirm":   "既存の {n} 行を置き換えます。\n続けますか？",
        "dlg_import_success":   "{n} 経路をインポートしました。",
        "dlg_import_error":     "インポートエラー",
        "dlg_export_empty":     "エクスポートする経路がありません。",
        "dlg_export_saved":     "保存しました:\n{path}",
        "dlg_export_title":     "エクスポート",
        "dlg_export_error":     "エクスポートエラー",
        "dlg_template_saved":   "テンプレートを保存しました:\n{path}",
        "dlg_validation_error": "入力エラー",
        "dlg_common_cfg_error": "共通設定エラー",
        "dlg_batch_complete_msg": "複数経路の実行が完了しました。\n保存先: {dir}\n\n「全ページを開く」は report_all.html（Ctrl+P で全ページ分の PDF）です。",
        "dlg_batch_error":      "複数経路 エラー",
        "select_batch_csv":     "経路 CSV を選択",

        # ===== Graph =====
        "graph_dist_axis":      "距離 [m]",
        "graph_alt_axis":       "高度 [m]",
        "graph_curve_note":     "地形は等価地球(k={k:.2f})曲率補正込み・実標高ではありません（中央ふくらみ +{bulge:.0f} m・縦倍率 約×{vexag:.0f}）",
        "legend_terrain":       "地形",
        "legend_vegetation":    "植生",
        "legend_los":           "見通し線",
        "legend_fresnel":       "第1フレネルゾーン",
        "slider_htx":           "送信アンテナ高 [m]",
        "slider_hrx":           "受信アンテナ高 [m]",
        "slider_rain":          "降雨量 [mm/h]",
        "btn_save_pkg":         "保存",
        "dlg_not_ready_title":  "未実行",
        "dlg_not_ready_msg":    "シミュレーション未実行です。\n先にシミュレーションを実行してください。",
        "dlg_saved_title":      "保存完了",
        "dlg_saved_msg":        "保存しました:\n{dir}\n\nreport.html を開きますか？",
        "dlg_save_error":       "保存エラー",
        "panel_link_budget":    "リンクバジェット",
        "panel_environment":    "伝搬環境",
        "pl_eirp":              "EIRP",
        "pl_fspl":              "FSPL",
        "pl_diff_loss":         "回折損失",
        "pl_veg_loss":          "植生損失",
        "pl_env_loss":          "環境損失",
        "pl_rain_loss":         "降雨損失",
        "pl_gas_loss":          "大気損失",
        "pl_total_loss":        "総損失",
        "pl_rx_ant_g":          "受信利得",
        "pl_rx_level":          "受信レベル",
        "pl_threshold":         "受信感度",
        "pl_act_margin":        "マージン",
        "pl_status":            "判定",
        "pl_env_type":          "環境区分",
        "pl_diff_model":        "回折モデル",
        "pl_k_factor":          "ライスK（推定）",
        "pl_f1_obs":            "F1遮蔽率",
        "pl_slant_dist":        "斜距離",

        # ===== Env types =====
        "env_urban":            "市街地",
        "env_suburban":         "郊外",
        "env_rural":            "農村",
        "env_los":              "見通し",

        # ===== HTML per-path report =====
        "html_path_title":      "無線回線レポート",
        "html_generated":       "生成日時",
        "html_batch_mode":      "一括シミュレーション",
        "html_single_mode":     "個別シミュレーション",
        "html_report_memo":     "メモ:",
        "html_status":          "判定",
        "html_rx_level":        "受信レベル",
        "html_act_margin":      "マージン",
        "html_total_loss":      "総損失",
        "html_site_info":       "サイト情報",
        "html_tx_coords":       "送信地点座標",
        "html_rx_coords":       "受信地点座標",
        "html_tx_height":       "送信アンテナ高",
        "html_rx_height":       "受信アンテナ高",
        "html_slant_dist":      "斜距離",
        "html_horiz_dist":      "水平距離",
        "html_aim_tx":          "TX指向 (AZ/EL)",
        "html_aim_rx":          "RX指向 (AZ/EL)",
        "html_map_title":       "経路地図",
        "html_map_unavailable": "地図は取得できませんでした（タイル未取得）。",
        # 成果物（PNG / report.html / KML）の生成に失敗した経路の印（I-010）。
        "html_artifact_missing": "成果物なし",
        "html_radio_settings":  "無線設定",
        "html_frequency":       "周波数",
        "html_tx_power":        "送信電力",
        "html_tx_gain":         "送信アンテナ利得",
        "html_rx_gain":         "受信アンテナ利得",
        "html_link_budget":     "リンクバジェット",
        "html_eirp":            "EIRP",
        "html_fspl":            "FSPL",
        "html_diff_loss":       "回折損失",
        "html_veg_loss":        "植生損失",
        "html_env_loss":        "環境損失",
        "html_rain_loss":       "降雨損失",
        "html_gas_loss":        "大気損失",
        "html_rx_ant_gain":     "受信アンテナ利得",
        "html_threshold":       "受信感度",
        "html_environment":     "伝搬環境",
        "html_env_type":        "環境区分",
        "html_diff_model":      "回折モデル",
        "html_rain_rate":       "降雨強度",
        "html_model_deygout":   "Deygout",
        "html_model_single":    "Single",

        # ===== 条件探索（A-1 比較 / A-2 スイープ） =====
        "scn_window_title":     "条件探索",
        "scn_mode":             "条件探索",
        "scn_tab_compare":      "比較（ベース＋条件）",
        "scn_tab_sweep":        "スイープ（1 軸 N 点）",
        "scn_compare_title":    "条件比較レポート",
        "scn_sweep_title":      "パラメータスイープレポート",
        "scn_fixed_group":      " 経路 ",
        "scn_fixed_path":       "固定した経路",   # レポート文中の表記＝据え置き
        "scn_tx_coord":         "送信座標",
        "scn_rx_coord":         "受信座標",
        "scn_samples":          "サンプル数",
        "scn_margin_axis":      "マージン (dB)",
        "scn_base":             "ベース",
        "scn_cond_n":           "条件 {n}",
        "scn_add_cond":         "＋ 条件を追加",
        "scn_del_cond":         "− 条件を削除",
        "scn_result":           "結果",
        "scn_col_label":        "条件 / 値",
        "scn_saved_msg":        "保存しました:\n{dir}",
        "scn_axis":             "軸",
        "scn_from":             "開始",
        "scn_to":               "終了",
        "scn_points":           "点数",
        "scn_delta":            "±",
        "scn_range_how":        "範囲の指定:",
        "scn_range_from_to":    "開始 / 終了",
        "scn_range_delta":      "ベース ±",
        "scn_phase_fetch":      "地形データ取得中...",
        "scn_phase_calc":       "条件を計算中...",
        "scn_phase_render":     "レポート生成中...",
        "scn_err_points":       "点数は 2〜{max} で指定してください。",
        "scn_err_range":        "開始と終了は別の値にしてください。",
        "scn_err_no_result":    "先に実行してください。",
        "scn_err_override":     "上書きできない項目です: {keys}",
        "scn_err_axis":         "スイープできない軸です: {axis}",
        "scn_err_value":        "{label}: {reason}（値: {value}）",
        "scn_axis_freq_mhz":    "周波数",
        "scn_axis_p_tx":        "送信電力",
        "scn_axis_gain_tx":     "送信アンテナ利得",
        "scn_axis_gain_rx":     "受信アンテナ利得",
        "scn_axis_sens":        "受信感度",
        "scn_axis_h_tx":        "送信アンテナ高",
        "scn_axis_h_rx":        "受信アンテナ高",
        "scn_axis_veg_h":       "植生高",
        "scn_axis_k_factor":    "ライスKファクター",
        "scn_axis_rain_rate":   "降雨強度",
        "scn_axis_env_type":    "環境",
        "scn_axis_diff_method": "回折モデル",

        # ===== HTML summary report =====
        "html_batch_title":     "一括シミュレーションレポート",
        # report_all.html（サマリ＋全 per-path を 1 文書へ連結）
        "html_all_title":       "一括レポート（全ページ）",
        "html_all_link":        "全ページをまとめて印刷（report_all.html）",
        "html_total":           "合計",
        "html_ok":              "OK",
        "html_ng":              "NG",
        "html_error":           "エラー",
        "html_col_id":          "ID",
        "html_col_status":      "判定",
        "html_col_freq":        "周波数 (MHz)",
        "html_col_gain_tx":     "送信利得 (dBi)",
        "html_col_gain_rx":     "受信利得 (dBi)",
        "html_col_h_tx":        "送信高 (m)",
        "html_col_h_rx":        "受信高 (m)",
        "html_col_rx":          "受信レベル (dBm)",
        "html_col_margin":      "マージン (dB)",
        "html_col_fspl":        "FSPL (dB)",
        "html_col_diff":        "回折 (dB)",
        "html_col_veg":         "植生 (dB)",
        "html_col_env":         "環境 (dB)",
        "html_col_rain":        "降雨 (dB)",
        "html_col_gas":         "大気 (dB)",
        "html_col_total_loss":  "総損失 (dB)",
        "html_col_slant":       "斜距離 (m)",
        "html_col_f1":          "F1遮蔽 (%)",
        "html_col_note":        "備考",
        "html_col_graph":       "グラフ",

        # ===== Tile Manager =====
        "map_window_title":     "地図",
        "map_mode_label":       "モード:",
        "map_mode_cache":       "キャッシュ管理",
        "map_mode_coords":      "座標入力",
        "map_mode_append":      "複数経路へ連続追加",
        "map_coords_hint_tx":   "座標入力: 地図をクリックして TX（送信点）を指定します。",
        "map_coords_hint_rx":   "座標入力: 地図をクリックして RX（受信点）を指定します。",
        "map_append_hint_tx":   "連続追加: 地図をクリックして TX（送信点）を指定します（RX 指定で1行追加）。",
        "map_append_hint_rx":   "連続追加: 地図をクリックして RX（受信点）を指定すると、複数経路に 1 行追加されます。",
        "map_append_added":     "行を追加しました: {pid}",
        "map_marker_tx":        "TX",
        "map_marker_rx":        "RX",
        "tm_hint":              "Ctrl＋ドラッグ:DL ／ Ctrl+Alt＋ドラッグ:強制再取得 ／ Shift＋Ctrl＋ドラッグ:削除 ／ ドラッグ:地図移動",
        "tm_dl_title":          "ダウンロード",
        "tm_dl_confirm":        "対象 {n} エリアの DEM をダウンロードしますか？",
        "tm_dl_force_title":    "強制再取得",
        "tm_dl_force_confirm":  "対象 {n} エリアの DEM を強制再取得しますか？（キャッシュ無視）",
        "tm_dl_size_hint":      "目安: 約 {mb} MB（精度により変動）",
        "tm_downloading":       "ダウンロード中…",
        "tm_dl_progress":       "ダウンロード中… {done}/{total} ({pct}%)",
        "tm_dl_done":           "完了: 5m航空 {dl5a}, 5m写真 {dl5b}, 10m {dl_dem} DL  /  スキップ {skipped}, 失敗 {failed}",
        "tm_delete_title":      "範囲削除",
        "tm_delete_confirm":    "対象 {n} エリアのキャッシュを削除しますか？この操作は元に戻せません。",
        "tm_delete_done":       "{deleted}枚削除しました。",
        "tm_delete_all_title":  "全キャッシュ削除",
        "tm_delete_all_confirm":"標高(DEM)タイルとレポート地図タイルの両方を含む、全キャッシュタイルを削除しますか？この操作は元に戻せません。",
        "tm_delete_all_done":   "全キャッシュを削除しました: {deleted}枚",
        "tm_stats":             "キャッシュ総量: {count}枚 / {mb} MB",
        "tm_attr_pale":         "出典: 地理院タイル（淡色地図）",
        "tm_attr_photo":        "出典: 地理院タイル（航空写真）",
        "map_layer_label":      "背景",
        "map_layer_pale":       "淡色地図",
        "map_layer_photo":      "航空写真",

        # ===== Terrain fetch =====
        "err_dem_unreachable":  (
            "地形データ（DEM）を取得できませんでした。標高 0m の平坦な地形を"
            "結果として出さないよう、実行を中止しました。\n\n"
            "ネットワーク接続を確認してください。社内ネットワークなどで"
            "プロキシが必要な場合は、設定 > プロキシ設定 で指定してください。"
        ),

        # ===== Multi-hop (relay) =====
        "mh_window_title":      "中継経路",
        "mh_role_tx":           "(送信)",
        "mh_role_rx":           "(受信)",
        "mh_role_relay":        "(中継)",
        "mh_route_id":          "経路 ID",
        "mh_waypoints":         " 地点 ",
        "mh_hops_group":        " 区間ごとの設定 ",
        "mh_result":            " 結果 ",
        "mh_col_no":            "#",
        "mh_col_name":          "名前",
        "mh_col_coord":         "座標（緯度, 経度）",
        "mh_col_height":        "アンテナ高(m)",
        "mh_col_section":       "区間",
        "mh_add_point":         "＋ 地点を追加",
        "mh_del_point":         "− 中継点を削除",
        "mh_from_map":          "地図から選択",
        "mh_hint_order":        "この順に経路をたどります（先頭が送信点・末尾が受信点）。",
        "mh_hint_inherit":      "空欄は共通設定を引き継ぎます。",
        "mh_running":           "区間 {i} / {n} を計算中…",
        "mh_summary":           "全体判定: {status}   {label}: {margin} dB   （{worst}）",
        "mh_err_coord":         "地点 {no}（{name}）: 座標は「緯度, 経度」、アンテナ高は数値で入力してください。",
        "mh_err_no_map":        "このウィンドウからは地図を開けません。",
        "map_mode_waypoints":   "中継点を追加",
        "map_status_waypoint":  "中継点の追加: 地図をクリックすると経路に地点を足します。",
        "mh_report_title":      "中継経路レポート",
        "mh_mode_label":        "中継経路",
        "mh_overall":           "全体判定",
        # 集約カードの語は**判定で変わる**（I-052）。OK は「あと何 dB 積めるか」、
        # NG は「あと何 dB 足りないか」＝問いが別物なので、語のほうを切り替える。
        "mh_overall_margin":    "全体マージン（最小余裕）",
        "mh_overall_shortfall": "最大不足",
        "mh_hops":              "区間数",
        "mh_worst_hop":         "最も苦しい区間",
        "mh_section":           "区間",
        "mh_heights":           "アンテナ高（送 / 受）",
        "mh_regenerative_note": "各区間は独立したリンクバジェットです（再生中継）。区間をまたいで損失は加算せず、全体判定は最も余裕の少ない区間で決まります。受動反射（反射板）は対象外です。",
        "mh_err_too_few":       "中継経路には地点が 2 つ以上必要です（送信点と受信点）。",
        "mh_err_too_many":      "区間が多すぎます（最大 {max}）。",
        "mh_err_rf_count":      "無線設定の数が区間数と合っていません（区間 {hops} / 設定 {rf}）。",
        "mh_err_id_too_long":   "経路 ID「{pid}」が長すぎます（最大 {max} 文字・現在 {n} 文字）。",
        "mh_err_bad_height":    "「{name}」: アンテナ高が数値ではありません。",
        "mh_err_height_range":  "「{name}」: アンテナ高 {val} が範囲外です（0〜500 m）。",

        # ===== Validation (config) =====
        "err_freq":             "周波数は 1〜100000 MHz の範囲で入力してください",
        "err_p_tx":             "送信電力は −30〜60 dBm の範囲で入力してください",
        "err_gain_tx":          "送信アンテナ利得は 0〜60 dBi の範囲で入力してください",
        "err_gain_rx":          "受信アンテナ利得は 0〜60 dBi の範囲で入力してください",
        "err_sens":             "受信感度は −130〜−20 dBm の範囲で入力してください",
        "err_h_tx":             "送信アンテナ高は 0〜500 m の範囲で入力してください",
        "err_h_rx":             "受信アンテナ高は 0〜500 m の範囲で入力してください",
        "err_veg_h":            "植生高は 0〜100 m の範囲で入力してください",
        "err_k_factor":         "ライスKファクターは 0〜30 の範囲で入力してください",
        "err_samples":          "サンプル数は 10〜2000 の範囲で入力してください",
        "err_rain_rate":        "降雨強度は 0〜200 mm/h の範囲で入力してください",
        "err_coord_format":     '"緯度, 経度" の形式で入力してください',
        "err_coord_invalid":    "座標に無効な数値が含まれています",
        "err_lat_range":        "緯度は −85.05〜85.05 の範囲で入力してください（Web Mercator 限界値）",
        "err_lon_range":        "経度は −180〜180 の範囲で入力してください",
        "err_coord_identical":  "開始座標と終了座標が同一です。異なる座標を指定してください。",
        "err_numeric":          "数値を入力してください",
        "err_not_finite":       "有限の数値を入力してください（NaN / Inf は使えません）",
        "err_env_type":         "env_type は次のいずれかです",
        "err_diff_method":      "diff_method は次のいずれかです",
        "err_label_start":      "開始",
        "err_label_end":        "終了",

        # ===== Validation (batch) =====
        "verr_empty":           "経路リストが空です。",
        "verr_duplicate_id":    "ID が重複しています: '{pid}'",
        "verr_invalid_id":      "[{pid}] ID に使用できるのは英数字・'_'・'-' のみです",
        "verr_id_too_long":     "[{pid}] ID が長すぎます（最大 {max} 文字）: {n} 文字",
        "verr_note_too_long":   "[{pid}] 備考が長すぎます（最大 {max} 文字）: {n} 文字",
        "verr_invalid_coord":   "[{pid}] 座標またはアンテナ高に無効な値があります。",
        "verr_tx_lat":          "[{pid}] TX緯度が範囲外: {val}",
        "verr_tx_lon":          "[{pid}] TX経度が範囲外: {val}",
        "verr_rx_lat":          "[{pid}] RX緯度が範囲外: {val}",
        "verr_rx_lon":          "[{pid}] RX経度が範囲外: {val}",
        "verr_identical":       "[{pid}] TX と RX の座標が同一です。",
        "verr_h_tx":            "[{pid}] h_tx が範囲外 (0〜500): {val}",
        "verr_h_rx":            "[{pid}] h_rx が範囲外 (0〜500): {val}",
        "verr_freq":            "[{pid}] 周波数が範囲外 (1〜100000): {val}",
        "verr_gain_tx":         "[{pid}] 送信利得が範囲外 (0〜60): {val}",
        "verr_gain_rx":         "[{pid}] 受信利得が範囲外 (0〜60): {val}",
    },
}

_lang = "en"


def set_lang(lang: str) -> None:
    global _lang
    if lang in _STRINGS:
        _lang = lang


def current_lang() -> str:
    """いま使っている言語コード。

    文字列だけでなく**書体の選択にも効く**（日本語は日本語グリフを持つ書体で
    ないと、Windows のフォントリンクで漢字だけ別書体に落ちる＝B-026）。
    """
    return _lang


def t(key: str) -> str:
    """現在の言語でキーに対応する文字列を返す。未定義の場合は英語フォールバック。"""
    return _STRINGS.get(_lang, _STRINGS["en"]).get(key, _STRINGS["en"].get(key, key))


# ============================================================
# 外部の言語ファイル（利用者が足す訳）
# ============================================================
#: 言語メニューに出す表示名。⚠️ **その言語自身の字で書く**（`Français`）＝選ぶ人は
#: いま読めない言語の画面に居るので、英語名では自分の言語を見つけられない。
_EXTERNAL_NAME_KEY = "_name"
#: **開かないキー**＝成果物（HTML レポート・KML）の語。出力契約なので画面の都合で
#: 動かさない。実測で `html_*` 70 + `pl_*` 18 = 88 キー。
_ARTIFACT_PREFIXES = ("html_", "pl_")
#: 同梱の言語は外部から上書きさせない（`ja.json` を置いて日本語を差し替えられると、
#: 用語集ゲートが守っている画面語彙が黙って外れる）。
_BUILTIN_LANGS = ("en", "ja")

_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


def _placeholders(text: str) -> set:
    """`{dir}` `{n}` のような差し込み名の集合。"""
    return set(_PLACEHOLDER_RE.findall(text))


def validate_external(table: dict) -> tuple:
    """外部の訳 1 本を検査し、`(採用する訳, 却下した (キー, 理由) の列)` を返す。

    **純関数**＝ファイルにもグローバルにも触らない（検査だけを単体で試せる形）。

    🔴 **黙って壊れさせない**（B-025 と同型）＝いちばん危ないのは**差し込みの取り違え**。
    実測で **57 キーが `{…}` を持つ**ので、訳す人が括弧を落とすと `str.format` が
    `KeyError` を投げ、**その画面を開いた瞬間にアプリが止まる**。⇒ 読み込みの時点で
    en と突き合わせ、**合わないキーは採用しない**（そのことは呼び出し側が画面で言う）。
    """
    base = _STRINGS["en"]
    accepted: dict = {}
    rejected: list = []
    for key, value in table.items():
        if key == _EXTERNAL_NAME_KEY:
            continue
        if not isinstance(value, str):
            rejected.append((key, "not_text"))
        elif key not in base:
            rejected.append((key, "unknown"))
        elif key.startswith(_ARTIFACT_PREFIXES):
            rejected.append((key, "artifact"))
        elif _placeholders(value) != _placeholders(base[key]):
            rejected.append((key, "placeholder"))
        else:
            accepted[key] = value
    return accepted, rejected


#: 直近の読み込みの報告＝`(コード, 表示名, 採用数, 却下した (キー, 理由) の列)`。
_external_reports: list = []


def external_reports() -> list:
    """直近の `load_external()` の報告（画面で知らせるのは呼び出し側の仕事）。"""
    return list(_external_reports)


def external_languages() -> list:
    """言語メニューへ足す `(コード, 表示名)`。読み込めた言語だけが並ぶ。"""
    return [(code, name) for code, name, _n, _rej in _external_reports]


def load_external(dir_path: str) -> list:
    """`<dir>/<コード>.json` を読み、**使える訳だけ**登録する。報告を返す。

    ⚠️ **1 本が壊れていても他は読む**＝ここで例外を投げると、訳を 1 つ置き損ねた
    利用者がアプリごと起動できなくなる。読めなかったファイルは報告に載せて次へ進む。
    """
    global _external_reports
    _external_reports = []
    if not os.path.isdir(dir_path):
        return []
    for name in sorted(os.listdir(dir_path)):
        if not name.endswith(".json"):
            continue
        code = name[:-len(".json")]
        if code in _BUILTIN_LANGS or not code:
            _external_reports.append((code, code, 0, [("", "builtin")]))
            continue
        try:
            with open(os.path.join(dir_path, name), encoding="utf-8") as f:
                table = json.load(f)
            if not isinstance(table, dict):
                raise ValueError("top level is not an object")
        # ⚠️ 例外の種類を絞らない＝JSON の壊れ方は無数にあるので、先に列挙するより
        # 「読めなければ理由を持って次へ」が正しい（理由は報告に載り画面へ出る）。
        except Exception as ex:  # noqa: BLE001
            _external_reports.append((code, code, 0, [("", f"unreadable: {ex}")]))
            continue
        accepted, rejected = validate_external(table)
        shown = table.get(_EXTERNAL_NAME_KEY)
        _STRINGS[code] = accepted
        _external_reports.append(
            (code, shown if isinstance(shown, str) and shown else code,
             len(accepted), rejected))
    return external_reports()
