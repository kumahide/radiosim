# 用語集（画面・レポートに出る語） / Glossary — the words that reach the screen and the reports

**この表は「画面とレポートに出る名詞のうち、二義になり得るものだけ」を載せる。**
網羅はしない——維持できない正典は、いずれ嘘になるほうが速い。

*This file is bilingual. Every Japanese rule is followed by its English counterpart in
italics, and every row of the table carries an English sentence in its `定義` cell.*
*⛔ There is no `glossary_en.md`: `tests/test_i18n_glossary.py` reads this file and only
this file, so a translated copy would be a canon no machine ever checks. One row, one truth.*

*⚠️ The `##` headings stay Japanese-only on purpose — two test files locate their sections
by splitting on the exact heading text (`## 用語`, `## 文書でも 1 語にそろえる`). The English
of each heading is on the line right below it.*

- **対象**＝利用者の目に触れる字（`i18n.py` の ja/en 文字列）。
- **対象外**＝コード内部の識別子・`i18n` のキー名・`{…}` の差し込み名・
  CSV の列名やファイル名（**出力契約**なので、読む側の互換のほうが優先する）。
  実装語彙と画面語彙は別物で、揃えようとすると改名の波及リスクだけが増える。

*In scope: the strings a user actually reads (the ja/en values in `i18n.py`).*
*Out of scope: internal identifiers, `i18n` key names, `{…}` placeholders, and CSV column
names and file names — those are the **output contract** (`core/output_contract.py`), where
compatibility for the reader wins. Implementation vocabulary and screen vocabulary are two
different things; forcing them together only spreads rename risk.*

**この表は `tests/test_i18n_glossary.py` が機械で守る**（散文の決めごとは静かにずれる）。
語を変える／足すときは、表と `i18n.py` を同じコミットで動かすこと。

*`tests/test_i18n_glossary.py` enforces this table (a rule kept only in prose drifts in
silence). Change the table and `i18n.py` in the same commit.*

**表の列見出しだけは、幅が語に優先する**（2026-08-07・スライス D で決定）。
`送信高` / `受信高` は `アンテナ高` の短縮形として読める——**同じ語の短縮**は許すが、
**別の語への言い換え**（`地上高`）は許さない。⚠️ 短縮形は「使わない言い換え」欄には
書かない（書くと列見出しのほうが赤くなる）。

*For table column headers only, width beats wording. An abbreviation of the same term
(`TX Height` for `Antenna Height`, `Freq` for `Frequency`) is allowed; a different term
for the same thing is not. ⚠️ Never list an abbreviation in the “使わない言い換え” column —
that would turn the gate against the column headers themselves.*

**成果物（レポート HTML）の語も、いまは画面と同じ 1 語**（2026-08-26 に最後の 1 組を揃えた）。
レポートの表題とフッタだけが `一括シミュレーション` と名乗っていたが、画面は同じ機能を
`複数経路` と呼んでいた。⇒ **`一括` と `Batch` は「使わない言い換え」へ**（下の表）。
⚠️ **出力契約は別の話**＝CSV の**列名とファイル名**は読む側との約束なので、画面の語に
揃えない（`summary.csv` の見出しは英字の識別子のまま）。**揃えるのは人が読む字だけ。**

*The reports now use the same word as the screen (the last pair was aligned on 2026-08-26):
the report title and footer used to say “Batch Simulation” where the screen said
`複数経路` / `Multiple Paths`, so `一括` and `Batch` are now in the “do not use” column.
⚠️ **The output contract is a separate matter**: CSV column names and file names are a
promise to whatever reads them and are never bent to match the screen. Only the wording a
human reads is aligned.*

## 表の読み方

*How to read the table.*

| 列 | 意味 |
|---|---|
| **用語** | 日本語の画面でこの意味に使う唯一の語 |
| **定義** | 何を指すか。数量なら**何を数えるか**まで書く。**英文を 1 文足す**（英語の読者向け） |
| **en** | 英語の画面で対応させる語 |
| **使わない言い換え** | 同じものを指してしまいがちな語。**画面に出したら失敗**（`/` 区切り。無ければ `—`） |

*Columns: the one Japanese word used for this meaning; what it denotes (for a quantity,
**what is counted**, plus one English sentence); the matching English word; and the wordings
that must never reach the screen (`/`-separated, `—` when there are none).*

## 用語

*The terms.*

