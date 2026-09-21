"""920 MHz へ移ったときの「感度＝空中時間＝刻み」の予算（2026-09-21）。

RadioSim Field のステージ2 の回線は、条件①（回折が数〜十数 dB）と
②（感度より 10〜15 dB 上）を両立させる必要があるが、2.4 GHz の ESP32
（11b 1 Mbps・感度 −98 dBm）では予算が足りない。920 MHz へ移って
**ビットレートを下げて感度を買う**とき、920 MHz 帯の Duty 制限
（ARIB STD-T108・特小 20 mW）が測定の刻みをどこまで縛るかを見る。

  S = -174 + 10*log10(Rb) + NF + SNR   [dBm]
  刻み I = 空中時間 T / Duty

⚠️ 感度は熱雑音からの理論値に NF と所要 SNR を**仮置き**したもの。
実装は数 dB 悪いので、ステージ0a の実測で置き換える。
"""

import math

NF = 8.0      # 受信機の雑音指数（仮）
SNR = 10.0    # 非コヒーレント 2FSK で BER 1e-3 級（仮）
BITS = {"12B相当(150bit)":150, "24B相当(290bit)":290}
print("=== 感度の見積り（熱雑音からの理論値＋NF/SNR の仮置き） ===")
for rb in (1200, 2400, 4800, 9600, 19200, 38400, 50000):
    s = -174 + 10*math.log10(rb) + NF + SNR
    row = [f"{rb/1000:6.1f} kbps  S~{s:7.1f} dBm"]
    for name, b in BITS.items():
        row.append(f"{name} T={1000*b/rb:6.1f} ms")
    print("  " + " | ".join(row))

print()
print("=== 刻み I = 空中時間 T / Duty（24B 相当・290 bit） ===")
for rb in (2400, 4800, 9600, 19200, 38400):
    T = 290/rb
    s = -174 + 10*math.log10(rb) + NF + SNR
    fh_ch = T/0.01
    print(f"  {rb/1000:5.1f} kbps  S~{s:7.1f} dBm  T={1000*T:6.1f} ms"
          f" | CS要10%: {T/0.10:5.2f} s | FH装置20%: {T/0.20:5.2f} s"
          f" | FH 1ch 1%: {fh_ch:5.2f} s (100ms 刻みに要るch数 {math.ceil(fh_ch/0.1):3d})"
          f" | LDC1%: {T/0.01:6.2f} s")

print()
print("=== 逆算: 深さ別に要る感度（§6.1-3C-2 の表・728m/余裕12dB）→ 必要ビットレート ===")
for depth, sens in (("0.0m",-98),("2.5m",-105),("6.0m",-110),("11.5m",-115),("20.5m",-120)):
    rb = 10**((sens + 174 - NF - SNR)/10)
    T  = 290/rb
    print(f"  深さ {depth:>6}  感度 {sens:5d} dBm -> Rb<={rb/1000:8.2f} kbps  T~{1000*T:8.1f} ms"
          f"  CS要10%刻み {T/0.10:7.2f} s  FH20%刻み {T/0.20:7.2f} s")
