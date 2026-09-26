<#
.SYNOPSIS
  セッションを自動でつないで版の作業を進める（リレー・I-186）。作業票は作らない。

.DESCRIPTION
  作業の中身は版計画のステージ行と台帳（在庫精査で書き終えている正典）から取る。
  ここがするのは「起こす → 見る → 引き継ぎ書が出たら止めて次を起こす」だけ＝ステージを
  知らない（司令塔のトークンは 0）。

    1. パネルの Claude が最初の引き継ぎ書 `.claude/relay/handoff.md`（目標と次の一手）を
       書き、`-Start` でこのスクリプトを別の窓に起こす。
    2. 引き継ぎ書を記録のフォルダへ移し、それを読めと言って対話型のセッションを裏で起こす
       （`claude --bg`）。Remote Control を付ける＝人は VS Code（`claude attach <id>`）か
       携帯から見て、選択肢のダイアログに答える。
    3. `claude agents --json` を見張る。セッションが新しい引き継ぎ書を書き、手が空いたら
       `claude stop` で止める（会話は残る）。`continue` なら 2 へ・`done` なら終わる。

  🔑 区切りの合図はトークン予算のフック（session_budget.py のリレー用の言い方＝
  RADIOSIM_RELAY=1）。push はセッションに任せる（2026-09-27 ユーザー決定）。

.PARAMETER Start
  別の窓にリレーを起こして、すぐ戻る（パネルの Claude が使う入口）。

.PARAMETER Probe
  haiku で 1 往復だけ起こし、`--bg` に `--remote-control` と `--settings` の `env` が
  通るか・`agents --json` の status に入力待ちが出るかを確かめて、結果を JSON で出す。

.PARAMETER DryRun
  引き継ぎ書を読み、1 本目の起動引数・環境・設定・入力文を JSON で出して終わる
  （git にも claude にも触らない）。

.PARAMETER Trips
  試しの間だけ予算の警告を早める往復数（例 30）。session_budget.py へ環境変数で渡す。

.EXAMPLE
  & tools\autorun\relay.ps1 -Probe
  & tools\autorun\relay.ps1 -Start -Trips 30
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$Probe,
    [switch]$DryRun,
    [switch]$Foreground,        # -Start が別の窓で自分を呼ぶときの印（人は使わない）
    [int]$MaxSessions = 8,
    [int]$Trips,
    [string]$Model,
    [string]$PermissionMode = 'auto',
    [int]$PollSeconds = 10,
    [string]$Handoff
)

$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8

$toolDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)
. (Join-Path $toolDir 'common.ps1')

# ⚠️ `--bg` はフォルダの信頼が承認済みでないと「Workspace not trusted」で即 exit 1。
#    VS Code のパネルは信頼の確認を通らないので、ターミナルで一度 `claude` を起こして
#    承認する（2026-09-27 の -Probe で実測。小文字の `d:` で渡しても本体は `D:` に直して
#    判定した＝綴りでは避けられない）。
$workDir = $repoRoot
$relayDir = Join-Path $repoRoot '.claude\relay'
$handoffPath = if ($Handoff) { $Handoff } else { Join-Path $relayDir 'handoff.md' }

if (-not ($Start -or $Probe -or $DryRun -or $Foreground)) {
    Write-Host '使い方: relay.ps1 -Start [-Trips 30] | -Probe | -DryRun（詳しくは Get-Help）'
    exit 2
}

# --- 引き継ぎ書を読む ---------------------------------------------------------------

function Read-Handoff([string]$Path) {
    $text = [IO.File]::ReadAllText($Path, $utf8)
    $m = [regex]::Match($text, '\A---\r?\n(?<head>.*?)\r?\n---\r?\n', 'Singleline')
    if (-not $m.Success) { throw "引き継ぎ書の頭（--- で挟んだ項目）がありません: $Path" }
    $h = [ordered]@{ status = ''; goal = '' }
    foreach ($line in ($m.Groups['head'].Value -split '\r?\n')) {
        if ($line -notmatch '^\s*(?<k>[a-z_]+)\s*:\s*(?<v>.*?)\s*$') { continue }
        if (-not $h.Contains($Matches['k'])) { throw "引き継ぎ書に知らない項目があります（$($Matches['k'])）: $Path" }
        $h[$Matches['k']] = $Matches['v']
    }
    if ($h.status -notin 'continue', 'done') { throw "引き継ぎ書の status は continue か done です（いま「$($h.status)」）: $Path" }
    if (-not $h.goal) { throw "引き継ぎ書に goal（どのステージまで）がありません: $Path" }
    return $h
}

