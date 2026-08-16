<#
.SYNOPSIS
  独立レビュー（Codex）を Claude 側から非対話で駆動する。

.DESCRIPTION
  feedback_independent_review の「独立性を壊さない渡し方」を、約束ではなく構造で守る:

    1. 入力文は tools/codex_review/prompt_*.txt から読む。実行のたびに書き起こさないので、
       その場の観点（「DPI 周りを見て」等）を混ぜる余地が無い。差し込むのは差分のパスと
       比較元のラベルだけ。
    2. 渡すのは git が作った生の差分。要約も抜粋もしない。
    3. 返答は読む前に .qa/codex_review/round<N>_codex_raw.md へ原文で落ちる。
       あとから私の要約と原文を突き合わせられる（過少に数えた実例が 2026-07-26 にある）。
    4. -s read-only 固定。Codex に直させない（発見は Codex・処方はこちら）。
    5. codex exec は毎回あたらしいセッション。前の巡の指摘に引きずられない。

.NOTES
  ⚠️ シェル実行は**間欠**（2026-08-16 訂正）。CreateProcessAsUserW error 1312 で
  落ちる呼び出しがある一方、素直な形（`<python.exe> -m pytest …`）は通る。
  8 巡目で Codex は実際に pytest を完走させ 293 件の成功を確認している。
  🔴 **7 巡目の記録「シェルが動かない」は誤り**＝1 回の失敗から一般化した。
  ⇒ **Codex 側の裏取りは付くこともあるが、当てにしない。** こちらで必ず実測する。

  ⚠️ **読み取り範囲は狭まらない**（2026-08-16・canary で実測）。`-C` は作業
  ディレクトリを決めるだけ、`-s read-only` は「書けない」であって「ここしか
  読めない」ではない。docs モードの staging は範囲を*明示*する仕掛けであって
  秘匿の保証ではない（詳細は下の docs 分岐のコメント）。

.PARAMETER Mode
  code = ①コード面（差分）／ docs = ②ドキュメント/メモリ面

.PARAMETER Base
  code のみ。比較元の ref（既定 HEAD~1）。RC/正式の工程 4b は前の正式タグを渡す。

.PARAMETER Round
  巡番号。省略時は .qa/codex_review/ の既存 raw から自動採番。

.PARAMETER DryRun
  差分と入力文だけ作り、Codex を呼ばずに終わる。

.PARAMETER OutDir
  差分と原文の置き場（既定 `<repo>\.qa\codex_review`）。**検査から隔離した場所を
  指すために在る**（2026-08-16・Codex 11 巡目 P1）＝実行時検査が実リポジトリの
  成果物と同じ場所へ書き、後始末で `round<N>_*` を一括削除していたため、
  正当な巡の成果物を消し得た。運用では指定しない。

.EXAMPLE
  # 工程 4b の 1 巡目（前の正式タグから）
  & tools\codex_review\run.ps1 -Mode code -Base 2.7 -Round 1

.EXAMPLE
  # 大きく直した回の追加の巡（直前のコミットから）
  & tools\codex_review\run.ps1 -Mode code

.EXAMPLE
  # ②ドキュメント面（memory を写してから渡す）
  & tools\codex_review\run.ps1 -Mode docs
#>
[CmdletBinding()]
param(
    [ValidateSet('code', 'docs')][string]$Mode = 'code',
    [string]$Base = 'HEAD~1',
    [int]$Round = 0,
    [switch]$DryRun,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'

$toolDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)
$outDir   = if ($OutDir) { $OutDir } else { Join-Path $repoRoot '.qa\codex_review' }
$memSrc   = $env:RADIOSIM_MEMORY_DIR

