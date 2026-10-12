<#
.SYNOPSIS
  セッションを自動でつないで版の作業を進める（リレー・I-186）。

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
       `claude stop` で止める（会話は残る）。`continue` なら 2 へ・`done` なら窓を閉じて終わる。
       同じ状態が続く間も -BeatSeconds ごとに経過時間と最後の手を relay.log に書く（I-188）。
       人の答えを待つ（`waiting`）間は Windows の通知を出し、-WaitNoticeMinutes ごとに出し直す（I-231）。
    4. 止め方（I-229）＝続けて -StallSessions 本、コミットも台帳・版計画の変化も無ければ止める。
       -MaxSessions は暴走を防ぐ絶対の上限。最後になりうる 1 本には入力文で知らせる（I-227）。
    5. どの終わり方でも終わりの合図 `relay.end` を書く。`done`＋push 済み以外は Windows の
       通知も出す（I-228）。パネルの Claude は `-Watch` を裏で走らせて、この合図を待つ（I-230）。

  🔑 区切りの合図はトークン予算のフック（session_budget.py のリレー用の言い方＝
  RADIOSIM_RELAY=1）。push はセッションに任せる（2026-09-27 ユーザー決定）＝ただし
  予算の助言で引き継ぐときはコミットだけ・目標まで済んだセッションがまとめて push する
  （同日ユーザー指示）。窓は `done` で閉じ、それ以外の終わり方（上限・止まった・push の
  漏れ）では理由を読めるよう開けたまま待つ。

.PARAMETER Start
  別の窓にリレーを起こして、すぐ戻る（パネルの Claude が使う入口）。リレーが既に走って
  いれば断る。出力の最後に、パネルが裏で走らせる見張りの呼び出し（-Watch）を出す。

.PARAMETER Watch
  終わりの合図 `relay.end` が現れるまで待ち、その中身と relay.log の末尾を出して終わる
  （`done` なら exit 0・ほかは 1）。relay.lock が -GraceSeconds 秒現れなければ「走っていない」、
  合図を書かずにプロセスが消えたら「消えた」で終わる。パネルの Claude が裏で走らせる（I-230）。

.PARAMETER TestNotice
  Windows の通知を 1 回出して終わる（人が目で確かめる入口・I-228）。

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
  & tools\autorun\relay.ps1 -Watch
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$Probe,
    [switch]$DryRun,
    [switch]$Watch,
    [switch]$TestNotice,
    [switch]$Foreground,        # -Start が別の窓で自分を呼ぶときの印（人は使わない）
    [int]$MaxSessions = 20,     # 暴走を防ぐ絶対の上限（I-229＝止める基準は -StallSessions）
    [int]$StallSessions = 2,    # 続けてこの本数、コミットも台帳・版計画の変化も無ければ止める（I-229）
    [int]$WaitNoticeMinutes = 15, # 人の答え待ちが続く間、この間隔で通知を出し直す（0＝出し直さない・I-231）
    [int]$Trips,
    [string]$Model,
    [string]$PermissionMode = 'auto',
    [int]$PollSeconds = 10,
    [int]$BeatSeconds = 300,    # 同じ状態が続く間も、この間隔で経過時間と最後の手を書く（I-188）
    [int]$GraceSeconds = 60,    # -Watch が relay.lock の現れを待つ秒数
    [string]$Handoff,
    [string]$RelayDir           # 記録・錠・終わりの合図の置き場（テスト用。既定は .claude\relay）
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
$relayDir = if ($RelayDir) { $RelayDir } else { Join-Path $repoRoot '.claude\relay' }
$handoffPath = if ($Handoff) { $Handoff } else { Join-Path $relayDir 'handoff.md' }
$lock = Join-Path $relayDir 'relay.lock'
$endPath = Join-Path $relayDir 'relay.end'    # 終わりの合図（I-230）
$logPath = Join-Path $relayDir 'relay.log'

if (-not ($Start -or $Probe -or $DryRun -or $Watch -or $TestNotice -or $Foreground)) {
    Write-Host '使い方: relay.ps1 -Start [-Trips 30] | -Watch | -Probe | -DryRun | -TestNotice（詳しくは Get-Help）'
    exit 2
}

# 走っているリレーの pid（錠があり、そのプロセスが生きているとき）。無ければ 0
function Get-RunningRelayPid {
    if (-not (Test-Path -LiteralPath $lock)) { return 0 }
    $other = [int]((Get-Content -LiteralPath $lock -Raw -ErrorAction SilentlyContinue) -as [int])
    if ($other -and (Get-Process -Id $other -ErrorAction SilentlyContinue)) { return $other }
    return 0
}

