# 野外で Claude が支える仕組み（X13 ＋ SSH）

三角点・水準点での GNSS の受け入れ試験のように、デスクトップを持ち出せない現場で、
デスクトップの Claude Code が X13 の記録を確かめるための道具と手順。

```
 GNSS ──USB── X13 ──(テザリング)──<internet>── デスクトップ（Claude Code）
               │                                    ▲
               └─ gnss log を X13 で回す             │ Tailscale 越しの SSH（読むだけ）
                  （生ログは X13 の手元に書く）        │
 人（スマホか X13 のブラウザ）── Remote Control ───────┘
```

- **記録は X13 の手元で完結させる**。COM ポートを網越しに引かない＝`gnss_index.csv` の
  受信時刻に回線の揺れが混ざり、瞬断で RAWX が抜けるため。回線が切れても記録は続く。
- **記録は Claude が SSH から切り離して起動し、秒数で止める**（`timed_log.py`）。SSH の中で
  直に起動したプロセスは切断で一緒に止まることがあるため、`Win32_Process` の `Create` で
  SSH のセッションの外に立てる（SSH を閉じても記録は続く）。止め方は `q` と同じ道＝秒数が
  来ると受信機を戻してから終わる。人が X13 で打つ道も残る（「人が X13 で打つとき」）。
- メモリ・フック・台帳はデスクトップにしかない＝Claude はデスクトップで動かす。

| ファイル | どこで動く | 役目 |
|---|---|---|
| `x13_ssh_setup.ps1` | X13（管理者・1 回だけ） | OpenSSH サーバー・受け付けは Tailscale（100.64.0.0/10）だけ・鍵だけ・既定のシェルは PowerShell |
| `timed_log.py` | X13（SSH から切り離して起動） | `gnss log` のキー入力だけを「N 秒たつか止めの合図のファイルが置かれたら `q`」に差し替えて回す |
| `x13_watch.ps1` | デスクトップ | SSH で X13 の最新の記録の様子・記録の処理の有無・電池と空き容量を見る |
| `gnss_peek.py` | X13（`x13_watch.ps1` が呼ぶ） | 生ログを記録器と同じ数え方で読む（読むだけ） |

接続先はデスクトップの `~/.ssh/config` の `x13`（鍵 `~/.ssh/id_ed25519_x13`）。

## 出発前（家で）

1. **X13 のコードを最新にする**
   ```
   cd C:\dev\radiosim-repo
   git pull
   ```
2. **X13 を満充電にする**。記録中は `gnss log` がスリープと画面の消灯を止める。
   **蓋を閉じたときのスリープは防げない**＝現場では蓋を開けたままにする。
3. **デスクトップ**：電源を入れたままにし、Windows Update を一時停止する
   （デスクトップが落ちると Claude も落ちる。記録は続く）。
   Claude Code のセッションを立てて Remote Control をつなぐ。
4. **リハーサル**（初回だけ）：X13 をスマホのテザリングにつなぎ、Remote Control から
   Claude に `x13_watch.ps1` を回してもらう。届けば準備は終わり。

## 現場で

1. アンテナを据え、受信機を USB で X13 につなぐ。
2. X13 をテザリングにつなぎ、Tailscale が接続中かを見る。Claude に「着いた」と伝える。
   Claude が SSH で COM 番号を確かめる。
