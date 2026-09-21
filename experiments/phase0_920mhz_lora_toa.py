"""E220-900T22x(JP) を使うときの「感度＝空中時間＝刻み」の予算（2026-09-21）。

`phase0_920mhz_budget.py` は **FSK** を前提にした机上計算だったが、E220 は
**LoRa（拡散）専用**なので空中時間の式が違う。感度は拡散利得で買えるが、
空中時間は SF を上げるほど指数で伸びるため、**刻み 100 ms が保てる範囲**は
FSK のときと別の場所にある。ここではデータシートの式をそのまま使って、
Field の測定が成り立つ SF/BW を出す。

  ToA  = 2^SF / BW * N_symbol            （データシート 8.2 節）
  感度 S = -174 + 10*log10(BW) + NF + SNR   （同 8.3 節・NF=6 dB は LLCC68）

刻みの制約は **2 系統あり、効くのは制度側**（2026-09-21 に引き直した）:

  (a) モジュール側（データシート 8.4 節・6 章）
      - 送信前にキャリアセンス（環境ノイズ < -80 dBm が **5 ms** 継続で送信開始）
      - 1 回の送信ごとに **50 ms の休止**（CH 0-14 = 920.6-923.4 MHz 側）
      ⇒ 刻み I >= ToA + 50 ms + CS 5 ms

  (b) ARIB STD-T108 第 3 編・**キャリアセンスを要する区分**（技適 001-P01730 は
      920.6-928.0 MHz の 38 波なので、920.5-928.1 のこの行に入る）
      - 送信時間の総和 **360 s/h（10%）**、複数チャネル切替なら **720 s/h（20%）**
      ⇒ 刻み I >= ToA / Duty

🔑 **(b) のほうが 4 倍以上きつい**。100 ms 刻みを守れる ToA は (a) なら 45 ms
だが、(b) では 20%（多チャネル）で 20 ms・10% で 10 ms しかない。
最初にこの計算を書いたときは (a) だけを見ていて、SF9（ToA 36.1 ms = Duty 36%）
を「成立」と判定していた。

⚠️ 表 7 の感度（-129.53 dBm 等）はデータシートの理論値。実装は数 dB 悪い
ので、ステージ0a の実測で置き換える（FSK 版と同じ扱い）。

🔴 **表 4 との差は「縮退していて解けない」**（末尾の検算）。**LDRO を有効
（DE=1）にすると 3 組とも正確に -27.000 シンボル = -110.592 ms** で一定になる
（DE=0 では -92/-77/-67 シンボルとばらける）。つまり式そのものは合っており、
固定の項が 1 つ足りないだけ。**ところが表 4 の 3 組はシンボル時間がすべて
4.096 ms** なので、その項が「一定の時間 110.592 ms」なのか「一定の 27 シンボル」
なのかを**この表からは分離できない**。短いペイロードでは答えが桁で変わる
（12 B・BW500/SF7 で 19.8 ms 対 123.5 ms）。
⇒ **実機で測るのは 1 点でよい＝シンボル時間の違う点（BW500/SF7）の ToA**。
そこが 19.8 ms 側なら 100 ms 刻みが 20% 枠で成立し、123.5 ms 側なら刻みは
625 ms 以上になる（`bench`/`probe` がそのまま使える）。
"""

import math

NF = 6.0  # LLCC68 の雑音指数（データシート 8.3 節）
SNR_LIMIT = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5}
BW_MAX_SF = {125: 9, 250: 10, 500: 11}  # 表 4/5/7 で値が入っている範囲
CS_MS = 5.0       # キャリアセンスの継続時間
PAUSE_MS = 50.0   # 1 送信ごとの休止（モジュールが強制）
DUTY = 0.20       # ARIB CS 要の区分・複数チャネル切替時（720 s/h）。単一なら 0.10


