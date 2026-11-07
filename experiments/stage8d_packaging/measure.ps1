# 新しい画面の起動を 3 つの形で交互に測る（4.0 ステージ8d）＝ソース（venv）・PyInstaller・embeddable。
# 各回は probe.py が 1 行の JSON を $Out へ足す。続けて CodeIntegrity（SAC）の 3033/3077 を数える。
#   pwsh -File experiments\stage8d_packaging\measure.ps1 -Root D:\dev\radiosim\stage8d -Runs 6
param(
    [Parameter(Mandatory)][string]$Root,
    [int]$Runs = 6
)
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Out = Join-Path $Root "startup.jsonl"
$Tagged = $Out.Replace(".jsonl", ".tagged.jsonl")
foreach ($p in $Out, $Tagged) { if (Test-Path $p) { Remove-Item $p } }
$since = Get-Date
$pyiExe = Join-Path $Root "pyi\dist\RadioSimLink\RadioSimLink.exe"
$embedPy = Join-Path $Root "embed\RadioSimLink\python.exe"
$embedProbe = Join-Path $Root "embed\RadioSimLink\app\probe.py"

$forms = [ordered]@{
    source = @{ exe = $env:RADIOSIM_PYTHON; args = @((Join-Path $PSScriptRoot "probe.py")); cwd = $Repo }
    pyi    = @{ exe = $pyiExe; args = @(); cwd = (Split-Path $pyiExe) }
    embed  = @{ exe = $embedPy; args = @($embedProbe); cwd = (Split-Path $embedPy) }
}
for ($i = 1; $i -le $Runs; $i++) {
    foreach ($k in $forms.Keys) {
        $f = $forms[$k]
        $env:RADIOSIM_STARTUP_PROBE = $Out
        # ソースは probe.py の隣でなくリポジトリを読む＝PYTHONPATH で core と webui を足す
        if ($k -eq "source") { $env:PYTHONPATH = $Repo } else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
        $before = if (Test-Path $Out) { (Get-Content $Out).Count } else { 0 }
        $p = Start-Process -FilePath $f.exe -ArgumentList $f.args -WorkingDirectory $f.cwd -PassThru -WindowStyle Hidden
        if (-not $p.WaitForExit(40000)) { Stop-Process -Id $p.Id -Force; Write-Host "$k #$i 時間切れ" }
        $after = if (Test-Path $Out) { (Get-Content $Out).Count } else { 0 }
        if ($after -gt $before) {
            $line = (Get-Content $Out)[-1]
            Add-Content $Tagged "{`"form`":`"$k`",`"run`":$i,`"r`":$line}"
        } else { Write-Host "$k #$i 記録なし（終了コード $($p.ExitCode)）" }
    }
}
Remove-Item Env:RADIOSIM_STARTUP_PROBE, Env:PYTHONPATH -ErrorAction SilentlyContinue
$ev = Get-WinEvent -FilterHashtable @{ LogName = "Microsoft-Windows-CodeIntegrity/Operational"; Id = 3033, 3077; StartTime = $since } -ErrorAction SilentlyContinue
Write-Host "CodeIntegrity 3033/3077 (since $since): $(@($ev).Count)"
$ev | Select-Object -First 10 | ForEach-Object { Write-Host "  $($_.Id) $($_.TimeCreated) $($_.Message.Substring(0, [Math]::Min(200, $_.Message.Length)))" }
