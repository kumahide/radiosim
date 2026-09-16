"""
core/batch_csv_schema.py
========================
バッチ入力 CSV（複数経路）の**列の契約**（列名・順序・必須/任意）の単一ソース
（ヘッドレス・純データ）。

⚠️ **`core/output_contract.py` とは向きが逆**（あちらの冒頭コメント参照）＝
こちらはアプリ自身が読み戻す**交換フォーマット**で、我々が読む側の約束。

移す前は `report/batch.py` がこの契約と `PathRow` の解析・実行を1ファイルに
持っていた。RadioSim Tracer（別アプリ・`apps/` 側）はこの列名・順序だけを
必要とし、`PathRow` の解析やバッチ実行の都合までは要らない＝
「apps どうしは import しない」規則の下で Tracer が本体側の `report/` を
直接 import せずに済むよう、契約だけをここへ切り出す（I-159）。

`report/batch.py` はここから読み、`README` の CSV 節を照合するドキュメント
整合テスト（`tests/test_docs_consistency.py`）もここを単一ソースにする。
"""

_REQUIRED_COLS = {"id", "start", "end", "h_tx", "h_rx"}

# CSV スキーマの正準（出力ヘッダ順）。required の後に optional。
CSV_COLUMNS = ["id", "start", "end", "h_tx", "h_rx", "freq", "gain_tx", "gain_rx", "note",
               "meas_dbm", "meas_method", "feeder_loss_db", "env_class"]
OPTIONAL_COLS = [c for c in CSV_COLUMNS if c not in _REQUIRED_COLS]