| 用語 | 定義 | en | 使わない言い換え |
|---|---|---|---|
| 区間 | 中継経路で隣り合う 2 地点を結ぶ 1 本の回線。数えるのは**リンクの本数**＝「地点数 − 1」。区間ごとに独立したリンクバジェットを持つ。 *One link between two adjacent waypoints of a relay path; what is counted is the number of links (waypoints − 1), and each section carries its own link budget.* | Section | ホップ / hop |
| 地点 | 中継経路が通る点すべて（送信点・中継点・受信点）。利用者が入力する面はこちらで、区間はここから導かれる。 *Every point a relay path runs through (TX, relay and RX points). This is what the user enters; sections are derived from it.* | Waypoint | ノード / node |
| 中継点 | 地点のうち、送信点でも受信点でもないもの。 *A waypoint that is neither the TX nor the RX point.* | Relay | リレー |
| マージン | 受信レベル − 受信感度（dB）。正なら OK。 *RX level minus RX threshold, in dB; OK when positive.* | Margin | 余裕度 |
| 受信レベル | 受信アンテナ利得まで含めた、受信端に届く電力（dBm）。 *The power arriving at the receiver, RX antenna gain included (dBm).* | RX Level | 受信強度 / 電界強度 / RSSI |
| 受信感度 | 判定の基準にする受信レベルの下限（dBm）。機器のカタログ値を利用者が入れる。 *The lowest RX level used as the pass/fail basis (dBm), taken from the radio's datasheet and entered by the user.* | Threshold | 閾値 / しきい値 |
| 判定 | OK / NG / ERR の 3 値。OK・NG は**マージンの符号**で決まり、ERR は**計算か成果物の生成に失敗して判定できなかった**ことを表す（レポートでは `ERROR`）。 *One of OK / NG / ERR. OK and NG follow the sign of the margin; ERR means no judgement was possible because the calculation or the report failed (`ERROR` in the reports).* | Status | ステータス |
| 全体判定 | 中継経路ぜんたいの判定。**区間と同じ 3 値**（OK / NG / ERR）。**最もマージンの小さい区間の判定**がそのまま全体になる（区間をまたいで損失は足さない）。判定できなかった区間（ERR）が 1 つでもあれば**全体も ERR**＝「計算できたが回線が成立しない（NG）」とは区別する。 *The status of a whole relay path — the same three values as a section. The status of the section with the smallest margin becomes the overall one; losses are never summed across sections. A single ERR section makes the whole path ERR, which stays distinct from “computed, but the link does not close” (NG).* | Overall | 総合判定 |
| 斜距離 | 送受アンテナ間の距離（**高低差を含む**）。 *Distance between the two antennas, height difference included.* | Slant Dist | 直線距離 |
| 水平距離 | 送受地点を地表へ投影した距離（**高低差を含まない**）。 *Distance between the two sites projected onto the ground, height difference excluded.* | Horiz Dist | 地表距離 |
| 総損失 | FSPL・回折・植生・環境・降雨・大気の各損失の合計（dB）。 *The sum of the FSPL, diffraction, vegetation, environment, rain and gas losses (dB).* | Total Loss | 伝搬損失 |
| 伝搬環境 | 環境まわりの入力欄の**まとまりの見出し**（環境区分・降雨強度など）。**1 つの区分を指す語ではない**（→ 環境区分）。 *The heading over the group of environment inputs (env type, rain rate, …). It never names a single class (→ Env Type).* | Environment | — |
| 環境区分 | 環境損失を決める 4 択（市街地 / 郊外 / 農村 / 見通し）。 *The four-way choice that sets the environment loss (urban / suburban / rural / line-of-sight).* | Env Type | 地域区分 / 環境タイプ |
| 植生高 | 地表からの植生の高さ（m）。経路全体へ一律に与える。 *Height of the vegetation above ground (m), applied uniformly along the whole path.* | Vegetation Height | 樹高 |
| 回折損失 | 障害物の上を回り込んで届く分の損失（dB）。**幾何で決まる**＝等価ナイフエッジ 1 枚の位置と高さで決まり、障害物が経路上でどれだけ続くかには依らない。 *The loss over an obstacle that the signal bends around (dB). It follows the geometry — the position and height of a single equivalent knife edge — not how far the obstacle runs along the path.* | Diff Loss | — |
| 植生損失 | 見通し線より上に出た植生の中を通る**長さ**から見積もる損失（dB）。上限 45 dB。**回折損失とは別の量**＝あちらは幾何で決まり、こちらは長さで決まる。 *The loss estimated from the length of vegetation the signal travels through above the line of sight (dB), capped at 45 dB. It is a different quantity from the diffraction loss: that one follows geometry, this one follows length.* | Veg Loss | — |
| ライスKファクター | 見通し波と散乱波の電力比（0〜30）。**語順はこの 1 つ**＝`Kファクター（ライス）` とは書かない。 *The ratio of line-of-sight to scattered power (0–30). One word order only.* | Rician K-Factor | Kファクター（ライス） |
| F1遮蔽率 | 第 1 フレネルゾーンの断面のうち、地形・植生に遮られた割合（%）。 *The share of the first Fresnel zone cross-section blocked by terrain or vegetation (%).* | F1 Obs | フレネル遮蔽率 |
| F1侵入深さ | 障害物が第 1 フレネルゾーンへ**どれだけ深く**食い込んでいるか。数えるのは **F1 半径の倍数**（×F1）で、**100% で頭打ちにしない**＝遮蔽率が 100% でも、1.00 なら*ちょうど*完全遮蔽、2.50 なら半径の 2.5 倍まで突き抜けている。 *How deep an obstacle reaches into the first Fresnel zone, counted in multiples of the F1 radius (×F1) and never capped: 1.00 is exactly full obstruction, 2.50 reaches 2.5 radii past it.* | F1 Depth | 侵入率 / 遮蔽深さ |
| 地形の解像度 | 地形を刻む細かさの段階（高＝約 4m 画素 / 中＝約 8m 画素 / 低＝20m 間隔）。⚠️ **名前が指すのは標本を置く画素の大きさ**で、*読む標高データの層*ではない（標高は段階によらず取れる中で最も細かい層から返る）。**利用者が選ぶのは段階だけ**で、点数はアプリが解く。⚠️ 「高」「中」は**その層の DEM 画素の縁ごと**に標本を置く＝**等間隔ではない**（「低」だけ 20m の等間隔）。 *The step at which the terrain is sampled (High = ~4 m pixels / Medium = ~8 m pixels / Low = 20 m spacing). The name refers to the size of the pixel a sample is placed on, not to the elevation layer that answers (elevations always come from the finest layer available). The user picks the step only; the app resolves the points. "High" and "medium" sample at the edges of each DEM pixel — not evenly spaced.* | Terrain Resolution | 地形サンプル数 / 分解能 |
| サンプル数 | 経路上で実際に標高を取った点の数。**解像度の段階と経路から決まる**結果であって、入力ではない（経路の長さだけでなく**向きと緯度**でも変わる＝斜めの経路ほど多い）。⚠️ **個々の標本の間隔はこの点数からは割り出せない**（等間隔ではないため。平均的な間隔だけは `horiz_m ÷ (samples − 1)` で近似できる）。 *How many points along the path an elevation was actually taken at — a result of the resolution step and the path (its bearing and latitude too, not just its length), never an input. The individual spacings cannot be derived from it (the samples are not evenly spaced); only the average spacing can be approximated, as `horiz_m ÷ (samples − 1)`.* | Samples | 標本数 |
| 経路 | 送信点から受信点までの 1 本のつながり。中継経路なら途中の中継点も含む。 *One connection from the TX point to the RX point, relay points included when it is a relay path.* | Path | Route |
| アンテナ高 | 地表からアンテナまでの高さ（m）。**地物の高さではない**（→ 植生高）。 *Height of the antenna above the ground (m) — not the height of anything standing on it (→ Vegetation Height).* | Antenna Height | 地上高 |
| 開始 | 条件探索で**軸を掃引する範囲**の下端。⚠️ 経路の端（送信点・受信点）はこの語で呼ばない。 *The lower end of the swept range in the scenario window. The ends of a path (TX / RX point) are never called this.* | From | — |
| 終了 | 同じ範囲の上端。 *The upper end of that same swept range.* | To | — |
| メモ | **実行 1 回**に付ける自由記述。レポートの見出しに `メモ:` として出る。 *A free-text note attached to one run; it appears in the report header as `Note:`.* | Note | — |
| 備考 | **1 行（＝1 経路）**に付ける記述。複数経路の表とサマリ台帳の列見出しになる。 *A note attached to one row (one path); it is a column header in the multiple-paths table and in the summary ledger.* | Remarks | — |
| 個別 | 1 本の回線を 1 回だけ計算する実行（ランチャーの実行ボタン）。成果物のフッタは `個別シミュレーション`。 *A run that computes one link once, started from the launcher's Run button; the report footer says “Single Mode”.* | Single Mode | シングル |
| 複数経路 | N 本の**独立した**回線をまとめて回すウィンドウ。1 行 = 1 経路（→ 中継経路は 1 本の内訳）。**レポートの表題・フッタもこの語**。 *The window that runs N independent links in one go; one row = one path (a relay path is the breakdown of a single link). The report title and footer use this word too.* | Multiple Paths | バッチ / 一括 / Batch |
| 地図 | 座標を拾い、DEM キャッシュを見るための補助のウィンドウ。**入力の道具**であって実行フローではない。 *A helper window for picking coordinates and inspecting the DEM cache — an input tool, not a step of the run.* | Map | マップウィンドウ |
| ウィンドウ | アプリが開く画面の単位（ランチャー・地図・複数経路・条件探索・中継経路・グラフ）。**開いた時点の値で凍結し、見えている値で実行する**のがこのアプリの流儀。 *One screen the app opens (launcher, map, multiple paths, scenario, relay path, graph). It freezes its values when opened and runs with what is on screen — the house style of this app.* | Window | 窓 |
| ドキュメント | ヘルプから開く同梱の文書。**実行形態で中身が変わる**（バイナリ版は利用者向け・ソース実行は開発者向け）ので、画面では**どちらも指す上位語**で呼ぶ。 *The bundled documents opened from Help. What they contain depends on how the app runs (user manual for the binary, developer docs from source), so the screen uses the term that covers both.* | Documentation | README / マニュアル |