# --- セッションの起こし方 -------------------------------------------------------------

$promptTemplate = [IO.File]::ReadAllText((Join-Path $toolDir 'prompt_relay.txt'), $utf8)
function Get-Prompt([int]$No, [string]$Prev) {
    return $promptTemplate.Replace('{SESSION_NO}', "$No").Replace('{PREV_HANDOFF}', $Prev).
        Replace('{HANDOFF_PATH}', $handoffPath)
}

# ⚠️ `CLAUDE_BG_ISOLATION=none`＝本体は `--bg` のセッションを既定で worktree に閉じ込め、
#    `EnterWorktree` を呼ぶまで元の作業場所への Edit/Write を拒む（2026-09-27 のリレーの
#    初回の試しで、追跡外の台帳と引き継ぎ書を道具で書けなかった）。本体 2.1.282 の判定は
#    この環境変数 → 裏の起動の記録 → 設定の `worktree.bgIsolation` の順に見る＝ここで渡せば
#    リレーのセッションにだけ効き、リポジトリの設定を触らない。
function Get-RelayEnv {
    $e = [ordered]@{ RADIOSIM_RELAY = '1'; RADIOSIM_RELAY_HANDOFF = $handoffPath; CLAUDE_BG_ISOLATION = 'none' }
    if ($Trips) { $e.RADIOSIM_RELAY_TRIPS = "$Trips" }
    return $e
}

# 環境変数は起動側のプロセスと `--settings` の `env` の両方で渡す（どちらも届くことを
# 2026-09-27 の -Probe で確かめた）。`autoContinueAtUsageLimit` と隔離の方式はリレーの
# セッションにだけ効かせる＝利用者設定もリポジトリの設定も触らない。
function Get-Settings([System.Collections.IDictionary]$Env) {
    return [ordered]@{ env = $Env; autoContinueAtUsageLimit = $true; worktree = [ordered]@{ bgIsolation = 'none' } }
}

# ⚠️ 入力文を最初に置く＝`--allowedTools` などの可変長のオプションの後ろに置くと、
#    入力文まで道具の名前として食われる。
# ⚠️ `--session-id` は渡さない＝`--bg` は「--bg manages the session id」と言って無視する
#    （2026-09-27 の -Probe）。id は起動の出力から取る。
function Get-SessionArgs([string]$Prompt, [string]$Name, [string]$SettingsPath, [string]$UseModel) {
    $a = @($Prompt, '--bg', '-n', $Name,
           '--remote-control', $Name, '--settings', $SettingsPath,
           '--permission-mode', $PermissionMode)
    if ($UseModel) { $a += '--model', $UseModel }
    return $a
}

function Invoke-Claude([string[]]$ArgList, [System.Collections.IDictionary]$Env = @{}, [int]$TimeoutMs = 120000) {
    $psi = [Diagnostics.ProcessStartInfo]::new($script:claude)
    foreach ($a in $ArgList) { $psi.ArgumentList.Add([string]$a) }
    $psi.WorkingDirectory = $workDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $utf8; $psi.StandardErrorEncoding = $utf8
    foreach ($k in $Env.Keys) { $psi.Environment[$k] = [string]$Env[$k] }
    $p = [Diagnostics.Process]::Start($psi)
    $o = $p.StandardOutput.ReadToEndAsync(); $e = $p.StandardError.ReadToEndAsync()
    # ⚠️ 時間つきで待つ＝`--bg` が残す裏のプロセスが出力の口を握り続けると、
    #    時間なしの WaitForExit と ReadToEnd は終わらない。
    $exited = $p.WaitForExit($TimeoutMs)
    [void][Threading.Tasks.Task]::WaitAll(@($o, $e), 3000)
    [pscustomobject]@{
        exit = $(if ($exited) { $p.ExitCode } else { $null })
        out  = $(if ($o.IsCompleted) { $o.Result } else { '' })
        err  = $(if ($e.IsCompleted) { $e.Result } else { '' })
    }
}

