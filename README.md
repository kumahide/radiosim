# radiosim

`radiosim` は、2地点間の地形断面と無線リンクバジェットを可視化する
Python/Tkinter アプリケーションです。

国土地理院の DEM PNG タイルから標高を取得し、地球曲率補正、Fresnel 第1ゾーン、
回折損、植生減衰、環境損失、降雨減衰、大気減衰を含めて受信レベルとマージンを
計算します。

## 主な機能

- 緯度経度で指定した TX/RX 間の地形プロファイル取得
- 地球曲率補正つきの断面グラフ表示
- Fresnel 第1ゾーン、LoS、アンテナ高の可視化
- 単一障害物モデル / Deygout 法による回折損計算
- Urban / Suburban / Rural / LoS の環境区分
- 降雨率スライダーによる降雨減衰の確認
- TX/RX アンテナ高スライダーによるリアルタイム再計算
- 結果パッケージの保存

## 必要環境

- Python 3.10 以上
- Tkinter が利用できる Python 環境
- インターネット接続
  - 初回または未キャッシュ地点の標高タイル取得に必要です。

Python パッケージ:

```powershell
pip install numpy requests Pillow matplotlib
```

開発・テスト用:

```powershell
pip install pytest coverage
```

## 実行方法

リポジトリ直下で実行します。

```powershell
python main.py
```

起動すると入力フォームが表示されます。地点、無線設定、環境条件を入力して
`RUN SIMULATION` を押すと標高データを取得し、結果グラフを開きます。

## 入力項目

### Site Info

- `Start Coords (Lat, Lon)`: 送信点の緯度・経度
- `End Coords (Lat, Lon)`: 受信点の緯度・経度
- `TX Antenna Height (m)`: 送信アンテナ高
- `RX Antenna Height (m)`: 受信アンテナ高

### Radio Settings

- `Frequency (MHz)`: 周波数
- `TX Power (dBm)`: 送信電力
- `TX Antenna Gain (dBi)`: 送信アンテナ利得
- `RX Antenna Gain (dBi)`: 受信アンテナ利得
- `Sensitivity (dBm)`: 受信感度

### Environment

- `Env Type`: 環境区分
  - `Urban`
  - `Suburban`
  - `Rural`
  - `LoS`
- `Vegetation Height (m)`: 植生高
- `Initial K-Factor (dB)`: 初期 K ファクタ
- `Sampling Points`: 地形サンプリング点数

## グラフ画面

グラフ画面では以下を確認できます。

- 地形断面
- 植生層
- LoS
- Fresnel 第1ゾーン
- リンクバジェット
- 受信レベル、受信感度、実マージン
- 回折損、植生減衰、環境損失、降雨減衰、大気減衰

操作:

- `TX Height [m]`: 送信アンテナ高を変更
- `RX Height [m]`: 受信アンテナ高を変更
- `Rain Rate [mm/h]`: 降雨率を変更
- `Diff: ...`: 回折モデルを `Deygout` / `Single` で切り替え
- `SAVE PACKAGE`: 現在の結果を保存

## 出力ファイル

`SAVE PACKAGE` を押すと、`results/YYYYMMDD_HHMMSS/` に以下を保存します。

- `profile.png`: グラフ画像
- `settings.json`: 実行設定
- `terrain_profile.csv`: 距離と標高の CSV
- `report.txt`: リンクバジェットのテキストレポート

## 設定・キャッシュ・ログ

- `radiosim_conf.json`
  - 最後に実行した設定を保存します。
- `terrain_cache/`
  - 国土地理院 DEM PNG タイルのローカルキャッシュです。
- `radiosim.log`
  - 実行ログです。
- `results/`
  - 保存したシミュレーション結果の出力先です。

## プロジェクト構成

```text
.
├── main.py                 # アプリケーションエントリーポイント
├── infrastructure.py       # ログ、設定、DEM取得、入力検証
├── models.py               # 地形・伝搬・リンクバジェット計算
├── simulation.py           # 計算処理のオーケストレーションと保存処理
├── views/
│   ├── launcher.py         # 入力フォーム
│   └── graph.py            # グラフ表示と操作
├── pyproject.toml          # pytest / coverage 設定
└── README.md
```

## 計算モデルの概要

- 地形距離: Haversine による水平距離
- 地球曲率補正: 等価地球半径係数 `Ke = 4/3`
- Fresnel 第1ゾーン: ITU-R P.526 ベースの式
- 回折損:
  - `single`: 単一障害物 Fresnel-Kirchhoff
  - `deygout`: 多重回折対応の Deygout 法
- 降雨減衰: ITU-R P.838-3
- 大気減衰: ITU-R P.676-13 Annex 2 の簡易式
- リンクバジェット:

```text
EIRP       = TX Power + TX Antenna Gain
Total Loss = FSPL + Diff Loss + Veg Loss + Env Loss + Rain Loss + Gas Loss
RX Level   = EIRP + RX Antenna Gain - Total Loss
Act Margin = RX Level - Sensitivity
```

## 注意事項

- 標高データ取得に失敗し、キャッシュもない場合、その地点の標高は `0.0 m` として扱われます。
- 初回実行時は DEM タイル取得のため、地点やサンプル数によって時間がかかることがあります。
- 本アプリの計算結果は無線リンク設計の検討・比較用です。実運用では現地調査、アンテナ仕様、
  法規制、実測値などと合わせて評価してください。
