"""
experiments/p526_delta_bullington_itu_probe.py
===============================================
**ITU-R 第 3 研究委員会の検証例で、製品の Bullington（P.526 §4.5.1）を突き合わせる探針**
（4.0 ステージ1・規格例照合コーパスの ③・作業記録 `.claude/org/work_log.md` の O-008）。

🔑 **なぜ探針か**（本人の決定 2026-10-24）＝検証例の断面データにはライセンスの表示が無い
（配布ページは「© ITU All Rights Reserved」と「as is」だけ）＝**リポジトリに置かない**。
テスト（`tests/test_p526_reference.py` の ③）は手計算できる合成断面で閉じ、実地形の照合は
**その都度取り寄せる**この探針で回す。⇒ **取り寄せた文書はメモリの上だけで扱い、ディスクへ
書かない**（`--file` で手元の写しを読むこともできる）。

相手＝「Validation examples for the delta Bullington diffraction prediction method」
（文書 3J/77・2022-05-11・P.526-15／P.452-17／P.1812-6／P.2001-4 向け）。`.docx` に埋め込まれた
`.xls`（OLE2 の BIFF8）に、断面 4 本（50 m 刻み・17〜132 km）× 条件 5 つ＝20 ケースの入力・
中間値・最終値がある。比べるのは**中間値 `Lba`＝実地形の Bullington 損**だけ（製品の球面項は
席＝常に 0 なので、最終値 `L` は比べない）。

依存を足さない＝`.xls` は**標準ライブラリだけの最小の読み手**（下の `_ole_stream`・`_biff_sheets`）
で読む。読むのは数値のセル（`NUMBER`・`RK`・`MULRK`・`FORMULA` の数値）と共有文字列（`SST`）だけ。

製品の入口＝`calculate_terrain_profile`（赤道上を東へ・`frac_axis` で距離の軸をそのまま渡す・
`earth_k = 8500/6371`＝検証例の等価地球半径 8500 km）→`calculate_propagation`（`bullington`・
植生 0）の `diff_loss`。あわせて `_bullington_loss` に検証例と同じ波長 0.3/f[GHz] を直接渡した値も
出す＝差が光速の丸め（検証例は 3e8・製品は 299792458）だけかを切り分ける。

結果（2026-10-24・文書の SHA-256 は下の `_SHA256` と一致）:
  - 製品の入口と `Lba` の差は 20 ケースで**最大 0.0031 dB**
  - 波長を 0.3/f に揃えると**差は 0.00001 dB 未満**＝差の全部が光速の丸め
  - 見通しの 2 ケース（断面 4 の 50/10 m・2500 MHz と 70/5 m・150 MHz）は両方 0
  - ⚠️ 等価地球半径を製品の既定（6371 km × 4/3＝8494.7 km）のままにすると最大 0.011 dB

実行（ネットワークに触る＝ほかの純計算の探針と違う・ディスクへは書かない）:

    python experiments/p526_delta_bullington_itu_probe.py
    python experiments/p526_delta_bullington_itu_probe.py --file 手元の.docx

終了コード＝0（20 ケースとも許容差 0.01 dB の内）／1（外れた・文書の形が想定と違う）。
"""

import argparse
import hashlib
import io
import math
import os
import ssl
import struct
import sys
import urllib.request
import zipfile

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（判定に ⚠️ を出す）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import diffraction, models  # noqa: E402

_URL = ("https://www.itu.int/en/ITU-R/study-groups/rsg3/ionotropospheric/"
        "Validation%20examples%20for%20the%20delta%20Bullington%20diffraction"
        "%20prediction%20method.docx")
#: 2026-10-24 に取り寄せた文書（144,022 バイト）。違えば ITU が差し替えた＝結果の記録と比べられない
_SHA256 = "b23609d26723246f9589a3935f0f2d0d2d086ac102d6604724f73079945c7208"
_EMBED = "word/embeddings/Microsoft_Excel_97-2003_Worksheet.xls"
_AE_KM = 8500.0          # 検証例の等価地球半径
_TOL_DB = 0.01           # テストの ③ と同じ（光速の丸め 0.0031 dB の 3 倍）


# ================================================================
# OLE2（複合文書）から 1 本のストリームを取り出す
# ================================================================
_END = 0xFFFFFFFE