3. **記録を始める（Claude が SSH から切り離して起動する）**。人と秒数を決めてから、
   X13 の PowerShell で（SSH 越しは `-EncodedCommand` で渡す＝`x13_watch.ps1` と同じ理由）
   ```
   $log = Join-Path $env:TEMP 'timed_log_run.txt'
   $cmd = "cmd /c .venv\Scripts\python.exe tools\field_remote\timed_log.py 1800 --port COM? --root C:\dev\field-data\gnss > `"$log`" 2>&1"
   Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = 'C:\dev\radiosim-repo' }
   ```
   `ReturnValue` が 0 なら立った。出力は `timed_log_run.txt`（UTF-8＝`Get-Content -Encoding UTF8 -Tail 5`）。
   「記録中はスリープせず画面も消しません」と出ることを確かめる。
   NTRIP の補正も流すときは `--ntrip host:port/接続先の名前` を足す（例 `--ntrip geortk.jp:2101/tamtam`）。
   利用者名の要る caster は `--ntrip-user 名前` も足す。補正が切れても記録は続き、つなぎ直す。
   届いた補正はフォルダの `ntrip.rtcm` に残る。
   ⚠️ **パスワードは人が X13 で利用者の環境変数に入れておく**＝切り離した起動には SSH の中で
   入れた `$env:…` が届かない（2026-10-17 に X13 で確かめた）。引数・ファイルには書かない。
   ```
   [Environment]::SetEnvironmentVariable('RADIOSIM_NTRIP_PASSWORD', '…', 'User')   # 使い終えたら '…' を $null にして消す
   ```
4. Claude が `x13_watch.ps1` で様子を見る。最後の受信からの秒数・RAWX の欠け・
   検査和の不一致・解の状態（補正を流していれば RTK Float／Fix と補正の最後の受信）・
   記録の処理が走っているかを報告する。
5. 秒数が来ると `q` と同じ道で終わる＝受信機は始めの速度と NMEA ありへ戻る（RAM 層）。
   `timed_log_run.txt` の末尾にまとめ（記録のバイト数・RAWX の欠け・解の内訳）が出る。
   **途中で止めるときは止めの合図のファイルを置く**（SSH から X13 の PowerShell で）
   ```
   New-Item (Join-Path $env:TEMP 'timed_log.stop') -ItemType File
   ```
   1 秒以内に「止めの合図（…）を受けました」と出て、秒数が来たときと同じ道で終わる。
   合図のファイルは読んだら消える（起動のときに残っていても消してから始める）。
   ⚠️ **プロセスを殺さない**＝受信機は生データを出したまま残り、次の起動が
   「MON-VER に応えません」で止まり得る。殺したら USB を挿し直す（RAM 層が消えて戻る）。

### 人が X13 で打つとき

SSH が届かないときなど。X13 の画面で打ち、終えるときは `q`。パスワードは
`$env:RADIOSIM_NTRIP_PASSWORD = '…'` でその窓にだけ入れればよい。
```
cd C:\dev\radiosim-repo
.venv\Scripts\python.exe -m apps.field.cli gnss log --port COM? --root C:\dev\field-data\gnss
```
⚠️ SSH の中でこの行を直に打たない＝切断で記録が止まることがある。

### 水準点 `3857` での受け入れの当日の手順

決めたこと（2026-10-18）＝`tamtam` を同時につなぐ・写真 4 種。
（2026-10-20）＝**現地で後処理し、Fix を見てから撤収する**・記録は 60 分以上で JST の 45 分に止める・
待つあいだに 2 本目の記録を回す。
（2026-10-24）＝10-24 の予定を天候不良で **10-25** へ延ばした。据え付けは**測量ポール**
（アンテナ取付面＝ARP から石突の先まで **1060 mm**・上端の銀色の部品を含む・三脚の挟みで支える・
円形気泡つき）＝**アンテナ高は 1.060 m に決まり、巻尺では測らない**。代わりに石突が標石の頂部に
乗っているかと気泡を 3 回確かめる。傾きは高さにほぼ響かない（0.5° で 0.04 mm・水平は 9 mm だが比べない）。
高さを比べる本番の値は、電子基準点 鹿野山（6.8 km）の 30 秒値での後処理
（`experiments/field_ppk.py`）。`tamtam`（40.6 km）は「長い基線で Fix するか」の試しで、
Fix しなくても本番に響かない。

なぜ現地で解くか＝帰ってから「データが使い物にならなかった」と分かっても、もう一度行くしかない。
現地で本番と同じ記録・基準局・道具・設定で解いて Fix すれば、帰って解き直しても Fix する。
ただし「正しい高さか」は現地でも分からない＝それは水準点との比較そのもので見る。

当日の流れ（例＝10:20 に記録を始めたとき）
```
10:20 ── 1 本目（60 分以上）── 11:45 止める
                                 ├ アンテナ高（終わり）→ 2 本目を始める
                                 ├ tamtam で見当（数分）
                                 └ 5 分ごとに鹿野山のファイルを見る
                   12:00〜12:38 ごろ ファイルが出る → 鹿野山で解く
                                 └ Fix → 2 本目を止める → アンテナ高（3 回目）→ 撤収
