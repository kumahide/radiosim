"""
webui/
======
**新しい画面**（4.0 ステージ8・Link 1.0 の土台）＝ローカルの HTTP サーバーと Edge のアプリモード。

- `state.py`  … 入力の集合と見出し（どの版の入力で出した結果かを持つ）
- `worker.py` … 計算のスレッド 1 本（地形の取得は取り消せる・計算は `core.engine`）
- `server.py` … `127.0.0.1` だけで待つ HTTP（起動ごとの鍵・`Host` の検査・静的ファイル）
- `edge.py`   … Edge を専用プロファイルのアプリモードで開き、閉じたら終わる
- `__main__.py` … `python -m webui` の入口

🔑 **tkinter を引かない**（I-144 の「新しい画面は tkinter なし」）＝`tests/test_smoke.py` が縛る。
層は `views/` と同じ位（`core/` と `report/` を引いてよい・`views/` とは互いに引かない）。
"""