# 一覧の項目（2026-09-27 の -Probe で見た形）＝裏のセッションは `id`（短い 8 桁＝attach・
# logs・stop が取る）・`sessionId`（会話の記録の名前）・`status` と `state` の組:
#   busy/working＝作業中・waiting/blocked＝人の入力待ち（質問のダイアログか許可）・
#   idle/blocked＝手が空いて次の入力を待つ・/stopped＝止めた後（status の欄が消える）。
function Get-Agent([string]$Id) {
    $r = Invoke-Claude @('agents', '--json', '--all')
    try { $list = $r.out | ConvertFrom-Json } catch { return $null }
    return @($list) | Where-Object { $_.PSObject.Properties['id'] -and $_.id -eq $Id } | Select-Object -First 1
}

function Get-AgentStatus($Agent) {
    if (-not $Agent) { return '<一覧に無い>' }
    return "$($Agent.status)/$($Agent.state)"
}

# `--bg` の出力＝「backgrounded · 250e095d · relay-probe」と、その id を使う
# `claude attach|logs|stop <id>` の案内。案内の行から取る（区切りの字に頼らない）。
function Get-LaunchId($Launch) {
    $m = [regex]::Match("$($Launch.out)", 'claude stop (?<id>[0-9A-Za-z]+)')
    if (-not $m.Success) { throw "起動の出力から id を読めません: $($Launch.out.Trim())" }
    return $m.Groups['id'].Value
}

function Test-Gone($Agent) {
    return (-not $Agent) -or ("$($Agent.state)" -match 'stop|complet|exit|fail|error|dead')
}

$transcriptDir = Join-Path $env:USERPROFILE '.claude\projects'

# --- DryRun --------------------------------------------------------------------------

if ($DryRun) {
    $h = Read-Handoff $handoffPath
    $env0 = Get-RelayEnv
    [ordered]@{
        work_dir = $workDir
        handoff  = $h
        env      = $env0
        settings = Get-Settings $env0
        claude_args = @(Get-SessionArgs '<PROMPT>' 'relay-1' '<SETTINGS>' $Model)
        first_prompt = Get-Prompt 1 '<PREV_HANDOFF>'
        max_sessions = $MaxSessions
    } | ConvertTo-Json -Depth 5
    return
}

# --- Start: 別の窓に起こして戻る -------------------------------------------------------

if ($Start) {
    $null = Read-Handoff $handoffPath   # 走り出す前に落とす
    $fwd = @('-NoProfile', '-NoExit', '-File', $PSCommandPath, '-Foreground',
             '-MaxSessions', $MaxSessions, '-PermissionMode', $PermissionMode, '-PollSeconds', $PollSeconds)
    if ($Trips) { $fwd += '-Trips', $Trips }
    if ($Model) { $fwd += '-Model', $Model }
    if ($Handoff) { $fwd += '-Handoff', $Handoff }
    $p = Start-Process pwsh -ArgumentList $fwd -WorkingDirectory $workDir -PassThru
    Write-Host "リレーを別の窓で起こしました（pid $($p.Id)）。記録: $(Join-Path $relayDir 'relay.log')"
    return
}

