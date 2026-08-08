<img src="logo.png" width="200">

# RadioSim Pro — 地上無線回線の伝搬シミュレーター（国土地理院 DEM 自動取得）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**国土地理院 DEM（数値標高モデル）を自動で取得し、地形断面・回折損・フレネル第1ゾーン遮蔽率・リンクバジェットを計算する Windows デスクトップアプリです。** 送信点と受信点の座標・アンテナ高・周波数を入れるだけで、その回線が通りそうかを数秒で判定します。**現地調査に出る前のスクリーニング**に用途を絞ったツールで、無料・MIT ライセンス・インストール不要の Windows バイナリも配布しています。

> **標高データが国土地理院タイル前提のため、対象は日本国内の回線に限られます。**

*A desktop propagation simulator for land mobile radio links: terrain profiles, diffraction loss, Fresnel zone clearance and link budgets, computed from **GSI DEM** (Geospatial Information Authority of Japan) elevation tiles fetched automatically. Because it relies on GSI tiles, **it covers links inside Japan only**. English documentation: [README_en.md](README_en.md).*

---

## 想定用途

- **回線設計の一次スクリーニング** — 現地調査に行く価値があるかを机上で絞り込む
- **エリア調査の下ごしらえ** — 候補地を複数比べ、見込みの薄い組み合わせを先に落とす
- **中継点の検討** — 直接は届かない 2 点を、尾根などの中継点で継げるか
- **アンテナ高・周波数の当たり付け** — 1 本の経路で条件を振り、成立ラインを探す
- **複数経路の一括計算（バッチ計算）** — CSV に並べた N 本をまとめて回し、レポートを自動生成

**使わないほうがよい用途**: 回線設計の最終判断。DEM の水平解像度は 5〜10m で、回折損の実質精度は **±5〜15 dB** 程度です。

## 主な機能

- **地形断面（パスプロファイル）の自動生成** — 座標を入れると標高タイルを取得して断面を描画
- **見通し（LOS）判定とフレネル第1ゾーン遮蔽率** — どこがどれだけ塞がっているかを数値と図で表示
- **リンクバジェット** — 送信電力・利得・各種損失から受信レベルとマージンを算出
- **4 つの実行フロー** — 個別シミュレーション / 複数経路（CSV バッチ）/ 条件探索（比較・スイープ）/ 中継経路
- **地図から座標を拾う** — 淡色地図をクリックして送受信点を指定、DEM キャッシュの可視化・事前取得も
- **プロジェクトファイル（`.rsproj`）** — 座標・パラメータ・案件情報をまとめて保存し、続きから再開
- **日本語 / 英語 UI**・ダークモード対応

## 計算モデル

| 項目 | 準拠・方式 |
| --- | --- |
| 回折損 | **Deygout 法**（多重ナイフエッジ）／**Fresnel-Kirchhoff** 単一エッジ（**ITU-R P.526** 系） |
| 自由空間伝搬損失 | FSPL（球面波） |
| 地球曲率補正 | 標準大気 K = 4/3 |
| フレネルゾーン | 第 1 フレネル半径と地形・植生の交差から遮蔽率を算出 |
| 植生減衰 | 見通し線への侵入深さモデル（植生高は経路一律） |
| 環境損失 | 市街地 / 郊外 / 農村 / 見通し の 4 区分 |
| 降雨減衰 | **ITU-R P.838-3** |
| 大気（ガス）減衰 | **ITU-R P.676-13** Annex 2 簡易式 |
| 標高データ | 国土地理院 標高タイル（5m / 10m メッシュ・自動取得＋ローカルキャッシュ） |

## 出力形式（KML・HTML・PNG・CSV）

| 形式 | 内容 |
| --- | --- |
| **HTML レポート** | A4 縦 1 枚に収まる印刷確定レポート。ブラウザの Ctrl+P だけで PDF 化できます（追加ソフト不要） |
| **KML** | 地形・見通し線・フレネルゾーン・遮蔽区間の 3D 表示。**Google Earth** でそのまま開けます |
| **PNG** | 地形断面グラフ |
| **CSV** | 全経路・全条件の数値と、経路上の地形断面データ |
| **JSON** | 入力パラメータ一式（再現用） |

## インストール

### Windows バイナリ版（Python 不要）

1. [Releases](https://github.com/kumahide/radiosim/releases) から `RadioSimPro.zip` をダウンロード
2. ZIP を展開し `RadioSimPro.exe` をダブルクリック

> 署名なし EXE のため SmartScreen 警告が出る場合があります。「詳細情報」→「実行」をクリックしてください。

### Python スクリプト版

Python 3.11 以降が必要です。

```bash
git clone https://github.com/kumahide/radiosim.git
cd radiosim
python -m pip install -r requirements.txt
python main.py
```

## 使う前に

### 国土地理院 DEM の取得に登録や API キーは必要ですか？

不要です。標高タイルは公開されており、アプリが必要な範囲だけを自動で取得します。取得したタイルはローカルにキャッシュされるので、同じ経路を何度計算しても再取得は起きません。

### 日本国外の回線には使えますか？

使えません。標高データが国土地理院のタイル前提です。海外の DEM への差し替えは現在のところ予定していません。

### 有料ですか？ 商用利用できますか？

MIT ライセンスの無料ソフトです。商用利用も可能です（[LICENSE](LICENSE)）。

### 社内プロキシの環境でも動きますか？

動きます。メニューからプロキシを設定できるほか、企業内 CA を使う環境向けに OS の証明書ストアを参照する経路も用意しています。

---

## ドキュメント

| 読みたいもの | 場所 |
| --- | --- |
| 使い方（Windows バイナリ版の利用者向け） | [README_binary_ja.md](README_binary_ja.md) |
| 開発者向け（構成・計算モデル詳細・アーキテクチャ） | [README_ja.md](README_ja.md) |
| 変更履歴 | [CHANGELOG.md](CHANGELOG.md) |
| English (for developers) | [README_en.md](README_en.md) |

---

© 2026 BearValley AI Craftworks. All rights reserved.
