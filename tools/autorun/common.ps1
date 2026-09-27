<#
.SYNOPSIS
  relay.ps1（リレー・I-186）が使う部品。ドットで読み込む（テストも同じく読み込んで試す）。
#>

function Find-Claude {
    if ($env:CLAUDE_EXE) { return $env:CLAUDE_EXE }
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
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
    throw 'claude が見つかりません ⇒ $env:CLAUDE_EXE で実体のパスを指定してください。'
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
        if (-not $what) { $what = "$($use.name)" }
        # 時刻は元の字から読む＝ConvertFrom-Json の日付の直し方は版で違う
        $at = $null
        $m = [regex]::Match($line, '"timestamp"\s*:\s*"(?<t>[^"]+)"')
        if ($m.Success) { try { $at = [DateTimeOffset]::Parse($m.Groups['t'].Value).LocalDateTime } catch { } }
        return [pscustomobject]@{ what = $what; at = $at }
    }
    return $null
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
