<img src="logo.png" width="200">

# RadioSim Pro

**地上無線回線伝搬シミュレーター** — Desktop propagation simulator for land mobile radio links

国土地理院 DEM（数値標高モデル）を自動取得し、地形断面・回折損・植生減衰・リンクバジェットをリアルタイムに計算します。現地調査前のスクリーニングに特化したツールです。

Automatically fetches GSI (Geospatial Information Authority of Japan) DEM data to compute terrain profiles, diffraction loss, vegetation attenuation, and link budgets in real time. Designed for pre-survey screening of radio link feasibility.

---

## 入手方法 / Getting Started

| | Python スクリプト版（正式版） | Windows バイナリ版（簡易） |
|---|---|---|
| 対象 | 開発者・上級ユーザー | Windows ユーザー（手軽に試したい方） |
| 必要環境 | Python 3.10+ | 不要 |
| 入手 | このリポジトリをクローン | [Releases](https://github.com/kumahide/radiosim/releases) から `RadioSimPro.zip` をDL |
| 起動 | `python main.py` | `RadioSimPro.exe` をダブルクリック |

**Python スクリプト版のセットアップ / Python setup:**

```bash
git clone https://github.com/kumahide/radiosim.git
cd radiosim
pip install numpy matplotlib requests Pillow sv-ttk darkdetect markdown truststore
python main.py
```

> **Windows SmartScreen 警告 / SmartScreen Warning**  
> バイナリは署名なしのため警告が表示される場合があります。「詳細情報」→「実行」をクリックしてください。  
> The binary is unsigned and may trigger a SmartScreen warning. Click "More info" → "Run anyway".

---

[日本語ドキュメント（開発者向け）](README_ja.md) | [English Documentation (for developers)](README_en.md)

---

© 2026 BearValley Corp. All rights reserved.
