# CHANGELOG — RadioSim Pro

形式: [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 準拠

---

## [Unreleased] — 2.0 正式リリースへの残作業

- プロキシ環境での SSL 動作確認（truststore）
- `version.py` の `APP_VERSION` を `"2.0RC3"` → `"2.0"` に変更
- `build.bat` による再ビルド・配布物確認

---

## [2.0RC3] — 2026-06-06

### 追加
- DEMタイル事前取得（`prefetch_tiles`）: シミュレーション実行前に bbox 内の全タイルを並列プリフェッチ。オフライン利用に対応。
- 企業プロキシ環境対応: `truststore.inject_into_ssl()` を `main.py` 先頭で呼び出し、Windows 証明書ストアの CA を `requests` に反映。`radiosim.spec` の `hiddenimports` に `truststore` 系を追加。

### 変更
- バッチ CSV のパス別設定（`env_type` / `rain_rate` / `diff_method`）を廃止し、Common Settings に一本化。`PathRow` フィールドと `export_csv()` ヘッダーを整理。
- `infrastructure.py` に `_enumerate_bbox` / `_download_tile_set` / `count_bbox_tiles` を追加（次期タイル管理メニュー向け流用可能 API）。

### 修正（2026-06-06）
- **Kファクターの概念混在を解消**: ランチャーの `k_factor` はライスKファクター（見通し/散乱電力比、表示専用）であり、リンクバジェット計算には影響しない。等価地球半径係数は内部で 4/3（標準大気）固定に変更。`graph.py` / `batch.py` の `calculate_terrain_profile` 呼び出しから `k_factor=params.k_factor` を削除。
- **`DEFAULT_CONFIG["k_factor"]` のデフォルト値修正**: 誤って設定されていた `"1.333"`（等価地球半径係数の値）を `"10.0"`（ライスKとして適切な初期値）に修正。
- **`i18n.py` ラベル修正**: ランチャーの k_factor ラベル「等価地球半径係数（K）」→「初期Kファクター（ライス）」、英語も同様に修正。ツールチップも Rician K-factor の説明に変更。
- **`_get_session()` の User-Agent をセッションレベルに移動**: `_fetch_tile` のリクエスト単位ではなく、セッション生成時に `headers.update()` で設定するよう変更。将来のエンドポイント追加でもヘッダー漏れが発生しない。

---

## [2.0RC2] — 2026-05

### 追加
- **ヘルプメニュー**: README を markdown → HTML → ブラウザ で表示（フォールバック付き）。バージョン情報ダイアログ。
- **スライダー + TextBox**: グラフウィンドウにアンテナ高・降雨強度のスライダーと連動する数値直接入力欄を実装。
- **Windowsアプリ体裁**: Per-Monitor DPI Aware・AppUserModelID・EXE ファイルプロパティ（`version.py` から自動生成）・全ウィンドウ共通アイコン（`icon.png` → `icon.ico`）。
- **ロゴ埋め込み**: バイナリ版 README に `logo.png` を base64 インライン埋め込み。

### 変更
- `bind_all("<MouseWheel>")` を `batch_builder` の Toplevel `<Destroy>` で `unbind_all` するよう修正（他ウィンドウへのスクロール漏れを防止）。
- ランチャーのボタンレイアウトを grid で横並びに整理（個別/一括: `Accent.TButton`、ユーティリティ: 下段）。

### 修正
- `_vegetation_loss` の第一引数に `veg_top`（`elevs + veg_h`）を正しく渡すよう修正（`elevs` 直渡しバグ）。
- `_env_loss` に `veg_loss` を渡さないよう修正（Veg Loss と Env Loss の二重計上を解消）。
- `calculate_terrain_profile` に `k_factor` を渡し忘れていた呼び出し箇所を修正。
- `load_config` を `dict.update()` から `DEFAULT_CONFIG` キー選択的上書きに変更（不正キー混入防止）。

---

## [2.0RC1] — 2026-04

### 追加
- **一括シミュレーション**: `batch.py` + `views/batch_builder.py`。CSV 入出力・KML/HTML レポート（`summary.html` / `summary.kml`）。
- **多言語対応**: `i18n.py`（en/ja）。メニューで切替・再起動で反映。
- **テーマ切替**: sv-ttk + darkdetect。システム追従 / ライト / ダークを切替可能。
- **個別シミュレーション HTML/KML 出力**: `report.html` / `path.kml` を保存パッケージに追加。
- **PyInstaller ビルド**: `radiosim.spec`（onedir モード）。未使用 matplotlib バックエンド 19 個・不要 stdlib モジュール・numpy test 系を除外。`README_binary_*.md` / `logo.png` をバイナリに同梱。
- **DEMマルチレイヤ**: dem5a_png → dem5b_png → dem_png の優先順で自動フォールバック（dem1a_png は除外）。
- **回折モデル選択**: Deygout（デフォルト）/ Epstein-Peterson / Bullington を選択可能。
- **DEMキャッシュ**: タイルキャッシュキーに `layer_id` を含む 3 要素タプル `(layer_id, xtile, ytile)` で dem5a/dem5b の衝突を防止。
- **並列 DEM 取得**: `daemon=True Thread + Semaphore(8)` による並列化（ThreadPoolExecutor は tkinter と競合するため使用禁止）。

---

## [2.0b2] — 2026-05

### 追加
- **テーマ切替**: `main.py` に `_ThemeManager` クラスを追加。sv-ttk + darkdetect を導入。system / light / dark の 3 モード切り替え、OS の明暗変化を自動検知してリアルタイム追従。
- **テーマメニュー**: `views/launcher.py` のメニューバーに Theme → System / Light / Dark ラジオボタンを追加。
- `DEFAULT_CONFIG` に `"theme": "system"` を追加。

### 変更
- 主要ウィジェットを `tk.*` から `ttk.*` に置き換え（sv-ttk テーマ適用のため）。
- **DEMマルチレイヤ取得**: `infrastructure.py` を単一 dem_png から 3 レイヤー優先順位へ刷新。
  - `DEM_LAYERS = [("dem5a_png", zoom=15), ("dem5b_png", zoom=15), ("dem_png", zoom=14)]`（5m航空→5m写真→10m の順でフォールバック）。
  - キャッシュキーを `(xtile, ytile)` → `(layer_id, xtile, ytile)` 3 要素タプルに変更（dem5a/dem5b の衝突を防止）。
  - `_failed_tiles: set[tuple]` を追加し、取得失敗タイルへの再リクエストを防止。
  - ロック外でネットワーク取得 → ロック内でキャッシュ書き込みの構造に変更（ロック保持中のブロッキング通信を廃止）。
- `load_config()` を `config.update()` から `DEFAULT_CONFIG` キー選択的上書き方式に変更（未知キーを無視、欠損キーはデフォルト維持）。

---

## [2.0b1] — 2026-05-31

### 追加
- `version.py` を新規追加（`APP_NAME` / `APP_VERSION` / `COPYRIGHT` / `USER_AGENT` の一元管理）。
- **一括シミュレーション**: `batch.py` を新規追加。
  - `PathRow` / `PathResult` データクラス。
  - `parse_csv()` / `export_csv()` で CSV 入出力。
  - `validate_rows()` でバッチ全体のバリデーション（ID 重複・座標範囲・同一点チェック）。
  - `run_batch()` でバッチをバックグラウンドスレッドで順次実行。
  - `save_path_html()`: per-path の report.html（グラフ Base64 埋め込み）を生成。
  - `save_summary_csv()` / `save_summary_html()` / `save_summary_kml()` でバッチサマリ出力。
  - `save_path_kml()` で Google Earth 用 KML（TX/RX placemark・地形・LoS・第1フレネルゾーン・遮蔽区間）を生成。
- **Batch Builder ウィンドウ**: `views/batch_builder.py` を新規追加。GUI テーブル直接入力と CSV インポートの 2 入力方式。行ドラッグ & ドロップ並び替え。`queue.Queue + root.after` ポーリングでスレッドセーフな UI 更新。
- **PyInstaller ビルド**: `build.bat` / `radiosim.spec` / `logo.png` を追加。onedir モード、未使用バックエンド等を除外。
- ランチャーにロゴ表示（`logo.png`）とフィールドホバーツールチップ（`_Tooltip` / `_FIELD_TIPS`）を追加。
- ランチャーに BATCH MODE ボタンを追加し `BatchBuilderWindow` を起動。
- プログレスバーに % 表示（"Fetching terrain… 42%"）を追加。
- `models.py`: `fresnel_zone_radii()` を独立関数として公開。`calculate_terrain_profile()` に `k_factor` 引数を追加し曲率補正に反映。`ENV_COEFFS` から `veg_coeff` 列を削除（Veg Loss と Env Loss を独立加算する設計に統一）。

### 変更
- `infrastructure.py`: `User-Agent` を `version.USER_AGENT` に統一。`_VALID_ENV_TYPES` / `_VALID_DIFF_METHODS` を frozenset で定義し `validate_config()` で参照（batch.py からも利用可能に）。

---

## [1.5] — 2026-05

### 追加
- **並列 DEM 取得**: `simulation.py` に `Semaphore(8)` + `daemon=True Thread` による並列フェッチを実装。完了順でなく座標順に整列して返す。`threading.Event` で全スレッド完了を同期。
- **地形キャッシュ**: `fetch_elevations_cached()` を追加。キャッシュキー `(lat_tx, lon_tx, lat_rx, lon_rx, num)` でヒット時は DEM 再取得なし。`_terrain_cache_lock` で保護。
- `README_ja.md` / `README_en.md` を追加（最初の開発者向け README）。

### 変更
- `views/launcher.py`: `fetch_elevations_cached()` を呼び出すよう変更。`rain_rate` / `diff_method` を `c.setdefault()` で config から補完する処理を追加。

---

## [1.5b4] — 2026-05-23

### 変更
- `infrastructure.py` の `DEFAULT_CONFIG` に `"rain_rate": "0.0"` / `"diff_method": "deygout"` を追加。
- `VALIDATION_RULES` に `rain_rate` の範囲チェック（0〜200 mm/h）を追加。

---

## [1.5b3] — 2026-05

### 追加
- **降雨減衰**: `calculate_rain_loss(freq_mhz, rain_rate_mmh, slant_dist_km)` を追加（ITU-R P.838-3 準拠）。
- **大気減衰**: `calculate_gas_loss(freq_mhz, slant_dist_km)` を追加（ITU-R P.676-13 Annex 2 準拠）。
- `PropagationResult` / `LinkBudgetResult` に `rain_loss` / `gas_loss` フィールドを追加。
- `calculate_propagation()` / `run_calculation()` に `rain_rate` 引数を追加。
- 保存 report.txt に `Rain Loss` / `Gas Loss` / `Diff Model` / `Env Type` を追加。
- グラフウィンドウに Rain Rate スライダー（0〜100 mm/h）を追加。
- グラフウィンドウ右パネルに回折モデル切り替えボタン（Deygout / Single）を追加。

---

## [1.5b2] — 2026-05

### 追加
- **環境区分 (`env_type`)**: `ENV_COEFFS` テーブル（urban / suburban / rural / los 等 7 パラメータ係数）を追加。
- `_env_loss()` を環境区分ごとの係数テーブル参照方式に刷新（旧は固定係数）。
- `DEFAULT_CONFIG` に `"env_type": "los"` を追加。`validate_config()` に `env_type` バリデーションを追加。
- ランチャーに Env Type コンボボックスを追加。LOAD SETTINGS で `env_type` を復元する処理を追加。

---

## [1.5b1] — 2026-05

### 追加
- **Deygout 多重回折**: `_deygout_loss()` 再帰関数を追加。主障害物を再帰的に分割し損失を加算。再帰打ち切り条件: ν < -0.8 または区間幅 < 50 m、深さ上限 20。
- `_nu()` ヘルパー関数（Fresnel-Kirchhoff パラメータ ν の計算）を追加。
- `_diffraction_loss_fk(v)` として FK 損失式を独立関数に分離。
- `calculate_propagation()` に `diff_method` 引数（"single" | "deygout"）を追加。デフォルト: "deygout"。

---

## [1.0] — 2026-05

### 追加
- **マルチモジュール分離**: 単一スクリプトから以下のモジュール構成に完全移行。
  - `infrastructure.py`（通信・キャッシュ・設定・バリデーション）
  - `models.py`（副作用ゼロの純粋計算。numpy/math のみ）
  - `simulation.py`（DEM 取得・計算・保存の統括）
  - `views/launcher.py` / `views/graph.py`（UI）
  - `main.py`（エントリーポイント）
- `models.py` にデータクラスを導入（`TerrainProfile` / `PropagationResult` / `LinkBudgetResult`）。
- TX/RX アンテナゲインを個別化（`gain_tx` / `gain_rx`）。旧来は `gain * 2` で共有。
- EIRP = P_tx + G_tx、P_rx = EIRP + G_rx - total_loss の式に統一。
- `actual_margin = P_rx - Sensitivity` を計算・表示。
- `_env_loss()` 関数（経験的環境損失: 3〜30 dB）を追加し合計損失に組み込み。
- 植生減衰の周波数帯域別係数（< 1 GHz / 1〜6 GHz / > 6 GHz）を導入。
- `VALIDATION_RULES` 辞書による入力検証、TX/RX 同一点チェックを追加。
- `setup_logging()` で ファイル＋コンソール二重出力のログを統合。
- DEM タイル取得を `_fetch_tile()` に分離し、無効値ピクセル `(128, 0, 0)` を 0.0 に変換。
- 保存レポートに `EIRP` / `Act Margin` / `Env Loss` を追加。
- テストディレクトリ（`tests/`）を追加。

### 変更
- Fresnel 第1ゾーン半径の分母を `d1 + d2` に修正（旧: `horiz_dist_km * 1000`）。

---

## [プロトタイプ期] — 2026-05-10 〜 2026-05-12

単一スクリプト構成で段階的に機能を追加した開発初期。ファイル名にバージョンが埋め込まれていた。

- `radio_sim.py`: 最初期。requests + PIL で国土地理院 DEM PNG を取得し地形プロファイルを描画。自由空間損失・回折損なし。
- `radio_sim_gui.py`: tkinter 入力フォームを追加。地球曲率補正（Ke=4/3）・Fresnel 第1ゾーン描画・回折損（簡易 FK 近似）・FSPL 計算・P_rx 表示を初実装。
- `antenna_slider_sim_v5〜v8`: matplotlib スライダー（TX/RX アンテナ高）によるリアルタイム更新、`config.json` でのパラメータ永続化、tkinter ランチャー + matplotlib グラフの 2 ウィンドウ構成、`blocked_ratio > 100%` 時の回折補正を実装。
- `radio_sim_ultimate_Rel1.0〜Rel1.3`: ディスクキャッシュ・植生レイヤー・K ファクター・受信感度・SAVE PACKAGE・別スレッドでの DEM 取得（`threading.Thread`）・進捗バーを追加。数値安定化（`np.nan_to_num`）・デバウンス（50ms）・キャッシュ排他制御（`threading.Lock`）を段階的に改善。著作権表示 "© 2026 BearValley Corp." を追加。
