<#
.SYNOPSIS
  内容が決まった実装を、作業票 1 枚ごとに新しい無人セッション（`claude -p`）で進める（I-181）。

.DESCRIPTION
  🔑 **司令塔を LLM にしない**。総コストは往復数の 2 乗で効く（[[feedback-token-budget]]）ので、
  親の会話が作業員を見張ると、その会話自体が長くなって高くつく。ここでは PowerShell が
  票を 1 枚ずつ取り、合否も自分で決める（司令塔のトークンは 0）。

    1. `main`（きれいな作業ツリー）から `auto/<版>-<票>` を切る。2 枚目からは直前の票の
       ブランチから切る＝積み重ね（後の票は前の票の結果に乗る）。人は最後のブランチを
       マージすれば全部が入る。
    2. 票 1 枚を渡して `claude -p` を起こす。入力文は prompt_worker.txt が正典
       （差し込むのは票のパス・ブランチ・結果ファイル・前回の試行の要約だけ）。
    3. 作業員が書いた結果ファイル（done / blocked / continue / failed）を読む。
    4. done なら**スクリプトが**確かめる＝ブランチ・作業ツリーがきれい・コミットが進んだ・
       差分が票の範囲の中・受け入れのテストが緑。
    5. 緑なら次の票。赤か failed なら、失敗の中身だけを渡して新しいセッションで 1 回だけ
       やり直す。それでも赤、または blocked なら、そこで止めて報告する。
       continue（予算の助言で区切った）は同じ票を新しいセッションで続ける（上限あり）。

  ⛔ push・PR・マージ・リリースはしない（人がする）。守りは 3 層:
    - 構造: 子プロセスだけ `remote.origin.pushurl` を無効な URL に差し替える（GIT_CONFIG_*）。
      fetch の先は変わらない（2026-09-26 に `git remote get-url` で確認）。
    - 規則: `--disallowedTools` で push・gh・merge・rebase・tag を拒む。⚠️ 字面の一致なので、
      別の綴りまでは塞げない＝保証ではなく二重の網。
    - 事後: 4 の検査（ブランチが替わっていたら赤）。
  ⛔ `--bare` は使わない＝フックを全部飛ばし（予算・言語・メモリの検査が効かなくなる）、
    OAuth でなく API キーでしか動かない。

.PARAMETER Tickets
  作業票のフォルダ（中の *.md を名前の順に回す）か、票 1 枚のパス。
  ブランチ名の <版> はフォルダの名前（.claude/tickets/3.8/01-foo.md → auto/3.8-01-foo）。
  票の置き場は追跡外の .claude/tickets/<版>/＝票は課題 ID を持つ（台帳と同じく非公開）。

.PARAMETER MaxTurns
  1 セッションの API 往復の硬い上限（票の max_turns が優先）。`--max-turns` はヘルプに
  出ない隠しオプションだが、2.1.282 で受け付けることを確かめた（未知のオプションは即エラー）。

.PARAMETER MaxContinue
  1 枚の票で continue を受け付ける回数。超えたら止める（票が大きすぎる合図）。

.PARAMETER PermissionMode
  無人では許可を聞けない＝`--permission-prompts none`（聞くはずだったものは拒否）と組む。

.PARAMETER DryRun
  票を読んで計画（ブランチ・起動引数・最初の入力文）を JSON で出し、git にも claude にも
  触らずに終わる。

.PARAMETER OutDir
  結果ファイル・起動ごとの JSON・テストのログの置き場（既定 `<repo>\.qa\autorun\<日時>`）。

.EXAMPLE
  & tools\autorun\run.ps1 -Tickets .claude\tickets\3.8 -DryRun
  & tools\autorun\run.ps1 -Tickets .claude\tickets\3.8
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Tickets,
    [int]$MaxTurns = 80,
    [int]$MaxContinue = 2,
    [string]$PermissionMode = 'auto',
    [string]$Model,
    [switch]$DryRun,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
# 入力文を stdin で渡す＝コマンドラインの引用で日本語の複数行を壊さない
# （[[feedback-shell-and-scripts]] の「届かない文字列」）。出力の JSON も UTF-8 で読む。
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

$toolDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)