$claude = Find-Claude
New-Item -ItemType Directory -Path $relayDir -Force | Out-Null
$runDir = Join-Path $relayDir ('runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$logPath = Join-Path $relayDir 'relay.log'

function Write-Log([string]$Text) {
    $line = '{0} {1}' -f (Get-Date -Format 'HH:mm:ss'), $Text
    Write-Host $line
    Add-Content -Path $logPath -Value $line -Encoding UTF8
}

# --- Probe ---------------------------------------------------------------------------

if ($Probe) {
    $procEnv = [ordered]@{ RADIOSIM_RELAY_PROBE_ENV = 'from-env'; CLAUDE_BG_ISOLATION = 'none' }
    $settingsPath = Join-Path $runDir 'probe.settings.json'
    $settings = [ordered]@{ env = [ordered]@{ RADIOSIM_RELAY_PROBE_SETTINGS = 'from-settings'; CLAUDE_BG_ISOLATION = 'none' }
                            autoContinueAtUsageLimit = $true; worktree = [ordered]@{ bgIsolation = 'none' } }
    [IO.File]::WriteAllText($settingsPath, ($settings | ConvertTo-Json -Depth 5), $utf8)
    # 閉じ込められていないか＝元の作業場所の追跡外のファイルを Write ツールで書かせる
    $probeWrite = Join-Path $relayDir 'probe_write.txt'
    Remove-Item -LiteralPath $probeWrite -ErrorAction SilentlyContinue
    $prompt = @'
これは確かめの 1 往復です。ほかのことはしないでください。
1. PowerShell で次の 1 行だけを走らせ、出力をそのまま書く:
   Write-Output "env=$env:RADIOSIM_RELAY_PROBE_ENV settings=$env:RADIOSIM_RELAY_PROBE_SETTINGS"
2. Write ツールで {PROBE_WRITE} に「ok」の 1 行を書く（EnterWorktree は呼ばない・シェルで書かない）。拒まれたら拒まれた文面をそのまま書く。
3. AskUserQuestion で「確かめの質問です。どちらでも構いません」と 2 択（はい・いいえ）を出して待つ。
'@.Replace('{PROBE_WRITE}', $probeWrite)
    $argv = @(Get-SessionArgs $prompt 'relay-probe' $settingsPath 'haiku')
    Write-Log '▶ 確かめ: claude --bg（haiku）'
    $launch = Invoke-Claude $argv $procEnv
    Write-Log "  起動の終わり方 exit=$($launch.exit)・出力: $($launch.out.Trim())・エラー: $($launch.err.Trim())"
    # 起こせなかったら見張らない（2026-09-27 の 1 回目＝信頼の未承認で即 exit 1 のまま 5 分待った）
    if ($launch.exit) { throw "確かめのセッションを起こせません（exit=$($launch.exit)）: $($launch.err.Trim())" }
    $id = Get-LaunchId $launch

    $seen = [Collections.Generic.List[object]]::new()
    $last = $null; $sinceChange = 0; $agent = $null; $wasBusy = $false
    $deadline = (Get-Date).AddSeconds(300)
    while ((Get-Date) -lt $deadline) {
        $agent = Get-Agent $id
        $st = Get-AgentStatus $agent
        if ($st -ne $last) {
            $seen.Add([ordered]@{ t = (Get-Date -Format 'HH:mm:ss'); status = $st; entry = $agent })
            Write-Log "  status: $st"
            $last = $st; $sinceChange = 0
        } else { $sinceChange += 3 }
        if ($st -like 'busy*') { $wasBusy = $true }
        # 働いた後に busy でない状態が 30 秒続いたら＝ダイアログで待っているはず
        if ($wasBusy -and $st -notlike 'busy*' -and $sinceChange -ge 30) { break }
        if ((Test-Gone $agent) -and $seen.Count -gt 3) { break }
        Start-Sleep -Seconds 3
    }
    $logs = Invoke-Claude @('logs', $id)
    $stop = Invoke-Claude @('stop', $id)
    Start-Sleep -Seconds 3
    $after = Get-Agent $id
    $sid = if ($agent) { "$($agent.sessionId)" } else { '' }
    $tr = if ($sid) { Get-ChildItem $transcriptDir -Recurse -Filter "$sid.jsonl" -File -ErrorAction SilentlyContinue | Select-Object -First 1 }
    $trText = if ($tr) { [IO.File]::ReadAllText($tr.FullName, $utf8) } else { '' }
    $result = [ordered]@{
        id = $id
        session_id = $sid
        launch = $launch
        statuses = $seen
        stop = $stop
        after_stop = $after
        logs_tail = (($logs.out -split "`n") | Select-Object -Last 30) -join "`n"
        transcript = $(if ($tr) { $tr.FullName } else { $null })
        env_reached = $trText.Contains('env=from-env')
        settings_env_reached = $trText.Contains('settings=from-settings')
        asked_user = $trText.Contains('AskUserQuestion')
        wrote_main_checkout = (Test-Path $probeWrite)
    }
    $outPath = Join-Path $runDir 'probe.json'
    [IO.File]::WriteAllText($outPath, ($result | ConvertTo-Json -Depth 8), $utf8)
    Write-Log ("■ 確かめの結果: 起動側の環境変数={0}・--settings の env={1}・元の作業場所へ書けた={2}・ダイアログ={3}・見えた status={4}・止めた後={5}" -f
        $result.env_reached, $result.settings_env_reached, $result.wrote_main_checkout, $result.asked_user,
        (($seen | ForEach-Object { $_.status }) -join ' → '), (Get-AgentStatus $after))
    Write-Log "  詳細: $outPath"
    return
}

# --- リレー本体（-Foreground）---------------------------------------------------------

$lock = Join-Path $relayDir 'relay.lock'
if (Test-Path $lock) {
    $other = [int]((Get-Content $lock -Raw) -as [int])
    if ($other -and (Get-Process -Id $other -ErrorAction SilentlyContinue)) { throw "リレーはもう走っています（pid $other）" }
}
Set-Content -Path $lock -Value $PID
try {
    $h = Read-Handoff $handoffPath
    if ($h.status -eq 'done') { Write-Log "引き継ぎ書が既に done です（目標: $($h.goal)）＝起こすものがありません"; return }
    Write-Log "■ リレー開始（目標: $($h.goal)・上限 $MaxSessions 本・記録 $runDir）"
    $relayEnv = Get-RelayEnv
    $finished = $false
    for ($n = 1; $n -le $MaxSessions; $n++) {
        # 前の引き継ぎ書を記録へ移す＝新しい handoff.md が現れたら「このセッションが書いた」
        $prev = Join-Path $runDir ('handoff.{0:D2}.md' -f ($n - 1))
        Move-Item -LiteralPath $handoffPath -Destination $prev
        $name = "relay-$n"
        $settingsPath = Join-Path $runDir "$name.settings.json"
        [IO.File]::WriteAllText($settingsPath, ((Get-Settings $relayEnv) | ConvertTo-Json -Depth 5), $utf8)
        $launch = Invoke-Claude @(Get-SessionArgs (Get-Prompt $n $prev) $name $settingsPath $Model) $relayEnv
        if ($launch.exit) { throw "セッションを起こせません（exit=$($launch.exit)）: $($launch.err.Trim())" }
        $id = Get-LaunchId $launch
        Write-Log "▶ $n 本目（id $id）＝見る・答える: claude attach $id ／ Remote Control の「$name」"

        $idle = 0; $lastSt = ''
        while ($true) {
            Start-Sleep -Seconds $PollSeconds
            $agent = Get-Agent $id
            $st = Get-AgentStatus $agent
            if ($st -ne $lastSt) {
                Write-Log ("  status: $st" + $(if ($st -like 'waiting*') { "＝人の入力待ち（claude attach $id か Remote Control で答える）" } else { '' }))
                $lastSt = $st
            }
            if (Test-Path $handoffPath) {
                # 書いた後に手が空いたら止める。2 回続けて見る＝Stop のフックが差し戻して
                # もう 1 往復するあいだに止めない。
                # ⚠️ `waiting` は人の入力待ち（質問か許可）＝手が空いたのではない
                $idle = if ($st -like 'idle*') { $idle + 1 } else { 0 }
                if ($idle -ge 2) { break }
            } elseif (Test-Gone $agent) {
                throw "$n 本目が引き継ぎ書を書かずに終わりました（status: $st）＝claude logs $id で見てください。前の引き継ぎ書は $prev"
            }
        }
        $null = Invoke-Claude @('stop', $id)
        $h = Read-Handoff $handoffPath
        Copy-Item -LiteralPath $handoffPath -Destination (Join-Path $runDir ('handoff.{0:D2}.md' -f $n))
        Write-Log "■ $n 本目が引き継ぎ書を書いて止まりました（status: $($h.status)）"
        if ($h.status -eq 'done') { $finished = $true; break }
    }
    if ($finished) { Write-Log "✅ 目標まで済みました（$($h.goal)）。記録: $runDir" }
    else { Write-Log "⛔ 上限 $MaxSessions 本に達しました＝引き継ぎ書は $handoffPath に残っています（-Start で続きから）" }
} finally {
    Remove-Item -LiteralPath $lock -ErrorAction SilentlyContinue
}