# パネルの Claude が裏で走らせる見張りの呼び出し（-Start の出力と Stop フックの言い方をそろえる）
function Get-WatchCommand {
    $c = "pwsh -NoProfile -File `"$PSCommandPath`" -Watch"
    if ($RelayDir) { $c += " -RelayDir `"$RelayDir`"" }
    return $c
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
function Get-Prompt([int]$No, [string]$Prev, [int]$Stall = 0) {
    return $promptTemplate.Replace('{SESSION_NO}', "$No").Replace('{MAX_SESSIONS}', "$MaxSessions").
        Replace('{LAST_NOTE}', (Get-LastNote $No $MaxSessions $Stall $StallSessions)).
        Replace('{PREV_HANDOFF}', $Prev).Replace('{HANDOFF_PATH}', $handoffPath)
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

# 環境変数に頼らない＝CI（Linux）には USERPROFILE が無く、ここで落ちると -DryRun も通らない
# （Windows の $HOME も USERPROFILE から作られる）
$transcriptDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\projects'

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
        last_prompt = Get-Prompt $MaxSessions '<PREV_HANDOFF>'
        max_sessions = $MaxSessions
        stall_sessions = $StallSessions
        wait_notice_minutes = $WaitNoticeMinutes
        beat_seconds = $BeatSeconds
        watch_command = Get-WatchCommand
    } | ConvertTo-Json -Depth 5
    return
}

# --- TestNotice: 通知を 1 回出す ------------------------------------------------------

if ($TestNotice) {
    Write-Host (Send-DesktopNotice 'RadioSim のリレー（試し）' "通知の試しです（$(Get-Date -Format 'HH:mm:ss')）。`n引用符「""」・<タグ>・& も割れずに出ること。")
    # アイコンを捨てると通知も消える＝見える間だけ待つ（記録へ書く試しでは待たない）
    if (-not $env:RADIOSIM_RELAY_NOTICE_LOG) { Start-Sleep -Seconds 8 }
    return
}

# --- Watch: 終わりの合図を待つ（I-230）------------------------------------------------

function Write-LogTail {
    if (Test-Path -LiteralPath $logPath) {
        Write-Host '--- relay.log の末尾 ---'
        Get-Content -LiteralPath $logPath -Tail 15 -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    }
}

if ($Watch) {
    $begun = Get-Date; $seen = $false
    while (-not (Test-Path -LiteralPath $endPath)) {
        if (Get-RunningRelayPid) { $seen = $true }
        elseif ($seen) {
            # 合図は錠を消す直前に書く＝消えた直後の 1 回だけ読み直す
            Start-Sleep -Seconds 2
            if (Test-Path -LiteralPath $endPath) { break }
            Write-Host '⛔ リレーのプロセスが終わりの合図を書かずに消えました（窓を閉じた・落ちた）'
            Write-LogTail
            exit 1
        } elseif (((Get-Date) - $begun).TotalSeconds -ge $GraceSeconds) {
            Write-Host "⛔ リレーは走っていません（$GraceSeconds 秒待っても relay.lock が現れない）"
            Write-LogTail
            exit 1
        }
        Start-Sleep -Seconds $PollSeconds
    }
    $end = [IO.File]::ReadAllText($endPath, $utf8)
    Write-Host "■ リレーが終わりました`n$($end.Trim())"
    Write-LogTail
    if ($end -match '(?m)^kind=done\s*$') { exit 0 }
    exit 1
}

# --- Start: 別の窓に起こして戻る -------------------------------------------------------

if ($Start) {
    $null = Read-Handoff $handoffPath   # 走り出す前に落とす
    $running = Get-RunningRelayPid
    if ($running) {
        Write-Host "⛔ リレーはもう走っています（pid $running）＝起こしません。終わりを見張るなら次を裏で走らせる:"
        Write-Host "  $(Get-WatchCommand)"
        exit 1
    }
    # 前の回の合図を消す＝見張りが古い合図を今回の終わりと取り違えない
    Remove-Item -LiteralPath $endPath -ErrorAction SilentlyContinue
    # -NoExit を付けない＝`done` で窓が閉じる。閉じない終わり方は -Foreground の側で待つ
    $fwd = @('-NoProfile', '-File', $PSCommandPath, '-Foreground',
             '-MaxSessions', $MaxSessions, '-StallSessions', $StallSessions, '-WaitNoticeMinutes', $WaitNoticeMinutes,
             '-PermissionMode', $PermissionMode, '-PollSeconds', $PollSeconds, '-BeatSeconds', $BeatSeconds)
    if ($Trips) { $fwd += '-Trips', $Trips }
    if ($Model) { $fwd += '-Model', $Model }
    if ($Handoff) { $fwd += '-Handoff', $Handoff }
    if ($RelayDir) { $fwd += '-RelayDir', $RelayDir }
    # 配列のまま渡すと空白でつなぐだけ＝空白を含むパスが割れる（B-314）
    $p = Start-Process pwsh -ArgumentList (Join-ProcessArgs $fwd) -WorkingDirectory $workDir -PassThru
    Write-Host "リレーを別の窓で起こしました（pid $($p.Id)）。記録: $logPath"
    Write-Host "⚠️ パネルの Claude は、次を裏（run_in_background）で走らせて終わりを待つ（I-230・見張らないと Stop フックが止める）:"
    Write-Host "  $(Get-WatchCommand)"
    return
}