## 文書でも 1 語にそろえる

*Words that the prose of the public documents must also keep to one form.*

**上の表は「画面に出したら失敗」の規則**なので、文書の散文には効かない（説明のために
禁止語そのものを引く行があり、`マニュアル` のように文書では普通に使う語もある）。
**文書の散文まで縛るのはこの表だけ**＝画面と文書で同じものを 2 語で呼んでいた組。

*The table above is a rule about what may reach the screen, so it cannot be applied to the
prose of the documents: some lines quote a banned word in order to explain it, and a word
like `マニュアル` is perfectly normal in prose. Only the pairs below — where the screen and
the documents called one thing by two names — are enforced in prose as well.*

| 使う | 使わない | 経緯 |
|---|---|---|
| ウィンドウ | 窓 | 2026-08-14 のユーザー指摘。文書 113 か所・画面 6 か所が短いほうの語で、同じ 1 つのものを 2 語で呼んでいた |

**この表は `tests/test_docs_consistency.py` が公開文書に対して機械で守る**（画面側は
上の表と `tests/test_i18n_glossary.py`）。⚠️ コードフェンスの中と表そのものは対象外
——ログや出力の実物を引用した行まで書き換えさせないため。

*`tests/test_docs_consistency.py` enforces this table across the public documents (the
screen side is covered by the table above and `tests/test_i18n_glossary.py`). ⚠️ Code fences
and table rows are exempt, so that lines quoting real log or output text are left alone.*

