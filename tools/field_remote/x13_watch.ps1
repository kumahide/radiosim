<#
tools/field_remote/x13_watch.ps1
================================
デスクトップから SSH で X13 の記録の様子を 1 回見る（読むだけ・X13 には何も書かない）。
野外の受け入れ試験で Claude が使う。手順の全体は同じフォルダの README.md。

  pwsh -NoProfile -File tools\field_remote\x13_watch.ps1            # 最新の記録を見る
  pwsh -NoProfile -File tools\field_remote\x13_watch.ps1 -Folder gnss-…  # フォルダを指定

出すもの＝最新の gnss-… の様子（gnss_peek.py）・記録の処理が走っているか・電池と空き容量。

⚠️ 遠隔のスクリプトは -EncodedCommand で渡す＝SSH と Windows PowerShell 5.1 の二重の
   引用符の解釈を通さない（素で渡すと `"` と `$` が途中で剥がれる）。
⚠️ 遠隔は 5.1＝`??`・三項演算子を使わない。
⚠️ 「記録の処理」は python の起動行で見分ける＝別の名前の起動用スクリプトから回すと
   「見当たらない」と出る（記録が進んでいるかは gnss_peek の「最後の受信から」が正）。
#>
param(
    [string]$HostName = 'x13',
    [string]$Root = 'C:\dev\field-data\gnss',
    [string]$Repo = 'C:\dev\radiosim-repo',
    [string]$Folder = ''
)
$ErrorActionPreference = 'Stop'

function Quote([string]$s) { "'" + $s.Replace("'", "''") + "'" }

$body = @'
$ProgressPreference = 'SilentlyContinue'   # 進み具合は -EncodedCommand だと CLIXML のまま漏れる
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
if ($Folder) { $d = Get-Item (Join-Path $Root $Folder) -ErrorAction SilentlyContinue }
else { $d = Get-ChildItem $Root -Directory -Filter 'gnss-*' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1 }
if (-not $d) { "記録のフォルダがありません: $Root" }
else { & (Join-Path $Repo '.venv\Scripts\python.exe') (Join-Path $Repo 'tools\field_remote\gnss_peek.py') $d.FullName }
# 人が打つ `gnss log` と、SSH から切り離して起動する `timed_log*.py`（起動行に gnss log が入らない）
$p = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'gnss\s+log|timed_log\w*\.py' })
if ($p.Count) { "記録の処理: 走っている（PID $($p.ProcessId -join ',')）" } else { "記録の処理: 見当たらない" }
$b = Get-CimInstance Win32_Battery | Select-Object -First 1
$ac = (Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue | Select-Object -First 1).PowerOnline
$acText = 'バッテリー'; if ($ac) { $acText = '電源につないでいる' }
$free = (Get-PSDrive C).Free / 1GB
"電池 $($b.EstimatedChargeRemaining)%（$acText）・C: の空き {0:N1} GB" -f $free
'@
$script = "`$Root = $(Quote $Root); `$Repo = $(Quote $Repo); `$Folder = $(Quote $Folder)`n$body"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
ssh -o LogLevel=ERROR $HostName "powershell -NoProfile -NonInteractive -EncodedCommand $encoded"
exit $LASTEXITCODE
