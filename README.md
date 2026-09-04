<img src="logo.png" width="200">

# RadioSim Pro — 地上無線回線の伝搬シミュレーター（国土地理院 DEM 自動取得）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**国土地理院 DEM（数値標高モデル）を自動で取得し、地形断面・回折損・フレネル第1ゾーン遮蔽率・リンクバジェットを計算する Windows デスクトップアプリです。** 送信点と受信点の座標・アンテナ高・周波数を入れるだけで、その回線が通りそうかを数秒で判定します。**現地調査に出る前のスクリーニング**に用途を絞ったツールで、無料・MIT ライセンス・インストール不要の Windows バイナリも配布しています。

> **標高データが国土地理院タイル前提のため、対象は日本国内の回線に限られます。**

*A desktop propagation simulator for land mobile radio links: terrain profiles, diffraction loss, Fresnel zone clearance and link budgets, computed from **GSI DEM** (Geospatial Information Authority of Japan) elevation tiles fetched automatically. Because it relies on GSI tiles, **it covers links inside Japan only**. English documentation: [docs/manual_en.md](docs/manual_en.md) (users) and [docs/developer_en.md](docs/developer_en.md) (developers).*

<img src="docs/images/shot_profile.png" width="720" alt="地形断面グラフ。送受信点を結ぶ見通し線とフレネル第 1 ゾーンが地形に重ねて描かれ、遮蔽区間と受信レベル・マージンが表示されている">

---

## 想定用途

- **回線設計の一次スクリーニング** — 現地調査に行く価値があるかを机上で絞り込む
- **エリア調査の下ごしらえ** — 候補地を複数比べ、見込みの薄い組み合わせを先に落とす
- **中継点の検討** — 直接は届かない 2 点を、尾根などの中継点で継げるか
- **アンテナ高・周波数の当たり付け** — 1 本の経路で条件を振り、成立ラインを探す
- **複数経路の一括計算（バッチ計算）** — CSV に並べた N 本をまとめて回し、レポートを自動生成

**使わないほうがよい用途**: 回線設計の最終判断。DEM の水平解像度は 5〜10m で、回折損の実質精度は **±5〜15 dB** 程度です。⚠️ **この範囲は、複数の障害物が重なる経路には当てはまりません**——合成損失は実測とも基準実装とも突き合わせていません（下記）。

> 🔴 **回折損は過大にも過小にも出ることがあり、どの経路で信頼できるかを事前に見分ける方法がありません。** 3.0 で回折モデルを **Bullington 等価ナイフエッジ（ITU-R P.526 §4.5.1）** に替え、①山越えで数百〜数千 dB になる**発散**と、②**植生高やアンテナ高を 1m 動かすだけで 84 dB 跳ぶ不連続**を解消しました。ただし**外れる可能性そのものが消えたわけではありません**＝①合成損失は**実測とも基準実装とも突き合わせていません**。②**離れた 2 つ以上の尾根がある経路では小さめに出ます**（この手法の既知の性質です）。③**地形の解像度を上げると回折損が増えます**（「低」の 20m 間隔 →「高」の画素の縁ごとで最大 +407%）。④**平滑地球の球面回折の項を持ちません**＝低いアンテナで見通し距離を超える長い平坦路（海上など）では過小になりえます。⚠️ **起伏の大きさでも F1 遮蔽率でも、どの経路が外れるかは判定できません。**⚠️ F1 遮蔽率は表示上 **100% で頭打ち**なので、率の側からは異常に気づけません。
>
> 🛡 **自衛策** — **「回折モデル」を `Single` に切り替えて計算し直し、2 つの値を見比べてください。**大きく食い違う経路では既定（Bullington）の値を信用しないでください。⚠️ **これは正しさを決める手段ではなく、結果がモデルの選び方にどれだけ依存しているかの診断**です（差が小さくても正しさは保証されません）。`Single` は複数障害物の合成損失を表現せず標準の補正項も掛けないので **Bullington より小さく出ます**が、それが真の値に近いという意味ではありません。
>
> *🔴 **Diffraction loss can come out too high or too low, and no advance test tells you which paths are safe.** Version 3.0 replaced the model with the **Bullington equivalent knife edge (ITU-R P.526 4.5.1)**, removing both the divergence that produced hundreds to thousands of dB over mountains and a discontinuity that moved the result by 84 dB when vegetation or antenna height changed by 1 m. Multi-obstacle results have **not been checked against measurements or a reference implementation**; the method **reads low where two or more ridges are well separated**; a finer terrain resolution still raises the diffraction loss (up to +407% from the 20 m "low" step to the pixel-edge "high" step); and the **spherical-earth term is not included**, so long flat paths with low antennas beyond the radio horizon can read low. Neither relief nor Fresnel blockage predicts which paths are affected. Fresnel blockage is **capped at 100%** wherever it is shown, so the percentage will not warn you either. **Mitigation**: re-run with the Diffraction Model set to `Single` and compare — where the two differ substantially, do not trust the default.*

## 主な機能