```
撤収できる時刻は**記録を始めた時刻で決まる**。後処理の窓は記録の前後 10 分で、その終わり
（例 11:55）を含む時（GPS 時刻）のファイルは、時が明けてから出る（2026-10-18 に分かったのは
「38 分以内」という上限だけ）。60 分で止めても待つ時間が延びるだけ＝その時間を記録に回す。
止めるのが JST の 45 分なのは、49 分 12 秒を過ぎると窓の終わりが次の時へはみ出し、
もう 1 時間待つことになるため（GPS 時刻は JST の秒より 18 秒進み、窓は 30 秒へ外向きに丸める）。

0. **出発前（家で・Claude）**
   - X13 を最新にする（`git pull`）＝`field_ppk.py solve` が、型の来ない局（`tamtam`）を基準局に
     据えても解ける直し（2026-10-20）が要る。
   - デスクトップの `D:\dev\field-data\gnss\bm-3857\` の `site.json`・`site_tamtam.json` を、
     X13 の `C:\dev\field-data\gnss\bm-3857\` へ写す（`scp`）。`rover.arp_height` は `1.060`・
     `base.obs` と `base_alt.obs` は当日の通日（10-25＝`298`）を入れてある。
   - X13 で `field_ppk.py setup`・`field_geonet.py setup` を回し、どちらも「既にある」と答えるか
     （置き場 `%LOCALAPPDATA%\radiosim_ppk` は 2026-10-18 に整えた・資格情報 `terras.json` もそこ）。
   - 現地に居る時間は 2〜3 時間になる＝X13 とスマホ（テザリング）を満充電にする。
1. **据える**＝ポールの石突を標石の頂部（突起のてっぺん）に立て、三脚の挟みで支えて気泡を円の中へ。
   向きは TNC の溝の側を磁北へ（ポールごと回す）・矢印の磁方位を 1 つ記録する。電柱の側に物を置かない。
2. **石突と気泡（1 回目）**＝石突が頂部に乗っているか・気泡が円の中かを見て、両方の近景を撮る。
3. **1 本目の記録を始める**＝「現場で」の 3 を `--ntrip geortk.jp:2101/tamtam` で。
   秒数は Claude が数える＝始める時刻から 60 分以上たった最初の JST の 45 分まで（60〜119 分・
   例 10:20 に始めれば 11:45 まで＝`5100`）。テザリングが要る（記録そのものは切れても続く）。
4. **写真**（記録のあいだに）＝①据えた全景（三脚・アンテナ・電柱と家が入る引きの絵）
   ②標石とアンテナの真上の近景 ③石突が標石に当たっている所と気泡（2・6・11 の 3 回）
   ④四方の上空（東西南北）。
5. **止まったら**＝`timed_log_run.txt` の末尾のまとめを確かめる。
6. **石突と気泡（2 回目）**＝2 と同じに見て撮る。**石突が頂部から外れていたら 1 本目は高さに使えない**
   （外れて落ちた分だけ高さがずれる）＝人と Claude で据え直すかを決める。
7. **2 本目の記録を始める**＝ポールと三脚に触れずに、3 と同じ起動を秒数 `7200` で
   （撤収のときに止めの合図で止める）。1 本目が Fix しなければ延長がもう始まっていて、
   Fix すれば同じ据え付けでもう 1 回分の高さが取れる（繰り返しの確かめ・解くのは帰ってから）。
8. **見当をつける（Claude・数分）**＝1 本目を `tamtam` を基準局にして解く
   （`rover.arp_height` はポールの長さ `1.060` が入っている＝書き換えない）。
   ```
   cd C:\dev\radiosim-repo
   .venv\Scripts\python.exe experiments\field_ppk.py rinex <1 本目> --out C:\dev\field-data\gnss\bm-3857\ppk
   .venv\Scripts\python.exe experiments\field_ppk.py rinex <1 本目> --rtcm --out C:\dev\field-data\gnss\bm-3857\ppk
   .venv\Scripts\python.exe experiments\field_ppk.py solve C:\dev\field-data\gnss\bm-3857\site_tamtam.json --out C:\dev\field-data\gnss\bm-3857\ppk-tamtam
   ```
   `<1 本目>`＝`C:\dev\field-data\gnss\gnss-…\`。`rinex --rtcm` が出す局の申告が
   `site_tamtam.json` の `base` と違えば、その日の値に替えて解き直す。
   見るのは **Fix するかだけ**（基線 40.6 km・高さの差には局の位相中心の分が乗る＝判定に使わない）。
   Fix しなくても構わないが、解が 1 つも出ないなら記録そのものを疑う材料になる。
   ここまでの形は 2026-10-20 に 10/17 の自宅の記録で通した（`tamtam` から 6.7 km・1620 回のうち Fix 1522）。
9. **鹿野山のファイルを待つ（Claude・5 分ごと）**
   ```
   .venv\Scripts\python.exe experiments\field_geonet.py fetch 3045 --from-log <1 本目> --out C:\dev\field-data\gnss\bm-3857\base
   ```
   まだ出ていなければ「要るファイルがありません（要る時＝…）」と、その時にある名前を出して止まる。
   取れたら富津（`3036`）も同じ形で 1 回試す（出ていなければ帰ってから取る）。
   **メモに書く**＝出ていなかった最後の試しと、取れた試しの時刻（区切りから何分で出るかの
   上限と下限＝2026-10-18 は上限 38 分だけ分かっている）。「⚠️ 回欠けている」と出たら、
   その数もメモに書き、解く前に Claude が欠けの場所を見る（局の欠測か、上がりきっていないか）。
10. **本番と同じ設定で解く（Claude）**＝`site.json` の `base.obs` を `fetch` が書いた名前
    （10-25 なら `base/3045298<英字>.26o`）に書き換えて
    ```
    .venv\Scripts\python.exe experiments\field_ppk.py solve C:\dev\field-data\gnss\bm-3857\site.json
    ```
    目安＝すべての回が Fix（中野は 6.2 km・997 回すべて Fix・ratio 12.5）。標高と成果
    14.244 の差もここで出るが、2 点目へ行くかの判断は帰ってから（ロードマップ）。
11. **撤収するか決める**
    - Fix した → 2 本目を止める（止めの合図）→ **石突と気泡（3 回目）** → 撤収。
    - Fix しない → 人と Claude で決める＝2 本目をそのまま延ばす（据え付けを替えない）か、
      据え直す（2 本目を止め、1 からやり直す）。材料＝8 の見当（`tamtam` でも解けないか）・
      `timed_log_run.txt` のまとめ（RAWX の欠け・解の内訳）・空の写真。
12. 持ち帰り＝「記録の持ち帰り」（2 本とも・`bm-3857` の `ppk`・`ppk-tamtam`・`base` も）。
    メモ＝石突と気泡の 3 回の様子・矢印の磁方位・ファイルが出た時刻・判断（自分の言葉のまま）。
    2 本目の基準局のデータは帰ってから取る。

### シールドルームでのステージ0・0b（無線機 2 台）の当日の手順

決めたこと（2026-10-19）＝ステージ0a（`bench run`）と 0b（基準器の絶対値）を同じ回で行う・
920 MHz と 2.4 GHz の両方・測る PC は X13・**人が一人で最後まで回せる形**（SSH が届けば
Claude が様子を見る。シールドルームの中や会社の網で届くかは分からない）。`bench analyze` と
校正ファイル作りは、帰ってから Claude が行う。各段の中身は `apps/field/BENCH.md`、
ここに書くのは当日の順番と、この回だけの決めごと。

2 台の呼び名（X13 の COM 番号・着いたら `.venv\Scripts\python.exe -m serial.tools.list_ports -v` の
`SER=` で確かめる）＝**A＝COM7＝`…92:AC`・B＝COM8＝`…F6:60`**。

**前もって職場で確かめる器**（借りられないものがあれば、出発前に Claude へ伝える）

| 器 | 使う所 |
|---|---|
| バーストを読める器（スペアナのゼロスパン・RBW 1 MHz 以上、またはピーク電力計） | 920 の送信出力。無ければ連続波の使い捨てのファームが要る（`BENCH.md` の「送信出力（920 MHz）」）＝家で準備するので早めに知らせる |
| 平均電力計（2.4 GHz を測れるもの） | 2.4 の送信出力（`cont`） |
| 損失を測る器（信号発生器と電力計、または VNA） | 921.4 MHz と 2437 MHz の 2 点 |
| 機械式のステップアッテネータ（10 dB 刻み 0〜110・1 dB 刻み 0〜11） | `bench run` の物差し |
| 固定アッテネータ 30 dB ×2・測定器の前に入れるパッド・シールドボックス ×2・50 Ω 終端器 ×2・SMA ケーブル | 配線 |
| （任意）LoRa か 11b を出せる信号発生器 | `rx_direct`（閉合チェック） |

**家から持って行くもの**＝X13 と AC アダプタ・2 台・USB ケーブル ×2・2.4 GHz 用の U.FL→SMA
ピッグテール ×2（付けたら外さない）・アンテナ（920 と 2.4 を 2 本ずつ）・ラベルを書くテープ。

**前の晩（家で）**
1. Claude が SSH で X13 を最新にし（`git pull --ff-only`）、2 台で `probe` を 920 と 2400 の両方で通す。
   飽和の上の端を測る計画 `C:\dev\field-data\plan920_top.json`（下の 6）も X13 に置く。
2. X13 を満充電にする（当日は AC につなぐ。AC ではスリープも画面の消灯も「なし」に設定済み）。

**当日の順番**（コマンドはすべて X13 の PowerShell で `cd C:\dev\radiosim-repo` してから打つ。
途中で止めても記録は残る＝足りない段は次の回へ回せる）

1. **着いたら**＝X13 を AC につなぐ。テザリングと Remote Control で Claude に「着いた」と伝えてみる
   （届かなければ一人で進める）。メモ用のファイル `C:\dev\field-data\bench\メモ.md` を作り、
   日時・室温・使う器の名前と型番と校正の期限を書く。固定アッテネータ・ケーブル・パッドには
   ラベルを貼る（損失を測った物と `bench run` で使う物を同じにするため）。
2. **損失を測る**（921.4 MHz と 2437 MHz の両方で・値はメモに書く）
   - 鎖全体＝A の SMA 端から B の SMA 端まで（固定 30 dB ×2・ケーブル・ステップアッテネータ）を
     **ステップを 0／0 にして**測る＝`fixed_loss_db`（ステップアッテネータの挿入損を含む）。
   - ステップアッテネータ単体＝**0 からの増分**を、10 dB 刻みのつまみ（10〜110）と 1 dB 刻みの
     つまみ（1〜11）で 1 段ずつ（もう一方のつまみは 0）。
   - 測定器の前に入れるパッド・（6 をするなら）固定アッテネータを抜いた鎖。
3. **920 MHz**
   1. 配線＝E220 の SMA 端どうしを `BENCH.md` の「構成」のとおりにつなぎ、2 台を箱に入れる。
      2.4 GHz のアンテナは外しておく。
   2. 動作確認（ステップは 0／0）
      ```
      .venv\Scripts\python.exe -m apps.field.cli probe --port-a COM7 --port-b COM8 --root C:\dev\field-data\probe-bench --band 920 --new-baseline
      ```
      `--root` を家の記録（`C:\dev\field-data\probe`）と分けるのは、配線した置き方を家の基準にしないため。
   3. 机上試験（1 時間強・画面の指示どおりにつまみを回して Enter）
      ```
      .venv\Scripts\python.exe -m apps.field.cli bench run --port-a COM7 --port-b COM8 --root C:\dev\field-data\bench --band 920
      ```
      SSH が届いていれば、Claude が `bench-<日時>\steps.csv` の進み具合を読む。
   4. 送信出力（バースト）＝1 台ずつ、SMA 端→パッド→測定器につなぐ。もう 1 台は RX にしておく。
      ```
      .venv\Scripts\python.exe -m apps.field.cli settings --port COM8 --band 920 --role rx
      .venv\Scripts\python.exe -m apps.field.cli settings --port COM7 --band 920 --role tx --power 13 --channel 0
      ```
      数分送らせてから、バースト（250 ms ごと・1 回 25〜46 ms）の高さを読む。`--channel` を 3・6 に
      変えて読み、役割を入れ替えて B でも同じく読む＝**6 つの読み**。メモには
      「個体・チャネル・読み（dBm）・パッド（dB）・器」を 1 行ずつ書く（`ref.json` へは帰ってから
      Claude が写す）。
4. **2.4 GHz**
   1. 配線＝ピッグテールの SMA 端どうしに付け替える（E220 の SMA 端は何もつながない）。
   2. 動作確認＝3-2 の `--band 920` を `--band 2400` に替えて。
   3. 机上試験＝3-3 の `--band 920` を `--band 2400` に替えて。
   4. 送信出力（`cont`）＝1 台ずつ、SMA 端→パッド→平均電力計につなぐ。もう 1 台は RX にする。
      ```
      .venv\Scripts\python.exe -m apps.field.cli settings --port COM8 --band 2400 --role rx
      .venv\Scripts\python.exe -m apps.field.cli settings --port COM7 --band 2400 --role tx --power 15 --channel 1
      .venv\Scripts\python.exe -m apps.field.cli cont --port COM7 --ref C:\dev\field-data\ref.json --pad-db <パッドの実測>
      ```
      60 秒たったら読みと器の名前を入力する（`ref.json` に自動で足される）。チャネル 6・11 と、
      役割を入れ替えた B でも同じく＝**6 回**。「1 Mbps 以外で出たフレーム」で止まったら、
      その回は測り直す。
5. **（任意）`rx_direct`**＝信号発生器を受信側の SMA 端へ直に入れ、受けた読みをメモに書く。
6. **（任意）920 の飽和の上の端**＝既定の計画では受信機への入力が最大 −47 dBm で、
   生値が −256 に回り込む強さに届かない。固定アッテネータを 2 本とも抜き、**送信 0 dBm** で入力を
   0 dBm（E220 のブロッキング電力の定格 10 dBm より 10 dB 下）から下げていく。
   - 先に 2 台を 0 dBm にし、ステップを 30 dB 以上にしてから固定アッテネータを抜く
     （13 dBm のまま抜くと、定格を超える入力が受信機に入る）。
     ```
     .venv\Scripts\python.exe -m apps.field.cli settings --port COM7 --band 920 --power 0
     .venv\Scripts\python.exe -m apps.field.cli settings --port COM8 --band 920 --power 0
     .venv\Scripts\python.exe -m apps.field.cli bench run --port-a COM7 --port-b COM8 --root C:\dev\field-data\bench --plan C:\dev\field-data\plan920_top.json --only coarse
     ```
   - 計画は 30 dB から 0 dB へ下げていく順（`coarse` の `atten_db`）。終わったら固定アッテネータを
     付け直してから、7 で電力を戻す。
7. **片付け**＝2 台を普段の形へ戻す（A＝TX・B＝RX・920・13 dBm・CH0）。
   ```
   .venv\Scripts\python.exe -m apps.field.cli settings --port COM7 --band 920 --role tx --power 13 --channel 0
   .venv\Scripts\python.exe -m apps.field.cli settings --port COM8 --band 920 --role rx --power 13 --channel 0
   ```
   記録は X13 の `C:\dev\field-data\`（`bench`・`probe-bench`・`ref.json`・`メモ.md`）に残る＝
   帰ってから Claude が `scp -r` でデスクトップへ写す。

メモに書くこと＝上の読みと損失のほかに、止まった・測り直した段（`--only` の指定も）・
エラーの全文（画面の写真でよい）・気づいたことや判断（自分の言葉のまま）。

## 回線が切れたら

記録はそのまま続く（2026-10-17 にテザリングを切って確かめた＝止まらず・欠けなし）。
つながり直したら、Claude がもう一度 `x13_watch.ps1` を回す。
欠けがあるかは生ログが正（表示は数えるだけ）。

## 記録の持ち帰り

生ログは X13 の `C:\dev\field-data\gnss\gnss-…\` に残る（リポジトリの外）。
後処理の段で、デスクトップへ `scp -r` で写す。
