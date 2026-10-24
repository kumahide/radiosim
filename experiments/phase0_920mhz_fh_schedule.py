"""920 MHz の FH（キャリアセンス不要）で、測定の刻みを守れるかを条文どおりに検証する（2026-09-21）。

RadioSim Field は「等間隔・無条件の送信」を打ち切り検出の前提にしている（メモリ §6.1-1C／2A）。
920 MHz でそれを守れるのは FH か LDC の枠だが、**効く制約は Duty ではなかった**。

出典＝ARIB STD-T108 v1.5（2023-03-03）英訳版 第 3 編 3.4.1(1) FH method ＋ Table 3-5:
  a) 1 時間あたりの送信時間の総和は 720 s 以下。加えて**各チャネルごとに 36 s 以下**。
  b) 電波を出し始めてから 400 ms 未満で、その周波数の送信を止める。加えて、
     **止めてから 4 s が経過するまで同じ周波数で送信してはならない**。
     ただし、最初の送信から 400 ms 以内で、その 400 ms の区間内に終わるものは 4 s を待たなくてよい。
  Table 3-5: 適用 CH 番号 24–46（23 波）・単位 CH 幅 200 kHz・束ねは 1 ch・キャリアセンス無し。

🔑 **効くのは「同じチャネルは 4 s 空ける」**。Duty ではない。
   ⇒ 400 ms の窓の中でまとめて撃ち、窓ごとにチャネルを移し、十分な数のチャネルを回す。

使い方: python experiments/phase0_920mhz_fh_schedule.py
"""

import math

CH_MIN, CH_MAX = 24, 46          # Table 3-5 の適用 CH
N_CH_AVAIL = CH_MAX - CH_MIN + 1  # 23 波
WINDOW_MS = 400.0                 # 1 つの周波数を使い続けてよい上限
REVISIT_MS = 4000.0               # 止めてから同じ周波数を再び使うまで
PER_CH_LIMIT_S = 36.0             # 1 時間あたり・チャネルごと
PER_DEV_LIMIT_S = 720.0           # 1 時間あたり・装置ごと
HOUR_S = 3600.0


def check(interval_ms: float, airtime_ms: float, n_ch: int) -> dict:
    """刻み interval_ms・空中時間 airtime_ms・回すチャネル数 n_ch の巡回計画を判定する。

    計画＝1 つのチャネルの 400 ms の窓の中で刻みどおりに k 回撃ち、次のチャネルへ移る。
    """
    # 400 ms の窓に入る送信回数（最後の送信も窓の中で終わること）
    k = 1 + int(math.floor((WINDOW_MS - airtime_ms) / interval_ms)) if airtime_ms <= WINDOW_MS else 0
    if k < 1:
        return {"ok": False, "why": "空中時間が 400 ms の窓に入らない"}
    last_start = (k - 1) * interval_ms
    last_stop = last_start + airtime_ms

    # 次のチャネルの先頭も刻みどおりに置く（＝周と周の継ぎ目でも等間隔を崩さない）。
    # ⚠️ ここを last_stop にすると継ぎ目だけ詰まり、実効の刻みが公称より速くなる。
    hop_ms = k * interval_ms
    cycle_ms = hop_ms * n_ch

    # b) 同じ周波数は「止めてから 4 s」空ける＝最後の送信の停止から次の巡回の先頭まで
    gap_ms = cycle_ms - last_stop
    revisit_ok = gap_ms >= REVISIT_MS

    # 実効の刻み（1 周の中では interval どおり、周と周の継ぎ目もそろえる）
    tx_per_cycle = k
    tx_rate_hz = tx_per_cycle * n_ch / (cycle_ms / 1000.0)

    per_ch_s = (HOUR_S / (cycle_ms / 1000.0)) * k * (airtime_ms / 1000.0)
    per_dev_s = per_ch_s * n_ch

    return {
        "ok": revisit_ok and per_ch_s <= PER_CH_LIMIT_S and per_dev_s <= PER_DEV_LIMIT_S,
        "k": k, "hop_ms": hop_ms, "cycle_ms": cycle_ms, "gap_ms": gap_ms,
        "revisit_ok": revisit_ok, "per_ch_s": per_ch_s, "per_dev_s": per_dev_s,
        "tx_rate_hz": tx_rate_hz,
        "min_ch_for_revisit": math.ceil((REVISIT_MS + last_stop) / hop_ms),
    }


def main() -> None:
    print(f"適用 CH {CH_MIN}-{CH_MAX}（{N_CH_AVAIL} 波）・窓 {WINDOW_MS:.0f} ms・再訪 {REVISIT_MS:.0f} ms")
    print(f"上限: チャネル {PER_CH_LIMIT_S:.0f} s/h（1%）・装置 {PER_DEV_LIMIT_S:.0f} s/h（20%）\n")

    # 感度ごとの空中時間は phase0_920mhz_budget.py の逆算（24 バイト相当・290 bit）
    cases = [
        ("深さ 6.0m 級 / -110 dBm", 7.3, 100.0),
        ("深さ 6.0m 級 / -110 dBm", 7.3, 200.0),
        ("深さ11.5m 級 / -115 dBm", 23.0, 100.0),
        ("深さ11.5m 級 / -115 dBm", 23.0, 125.0),
        ("深さ11.5m 級 / -115 dBm", 23.0, 150.0),
        ("深さ20.5m 級 / -120 dBm", 72.8, 400.0),
    ]
    for label, airtime, interval in cases:
        print(f"[{label}] 空中時間 {airtime:5.1f} ms / 刻み {interval:5.1f} ms")
        for n_ch in (11, 23):
            r = check(interval, airtime, n_ch)
            if not r.get("k"):
                print(f"   {n_ch:2d} 波: {r['why']}")
                continue
            mark = "OK  " if r["ok"] else "NG  "
            print(f"   {n_ch:2d} 波: {mark}窓あたり {r['k']} 回 / 1 周 {r['cycle_ms']:7.1f} ms"
                  f" / 再訪の空き {r['gap_ms']:7.1f} ms {'>=' if r['revisit_ok'] else '< '} 4000"
                  f" / ch {r['per_ch_s']:6.2f} s/h / 装置 {r['per_dev_s']:7.2f} s/h"
                  f" / 再訪に要る波数 {r['min_ch_for_revisit']:2d}")
        print()


if __name__ == "__main__":
    main()
