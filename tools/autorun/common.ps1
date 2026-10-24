<#
.SYNOPSIS
  relay.ps1（リレー・I-186）が使う部品。ドットで読み込む（テストも同じく読み込んで試す）。
#>

function Find-Claude {
    if ($env:CLAUDE_EXE) {
        # 無い実体を返すと、起こす所まで進んでから分かりにくい理由で落ちる（B-347）
        if (Test-Path -LiteralPath $env:CLAUDE_EXE -PathType Leaf) { return $env:CLAUDE_EXE }
        throw "`$env:CLAUDE_EXE の指す実体がありません: $($env:CLAUDE_EXE)"
    }
    # PATH の当たりは実行ファイル（.exe）だけを採る（B-313）＝npm の claude.cmd・claude.ps1 は
    # relay.ps1 の Invoke-Claude（UseShellExecute = $false）では直に起動できない。
    $cmd = Get-Command claude -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { [IO.Path]::GetExtension($_.Source) -eq '.exe' } | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    # PATH に無ければ VS Code 拡張の同梱の実体（codex_review/run.ps1 と同じ探し方＝
    # 版は意味的バージョンで比べる。文字列順だと 2.1.99 が 2.1.282 より新しくなる）。
    $best = Get-ChildItem (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.vscode\extensions') -Directory -ErrorAction SilentlyContinue |
        Where-Object Name -like 'anthropic.claude-code-*' |
        ForEach-Object {
            $v = [version]'0.0'
            if ($_.Name -match 'claude-code-(\d+(?:\.\d+)+)') { [void][version]::TryParse($Matches[1], [ref]$v) }
            [pscustomobject]@{ Version = $v; Exe = Join-Path $_.FullName 'resources\native-binary\claude.exe' }
        } |
        Where-Object { Test-Path $_.Exe } | Sort-Object Version -Descending | Select-Object -First 1
    if ($best) { return $best.Exe }
    throw 'claude の実行ファイル（.exe）が見つかりません（PATH のスクリプトは直に起動できない） ⇒ $env:CLAUDE_EXE で実体のパスを指定してください。'
}

# 引数の配列を、Windows の規則（CommandLineToArgvW）で 1 本の文字列にする（B-314）。
# ⚠️ Start-Process -ArgumentList に配列を渡すと空白でつなぐだけ＝空白を含むパスが割れる。
#    空白・引用符を含む値と空の値だけを引用し、引用符の前の \ と末尾の \ を倍にする。
function Join-ProcessArgs([object[]]$ArgList) {
    ($ArgList | ForEach-Object {
        $s = [string]$_
        if ($s -ne '' -and $s -notmatch '[\s"]') { $s }
        else { '"' + ($s -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"' }
    }) -join ' '
}

# --- 見張りの輪の書き足し（I-188）------------------------------------------------------
# 同じ状態が長く続く間（push のゲートのフルなど）も relay.log に経過時間と最後の手を書く。
# 会話の記録が読めなくても落とさない＝そのときは経過時間だけ書く。

# 会話の記録は `~/.claude/projects/<プロジェクト>/<sessionId>.jsonl`（1 段下だけを見る＝
# 子エージェントの記録は更に下にあって、名前が違う）
function Find-Transcript([string]$ProjectsDir, [string]$SessionId) {
    if (-not $SessionId -or -not (Test-Path -LiteralPath $ProjectsDir)) { return $null }
    foreach ($d in Get-ChildItem -LiteralPath $ProjectsDir -Directory -ErrorAction SilentlyContinue) {
        $p = Join-Path $d.FullName "$SessionId.jsonl"
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $null
}

# 記録の末尾から、最後の道具の呼び出しの説明と時刻を読む。読めなければ $null。
# ⚠️ 末尾だけ読む＝長いセッションの記録は数 MB になる。切り口の行は壊れているので飛ばす
function Get-LastMove([string]$TranscriptPath, [int]$TailBytes = 524288) {
    if (-not $TranscriptPath -or -not (Test-Path -LiteralPath $TranscriptPath)) { return $null }
    try {
        # 書いている最中のファイルを読む＝相手の書き込みを妨げない共有で開く
        $fs = [IO.FileStream]::new($TranscriptPath, 'Open', 'Read', 'ReadWrite')
        try {
            $start = [Math]::Max(0, $fs.Length - $TailBytes)
            [void]$fs.Seek($start, 'Begin')
            $buf = [byte[]]::new($fs.Length - $start)
            $read = 0
            while ($read -lt $buf.Length) {
                $k = $fs.Read($buf, $read, $buf.Length - $read)
                if ($k -le 0) { break }
                $read += $k
            }
        } finally { $fs.Dispose() }
        $lines = [Text.Encoding]::UTF8.GetString($buf, 0, $read) -split "`n"
    } catch { return $null }
    for ($i = $lines.Count - 1; $i -ge 0; $i--) {
        $line = $lines[$i]
        if ($line -notmatch '"tool_use"') { continue }
        try { $rec = $line | ConvertFrom-Json } catch { continue }
        if ($rec.type -ne 'assistant') { continue }
        $use = @($rec.message.content) | Where-Object { $_.type -eq 'tool_use' } | Select-Object -Last 1
        if (-not $use) { continue }
        $what = "$($use.input.description)".Trim()
        # 人の答えを待つ質問は、最初の問いの文を出す（I-231＝通知の本文に載せる）
        if (-not $what -and $use.name -eq 'AskUserQuestion') {
            $q = @($use.input.questions) | Select-Object -First 1
            if ($q -and "$($q.question)".Trim()) { $what = "質問＝$("$($q.question)".Trim())" }
        }
        if (-not $what) { $what = "$($use.name)" }
        # 時刻は元の字から読む＝ConvertFrom-Json の日付の直し方は版で違う
        $at = $null
        $m = [regex]::Match($line, '"timestamp"\s*:\s*"(?<t>[^"]+)"')
        if ($m.Success) { try { $at = [DateTimeOffset]::Parse($m.Groups['t'].Value).LocalDateTime } catch { } }
        return [pscustomobject]@{ what = $what; at = $at }
    }
    return $null
}

# --- 進み具合で止める（I-229）・最後の 1 本に知らせる（I-227）-----------------------------
# 進み具合の物差し＝HEAD と台帳 2 本と版計画（メモリの project_roadmap.md）の中身。
# 版計画を数えるのは B-345＝監査や調査の回は結果を版計画の途中行にだけ書くことがあり、
# それを「進まない回」に数えていた。メモリ全体（更新時刻）は見ない＝索引の整えや
# 凍結の写しだけで「進んだ」にしない。読めないものは空として数える。
# $Roadmap＝版計画のパス（既定は Claude Code がこのリポジトリに割り当てるメモリの置き場）
function Get-RoadmapPath([string]$Root) {
    $slug = $Root.TrimEnd('\', '/') -replace '[:\\/]', '-'
    return Join-Path ([Environment]::GetFolderPath('UserProfile')) ".claude\projects\$slug\memory\project_roadmap.md"
}

function Get-ProgressMark([string]$Root, [string]$Roadmap = (Get-RoadmapPath $Root)) {
    $head = & git -C $Root rev-parse HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { $head = '' }
    $parts = @("head=$head")
    $files = [ordered]@{ 'ISSUES.md' = (Join-Path $Root 'ISSUES.md')
                         'ISSUES_archive.md' = (Join-Path $Root 'ISSUES_archive.md')
                         'roadmap' = $Roadmap }
    foreach ($name in $files.Keys) {
        $p = $files[$name]
        $hash = if ($p -and (Test-Path -LiteralPath $p)) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash } else { '' }
        $parts += "$name=$hash"
    }
    return $parts -join ' '
}

# 入力文の {LAST_NOTE}＝このセッションが「最後」になりうるときだけ一言を返す（無ければ空）。
# $Stall＝続けて進まなかった本数（このセッションの前まで）。
function Get-LastNote([int]$No, [int]$Max, [int]$Stall, [int]$StallMax) {
    if ($No -ge $Max) {
        return "`n⚠️ これが最後の 1 本です（上限 $Max 本）＝下の手順 6「最後の 1 本」に従って終える。"
    }
    if ($StallMax -gt 0 -and $Stall -ge $StallMax - 1) {
        return "`n⚠️ 前の $Stall 本はコミットも台帳・版計画の変化も無かった＝この 1 本も進まなければリレーは止まる。進めないと分かったら下の手順 6「最後の 1 本」に従って終える。"
    }
    return ''
}

# 終わりの通知の題（I-228）。終わり方の名前は relay.ps1 の $endKind
function Get-EndTitle([string]$Kind) {
    switch ($Kind) {
        'done_unpushed' { return '済んだが push していない' }
        'max'           { return '上限で止まった' }
        'stall'         { return '進まないので止まった' }
        'failed'        { return '失敗で止まった' }
        'interrupted'   { return '中断された' }
        default         { return "終わった（$Kind）" }
    }
}

# 人の答え待ちの通知の本文（I-231）。最後の手が質問なら、その問いの文が載る
function Format-WaitNotice([int]$No, [string]$Id, [string]$Name, [TimeSpan]$Elapsed, $Move) {
    $s = "$No 本目が人の答えを待っています（$([int][Math]::Floor($Elapsed.TotalMinutes)) 分）。claude attach $Id ／ Remote Control の「$Name」"
    if ($Move) { $s += "`n$($Move.what)" }
    return $s
}

# --- 人へ知らせる（I-228・I-231）-------------------------------------------------------
# リレーが `done` 以外で終わったとき・人の答えを待っているときに、Windows の通知を出す
# （セッションが死んだときに届くのはこれだけ）。
# 🔑 子のプロセスを起こさず、このプロセスの通知領域のアイコンから吹き出しを出す
#    （Windows 11 ではトーストとして出る）。WinRT のトーストを Windows PowerShell 5.1 の
#    -EncodedCommand で出す案は、この PC では起動が「アクセスが拒否されました」で止まった
#    （2026-10-10 に実測＝端末の保護機能の拒否と見て、迂回を探さずにこの形へ切り替えた）。
# ⚠️ アイコンを捨てると通知も消える＝プロセスの終わりまで持つ（リレーの窓は done 以外では
#    開けたまま待つので、人が読むまで残る）。吹き出しの字数は Windows の上限（題 63・本文 255）で切る。
# 🔑 失敗しても投げない＝通知が出せないことでリレーを止めない。結果の 1 行を返す（記録に書く）。
#    RADIOSIM_RELAY_NOTICE_LOG があれば、通知の代わりにそのファイルへ 1 行書く（テスト用）。
$script:noticeIcon = $null
function Send-DesktopNotice([string]$Title, [string]$Body) {
    try {
        if ($env:RADIOSIM_RELAY_NOTICE_LOG) {
            Add-Content -LiteralPath $env:RADIOSIM_RELAY_NOTICE_LOG -Value "$Title`t$($Body -replace '\r?\n', ' / ')" -Encoding UTF8
            return '通知＝記録へ書いた（試し）'
        }
        if ($PSVersionTable.PSEdition -eq 'Core' -and -not $IsWindows) { return '通知＝出さない（Windows でない）' }
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        if (-not $script:noticeIcon) {
            $script:noticeIcon = [System.Windows.Forms.NotifyIcon]::new()
            $script:noticeIcon.Icon = [System.Drawing.SystemIcons]::Information
            $script:noticeIcon.Text = 'RadioSim のリレー'
        }
        $cut = { param($s, $n) if ($s.Length -gt $n) { $s.Substring(0, $n - 1) + '…' } else { $s } }
        $script:noticeIcon.BalloonTipTitle = & $cut $Title 63
        $script:noticeIcon.BalloonTipText = & $cut $Body 255
        $script:noticeIcon.Visible = $true
        $script:noticeIcon.ShowBalloonTip(30000)
        return '通知＝出した'
    } catch {
        return "通知＝出せない: $($_.Exception.Message)"
    }
}

# 例「  … busy/working のまま 6 分・最後の手＝ステージ6のコミットを main へ送る（11:48:56 から）」
function Format-Beat([string]$State, [TimeSpan]$Elapsed, $Move) {
    $s = "  … $State のまま $([int][Math]::Floor($Elapsed.TotalMinutes)) 分"
    if ($Move) {
        $s += "・最後の手＝$($Move.what)"
        if ($Move.at) { $s += "（$($Move.at.ToString('HH:mm:ss')) から）" }
    }
    return $s
}