# --- 作業票を読む -----------------------------------------------------------------

function Read-Ticket([string]$Path) {
    $text = [IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false))
    $m = [regex]::Match($text, '\A---\r?\n(?<head>.*?)\r?\n---\r?\n', 'Singleline')
    if (-not $m.Success) { throw "作業票の頭（--- で挟んだ項目）がありません: $Path" }
    $t = [ordered]@{ path = $Path; issues = @(); accept = @(); scope = @(); max_turns = $null }
    foreach ($line in ($m.Groups['head'].Value -split '\r?\n')) {
        if ($line -notmatch '^\s*(?<k>[a-z_]+)\s*:\s*(?<v>.*?)\s*$') { continue }
        $v = $Matches['v']
        switch ($Matches['k']) {
            'issues'    { $t.issues += @($v -split '\s+' | Where-Object { $_ }) }
            'accept'    { if ($v) { $t.accept += $v } }
            'scope'     { $t.scope += @($v -split '\s+' | Where-Object { $_ }) }
            'max_turns' { $t.max_turns = [int]$v }
            default     { throw "作業票に知らない項目があります（$($Matches['k'])）: $Path" }
        }
    }
    # ⛔ 足りない票は回す前に落とす＝無人で走り出してから気づくと、一番高い所で止まる。
    $missing = @('issues', 'accept', 'scope') | Where-Object { $t[$_].Count -eq 0 }
    if ($missing) { throw ("作業票に {0} がありません: $Path" -f ($missing -join '・')) }
    $bad = $t.issues | Where-Object { $_ -notmatch '^[BI]-\d{3}$' }
    if ($bad) { throw "課題 ID の形が違います（$($bad -join ' ')）: $Path" }
    return $t
}

$ticketsPath = if ([IO.Path]::IsPathRooted($Tickets)) { $Tickets } else { Join-Path $repoRoot $Tickets }
if (-not (Test-Path $ticketsPath)) { throw "作業票が見つかりません: $Tickets" }
$files = if (Test-Path $ticketsPath -PathType Container) {
    Get-ChildItem $ticketsPath -Filter '*.md' -File | Sort-Object Name
} else { @(Get-Item $ticketsPath) }
if (-not $files) { throw "作業票のフォルダに *.md がありません: $Tickets" }
$version = Split-Path -Leaf (Split-Path -Parent $files[0].FullName)
# ⚠️ @() で必ず配列にする＝票が 1 枚だと foreach は辞書そのものを返し、$plan[0] が
#    「辞書の最初の値」（path の文字列）になる。
$plan = @(foreach ($f in $files) {
    $t = Read-Ticket $f.FullName
    $t.name   = [IO.Path]::GetFileNameWithoutExtension($f.Name)
    $t.branch = "auto/$version-$($t.name)"
    $t.rel    = [IO.Path]::GetRelativePath($repoRoot, $f.FullName) -replace '\\', '/'
    $t
})

# --- 作業員の起動 -------------------------------------------------------------------

. (Join-Path $toolDir 'common.ps1')   # Find-Claude（relay.ps1 と共有）

$deny = foreach ($sh in 'Bash', 'PowerShell') {
    "$sh(git push)", "$sh(git push *)", "$sh(gh *)",
    "$sh(git merge *)", "$sh(git rebase *)", "$sh(git tag *)"
}
$deny += 'AskUserQuestion'   # 答える人がいない＝blocked を書かせる

function Get-ClaudeArgs([int]$Turns) {
    $a = @(
        '-p', '--output-format', 'json',
        '--max-turns', $Turns,
        '--permission-mode', $PermissionMode,
        '--permission-prompts', 'none',
        '--strict-mcp-config',          # MCP（Docs・Drive 等）を外して立ち上げを軽くする
        '--disable-slash-commands'      # スキル（/release を含む）を使わせない
    )
    if ($Model) { $a += '--model', $Model }
    return $a + (@('--disallowedTools') + $deny)
}