# --- codex.exe を見つける（VS Code 拡張が同梱している実体。npm 導入は不要） ---
#
# ⚠️ 拡張の bin\windows-x86_64 を使う。~\.codex\.sandbox-bin\codex.exe も同一版だが、
#    そちらには codex-code-mode-host.exe が居らずファイル読み取りが fail closed になる
#    （実測 2026-08-15＝差分を読めずレビュー不能で戻ってきた）。ホストは codex.exe の
#    隣を見に行くので、両方が揃っている拡張の bin から起動するのが唯一の条件。
#
# ⚠️ 版の選び方は**意味的バージョンで比較**する（2026-08-16・Codex 8 巡目 P2）。
#    ディレクトリ名の文字列順で並べると `…-26.810.9` が `…-26.810.10` より
#    新しいと判定される。VS Code の更新で複数版が残ったときに古い CLI を掴み、
#    sandbox やオプションの挙動が再現しなくなる。
$codex = $env:CODEX_EXE
if (-not $codex) {
    $cand = Get-ChildItem (Join-Path $env:USERPROFILE '.vscode\extensions') -Directory -ErrorAction SilentlyContinue |
        Where-Object Name -like 'openai.chatgpt-*' |
        ForEach-Object {
            $v = $null
            if ($_.Name -match 'openai\.chatgpt-(\d+(?:\.\d+)*)') {
                [void][version]::TryParse($Matches[1], [ref]$v)
            }
            [pscustomobject]@{
                Version = if ($v) { $v } else { [version]'0.0' }
                Exe     = Join-Path $_.FullName 'bin\windows-x86_64\codex.exe'
            }
        } |
        Where-Object { (Test-Path $_.Exe) -and (Test-Path (Join-Path (Split-Path $_.Exe) 'codex-code-mode-host.exe')) } |
        Sort-Object Version -Descending
    $codex = ($cand | Select-Object -First 1).Exe
}
if (-not $codex -or -not (Test-Path $codex)) {
    throw "codex.exe（code-mode host 同梱）が見つかりません。`$env:CODEX_EXE で明示できます。"
}
if (-not (Test-Path (Join-Path (Split-Path $codex) 'codex-code-mode-host.exe'))) {
    throw "codex-code-mode-host.exe が codex.exe の隣にありません: $codex （このままではファイルを読めません）"
}
Write-Host ("codex: {0}" -f $codex)

if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

# --- 巡番号の自動採番 ---
#
# ⚠️ 数えるのは**成立した巡だけ**（2026-08-16・Codex 9 巡目 P2）。異常終了した回の
#    raw は下で `…_codex_FAILED_<時刻>.md` へ退避するので、この glob には掛からない。
#    退避しないと「成立していない」と throw した回が採番を 1 つ進めてしまい、
#    しかも同じ `-Round` で再実行すると「既にあります」で弾かれる＝**失敗した巡が
#    番号を食ったまま再試行できない**という、8 巡目の直しが作った矛盾になる。
if ($Round -le 0) {
    $used = Get-ChildItem $outDir -Filter 'round*_codex_raw.md' -ErrorAction SilentlyContinue |
        ForEach-Object { if ($_.Name -match '^round(\d+)_') { [int]$Matches[1] } }
    $Round = 1 + (($used | Measure-Object -Maximum).Maximum)
}

$rawPath = Join-Path $outDir ("round{0}_{1}_codex_raw.md" -f $Round, $Mode)
if (Test-Path $rawPath) { throw "既にあります（巡番号を確認してください）: $rawPath" }

