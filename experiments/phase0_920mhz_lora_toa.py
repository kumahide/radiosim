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

🔴 **表 4 は式と矛盾している**（末尾の検算・2026-09-21 に表の 18 セル全部で引き直した）。

  1. 残差は **シンボル一定でも時間一定でもない**（DE=1 で +27〜+87 シンボル ＝
     +5.568〜+110.592 ms）。
  2. 決定的なのは **表 4 のシンボル数が BW に依存している**こと＝BW を 2 倍に
     するごとに **ちょうど +10 シンボル**（SF5〜SF10 の全行で同じ）。LoRa の
     シンボル数は SF・ペイロード長・CR・CRC・ヘッダだけで決まり、**BW には
     依存しない**。⇒ 表 4 は 8.2 節の式では再現できず、**表から「式に足りない
     項」を復元する道は閉じている**。

⚠️ **前の版（3 セルだけ見ていた）の結論は誤りだった**＝「DE=1 なら 3 組とも
-27.000 シンボルで一定なので、式は合っていて固定の項が 1 つ足りないだけ」と
書いたが、その 3 組（BW125/SF9・BW250/SF10・BW500/SF11）は **たまたま
シンボル時間がすべて 4.096 ms** で、しかも各 BW 列の最大 SF という端だけを
拾っていた。**18 セルに広げると一定性は消える。**
⇒ [[feedback-refine-the-step]] の「範囲まで 1/10 にしない」と同じ形＝
**標本の取り方が結論を作っていた。**

⇒ **実機で ToA を測る 1 点は消えない**（むしろ表から導く望みが消えたぶん必須）。
⚠️ **AUX ピンでは測れない見込み**＝5.4 節は「データ送信が完了すると Low→High」と
書くが、6.14 節は「内部送信バッファのデータを**無線チップに書き込み終わり**
バッファが空になったタイミングで High」と書く＝**同じ文書の 2 か所が食い違う**。
後者なら AUX の立ち上がりは空中の送信完了ではない。⇒ 測るのは母艦側で
送信間隔を見る形（`bench`/`probe` がそのまま使える）。
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
    """LoRa のシンボル数（データシート 8.2 節。SF 5/6 は定数が 6.25、他は 4.25）。"""
    de = 1 if (low_data_rate if low_data_rate is not None else 0) else 0
    const = 6.25 if sf in (5, 6) else 4.25
    num = 8 * payload_bytes - 4 * sf + 28 + 16 * crc - 20 * implicit_header
    den = 4 * (sf - 2 * de)
    return preamble + const + 8 + max(math.ceil(num / den) * (cr + 4), 0)


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

# データシート表 4（ペイロード 200 B 時の ToA・ms）の全セル。
# 🔑 3 セルだけ見ると「残差が一定」に見えるので、必ず全部を当てる（docstring 参照）。
TABLE4 = {
    (125, 5): 196.928, (125, 6): 299.136, (125, 7): 488.704, (125, 8): 813.568,
    (125, 9): 1381.376,
    (250, 5): 99.744, (250, 6): 152.128, (250, 7): 249.472, (250, 8): 417.024,
    (250, 9): 711.168, (250, 10): 1238.016,
    (500, 5): 50.512, (500, 6): 77.344, (500, 7): 127.296, (500, 8): 213.632,
    (500, 9): 365.824, (500, 10): 639.488, (500, 11): 1115.136,
}

print("=== 検算: データシート表 4 の全 18 セル（ペイロード 200 バイト）===")
print("  BW  SF     Ts(ms)    表4(ms)   残差(DE=0)         残差(DE=1)")
resid = {0: [], 1: []}
for (bw, sf), ds in sorted(TABLE4.items()):
    ts = symbol_ms(sf, bw)
    cells = []
    for de in (0, 1):
        diff_ms = ds - toa_ms(sf, bw, 200, low_data_rate=de)
        resid[de].append((diff_ms / ts, diff_ms))
        cells.append(f"{diff_ms:+9.3f} ms ={diff_ms / ts:+7.2f} sym")
    print(f"  {bw:3d} {sf:2d}  {ts:8.4f} {ds:10.3f}   " + "   ".join(cells))

for de in (0, 1):
    sym = [s for s, _ in resid[de]]
    ms = [m for _, m in resid[de]]
    print(f"\n  DE={de}: シンボル一定なら幅 0 → {min(sym):+.2f}〜{max(sym):+.2f} sym"
          f" (幅 {max(sym) - min(sym):.2f})")
    print(f"         時間一定なら幅 0   → {min(ms):+.2f}〜{max(ms):+.2f} ms"
          f" (幅 {max(ms) - min(ms):.2f})")

print("\n=== 決定的な矛盾: 表 4 のシンボル数が BW に依存している ===")
print("   （LoRa のシンボル数は SF・ペイロード・CR・CRC・ヘッダだけで決まり BW に依らない）")
for sf in range(5, 12):
    got = [(bw, TABLE4[(bw, sf)] / symbol_ms(sf, bw))
           for bw in (125, 250, 500) if (bw, sf) in TABLE4]
    if len(got) > 1:
        print(f"   SF{sf:2d}: " + "  ".join(f"BW{bw}={n:7.2f}" for bw, n in got)
              + f"   → BW 倍ごとに {(got[-1][1] - got[0][1]) / (len(got) - 1):+.2f} シンボル")

print("\n⇒ 表からは式の欠けた項を復元できない。実機で BW500/SF7 の ToA を 1 点測る。")
print("⚠️ AUX の立ち上がりは「無線チップへ書き終えた時刻」の可能性がある（5.4 節と")
print("   6.14 節が食い違う）ので、母艦側で送信間隔を測る（bench/probe）。")