$promptTemplate = [IO.File]::ReadAllText((Join-Path $toolDir 'prompt_worker.txt'), [Text.UTF8Encoding]::new($false))
function Get-Prompt($T, [string]$ResultPath, [string]$Prior) {
    $p = if ($Prior) { "## 前回の試行から（駆動スクリプトが渡す）`n`n$Prior" } else { '' }
    return $promptTemplate.Replace('{TICKET_PATH}', $T.rel).Replace('{BRANCH}', $T.branch).
        Replace('{RESULT_PATH}', $ResultPath).Replace('{PRIOR}', $p)
}

$py = if ($env:RADIOSIM_PYTHON) { $env:RADIOSIM_PYTHON } else { 'python' }

if ($DryRun) {
    [ordered]@{
        version = $version
        python  = $py
        tickets = @($plan | ForEach-Object {
            [ordered]@{ name = $_.name; branch = $_.branch; issues = $_.issues; accept = $_.accept;
                        scope = $_.scope; max_turns = $(if ($_.max_turns) { $_.max_turns } else { $MaxTurns }) }
        })
        claude_args = @(Get-ClaudeArgs $MaxTurns)
        first_prompt = Get-Prompt $plan[0] '<RESULT_PATH>' ''
    } | ConvertTo-Json -Depth 5
    return
}

# --- ここから git と claude に触る --------------------------------------------------

function Invoke-Git {
    $out = & git -C $repoRoot @args 2>&1
    if ($LASTEXITCODE) { throw ("git {0} が失敗: {1}" -f ($args -join ' '), ($out -join "`n")) }
    $out
}

$startBranch = (Invoke-Git branch --show-current) -join ''
if ($startBranch -ne 'main') { throw "main から始めてください（いま: $startBranch）" }
if (Invoke-Git status --porcelain) { throw '作業ツリーがきれいではありません＝票を含め、先にコミットしてください' }
foreach ($t in $plan) {
    & git -C $repoRoot rev-parse --verify --quiet "refs/heads/$($t.branch)" | Out-Null
    if ($LASTEXITCODE -eq 0) { throw "ブランチが既にあります（上書きしない）: $($t.branch)" }
}

