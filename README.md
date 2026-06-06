<img src="logo.png" width="200">

# RadioSim Pro

**地上無線回線伝搬シミュレーター** — Desktop propagation simulator for land mobile radio links

国土地理院 DEM（数値標高モデル）を自動取得し、地形断面・回折損・植生減衰・リンクバジェットをリアルタイムに計算します。現地調査前のスクリーニングに特化したツールです。

Automatically fetches GSI (Geospatial Information Authority of Japan) DEM data to compute terrain profiles, diffraction loss, vegetation attenuation, and link budgets in real time. Designed for pre-survey screening of radio link feasibility.

---

## Python スクリプト版（正式版）

Python 3.10+ がインストールされた環境で動作します。

```bash
git clone https://github.com/kumahide/radiosim.git
cd radiosim
pip install numpy matplotlib requests Pillow sv-ttk darkdetect markdown truststore
python main.py
```

## Windows バイナリ版（手軽に使いたい方向け）

Python 不要。Windows 環境で手軽に試したい場合はこちら。

1. [Releases](https://github.com/kumahide/radiosim/releases) から `RadioSimPro.zip` をダウンロード
2. ZIP を展開し `RadioSimPro.exe` をダブルクリック

> 署名なし EXE のため SmartScreen 警告が出る場合があります。「詳細情報」→「実行」をクリックしてください。  
> The binary is unsigned and may trigger a SmartScreen warning — click "More info" → "Run anyway".

---

[日本語ドキュメント（開発者向け）](README_ja.md) | [English Documentation (for developers)](README_en.md)

---

© 2026 BearValley Corp. All rights reserved.
