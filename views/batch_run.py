"""
views/batch_run.py
==================
バッチ窓の**実行と進捗**（`BatchBuilderWindow` の Mixin）。

実行そのものは `batch.run_batch()` に委譲し、ここはワーカースレッドからの
イベントを画面へ流す側だけを持つ（描画は必ずメインスレッド＝`ProgressPump`）。

⚠️ **これは `BatchBuilderWindow` の一部**であって独立した部品ではない。
切り出しは 2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import os

import batch
import config
import i18n
import simulation as sim
from views import dialogs

# 1 パスの進捗区間のうち、標高取得が占める割合。残りはレポート描画に充てる。
# 実測（2.4b1・200 サンプル）では取得 ≒35ms に対し描画はその数十倍で、取得を
# 区間全体に割り当てるとバーが即座に振り切れて実態と乖離する。
_FETCH_FRAC = 0.15


class _RunMixin:
    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
    def _read_base_params(self) -> sim.SimParams:
        """Common Settings の現在値から SimParams を生成する。"""
        c: dict[str, str] = {
            "start"      : f"{self._base_params.lat_tx}, {self._base_params.lon_tx}",
            "end"        : f"{self._base_params.lat_rx}, {self._base_params.lon_rx}",
            "h_tx"       : str(self._base_params.h_tx),
            "h_rx"       : str(self._base_params.h_rx),
            "freq"       : self._common_vars["freq_mhz"].get(),
            "p_tx"       : self._common_vars["p_tx"].get(),
            "gain_tx"    : self._common_vars["gain_tx"].get(),
            "gain_rx"    : self._common_vars["gain_rx"].get(),
            "sens"       : self._common_vars["sens"].get(),
            "veg_h"      : self._common_vars["veg_h"].get(),
            "k_factor"   : self._common_vars["k_factor"].get(),
            "samples"    : self._common_vars["num"].get(),
            "rain_rate"  : self._common_vars["rain_rate"].get(),
            "env_type"   : self._env_label_to_key.get(self._env_var.get(), "los"),
            "diff_method": self._diff_var.get(),
        }
        # **値域の検証はここが関門**（B-018）。共通設定は readonly だが、値はランチャー
        # の生の入力（窓を開いたときのスナップショット／↻更新）で、ランチャー自身は
        # 実行時にしか検証しない＝単一実行を一度も走らせなければ無検証のまま届く。
        # SimParams は float 化しかしないので、freq=0（ZeroDivisionError）や
        # samples=999999（DEM 取得後に固まる）、inf/nan が計算まで通ってしまう。
        # B-016（条件探索）と同じ欠陥のバッチ版で、範囲の出所も同じ VALIDATION_RULES。
        #
        # start/end は検証しない：実際の座標は各行が持ち（validate_rows が検証済み）、
        # ここの start/end はランチャー由来の飾り。validate_config を通すと「送受信が
        # 同一地点」で**正当なバッチ実行が偽陽性で止まる**。h_tx/h_rx も同じ理由で除外。
        errors = [
            f"[{key}] {reason}"
            for key in ("freq", "p_tx", "gain_tx", "gain_rx", "sens",
                        "veg_h", "k_factor", "samples", "rain_rate",
                        "env_type", "diff_method")
            if (reason := config.validate_value(key, c[key])) is not None
        ]
        if errors:
            raise ValueError("\n".join(errors))
        return sim.SimParams(c)

    def _on_run(self) -> None:
        if self._running:
            return

        rows = self._read_table_rows()
        errors = batch.validate_rows(rows)
        if errors:
            dialogs.alert(self, i18n.t("dlg_validation_error"), "\n".join(errors[:10]))
            return

        try:
            base_params = self._read_base_params()
        except Exception as e:
            dialogs.alert(self, i18n.t("dlg_common_cfg_error"), str(e))
            return

        self._running   = True
        self._ok_count  = 0
        self._ng_count  = 0
        self._err_count = 0
        self._run_cur     = 0
        self._run_samples = base_params.num
        self._run_btn.config(state="disabled")
        self._prog_bar.config(maximum=len(rows), value=0)
        self._prog_label.config(text=i18n.t("batch_starting"))
        self._prog_count_label.config(text=f"0 / {len(rows)}  (0%)")
        self._ok_label.config(text="✓ 0 OK")
        self._ng_label.config(text="✗ 0 NG")
        self._err_label.config(text="⚠ 0 ERR")

        # ポンプは実行のあいだだけ回す（完了・エラーのハンドラで停止する）。
        self._pump.start()
        push = self._pump.push
        batch.run_batch(
            rows              = rows,
            base_params       = base_params,
            on_path_start     = lambda cur, tot, pid, p=push: p(("start",    (cur, tot, pid))),
            on_path_progress  = lambda done, p=push: p(("progress", (done,))),
            on_path_complete  = lambda cur, tot, pr,  p=push: p(("done",     (cur, tot, pr))),
            on_batch_complete = lambda d,   rs,        p=push: p(("complete", (d, rs))),
            on_error          = lambda ex,             p=push: p(("error",    (ex,))),
            coord_format      = self._coord_format,
            on_path_stage     = lambda stage, p=push: p(("stage", (stage,))),
            project_name      = self._project_name_var.get().strip(),
            memo              = self._memo_var.get().strip(),
        )

    # ----------------------------------------------------------
    # コールバック（メインスレッドから呼ばれる）
    # ----------------------------------------------------------
    def _dispatch_event(self, item: tuple) -> None:
        """ポンプから届いた 1 イベントを対応するハンドラへ振り分ける。

        complete / error のハンドラがポンプを停止する（実行の終わり）。
        """
        event, args = item
        if event == "start":
            self._on_path_start(*args)
        elif event == "progress":
            self._on_path_progress_tick(*args)
        elif event == "stage":
            self._on_path_stage(*args)
        elif event == "done":
            self._on_path_done(*args)
        elif event == "complete":
            self._on_batch_complete(*args)
        elif event == "error":
            self._on_error(*args)

    def _on_path_start(self, cur: int, tot: int, pid: str) -> None:
        self._run_cur = cur
        pct = int((cur - 1) / tot * 100) if tot else 0
        self._prog_bar.config(value=cur - 1)
        self._prog_label.config(text=f"▶  {pid}")
        self._prog_count_label.config(text=f"{cur - 1} / {tot}  ({pct}%)")

    def _on_path_progress_tick(self, done: int) -> None:
        """パス内の標高取得進捗（0..samples）をバーへ小数値として反映する。

        バーの目盛り（maximum）はパス本数のまま、現在パスの内部進捗を小数部として
        加算する。従来 on_path_progress が no-op でパス境界でしかバーが動かず、
        DEM キャッシュ済みだと一瞬で完了してバーが「機能していない」ように見えた
        （β報告）。単発シングル実行（launcher._on_progress）と同じ fetch 進捗を使う。
        """
        if not self._running or self._run_samples <= 0:
            return
        frac = min(done / self._run_samples, 1.0) * _FETCH_FRAC
        self._prog_bar.config(value=(self._run_cur - 1) + frac)

    def _on_path_stage(self, stage: str) -> None:
        """パス内の段階（取得 / 描画 / サマリ）をラベルとバーへ反映する。

        実測（2.4b1）では 200 サンプルの取得が約 35ms に対しレポート描画は
        その数十倍かかる。取得だけを 100% として描くと「一瞬で終わって残りは
        無反応」に見えるため、取得はパス区間の先頭 _FETCH_FRAC までに抑える。
        描画は matplotlib から下位進捗を取れないので、バーは止めたまま
        ラベルで何をしているかを示す（偽の進捗は出さない）。
        """
        if not self._running:
            return
        if stage == "render":
            self._prog_bar.config(value=(self._run_cur - 1) + _FETCH_FRAC)
            self._prog_label.config(text=i18n.t("batch_stage_render"))
        elif stage == "summary":
            self._prog_label.config(text=i18n.t("batch_stage_summary"))

    def _on_path_done(self, cur: int, tot: int, pr: batch.PathResult) -> None:
        pct = int(cur / tot * 100) if tot else 0
        self._prog_bar.config(value=cur)
        if pr.result is not None:
            if pr.result.status == "OK":
                self._ok_count += 1
            else:
                self._ng_count += 1
            status_text = pr.result.status
        else:
            self._err_count += 1
            status_text = "ERROR"
        self._prog_label.config(text=f"   {pr.row.path_id}  →  {status_text}")
        self._prog_count_label.config(text=f"{cur} / {tot}  ({pct}%)")
        self._ok_label.config(text=f"✓ {self._ok_count} OK")
        self._ng_label.config(text=f"✗ {self._ng_count} NG")
        self._err_label.config(text=f"⚠ {self._err_count} ERR")

    def _on_batch_complete(self, batch_dir: str, results: list) -> None:
        # 成果物（per-path PNG/HTML/KML・サマリ地図/HTML/KML）は batch 側の
        # ワーカースレッドで生成済み。ここは UI 更新のみ＝メインスレッドを
        # 塞がない（従来はここで地図のネットワーク取得まで走り GUI が固まった）。
        self._running = False
        # 実行の終わり＝ポンプを止める。停止後にワーカーが押した分は捨てられ、
        # 残存ポーリングも早期 return するので完了表示は上書きされない。
        self._pump.stop()
        self._run_btn.config(state="normal")
        # 完了時はバーを 0 に戻す（シングル側 _on_fetch_complete と挙動を揃える）。
        # 進捗カウントもバーに合わせて消し、結果サマリは OK/NG/ERR ラベルに残す。
        self._prog_bar.config(value=0)
        self._prog_label.config(text=f"Done: {os.path.basename(batch_dir)}")
        self._prog_count_label.config(text="")
        # 成果物は 2 種類ある＝**閲覧用のサマリ**（台帳・サムネイル・各経路への
        # リンク）と**印刷用の連結レポート**（report_all.html＝Ctrl+P で全ページ分の
        # PDF）。どちらを開きたいかは場面で変わるので、完了時にその場で選ばせる
        # （ウィンドウに常設ボタンを増やさない＝設計哲学⑤）。閉じたあとは
        # 保存先フォルダから開ける。
        choice = dialogs.choose(
            self,
            i18n.t("dlg_batch_complete"),
            i18n.t("dlg_batch_complete_msg").format(dir=batch_dir),
            [("summary", i18n.t("dlg_open_summary")),
             ("all", i18n.t("dlg_open_all")),
             # 「保存先を開く」＝ランチャーから恒久ボタンを外した代わりの受け皿
             # （I-030）。KML/CSV/PNG を掴むにはフォルダが要り、その必要は
             # **実行した直後**に局在している。
             ("folder", i18n.t("dlg_open_folder"))],
        )
        if choice == "summary":
            os.startfile(os.path.join(batch_dir, "summary.html"))
        elif choice == "all":
            os.startfile(os.path.join(batch_dir, "report_all.html"))
        elif choice == "folder":
            os.startfile(batch_dir)

    def _on_error(self, ex: Exception) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        # 失敗時もバーを 0 に戻す（シングル側 _on_fetch_error と挙動を揃える）。
        self._prog_bar.config(value=0)
        self._prog_count_label.config(text="")
        self._prog_label.config(text=i18n.t("batch_error_msg"))
        dialogs.alert(self, i18n.t("dlg_batch_error"), str(ex))