## この表ができた経緯

*Where this table came from.*

2026-08-01 に、**同じものを 2 語で呼んでいた**欠陥が 1 セッションで 4 件出た。
そのうち「区間」は、数字（リンクの本数）が正しいのに**語だけが別の量として伝わる**
という形の表記バグで、2.6b で `ホップ` → `区間` へ直した。
残りのうち**画面だけで閉じる組は 2.7〜2.8a1 で片付けた**。
**成果物にも出る 5 組は 3.0a1（2026-08-26）で片付けた**＝語を動かすと
レポートの字が動くので、出力契約の版までまとめて据え置いていた組。
**同じ回で、機能そのものの名前が食い違っていた最後の 2 件も揃えた**
（レポートの `一括シミュレーション` → `複数経路` ／ メニューの `シングル` → `個別`）。

*On 2026-08-01, four defects of the same shape — one thing called by two words — surfaced in
a single session. In one of them (“section”) the number was right but the word carried it as
a different quantity; it was renamed from `ホップ` to `区間` in 2.6b. The pairs that stayed
inside the screen were cleared in 2.7–2.8a1, and the five pairs that also reached the reports
were cleared in 3.0a1, the release where changing output wording was on the table.*

**1 か所だけ直すと語彙が 3 通りに増える**——だから直したあと、語の統一を機械で縛る。

*Fixing one place out of three leaves you with three wordings, not one — which is why the
rule is handed to a gate as soon as the fix lands.*
