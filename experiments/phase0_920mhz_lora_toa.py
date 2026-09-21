"""E220-900T22x(JP) を使うときの「感度＝空中時間＝刻み」の予算（2026-09-21）。

`phase0_920mhz_budget.py` は **FSK** を前提にした机上計算だったが、E220 は
**LoRa（拡散）専用**なので空中時間の式が違う。感度は拡散利得で買えるが、
空中時間は SF を上げるほど指数で伸びるため、**刻み 100 ms が保てる範囲**は
FSK のときと別の場所にある。ここではデータシートの式をそのまま使って、
Field の測定が成り立つ SF/BW を出す。

  ToA  = 2^SF / BW * N_symbol            （データシート 8.2 節）
  感度 S = -174 + 10*log10(BW) + NF + SNR   （同 8.3 節・NF=6 dB は LLCC68）

刻みの制約（データシート 8.4 節・6 章）:
  - 送信前にキャリアセンス（環境ノイズ < -80 dBm が **5 ms** 継続で送信開始）
  - 1 回の送信ごとに **50 ms の休止**（CH 0-14 = 920.6-923.4 MHz 側）
  ⇒ 刻み I >= ToA + 50 ms + CS 5 ms

⚠️ 表 7 の感度（-129.53 dBm 等）はデータシートの理論値。実装は数 dB 悪い
ので、ステージ0a の実測で置き換える（FSK 版と同じ扱い）。

🔴 **この ToA はデータシートの表 4 を再現できていない**（末尾の検算）。
標準の LoRa の式（プリアンブル 8・CR 4/5）で 200 バイトを計算すると、
表 4 より **短く**出る（BW125/SF9 で 1004.5 ms 対 1381.4 ms）。プリアンブル長
を 1 つ選んでも 3 組すべてには合わないので、表 4 は別の前提（サブパケット
分割・休止の加算など）で作られている可能性がある。⇒ **下の「刻み」の判定は
楽観側**であり、採否は実機で送信間隔を測ってから決める（`bench`/`probe` が
そのまま使える）。
"""

import math

NF = 6.0  # LLCC68 の雑音指数（データシート 8.3 節）
SNR_LIMIT = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5}
BW_MAX_SF = {125: 9, 250: 10, 500: 11}  # 表 4/5/7 で値が入っている範囲
CS_MS = 5.0       # キャリアセンスの継続時間
PAUSE_MS = 50.0   # 1 送信ごとの休止（モジュールが強制）


def n_symbol(sf: int, payload_bytes: int, *, preamble: int = 8,
             cr: int = 1, crc: int = 1, implicit_header: int = 0,
             low_data_rate: int | None = None) -> float:
    """LoRa のシンボル数（SF>=7 の一般式。SF 5/6 は別式なので扱わない）。"""
    de = 1 if (low_data_rate if low_data_rate is not None else 0) else 0
    num = 8 * payload_bytes - 4 * sf + 28 + 16 * crc - 20 * implicit_header
    den = 4 * (sf - 2 * de)
    return preamble + 4.25 + 8 + max(math.ceil(num / den) * (cr + 4), 0)


def toa_ms(sf: int, bw_khz: int, payload_bytes: int) -> float:
    return (2 ** sf) / (bw_khz * 1000.0) * 1000.0 * n_symbol(sf, payload_bytes)


def sensitivity(sf: int, bw_khz: int) -> float:
    return -174 + 10 * math.log10(bw_khz * 1000.0) + NF + SNR_LIMIT[sf]


for payload in (12, 24):
    print(f"=== ペイロード {payload} バイト（プリアンブル 8・CR 4/5・明示ヘッダ・CRC 有） ===")
    print("  BW   SF   感度       ToA      最小の刻み(ToA+50+5)   100ms 刻み  125ms 刻み")
    for bw in (125, 250, 500):
        for sf in range(7, BW_MAX_SF[bw] + 1):
            t = toa_ms(sf, bw, payload)
            floor_ms = t + PAUSE_MS + CS_MS
            ok100 = "OK" if floor_ms <= 100 else "--"
            ok125 = "OK" if floor_ms <= 125 else "--"
            print(f"  {bw:3d}  {sf:2d}  {sensitivity(sf, bw):8.2f} dBm"
                  f"  {t:7.1f} ms  {floor_ms:7.1f} ms"
                  f"            {ok100}          {ok125}")
    print()

print("=== 参考: データシート表 4（ペイロード 200 バイト）と同じ条件で検算 ===")
for bw, sf, ds in ((125, 9, 1381.376), (250, 10, 1238.016), (500, 11, 1115.136)):
    # 表 4 は LDRO(low data rate optimize) 有効時の値と一致する組み合わせがある
    for de in (0, 1):
        t = (2 ** sf) / (bw * 1000.0) * 1000.0 * n_symbol(sf, 200, low_data_rate=de)
        print(f"  BW{bw} SF{sf} DE={de}: 計算 {t:8.3f} ms / データシート {ds:8.3f} ms"
              f"  差 {t - ds:+7.3f} ms")