$claude = Find-Claude
New-Item -ItemType Directory -Path $relayDir -Force | Out-Null
$runDir = Join-Path $relayDir ('runs\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $runDir -Force | Out-Null

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
    $tr = Find-Transcript $transcriptDir $sid
    $trText = if ($tr) { [IO.File]::ReadAllText($tr, $utf8) } else { '' }
    $result = [ordered]@{
        id = $id
        session_id = $sid
        launch = $launch
        statuses = $seen
        stop = $stop
        after_stop = $after
        logs_tail = (($logs.out -split "`n") | Select-Object -Last 30) -join "`n"
        transcript = $tr
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

# 途中のセッションはコミットだけ＝push していないコミットが手元に溜まる。数えられなければ -1
function Get-UnpushedCount {
    $c = & git -C $repoRoot rev-list --count '@{u}..HEAD' 2>$null
    if ($LASTEXITCODE -ne 0) { return -1 }
    return [int]$c
}

# `done` で push まで済んだときだけ窓を閉じる。ほかの終わり方は理由を読めるよう待つ
function Wait-BeforeClose {
    if ($Foreground) { [void](Read-Host 'Enter で窓を閉じます') }
}

$other = Get-RunningRelayPid
if ($other) {
    # ⚠️ 終わりの合図は書かない＝それは走っている方の合図（見張りが取り違える）
    Write-Log "⛔ リレーはもう走っています（pid $other）"
    Write-Log "  $(Send-DesktopNotice 'RadioSim のリレー：二重起動' "リレーはもう走っています（pid $other）。こちらは起こさずに止めました。")"
    Wait-BeforeClose
    exit 1
}
Set-Content -Path $lock -Value $PID
Remove-Item -LiteralPath $endPath -ErrorAction SilentlyContinue
$keepOpen = $true
$failed = $false
# 終わり方（I-228）＝done・done_unpushed・max・stall・failed・nothing（既に done）。finally で
# relay.end に書き、done と nothing のほかは通知を出す。書かれないまま finally に来たら中断。
$endKind = $null; $endText = ''
try {
    $h = Read-Handoff $handoffPath
    if ($h.status -eq 'done') {
        $endKind = 'nothing'; $endText = "引き継ぎ書が既に done です（目標: $($h.goal)）＝起こすものがありません"
        Write-Log $endText; $keepOpen = $false; return
    }
    Write-Log "■ リレー開始（目標: $($h.goal)・上限 $MaxSessions 本・進まない $StallSessions 本で止める・記録 $runDir）"
    $relayEnv = Get-RelayEnv
    $finished = $false
    $stall = 0          # 続けて進まなかった本数（I-229）
    for ($n = 1; $n -le $MaxSessions; $n++) {
        $markBefore = Get-ProgressMark $repoRoot
        # 前の引き継ぎ書を記録へ移す＝新しい handoff.md が現れたら「このセッションが書いた」
        # ⚠️ -Force＝前のセッションの終わりに同じ名前へ写しを置いている（2026-09-27 の 2 回目の
        #    試しで、2 本目の前のここが「既にある」で止まった）
        $prev = Join-Path $runDir ('handoff.{0:D2}.md' -f ($n - 1))
        Move-Item -LiteralPath $handoffPath -Destination $prev -Force
        $name = "relay-$n"
        $settingsPath = Join-Path $runDir "$name.settings.json"
        [IO.File]::WriteAllText($settingsPath, ((Get-Settings $relayEnv) | ConvertTo-Json -Depth 5), $utf8)
        $launch = Invoke-Claude @(Get-SessionArgs (Get-Prompt $n $prev $stall) $name $settingsPath $Model) $relayEnv
        if ($launch.exit) { throw "セッションを起こせません（exit=$($launch.exit)）: $($launch.err.Trim())" }
        $id = Get-LaunchId $launch
        Write-Log "▶ $n 本目（id $id）＝見る・答える: claude attach $id ／ Remote Control の「$name」"

        $idle = 0; $lastSt = ''; $stSince = Get-Date; $lastBeat = $stSince; $trPath = $null
        $waitSince = $null; $lastWaitNotice = $null
        while ($true) {
            Start-Sleep -Seconds $PollSeconds
            $agent = Get-Agent $id
            $st = Get-AgentStatus $agent
            $now = Get-Date
            if ($st -ne $lastSt) {
                Write-Log ("  status: $st" + $(if ($st -like 'waiting*') { "＝人の入力待ち（claude attach $id か Remote Control で答える）" } else { '' }))
                $lastSt = $st; $stSince = $now; $lastBeat = $now
            } elseif (($now - $lastBeat).TotalSeconds -ge $BeatSeconds) {
                # 同じ状態のまま黙らない（I-188＝push のゲートのフルの 677 秒、1 行も出なかった）
                if (-not $trPath -and $agent) { $trPath = Find-Transcript $transcriptDir "$($agent.sessionId)" }
                Write-Log (Format-Beat $st ($now - $stSince) (Get-LastMove $trPath))
                $lastBeat = $now
            }
            # 人の答え待ちは通知で知らせる（I-231＝relay.log の 1 行では誰にも届かなかった）。
            # 入った時点で 1 回、続く間は -WaitNoticeMinutes ごとに出し直す
            if ($st -like 'waiting*') {
                if (-not $waitSince) { $waitSince = $now }
                if (-not $lastWaitNotice -or
                    ($WaitNoticeMinutes -gt 0 -and ($now - $lastWaitNotice).TotalMinutes -ge $WaitNoticeMinutes)) {
                    if (-not $trPath -and $agent) { $trPath = Find-Transcript $transcriptDir "$($agent.sessionId)" }
                    $body = Format-WaitNotice $n $id $name ($now - $waitSince) (Get-LastMove $trPath)
                    Write-Log "  $(Send-DesktopNotice 'RadioSim のリレー：人の答え待ち' $body)"
                    $lastWaitNotice = $now
                }
            } else { $waitSince = $null; $lastWaitNotice = $null }
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
        # 進み具合（I-229）＝コミットも台帳・版計画の変化も無い回が続いたら止める
        if ((Get-ProgressMark $repoRoot) -eq $markBefore) { $stall++ } else { $stall = 0 }
        if ($StallSessions -gt 0 -and $stall -ge $StallSessions) { break }
        if ($stall) { Write-Log "  ⚠️ この 1 本はコミットも台帳・版計画の変化も無かった（続けて $stall 本・$StallSessions 本で止める）" }
    }
    $unpushed = Get-UnpushedCount
    if ($finished -and $unpushed -eq 0) {
        $endKind = 'done'; $endText = "✅ 目標まで済みました（$($h.goal)）。push 済み＝窓を閉じます。記録: $runDir"
        $keepOpen = $false
    } elseif ($finished) {
        $endKind = 'done_unpushed'; $endText = "⚠️ 目標まで済みましたが、push していないコミットが残っています（$unpushed 件・-1＝数えられない）＝手で push してください。記録: $runDir"
    } elseif ($StallSessions -gt 0 -and $stall -ge $StallSessions) {
        $endKind = 'stall'; $endText = "⛔ 続けて $stall 本、コミットも台帳・版計画の変化も無かったので止めました＝引き継ぎ書は $handoffPath に残っています（-Start で続きから）。push していないコミット $unpushed 件"
    } else {
        $endKind = 'max'; $endText = "⛔ 上限 $MaxSessions 本に達しました＝引き継ぎ書は $handoffPath に残っています（-Start で続きから）。push していないコミット $unpushed 件"
    }
    Write-Log $endText
} catch {
    # 止まった理由を記録にも残す＝窓にだけ出ると、記録を見張る側は終わりに気づけない
    Write-Log "⛔ リレーが止まりました: $($_.Exception.Message)（引き継ぎ書: $handoffPath・push していないコミット $(Get-UnpushedCount) 件）"
    $endKind = 'failed'; $endText = "⛔ リレーが止まりました: $($_.Exception.Message)"
    $failed = $true
} finally {
    if (-not $endKind) { $endKind = 'interrupted'; $endText = '⛔ リレーが途中で中断されました（Ctrl+C など）' }
    # done と nothing のほかは人へ知らせる（I-228＝セッションが死んだときに届くのはこれだけ）
    if ($endKind -notin 'done', 'nothing') {
        Write-Log "  $(Send-DesktopNotice "RadioSim のリレー：$(Get-EndTitle $endKind)" $endText)"
    }
    # 合図は錠を消す前に書く＝見張りは「錠が消えたのに合図が無い」を異常と読む
    try {
        [IO.File]::WriteAllText($endPath, "kind=$endKind`nat=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`nrun=$runDir`n$endText`n", $utf8)
    } catch { Write-Log "  終わりの合図を書けません: $($_.Exception.Message)" }
    Remove-Item -LiteralPath $lock -ErrorAction SilentlyContinue
}
if ($keepOpen) { Wait-BeforeClose }
if ($failed) { exit 1 }
