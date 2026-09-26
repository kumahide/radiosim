<#
.SYNOPSIS
  run.ps1（作業票・I-181）と relay.ps1（リレー・I-186）が共有する部品。ドットで読み込む。
#>

function Find-Claude {
    if ($env:CLAUDE_EXE) { return $env:CLAUDE_EXE }
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # PATH に無ければ VS Code 拡張の同梱の実体（codex_review/run.ps1 と同じ探し方＝
    # 版は意味的バージョンで比べる。文字列順だと 2.1.99 が 2.1.282 より新しくなる）。
    $best = Get-ChildItem (Join-Path $env:USERPROFILE '.vscode\extensions') -Directory -ErrorAction SilentlyContinue |
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
