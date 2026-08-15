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
  ⚠️ 既知の制約（2026-08-15 実測）＝**Codex はファイルを読めるがシェルを実行できない。**
  非対話で起動すると Codex 内蔵の Windows サンドボックスが CreateProcessAsUserW で
  error 1312（ログオンセッションが無い）を返す。elevated / unelevated の両方で再現し、
  こちらのサンドボックスを外しても変わらない＝対話セッションを持つ拡張との差。
  ⇒ 読解に基づく指摘は出るが、**Codex 側でテストを走らせた裏取りは付かない**
     （実際 7 巡目の返答が「指定 Python での再実行は完了できませんでした」と述べている）。
  裏取りはこちらでやる工程なので運用上の穴にはならない。どうしても走らせたい回だけ
  `--dangerously-bypass-approvals-and-sandbox` を足すことになるが、これは Codex に
  無制限のコマンド実行を許すのでこのスクリプトでは既定にしない。

.PARAMETER Mode
  code = ①コード面（差分）／ docs = ②ドキュメント/メモリ面

.PARAMETER Base
  code のみ。比較元の ref（既定 HEAD~1）。RC/正式の工程 4b は前の正式タグを渡す。

.PARAMETER Round
  巡番号。省略時は .qa/codex_review/ の既存 raw から自動採番。

.PARAMETER DryRun
  差分と入力文だけ作り、Codex を呼ばずに終わる。

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
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$toolDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)
$outDir   = Join-Path $repoRoot '.qa\codex_review'
$memSrc   = $env:RADIOSIM_MEMORY_DIR

# --- codex.exe を見つける（VS Code 拡張が同梱している実体。npm 導入は不要） ---
#
# ⚠️ 拡張の bin\windows-x86_64 を使う。~\.codex\.sandbox-bin\codex.exe も同一版だが、
#    そちらには codex-code-mode-host.exe が居らずファイル読み取りが fail closed になる
#    （実測 2026-08-15＝差分を読めずレビュー不能で戻ってきた）。ホストは codex.exe の
#    隣を見に行くので、両方が揃っている拡張の bin から起動するのが唯一の条件。
$codex = $env:CODEX_EXE
if (-not $codex) {
    $cand = Get-ChildItem (Join-Path $env:USERPROFILE '.vscode\extensions') -Directory -ErrorAction SilentlyContinue |
        Where-Object Name -like 'openai.chatgpt-*' |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'bin\windows-x86_64\codex.exe' } |
        Where-Object { (Test-Path $_) -and (Test-Path (Join-Path (Split-Path $_) 'codex-code-mode-host.exe')) }
    $codex = $cand | Select-Object -First 1
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

    Push-Location $repoRoot
    try {
        git diff "$Base..HEAD" | Set-Content -LiteralPath $diffPath -Encoding UTF8
        if ($LASTEXITCODE -ne 0) { throw "git diff が失敗しました（ref を確認してください）: $Base..HEAD" }
    } finally { Pop-Location }

    $bytes = (Get-Item $diffPath).Length
    if ($bytes -eq 0) { throw "差分が空です: $Base..HEAD" }
    Write-Host ("差分: {0}  ({1:N1} KB)" -f $diffPath, ($bytes / 1KB))

    $relDiff = $diffPath.Substring($repoRoot.Length).TrimStart('\')
    $prompt  = (Get-Content (Join-Path $toolDir 'prompt_code.txt') -Raw -Encoding UTF8).
                    Replace('{DIFF_PATH}', $relDiff).Replace('{BASE}', $Base)
}
else {
    # ⛔ ISSUES.md を渡さない（未修正の脆弱性・実機スクショを持つ）。
    #    「読まないでください」と頼むのではなく、作業根を staging に切って
    #    そもそも見えなくする。自動化で新しく増えた露出はここだけなので、ここは構造で塞ぐ。
    $workRoot = Join-Path $outDir 'doc_review'
    $memDst   = Join-Path $workRoot 'memory'

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
    Write-Host "ISSUES.md は staging の外（Codex からは見えません）"

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
if ((Test-Path $rawPath) -and (Get-Item $rawPath).Length -gt 0) {
    Write-Host ("原文: {0}" -f $rawPath)
    Write-Host '次の一手: 原文のまま ISSUES.md へ起票 → 実測で裏取り → 指摘のクラス全出現箇所を洗う'
} else {
    throw "Codex が返答を書きませんでした（exit=$rc）。$rawPath が空です。"
}
