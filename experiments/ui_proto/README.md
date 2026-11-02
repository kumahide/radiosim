# 画面の道具の試作（4.0 ステージ2）

同じ課題＝1 回線を入力→地図→断面図→判定を 1 ウィンドウで・入力したらすぐ計算（地形は端点が決まったときだけ取得）。
計算は `core/` をそのまま呼ぶ。製品のコードと `requirements.txt` は触らない。

| 候補 | 置き場 | 窓 | 画面 |
|---|---|---|---|
| A | `a_pywebview/` | `pywebview`（WebView2） | `web/` |
| B | `b_edge_app/` | Edge のアプリモード（実行時の依存なし） | `web/` |
| C | `c_tk/` | Tk 9（今の道具＝物差し） | `tkintermapview`・`matplotlib` |

- `common/`＝計算の口・タイルの配信（製品と同じディスクキャッシュ）・環境の測定・`127.0.0.1` の HTTP サーバ。
- `web/vendor/leaflet/`＝Leaflet 1.9.4（BSD-2-Clause・`LICENSE` を同梱・npm の shasum を照合済み）。WebGL を使わないラスタ表示。
- 画面の「測定」欄＝会社 PC ではスクショ 1 枚で読む。

## 動かす

製品の外の venv を使う（`D:\dev\radiosim\venv-ui-proto`・親は `D:\tools\py3150`）。

```
D:\dev\radiosim\venv-ui-proto\Scripts\python.exe experiments\ui_proto\a_pywebview\main.py
D:\dev\radiosim\venv-ui-proto\Scripts\python.exe experiments\ui_proto\measure.py --runs 5 --size 1900x950 --out D:\dev\radiosim\ui_proto\measure
```

`--offline` は届かないプロキシを挟み、ネットワークを切った状態を作る。