def n_symbol(sf: int, payload_bytes: int, *, preamble: int = 8,
             cr: int = 1, crc: int = 1, implicit_header: int = 0,
             low_data_rate: int | None = None) -> float:
    """LoRa のシンボル数（SF>=7 の一般式。SF 5/6 は別式なので扱わない）。"""
    de = 1 if (low_data_rate if low_data_rate is not None else 0) else 0
    num = 8 * payload_bytes - 4 * sf + 28 + 16 * crc - 20 * implicit_header
    den = 4 * (sf - 2 * de)
    return preamble + 4.25 + 8 + max(math.ceil(num / den) * (cr + 4), 0)


def symbol_ms(sf: int, bw_khz: int) -> float:
    return (2 ** sf) / (bw_khz * 1000.0) * 1000.0


def toa_ms(sf: int, bw_khz: int, payload_bytes: int, *,
           low_data_rate: int | None = None) -> float:
    return symbol_ms(sf, bw_khz) * n_symbol(sf, payload_bytes,
                                            low_data_rate=low_data_rate)


def sensitivity(sf: int, bw_khz: int) -> float:
    return -174 + 10 * math.log10(bw_khz * 1000.0) + NF + SNR_LIMIT[sf]


def floors(sf: int, bw: int, payload: int) -> tuple[float, float, float]:
    """(ToA, モジュール側の刻みの下限, 制度側の刻みの下限) を返す。"""
    t = toa_ms(sf, bw, payload)
    return t, t + PAUSE_MS + CS_MS, t / DUTY


for payload in (12, 24):
    print(f"=== ペイロード {payload} バイト（プリアンブル 8・CR 4/5・明示ヘッダ・CRC 有） ===")
    print("  BW   SF   感度         ToA    モジュール   制度(Duty20%)   刻みの下限  100ms 125ms 250ms")
    for bw in (125, 250, 500):
        for sf in range(7, BW_MAX_SF[bw] + 1):
            t, f_mod, f_arib = floors(sf, bw, payload)
            floor_ms = max(f_mod, f_arib)
            ok = [("OK" if floor_ms <= i else "--") for i in (100, 125, 250)]
            print(f"  {bw:3d}  {sf:2d}  {sensitivity(sf, bw):8.2f} dBm"
                  f"  {t:6.1f} ms  {f_mod:7.1f} ms  {f_arib:9.1f} ms"
                  f"  {floor_ms:9.1f} ms   {ok[0]}    {ok[1]}    {ok[2]}")
    print()

print("=== 参考: データシート表 4（ペイロード 200 バイト）と同じ条件で検算 ===")
print("※ DE=1 の残差は 3 組とも -110.592 ms = -27.000 シンボルで一定だが、")
print("   3 組ともシンボル時間が 4.096 ms なので「時間一定」と「シンボル数一定」を分離できない。")
for bw, sf, ds in ((125, 9, 1381.376), (250, 10, 1238.016), (500, 11, 1115.136)):
    for de in (0, 1):
        t = toa_ms(sf, bw, 200, low_data_rate=de)
        print(f"  BW{bw} SF{sf} Ts={symbol_ms(sf, bw):.3f} ms DE={de}:"
              f" 計算 {t:8.3f} ms / データシート {ds:8.3f} ms"
              f"  差 {t - ds:+8.3f} ms = {(t - ds) / symbol_ms(sf, bw):+7.3f} シンボル")

print()
print("=== 縮退を解く 1 点: シンボル時間の違う BW500/SF7・12 B の ToA ===")
_sf, _bw, _pl = 7, 500, 12
_base = toa_ms(_sf, _bw, _pl, low_data_rate=1)
for name, t in (("いまの表(DE=0)", toa_ms(_sf, _bw, _pl)),
                ("DE=1", _base),
                ("DE=1 + 27 シンボル", _base + 27 * symbol_ms(_sf, _bw)),
                ("DE=1 + 110.592 ms", _base + 110.592)):
    print(f"  {name:20s} ToA {t:6.1f} ms → 刻みの下限"
          f" {max(t + PAUSE_MS + CS_MS, t / DUTY):7.1f} ms")
