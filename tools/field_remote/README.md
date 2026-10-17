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
- **記録の開始と終了は人が X13 で打つ**。SSH から起動したプロセスは、切断で一緒に止まる
  ことがあるため。Claude は SSH で読んで確かめる側。
- メモリ・フック・台帳はデスクトップにしかない＝Claude はデスクトップで動かす。

| ファイル | どこで動く | 役目 |
|---|---|---|
| `x13_ssh_setup.ps1` | X13（管理者・1 回だけ） | OpenSSH サーバー・受け付けは Tailscale（100.64.0.0/10）だけ・鍵だけ・既定のシェルは PowerShell |
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
3. **記録を始める（X13 で人が打つ）**
   ```
   cd C:\dev\radiosim-repo
   .venv\Scripts\python.exe -m apps.field.cli gnss log --port COM? --root C:\dev\field-data\gnss
   ```
   「記録中はスリープせず画面も消しません」と出ることを確かめる。
   NTRIP の補正も流すときは `--ntrip host:port/接続先の名前` を足す（例 `--ntrip geortk.jp:2101/tamtam`）。
   利用者名の要る caster は `--ntrip-user 名前` も足し、パスワードは先に
   `$env:RADIOSIM_NTRIP_PASSWORD = '…'` で入れる（引数・ファイルには書かない）。
   補正が切れても記録は続き、つなぎ直す。届いた補正はフォルダの `ntrip.rtcm` に残る。
4. Claude が `x13_watch.ps1` で様子を見る。最後の受信からの秒数・RAWX の欠け・
   検査和の不一致・解の状態（補正を流していれば RTK Float／Fix と補正の最後の受信）を報告する。
5. 終えるときは X13 で `q`。受信機は始めの速度と NMEA ありへ戻る（RAM 層）。

## 回線が切れたら

記録はそのまま続く。つながり直したら、Claude がもう一度 `x13_watch.ps1` を回す。
欠けがあるかは生ログが正（表示は数えるだけ）。

## 記録の持ち帰り

生ログは X13 の `C:\dev\field-data\gnss\gnss-…\` に残る（リポジトリの外）。
後処理の段で、デスクトップへ `scp -r` で写す。