- **地形断面（パスプロファイル）の自動生成** — 座標を入れると標高タイルを取得して断面を描画
- **見通し（LOS）判定とフレネル第1ゾーン遮蔽率** — どこがどれだけ塞がっているかを数値と図で表示
- **リンクバジェット** — 送信電力・利得・各種損失から受信レベルとマージンを算出
- **4 つの実行フロー** — 個別シミュレーション / 複数経路（CSV バッチ）/ 条件探索（比較・スイープ）/ 中継経路
- **地図から座標を拾う** — 淡色地図をクリックして送受信点を指定、DEM キャッシュの可視化・事前取得も
- **帳票が前提と適用範囲を自分で名乗る**（3.0 から） — レポートの下部に「結果の取扱に関する補足」の節が入り、標高データが地表面モデルであること・植生高が一律の仮定値であること・地面反射を考慮していないこと・その周波数では範囲外につき 0 dB として扱った項目などを、**成果物そのものが持ち歩きます**
- **プロジェクトファイル（`.rsproj`）** — 座標・パラメータ・案件情報をまとめて保存し、続きから再開
- **日本語 / 英語 UI**・ダークモード対応
- **表示言語を自分で足せる**（2.8 から） — `lang` フォルダに `<言語コード>.json` を置くと言語メニューに現れます。訳が無いキーは英語のまま出るので、全部を訳さなくても壊れません（**非公式な訳**という位置づけで、追加した言語では画面の見切れを保証しません）

<img src="docs/images/shot_map.png" width="480" alt="地図上に送信点・受信点と経路が表示された画面。地図をクリックして座標を拾える">
<img src="docs/images/shot_batch.png" width="480" alt="複数経路の入力表。1 行 1 経路で、実行後の判定（OK / NG）と水平距離が各行に返っている">

## 計算モデル

| 項目 | 準拠・方式 |
| --- | --- |
| 回折損 | **Fresnel-Kirchhoff** 単一エッジ（**ITU-R P.526** のナイフエッジ式）／**Bullington 等価ナイフエッジ**（**ITU-R P.526 §4.5.1**・標準の補正項つき。⚠️ P.452 の完全法が足す球面回折の項は持ちません） |
| 自由空間伝搬損失 | FSPL（球面波） |
| 地球曲率補正 | 標準大気 K = 4/3 |
| フレネルゾーン | 第 1 フレネル半径と地形・植生の交差から遮蔽率を算出 |
| 植生減衰 | 見通し線への侵入深さモデル（植生高は経路一律） |
| 環境損失 | 市街地 / 郊外 / 農村 / 見通し の 4 区分 |
| 降雨減衰 | **ITU-R P.838-3** |
| 大気（ガス）減衰 | **ITU-R P.676-13** Annex 2 簡易式 |
| 標高データ | 国土地理院 標高タイル（5m / 10m メッシュ・自動取得＋ローカルキャッシュ） |

## 出力形式（HTML・KML・PNG・CSV・JSON）

| 形式 | 内容 |
| --- | --- |
| **HTML レポート** | 印刷確定レポート。ブラウザの Ctrl+P だけで PDF 化できます（追加ソフト不要）。**経路 1 本の詳細（`report.html`）は A4 縦 1 枚**に収まります。複数経路の台帳や全ページ連結版は、行数・経路数に応じて複数ページになります |
| **KML** | 地形・見通し線・フレネルゾーン・遮蔽区間の 3D 表示。**Google Earth** でそのまま開けます |
| **PNG** | 地形断面グラフ |
| **CSV** | 全経路・全条件の数値と、経路上の地形断面データ |
| **JSON** | 入力パラメータ一式（再現用） |

## インストール

### Windows バイナリ版（Python 不要）

**インストーラ版（推奨）**: [Releases](https://github.com/kumahide/radiosim/releases) から `RadioSimPro-Setup-<版>.exe` をダウンロードして実行（管理者権限不要）。設定・キャッシュ・結果は OS 標準の場所（`%APPDATA%`／`%LOCALAPPDATA%`／ドキュメント）へ保存されます。

**ポータブル版**: 同じく Releases から `RadioSimPro-<版>.zip`（例: `RadioSimPro-3.1.zip`）をダウンロードし、ZIP を展開して `RadioSimPro.exe` をダブルクリック。設定・キャッシュ・結果は展開したフォルダの中に作られ、USB メモリ等で持ち運べます。

> 署名なし EXE のため SmartScreen 警告が出る場合があります。「詳細情報」→「実行」をクリックしてください。詳細は [docs/manual_ja.md](docs/manual_ja.md#インストール・起動) を参照してください。

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

使えません。標高データが国土地理院のタイル前提です。海外の DEM に対応する予定は**現行版にはありません**（将来の版で DEM の入手先を増やすことは検討していますが、時期も可否も未定です）。

### 有料ですか？ 商用利用できますか？

MIT ライセンスの無料ソフトです。商用利用も可能です（[LICENSE](LICENSE)）。

### 社内プロキシの環境でも動きますか？

動きます。メニューからプロキシを設定できるほか、企業内 CA を使う環境向けに OS の証明書ストアを参照する経路も用意しています。

---

## ドキュメント

| 読みたいもの | 場所 |
| --- | --- |
| 使い方（Windows バイナリ版の利用者向け） | [docs/manual_ja.md](docs/manual_ja.md) |
| 開発者向け（構成・計算モデル詳細・アーキテクチャ） | [docs/developer_ja.md](docs/developer_ja.md) |
| 変更履歴 | [CHANGELOG.md](CHANGELOG.md) |
| English — user manual | [docs/manual_en.md](docs/manual_en.md) |
| English — for developers | [docs/developer_en.md](docs/developer_en.md) |

---

© 2026 BearValley AI Craftworks. All rights reserved.