# --- モードごとに「渡すもの」と作業根を決める ---
if ($Mode -eq 'code') {
    # 作業根はリポジトリ本体。差分の外まで読みに行くのは止めない
    # （2.5 の回は差分 2 行で、全体を読んで実在の欠陥 2 件を出した）。
    $workRoot = $repoRoot

    $safeBase = ($Base -replace '[^\w.\-]', '_')
    $diffPath = Join-Path $outDir ("round{0}_diff_{1}_to_HEAD.diff" -f $Round, $safeBase)

    # ⚠️ パイプで直に書かない（2026-08-16）。差分が空だと Set-Content が
    #    **ファイルを作らない**ので、次の Get-Item が「パスが存在しません」で落ち、
    #    *base の指定を間違えた*という本当の原因が見えなかった（実際に踏んだ＝
    #    9 巡目で HEAD 自身を base に渡した）。⇒ 先に文字列で受けて自分で判定する。
    Push-Location $repoRoot
    try {
        $diffText = (git diff "$Base..HEAD" | Out-String)
        if ($LASTEXITCODE -ne 0) { throw "git diff が失敗しました（ref を確認してください）: $Base..HEAD" }
    } finally { Pop-Location }

    if ([string]::IsNullOrWhiteSpace($diffText)) {
        throw ("差分が空です: $Base..HEAD" +
               " ⇒ base が HEAD 自身か、変更が git 管理外（ISSUES.md・memory は追跡していません）。" +
               " 直したコミットそのものを見せたいなら base は 1 つ前（例 `<sha>^`）です。")
    }
    Set-Content -LiteralPath $diffPath -Value $diffText -Encoding UTF8
    $bytes = (Get-Item $diffPath).Length
    Write-Host ("差分: {0}  ({1:N1} KB)" -f $diffPath, ($bytes / 1KB))

    # ⚠️ **リポジトリ配下のときだけ相対化する**（2026-08-16・Codex 12 巡目 P1）。
    #    `-OutDir` を足した回に、ここが「差分は必ず repo の下」という前の前提の
    #    ままだった＝外部ディレクトリを指すと `Substring` が無関係な位置で切り、
    #    **存在しないパスを入力文に渡して Codex が差分を読めない**。
    #    作業根（`-C`）は repo なので、外なら絶対パスで渡すのが正しい。
    $relDiff = if ($diffPath.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        $diffPath.Substring($repoRoot.Length).TrimStart('\')
    } else {
        $diffPath
    }
    $prompt  = (Get-Content (Join-Path $toolDir 'prompt_code.txt') -Raw -Encoding UTF8).
                    Replace('{DIFF_PATH}', $relDiff).Replace('{BASE}', $Base)
}
else {
    # ⛔ ISSUES.md を渡さない（未修正の脆弱性・実機スクショを持つ）。
    #
    # 🔴 **2026-08-16 訂正＝これは「保証」ではない。** 当初このコメントは、
    #    作業根を staging に切れば親の ISSUES.md には手が届かない、という趣旨の
    #    強い言い方をしていた。**Codex 8 巡目 P1 の指摘どおり誤りだった**
    #    （その断定の文言は `tests/test_codex_review_tool.py` が禁止語として
    #    持っているので、ここには書き写さない）。`-C` は作業ディレクトリを
    #    決めるだけで読み取り範囲を狭めず、`-s read-only` は「書けない」であって
    #    「ここしか読めない」ではない。**canary で実測して確認済み**＝staging の
    #    3 階層上に置いたファイルを Codex はそのまま読み出した
    #    （`tests/test_codex_review_tool.py` に実測の記録）。
    #
    # ⇒ いま staging がしているのは **①渡す範囲を明示すること ②リポジトリを
    #    作業根にしないこと**（自分で歩くときの起点が repo でなくなる）だけ。
    #    **ISSUES.md を渡さないのは運用規則**であり、手渡し時代と強度は同じ。
    #    強度を上げたと書かないこと（[[feedback-promote-recurring-checks]] 実証31
    #    ＝「ここまでは大丈夫」は反例 1 つで嘘になる）。
    #
    # staging はリポジトリの外に置く。中に置くと、Codex が親を 1 つ上がるだけで
    # ISSUES.md に届く配置になる（届けること自体は上のとおり止められないが、
    # **偶然踏む確率**は下げられる）。
    #
    # ⚠️ 名前は clone とプロセスで分ける（2026-08-16・Codex 9 巡目 P2）。固定名だと
    #    `%TEMP%` は全 clone 共通なので、別 clone や 2 本目の実行が**先発の staging を
    #    再帰削除**し、Codex が欠損中の文書や別 clone の文書を読み得る。
    #    リポジトリ内に置いていた頃は clone ごとに分かれていたので、**外へ出した
    #    ことで失った分離を名前で取り戻す**（8 巡目の直しが作った退行）。
    $repoTag = ([BitConverter]::ToString(
        [Security.Cryptography.SHA1]::HashData(
            [Text.Encoding]::UTF8.GetBytes($repoRoot.ToLowerInvariant()))
    ) -replace '-', '').Substring(0, 8).ToLowerInvariant()
    $stagePrefix = "radiosim_codex_doc_review_$repoTag"
    $workRoot = Join-Path ([IO.Path]::GetTempPath()) ("{0}_{1}" -f $stagePrefix, $PID)
    $memDst   = Join-Path $workRoot 'memory'

    # ⛔ **他プロセスの staging を消さない**（2026-08-16・Codex 10 巡目 P1）。
    #    9 巡目の直しは `$PID` でパスを分けたのに、掃除のほうは「自分以外を全部」
    #    消しており、**直したはずの競合が同一 clone 内でそのまま残っていた**
    #    （分離した意味が掃除で打ち消されていた）。
    # ⇒ 掃除してよいのは **PID がもう生きていない** staging だけ。
    Get-ChildItem ([IO.Path]::GetTempPath()) -Directory -Filter "$stagePrefix*" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -ne $workRoot } |
        ForEach-Object {
            $owner = 0
            if ($_.Name -match '_(\d+)$') { $owner = [int]$Matches[1] }
            $alive = $owner -gt 0 -and (Get-Process -Id $owner -ErrorAction SilentlyContinue)
            if (-not $alive) { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
        }
    if (Test-Path $workRoot) { Remove-Item $workRoot -Recurse -Force }
    New-Item -ItemType Directory -Path $memDst -Force | Out-Null

    # memory はワークスペースの外にあるので写す（写しが古くならないよう毎回作り直す）
    Copy-Item (Join-Path $memSrc '*.md') -Destination $memDst -Force

    # 公開文書 5 本 ＋ CHANGELOG は実体をコピー（staging の外は見せない）
    $docs = @(
        'README.md', 'CHANGELOG.md',
        'docs\manual_ja.md', 'docs\manual_en.md',
        'docs\developer_ja.md', 'docs\developer_en.md'
    )
    foreach ($d in $docs) {
        $src = Join-Path $repoRoot $d
        if (-not (Test-Path $src)) { throw "渡す文書が見つかりません: $d" }
        $dst = Join-Path $workRoot $d
        $dstDir = Split-Path -Parent $dst
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
        Copy-Item $src $dst -Force
    }

    $n  = (Get-ChildItem $memDst -Filter '*.md').Count
    $kb = ((Get-ChildItem $workRoot -Recurse -File | Measure-Object Length -Sum).Sum) / 1KB
    Write-Host ("渡す範囲: memory {0} 本 ＋ 公開文書 5 本 ＋ CHANGELOG  ({1:N1} KB)" -f $n, $kb)
    Write-Host ("staging: {0}（リポジトリの外）" -f $workRoot)
    Write-Host "⚠️ ISSUES.md は渡していません（ただし read-only は任意のパスを読めるので、これは運用規則であって保証ではありません）"

    $prompt = Get-Content (Join-Path $toolDir 'prompt_docs.txt') -Raw -Encoding UTF8
}

Write-Host ''
Write-Host '--- Codex へ送る入力文（prompt_*.txt が正典・その場では足さない） ---'
Write-Host $prompt
Write-Host '--------------------------------------------------------------------'
Write-Host ''

if ($DryRun) { Write-Host "DryRun: Codex は呼びませんでした。"; return }

$codexArgs = @(
    'exec',
    '-s', 'read-only',          # 直させない（発見は Codex・処方はこちら）
    '-C', $workRoot,
    '-o', $rawPath
)
if ($Mode -eq 'docs') { $codexArgs += '--skip-git-repo-check' }
$codexArgs += $prompt

Write-Host ("Codex 実行中（{0} 巡目 / {1}）… 返答は原文のまま {2} へ落ちます" -f $Round, $Mode, $rawPath)
& $codex @codexArgs
$rc = $LASTEXITCODE

Write-Host ''
$wrote = (Test-Path $rawPath) -and ((Get-Item $rawPath).Length -gt 0)
if ($wrote) { Write-Host ("原文: {0}" -f $rawPath) }

# ⚠️ 異常終了は必ず失敗にする（2026-08-16・Codex 8 巡目 P2）。
#    以前は raw が非空かどうかだけを見ていたので、**途中まで書いて落ちた回**が
#    「レビュー完了」として通り、巡を 1 つ消化したことになってしまった。
#    ⇒ raw は診断用に残したうえで throw する（黙って握りつぶさない）。
# 成立しなかった raw は採番の対象から外す（9 巡目 P2）。残すのは診断のため。
# ⚠️ **サイズを条件にしない**（2026-08-16・Codex 10 巡目 P2）。9 巡目の実装は
#    「非空なら退避」だったので、**空の raw が作られて落ちた回**は退避されず
#    `…_codex_raw.md` のまま残り、採番を進めたうえ再実行も拒否した
#    ＝退避を入れた目的をそのまま取りこぼしていた。存在すれば必ず退避する。
function Move-FailedRaw {
    if (-not (Test-Path $script:rawPath)) { return $null }
    $p = Join-Path $script:outDir ("round{0}_{1}_codex_FAILED_{2}.md" -f
        $script:Round, $script:Mode, (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    Move-Item -LiteralPath $script:rawPath -Destination $p -Force
    return $p
}

if ($rc -ne 0) {
    $failPath = Move-FailedRaw
    throw ("Codex が異常終了しました（exit=$rc）。" +
           $(if ($failPath) { "書かれかけの raw は $failPath へ退避しました（採番には数えません）" }
             else { "raw は書かれませんでした" }) +
           " ⇒ この巡は成立していません。同じ -Round $Round で回し直せます。")
}
if (-not $wrote) {
    $failPath = Move-FailedRaw
    throw ("Codex は正常終了しましたが返答を書きませんでした。" +
           $(if ($failPath) { "空の raw は $failPath へ退避しました（採番には数えません）" }
             else { "raw は作られませんでした: $rawPath" }) +
           " ⇒ この巡は成立していません。同じ -Round $Round で回し直せます。")
}
Write-Host '次の一手: 原文のまま ISSUES.md へ起票 → 実測で裏取り → 指摘のクラス全出現箇所を洗う'
