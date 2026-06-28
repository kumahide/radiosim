# CHANGELOG — RadioSim Pro

形式: [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 準拠

---

## [Unreleased]

### 修正
- **バッチHTMLレポートの地形グラフが日本語で文字化け（豆腐化）**: 日本語モードで個別グラフを開かずにバッチを実行すると、レポート PNG の軸ラベル・凡例が□で表示される不具合を修正。日本語フォント適用が `views/graph.py` の個別グラフ表示時にしか行われず、その rcParams 設定がプロセスに残ることに依存していたため、操作順によって発生していた。フォント適用を**ヘッドレスな共通ヘルパ `mpl_fonts.py` に単一ソース化**し、バッチのレンダリング（`batch.save_profile_png`）でも明示的に適用するようにした（操作順非依存）。`views/graph.py` は同ヘルパへ委譲。

---

## [2.2] — 2026-06-20

正式リリース。RC1〜RC3 のエンドユーザー検証（GitHub Issues #9〜#12）とバイナリ実機確認を経て正式版とした。マップウィンドウ（地図クリック座標入力／DEM キャッシュ可視化）・HTML レポートへの経路地図添付・起動高速化が 2.2 の主な追加（詳細は下記 RC1〜RC3）。

### 変更
- `APP_VERSION` を `"2.2RC3"` → `"2.2"` に変更。

### ドキュメント
- **リリース前にソース全体とドキュメント群の記載内容を突き合わせて整合を確認**（バージョン文字列だけでなく内容を監査）。以下の実装との乖離を修正:
  - 依存ライブラリに `tkintermapview`（マップウィンドウ用）が未記載だったのを README 群の `pip install` 行・依存表へ追記（`requirements.txt` とも一致）。
  - 開発者向け README のテスト件数が古かった（226 件）のを実数 296 件へ更新し、欠落していた `test_report_map.py` / `test_map_window.py` の行とファイル構成の記載を追加。
  - アーキテクチャのレイヤー構成図に 2.2 の新モジュール（`views/map_window.py` / `views/dialogs.py` / `report_map.py` / `map_graphics.py`）が未反映だったのを、各層（表示／オーケストレーター／純粋描画）へ追記。
  - **再発防止**: ドキュメントのドリフトを自動検出する `tests/test_docs_consistency.py` を追加。コードから生成した正準リスト（モジュール／テストファイル／依存）に対し、README の各構造セクション（ファイル構成ツリー・アーキテクチャ層構成図・テスト表・`pip install` 行）を**個別に**照合する。今回見落とした「図にだけ無い」型のセクション固有ドリフトを CI で捕捉できる。

---

## [2.2RC3] — 2026-06-19

2.2 リリース候補。RC2 のエンドユーザー検証（GitHub Issues #9〜#12）で見つかった不具合・表記の不整合を修正した。

### 修正
- **キャッシュ総量表示の同期**（#9）: マップウィンドウのキャッシュ管理モードを開いたまま個別シミュレーションを実行すると、プリフェッチで増えたタイルがキャッシュ総量・カバレッジ表示に反映されない不具合を修正。
- **HTML レポートの地形グラフが英語のまま**（#12）: 日本語モードでレポートの地形断面 PNG の軸ラベル・凡例（距離／高度／地形／植生／見通し線／第1フレネルゾーン）が英語表記だったのを表示言語に追従するよう修正。
- **地図を開いたままアプリ本体を閉じると終了時にエラー**: マップウィンドウを開いた状態でランチャーを閉じると、コンソールに `invalid command name ...update_canvas_tile_images` が出る不具合を修正。地図の after ループを止めてから破棄する手順を 1 つの関数（`close_map_safely`）に集約し、マップウィンドウの×・アプリ終了の双方が同手順を通るようにした（破棄経路ごとの手順コピーによる再発を防止）。

### 変更
- **README の精度レイヤ説明を訂正**（#10）: 緑のカバレッジ層は「航空写真由来」ではなく航空レーザ測量由来（dem5a）。全 README の表記を修正。
- **日本語 README の環境区分表記を日本語化**（#11）: 環境区分名を 市街地／郊外／農村／見通し に統一（幾何の「見通し線（LoS）」は線そのものの略号として維持）。

---

## [2.2RC2] — 2026-06-18

2.2 リリース候補（エンドユーザー検証用プレリリース）。RC1 の実機検証で見つかった、エンドユーザーに見える表記・挙動の不整合を修正した。

### 変更
- **環境区分（環境タイプ）の表記を言語連動で統一**: 地形ウィンドウとランチャー／バッチのドロップダウンが英語（Urban / LoS）、HTML レポートだけ日本語という不整合を解消し、すべて表示言語に追従するようにした（内部値は不変のため既存設定・CSV と互換）。
- **タイルキャッシュの範囲削除は標高（DEM）タイルのみを対象に**: レポート用の淡色地図タイルは範囲削除では消さず、「全キャッシュ削除」でのみ消去する。全削除の確認ダイアログに、標高タイルとレポート地図タイルの両方が消える旨を明記した。

### 修正
- 地形ウィンドウを開いたままランチャーを閉じると、アプリのプロセスが終了しない不具合を修正。

---

## [2.2RC1] — 2026-06-16

2.2 リリース候補（エンドユーザー検証用プレリリース）。座標入力モードの仕上げ、シミュレーションレポートへの経路地図添付、起動の高速化を行った。

### 追加
- **シミュレーションレポートへの経路地図添付**（`report_map.py` / `map_graphics.py`）: 個別シミュレーションの `report.html` に、TX/RX・経路・距離を国土地理院 淡色地図へ重ねた静的地図を埋め込む。
  - 経路が常に水平（TX=左 / RX=右）になるよう自動回転し、方角の手がかりに北矢印を重ねる。
  - 出力サイズは地形断面図と同じ縦横比に揃え、レポート上で両者の高さが一致する。
  - タイルは経路にフィットする範囲のみ取得（外部サーバー配慮）。取得できないときは地図を省いて注記を残す（レポート自体は必ず生成）。
- **マップウィンドウの初期ズーム自動調整**: 開いたとき、設定中の TX/RX の経路長に合わせてズームを自動設定する。
- **グラフの縦倍率注記**: 長距離プロファイルで地形が湾曲して見える誤解を避けるため、曲率注記に「縦倍率 約×N」を併記する。

### 変更
- **マップウィンドウの既定モードを「座標入力」に**。モードセレクタの並びも座標入力を左（主機能）にした。
- **バッチウィンドウのダイアログを親ウィンドウ中央に表示**（`views/dialogs.py` に統一）。
- **起動の高速化**: 同梱ファイル数を削減（未使用の Tcl データ等を除外、1273→約495ファイル）し、未署名 exe に対するウイルススキャンの起動時負荷を軽減。matplotlib を遅延 import 化。
- レポート地図の地名・等高線ラベルを 2 倍スーパーサンプルで鮮明化（追加のタイル取得なし）。

### 修正
- 座標入力モードで地図左上のズーム +/- ボタンを押すと、その位置が座標として誤登録される不具合を修正。
- マップウィンドウを閉じる/アプリ終了時に `invalid command name "...update_canvas_tile_images"` の Tcl エラーが出る不具合を修正（地図の after ループを停止してから破棄）。

---

## [2.2b2] — 2026-06-13

`feature/map-window` / `feature/map-coords`。タイルキャッシュ管理ウィンドウを「マップウィンドウ」に発展させ、地図クリックで座標を指定する座標入力モードを追加。

### 追加
- **マップウィンドウ**（`views/map_window.py`、旧 `views/tile_manager.py`）: ランチャーのユーティリティボタン「マップウィンドウ」から起動。上部のモードセレクタ（セグメントボタン）でモードを切り替える。
  - **キャッシュ管理モード**: 従来のタイルキャッシュ管理機能（自動カバレッジ表示・ジェスチャ DL/削除）。
  - **座標入力モード**: 地図を素クリックすると TX→RX を交互に指定し、ランチャーの座標欄（start/end）へ自動で書き戻す（数値欄が常に source of truth）。ウィンドウを開いた時点で既存座標を読み込み、マーカー表示＋センタリングする。
    - **UISP 風マーカー**: 半透明シアンのハロー＋ノード（TX=塗り / RX=白抜き）。
    - **経路ライン＋水平距離ラベル**: TX/RX を結ぶ細線（シアン）と、中点に半透明背景つきの距離ラベルを重ねて表示（パン/ズーム追従）。
- **「アプリ設定を読込む」メニュー**: 設定ファイルから app 設定（テーマ/言語/プロキシ）のみ取り込む。シミュレーション条件は変えない。
- `models.horizontal_distance_km()`: 2地点間の水平距離（haversine）を返す純関数。距離ラベルと地形プロファイル計算で共有。

### 変更
- **マップは各モードの関心レイヤだけを表示**: キャッシュ管理モード＝カバレッジ塗りのみ／座標入力モード＝経路レイヤ（マーカー/線/距離ラベル）のみ。モードを往復しても座標は保持される。
- **設定の app/sim 分離**: `save_app`/`save_sim`・`select_app`/`select_sim` でキー群を分離（ファイル形式は不変）。「パラメータ読込」は sim 限定、「アプリ設定読込」は app 限定で、互いの設定を上書きしない。
- タイルキャッシュ管理の入口を設定メニューからマップウィンドウへ移行（全キャッシュ削除は設定メニューに残置）。
- 確認ダイアログを親ウィンドウ中央に表示する共通ヘルパー `views/dialogs.py` に集約。
- `APP_VERSION` を `"2.1"` → `"2.2b2"` に変更。

---

## [2.1] — 2026-06-12

`feature/tile-manager`。DEM タイルキャッシュを地図上で可視化・管理する機能を追加。

### 追加
- **タイルキャッシュ管理ウィンドウ**（`views/tile_manager.py`）: 設定メニュー →「タイルキャッシュ管理」から起動。GSI 淡色地図上で DEM キャッシュを操作する。
  - **自動カバレッジ表示**: 地図のパン/ズームに追従し、キャッシュ済み領域を半透明塗り＋外周線で常時表示。クアッドツリーで集約（埋まったブロックは粗く、エッジは細かく zoom-14 まで）。最高精度レベルで色分け（5a=緑 / 5b=黄 / dem=水色）。
  - **全ジェスチャ操作**: Ctrl＋ドラッグ=ダウンロード / Ctrl+Alt＋ドラッグ=強制再取得 / Shift+Ctrl＋ドラッグ=範囲削除 / 素のドラッグ=地図パン。DL・削除は確認ダイアログ（新規エリア数・容量目安／実キャッシュ数を提示）。
  - **下部ステータスバー**: 動的メッセージ（アイドル時は操作ヒント、操作中・直後は状態/結果を表示し一定時間後にヒントへ復帰）＋キャッシュ総量＋細線プログレス（アイドル時は高さ予約のみで非表示）。出典表記は地図右下にオーバーレイ。
  - **全キャッシュ削除**: 設定メニュー →「全キャッシュ削除」（中央配置の確認ダイアログ）。
- `infrastructure.py` に公開 API を追加: `prefetch_tiles` / `scan_cache_overlay` / `coverage_outline` / `count_bbox_tiles` / `count_cached_areas` / `delete_tile_cache` / `delete_all_tile_cache` / `get_cache_stats` / `tile_to_latlng` ほか。

### 変更
- **DEM 欠損(128,0,0)とプリフェッチの整合（B-1 方式）**: 実行時の `get_elevation` がピクセル単位で欠損を下位レイヤーへフォールバックするのに対し、旧プリフェッチはタイル単位で打ち切っていた不整合を解消。`_process_position` を欠損ピクセル認識型の精密降下に変更し、欠損が残る限り下位レイヤー（5a→5b→dem_png）まで取得。dem_png（最下層）の存在を終端マーカーとする。
- **`build.bat` を非対話化**: 末尾の `explorer dist\RadioSimPro` と `pause` を削除（自動/バックグラウンドビルドのハング・ウィンドウポップを防止）。
- `APP_VERSION` を `"2.0"` → `"2.1"` に変更。

### 修正
- 長距離地形プロファイルにおける等価地球曲率の扱いを明確化（注記）。

---

## [2.0] — 2026-06-09

### 変更
- `APP_VERSION` を `"2.0RC3"` → `"2.0"` に変更（正式リリース）
- プロキシ環境での SSL 動作確認済み（`truststore` + Windows 証明書ストア）
- `dist/RadioSimPro/RadioSimPro.exe` 動作確認・ファイルプロパティ確認済み

---

## [2.0RC3] — 2026-06-06

### 追加
- DEMタイル事前取得（`prefetch_tiles`）: シミュレーション実行前に bbox 内の全タイルを並列プリフェッチ。オフライン利用に対応。
- 企業プロキシ環境対応: `truststore.inject_into_ssl()` を `main.py` 先頭で呼び出し、Windows 証明書ストアの CA を `requests` に反映。`radiosim.spec` の `hiddenimports` に `truststore` 系を追加。

### 変更
- バッチ CSV のパス別設定（`env_type` / `rain_rate` / `diff_method`）を廃止し、Common Settings に一本化。`PathRow` フィールドと `export_csv()` ヘッダーを整理。
- `infrastructure.py` に `_enumerate_bbox` / `_download_tile_set` / `count_bbox_tiles` を追加（次期タイル管理メニュー向け流用可能 API）。

### 変更（2026-06-08 追記）
- **k_factor 命名の曖昧さを解消**: 同一ファイル内で「等価地球半径係数」と「ライスKファクター」が同じ名前 `k_factor` を共有していた問題を修正。`TerrainProfile.k_factor` および `calculate_terrain_profile` の引数を `earth_k`（等価地球半径係数・4/3 固定）に、`calculate_propagation` の引数を `initial_k`（入力ライスK）に改名。`PropagationResult` / `LinkBudgetResult` の `current_k` フィールドコメントを「推定ライスKファクター（表示専用、計算不使用）」に更新。
- **i18n ラベルのライスK表記を統一**: バッチ入力欄 `lbl_b_k_factor` を "K係数" / "K-Factor" から "Kファクター（ライス）" / "Rician K-Factor" に変更。グラフパネル・HTML レポートの `pl_k_factor` / `html_k_factor` を "Kファクター" / "K-Factor" から "ライスK（推定）" / "K-Factor (est.)" に変更。
- **`_NU_THRESHOLD` コメント修正**: 「見通しとみなす」という曖昧な表現を「回折損 0 dB として打ち切る（ITU-R P.526 の見通し判定相当）」に変更し、コードの動作を直接説明するよう修正。

### 修正（2026-06-08 追記）
- **プロキシ設定後にキャンセルすると次回起動時に設定が消える**: `_on_run` で `save_config(c)` に渡す dict に `proxy_url` が含まれておらず、シミュレーション実行のたびに config ファイルから `proxy_url` が除去されていた。`proxy_url` を `self.config` からコピーするよう修正。
- **プロキシ未設定で実行後にプロキシを設定しても地形が 0m のまま**: `set_proxy()` でセッションをリセットしても `_failed_tiles`（タイル取得失敗セット）がクリアされないため、失敗したタイルが再取得されなかった。また `_terrain_cache`（地形プロファイルキャッシュ）に 0m で保存された結果が残り続けた。`set_proxy()` 内で `_failed_tiles.clear()` を追加し、プロキシダイアログの OK 時に `sim.clear_terrain_cache()` を呼ぶよう修正。

### 修正（2026-06-06 追記）
- **連続シミュレーション時のプログレスバー表示崩れ**: `_download_tile_set` の `_worker` で `work_q.task_done()` を `progress_cb` より先に呼んでいたため、`work_q.join()` が最後のコールバック発火前に返る競合があった。`task_done()` を `progress_cb` の後に移動し、Phase 1 の全 `root.after()` が確実にキューに積まれてから Phase 2 が開始されるよう修正。
- **`radiosim.spec` の truststore hiddenimports 更新**: truststore がモジュール名を変更（`_api_windows` → `_api`、`_stdlib_ssl` → `_windows`）。旧名のまま残っていたためビルドログに ERROR が出ていた。現行名に修正し `_ssl_constants` を追加。
- **バッチ Common Settings ラベルの残留記述を削除**: `batch_common_cfg` に「経路別設定が優先」と記載されていたが、当該機能（PathRow の env_type / rain_rate / diff_method）は廃止済みのため除去。

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