def _ole_stream(blob: bytes, name: str) -> bytes:
    """OLE2 の `name` ストリームを返す。⚠️ ミニストリーム（4096 バイト未満）は読まない＝要らない。"""
    if blob[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise ValueError("OLE2 の署名ではない")
    ssz = 1 << struct.unpack_from("<H", blob, 0x1E)[0]
    n_fat, dir_start = struct.unpack_from("<II", blob, 0x2C)
    cutoff = struct.unpack_from("<I", blob, 0x38)[0]
    difat_start, n_difat = struct.unpack_from("<II", blob, 0x44)

    def sector(i: int) -> bytes:
        return blob[(i + 1) * ssz:(i + 2) * ssz]

    difat = list(struct.unpack_from("<109I", blob, 0x4C))
    s = difat_start
    for _ in range(n_difat):                      # 109 本を超える FAT の在りか
        ent = struct.unpack(f"<{ssz // 4}I", sector(s))
        difat.extend(ent[:-1])
        s = ent[-1]
    fat: list[int] = []
    for i in difat[:n_fat]:
        fat.extend(struct.unpack(f"<{ssz // 4}I", sector(i)))

    def chain(start: int) -> bytes:
        out, s = [], start
        while s != _END:
            out.append(sector(s))
            s = fat[s]
        return b"".join(out)

    dirs = chain(dir_start)
    for off in range(0, len(dirs), 128):
        e = dirs[off:off + 128]
        nlen = struct.unpack_from("<H", e, 0x40)[0]
        if e[:max(nlen - 2, 0)].decode("utf-16-le") != name:
            continue
        start, size = struct.unpack_from("<II", e, 0x74)
        if size < cutoff:
            raise ValueError(f"{name} はミニストリーム（{size} バイト）＝この読み手の外")
        return chain(start)[:size]
    raise KeyError(name)


# ================================================================
# BIFF8（Excel 97-2003）の数値と文字列のセル
# ================================================================
def _records(stream: bytes, pos: int = 0):
    while pos + 4 <= len(stream):
        rid, ln = struct.unpack_from("<HH", stream, pos)
        yield rid, stream[pos + 4:pos + 4 + ln]
        pos += 4 + ln


class _Segments:
    """`SST` と後続の `CONTINUE` の本体を 1 本の読み口にする。

    ⚠️ 文字の並びが `CONTINUE` をまたぐと、続きの先頭に**文字幅の旗 1 バイト**が入る
    （それ以外の欄はまたいでも何も入らない）＝`chars` だけがそれを読む。
    """

    def __init__(self, parts: list[bytes]):
        self.parts, self.i, self.pos = parts, 0, 0

    def _roll(self) -> None:
        while self.pos >= len(self.parts[self.i]) and self.i + 1 < len(self.parts):
            self.i, self.pos = self.i + 1, 0

    def take(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            self._roll()
            p = self.parts[self.i]
            k = min(n - len(out), len(p) - self.pos)
            if k <= 0:
                raise ValueError("SST が途中で尽きた")
            out += p[self.pos:self.pos + k]
            self.pos += k
        return out

    def chars(self, cch: int, wide: bool) -> str:
        out = []
        while cch:
            if self.pos >= len(self.parts[self.i]):
                self.i, self.pos = self.i + 1, 0
                wide = bool(self.take(1)[0] & 1)
            p, w = self.parts[self.i], 2 if wide else 1
            k = min(cch, (len(p) - self.pos) // w)
            raw = p[self.pos:self.pos + k * w]
            out.append(raw.decode("utf-16-le" if wide else "latin-1"))
            self.pos += k * w
            cch -= k
        return "".join(out)


def _sst(parts: list[bytes]) -> list[str]:
    seg = _Segments(parts)
    _, unique = struct.unpack("<II", seg.take(8))
    out = []
    for _ in range(unique):
        cch, flags = struct.unpack("<HB", seg.take(3))
        runs = struct.unpack("<H", seg.take(2))[0] if flags & 0x08 else 0
        ext = struct.unpack("<I", seg.take(4))[0] if flags & 0x04 else 0
        out.append(seg.chars(cch, bool(flags & 0x01)))
        seg.take(4 * runs + ext)
    return out


def _rk(v: int) -> float:
    """RK＝下位 2 ビットが旗（bit0＝100 で割る・bit1＝30 ビットの符号つき整数）。
    整数でなければ上位 30 ビットが double の上位 32 ビット（下位は 0）。"""
    if v & 2:
        i = v >> 2
        x = float(i - (1 << 30) if i & (1 << 29) else i)
    else:
        x = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return x / 100.0 if v & 1 else x


def _biff_sheets(wb: bytes) -> dict[str, dict[tuple[int, int], object]]:
    """表の名前 → {(行, 列): 数値か文字列}。"""
    sheets, sst_parts, in_sst = [], [], False
    for rid, d in _records(wb):
        if rid == 0x0085:                          # BOUNDSHEET
            pos, cch, flags = struct.unpack_from("<I", d)[0], d[6], d[7]
            nm = d[8:8 + cch * (2 if flags & 1 else 1)]
            sheets.append((nm.decode("utf-16-le" if flags & 1 else "latin-1"), pos))
        elif rid == 0x00FC:                        # SST
            sst_parts, in_sst = [d], True
        elif rid == 0x003C and in_sst:             # CONTINUE（SST の続き）
            sst_parts.append(d)
        else:
            in_sst = False
            if rid == 0x000A:                      # 表全体の情報の終わり
                break
    strings = _sst(sst_parts) if sst_parts else []

    out = {}
    for name, pos in sheets:
        cells: dict[tuple[int, int], object] = {}
        for rid, d in _records(wb, pos):
            if rid == 0x0203:                      # NUMBER
                r, c = struct.unpack_from("<HH", d)
                cells[r, c] = struct.unpack_from("<d", d, 6)[0]
            elif rid == 0x027E:                    # RK
                r, c = struct.unpack_from("<HH", d)
                cells[r, c] = _rk(struct.unpack_from("<I", d, 6)[0])
            elif rid == 0x00BD:                    # MULRK
                r, c0 = struct.unpack_from("<HH", d)
                for k in range((len(d) - 6) // 6):
                    cells[r, c0 + k] = _rk(struct.unpack_from("<I", d, 6 + 6 * k)[0])
            elif rid == 0x00FD:                    # LABELSST
                r, c, _, isst = struct.unpack_from("<HHHI", d)
                cells[r, c] = strings[isst]
            elif rid == 0x0006 and d[12:14] != b"\xff\xff":   # FORMULA（数値の結果だけ）
                r, c = struct.unpack_from("<HH", d)
                cells[r, c] = struct.unpack_from("<d", d, 6)[0]
            elif rid == 0x000A:                    # EOF
                break
        out[name] = cells
    return out


def _rk_check() -> None:
    """RK の 4 通り（整数／浮動 × 100 倍の有無）を手で組んだ値で確かめる。"""
    assert _rk((805 << 2) | 2) == 805.0
    assert _rk((12345 << 2) | 3) == 123.45
    assert _rk(((1 << 30) - 3) << 2 | 2) == -3.0
    hi = struct.unpack("<Q", struct.pack("<d", 0.05))[0] >> 32
    assert abs(_rk(hi & 0xFFFFFFFC) - 0.05) < 1e-6


# ================================================================
# 取り寄せと照合
# ================================================================
def _fetch(path: str | None) -> bytes:
    if path:
        with open(path, "rb") as f:
            return f.read()
    req = urllib.request.Request(_URL, headers={"User-Agent": "radiosim p526_probe"})
    with urllib.request.urlopen(req, timeout=120,  # nosec B310（https 固定の定数）
                                context=ssl.create_default_context()) as r:
        return r.read()


def _cases(sheets):
    out_tab = sheets["outputs"]
    head = [out_tab.get((1, c)) for c in range(16)]
    if head[:4] != ["Path", "hts", "hrs", "f"] or head[12] != "Lba":
        raise ValueError(f"outputs の見出しが想定と違う: {head}")
    rows = sorted({r for (r, _) in out_tab if r >= 2 and isinstance(out_tab.get((r, 0)), float)})
    for r in rows:
        p, hts, hrs, f = (out_tab[r, c] for c in range(4))
        yield int(p), hts, hrs, f, out_tab[r, 12]


def _profile(sheets, p: int):
    tab = sheets[f"Path {p}"]
    rows = sorted(r for (r, c) in tab if c == 0 and r >= 1)
    d = [tab[r, 0] for r in rows]
    h = [tab[r, 1] for r in rows]
    return d, h


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[3])
    ap.add_argument("--file", help="取り寄せずに手元の .docx を読む")
    args = ap.parse_args()
    _rk_check()

    blob = _fetch(args.file)
    digest = hashlib.sha256(blob).hexdigest()
    same = digest == _SHA256
    print(f"文書 {len(blob):,} バイト・SHA-256 {digest[:16]}…（記録と{'一致' if same else '不一致＝ITU が差し替えた'}）")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        xls = z.read(_EMBED)
    sheets = _biff_sheets(_ole_stream(xls, "Workbook"))

    import numpy as np
    earth_k = _AE_KM / 6371.0
    print(f"\n{'断面':>4} {'hts':>5} {'hrs':>5} {'f MHz':>7} {'Lba':>9} {'製品':>9} {'差':>9} {'λ=0.3/f':>9}")
    worst = worst_c = 0.0
    n = 0
    for p, hts, hrs, f, lba in _cases(sheets):
        d, h = _profile(sheets, p)
        dmax = d[-1]
        terr = models.calculate_terrain_profile(
            np.asarray(h, dtype=float), 0.0, 0.0, 0.0, math.degrees(dmax / 6371.0),
            earth_k=earth_k, frac_axis=np.asarray(d) / dmax)
        got = models.calculate_propagation(
            terr, hts, hrs, f, 0.0, 10.0, diff_method=models.DIFF_METHOD_MULTI).diff_loss
        el = terr.elevs_with_curve
        got_c = diffraction._bullington_loss(
            el, terr.d_km_axis * 1000.0, float(el[0]) + hts, float(el[-1]) + hrs, 0.3 / (f / 1000.0))
        worst, worst_c = max(worst, abs(got - lba)), max(worst_c, abs(got_c - lba))
        n += 1
        print(f"{p:>4} {hts:>5.0f} {hrs:>5.0f} {f:>7.0f} {lba:>9.4f} {got:>9.4f} {got - lba:>+9.5f} {got_c - lba:>+9.6f}")

    ok = n == 20 and worst <= _TOL_DB
    print(f"\n{n} ケース・製品の入口との差の最大 {worst:.5f} dB（許容差 {_TOL_DB}）"
          f"・波長を 0.3/f に揃えた差の最大 {worst_c:.7f} dB")
    print("判定：" + ("許容差の内" if ok else "⚠️ 外れた（ケース数か差）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