$claude = Find-Claude
$outDir = if ($OutDir) { $OutDir } else { Join-Path $repoRoot ('.qa\autorun\' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$ledgerPath = Join-Path $outDir 'runs.jsonl'

function Invoke-Worker($T, [int]$Attempt, [string]$Prior) {
    $resultPath = Join-Path $outDir ("{0}.attempt{1}.result.json" -f $T.name, $Attempt)
    $jsonPath   = Join-Path $outDir ("{0}.attempt{1}.claude.json" -f $T.name, $Attempt)
    $errPath    = Join-Path $outDir ("{0}.attempt{1}.stderr.txt" -f $T.name, $Attempt)
    $turns = if ($T.max_turns) { $T.max_turns } else { $MaxTurns }
    $saved = @{}
    $envs = @{
        RADIOSIM_AUTORUN = '1'; RADIOSIM_AUTORUN_RESULT = $resultPath
        GIT_CONFIG_COUNT = '1'; GIT_CONFIG_KEY_0 = 'remote.origin.pushurl'
        GIT_CONFIG_VALUE_0 = 'autorun-push-disabled://'
    }
    foreach ($k in $envs.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $envs[$k]) }
    try {
        Write-Host ("▶ {0}（{1} 回目・上限 {2} 往復）" -f $T.name, $Attempt, $turns)
        $out = (Get-Prompt $T $resultPath $Prior) | & $claude @(Get-ClaudeArgs $turns) 2> $errPath
        $rc = $LASTEXITCODE
    } finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
    $raw = ($out -join "`n")
    [IO.File]::WriteAllText($jsonPath, $raw, [Text.UTF8Encoding]::new($false))
    $meta = $null
    try { $meta = $raw | ConvertFrom-Json } catch { }
    $result = $null
    if (Test-Path $resultPath) {
        try { $result = Get-Content $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
    }
    $status = if ($result -and $result.status -in 'done', 'blocked', 'continue', 'failed') { $result.status } else { 'failed' }
    $u = if ($meta) { $meta.usage } else { $null }
    $rec = [ordered]@{
        ticket = $T.name; attempt = $Attempt; exit = $rc; status = $status
        subtype = $meta.subtype; num_turns = $meta.num_turns; session_id = $meta.session_id
        input_tokens = if ($u) { [long]$u.input_tokens + [long]$u.cache_creation_input_tokens + [long]$u.cache_read_input_tokens } else { $null }
        output_tokens = if ($u) { [long]$u.output_tokens } else { $null }
        cost_usd = $meta.total_cost_usd
        summary = if ($result) { $result.summary } else { "結果ファイルが無いか読めない（claude の終わり方: $($meta.subtype)・exit=$rc）" }
        question = $result.question; handoff = $result.handoff
    }
    Add-Content -Path $ledgerPath -Value ($rec | ConvertTo-Json -Compress) -Encoding UTF8
    return $rec
}

function Test-Outcome($T, [string]$StartSha) {
    $problems = @()
    $branch = (Invoke-Git branch --show-current) -join ''
    if ($branch -ne $T.branch) { return @("ブランチが替わっている（期待 $($T.branch)・いま $branch）") }
    $dirty = Invoke-Git status --porcelain
    if ($dirty) { $problems += "コミットされていない変更がある:`n$($dirty -join "`n")" }
    if ([int]((Invoke-Git rev-list --count "$StartSha..HEAD") -join '') -eq 0) { $problems += 'コミットが 1 つも無い' }
    $outside = Invoke-Git diff --name-only $StartSha HEAD | Where-Object {
        $f = $_; -not ($T.scope | Where-Object { $f.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) })
    }
    if ($outside) { $problems += "票の範囲の外を変えている: $($outside -join ' ')" }
    $i = 0
    foreach ($acc in $T.accept) {
        $i++
        $log = Join-Path $outDir ("{0}.accept{1}.log" -f $T.name, $i)
        $argv = @('-m', 'pytest', '-q') + @($acc -split '\s+' | Where-Object { $_ })
        Push-Location $repoRoot
        try { & $py @argv *> $log; $rc = $LASTEXITCODE } finally { Pop-Location }
        if ($rc -ne 0) {
            $tail = (Get-Content $log -Encoding UTF8 | Select-Object -Last 40) -join "`n"
            $problems += "受け入れのテストが赤（pytest $acc・exit=$rc）:`n$tail"
        }
    }
    return $problems
}

# --- 票を回す -------------------------------------------------------------------------

$base = 'main'
$report = @()
$stopped = $null
foreach ($t in $plan) {
    Invoke-Git switch -c $t.branch $base | Out-Null
    $startSha = (Invoke-Git rev-parse HEAD) -join ''
    $attempt = 0; $retried = $false; $continues = 0; $prior = ''
    while ($true) {
        $attempt++
        $rec = Invoke-Worker $t $attempt $prior
        if ($rec.status -eq 'blocked') { $stopped = "blocked: $($rec.question)"; break }
        if ($rec.status -eq 'continue') {
            if (++$continues -gt $MaxContinue) { $stopped = "continue が $MaxContinue 回を超えた（票が大きすぎる）"; break }
            $prior = "前のセッションは予算の助言で区切った。引き継ぎ:`n$($rec.handoff)"
            continue
        }
        $problems = if ($rec.status -eq 'done') { @(Test-Outcome $t $startSha) } else { @("作業員が failed を返した: $($rec.summary)") }
        if (-not $problems) { break }
        if ($retried) { $stopped = "やり直しても赤:`n$($problems -join "`n`n")"; break }
        $retried = $true
        $prior = "前の試行はスクリプトの検査で赤だった。直すべきこと:`n`n$($problems -join "`n`n")"
    }
    $report += [pscustomobject]@{ ticket = $t.name; branch = $t.branch; attempts = $attempt; result = $(if ($stopped) { '止めた' } else { '緑' }) }
    if ($stopped) { break }
    $base = $t.branch
}

Write-Host ''
$report | Format-Table -AutoSize | Out-String | Write-Host
Write-Host "記録: $ledgerPath"
if ($stopped) {
    Write-Host "⛔ 止めました（ブランチはそのまま＝調べられるように）: $stopped"
    exit 1
}
Invoke-Git switch $startBranch | Out-Null
Write-Host "✅ 全部緑。マージするのは最後のブランチ $base（前の票を全部含む）。push・PR・マージは人がする。"
