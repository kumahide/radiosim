<#
tools/field_remote/x13_ssh_setup.ps1
====================================
X13 で 1 回だけ実行する＝デスクトップの Claude Code が Tailscale 越しに SSH で入れるようにする。
野外の受け入れ試験では、記録は X13 の手元で回し、Claude は SSH で読んで確かめる
（COM ポートを網越しに引かない＝gnss_index.csv の受信時刻に回線の揺れを混ぜないため）。

実行（X13 で「管理者として実行」した PowerShell）:
  powershell -ExecutionPolicy Bypass -File .\x13_ssh_setup.ps1
同じフォルダに id_ed25519_x13.pub（デスクトップの公開鍵）を置く。何度実行しても同じ状態になる。

やること
  1. OpenSSH サーバーを入れ、自動起動にする
  2. 受け付ける相手を Tailscale のアドレス帯（100.64.0.0/10）だけに絞る
  3. パスワードでのログインを止め、鍵だけにする
  4. 公開鍵を登録する（管理者の口と一般ユーザーの口の両方＝どちらで入っても通る）
  5. SSH の既定のシェルを PowerShell にする
  最後にユーザー名を出す＝デスクトップの ~/.ssh/config の User に書く。

⚠️ Windows PowerShell 5.1 で動かす＝`??`・三項演算子を使わない。BOM 付き UTF-8 で保存する
   （BOM が無いと 5.1 は CP932 で読み、日本語の行が化けて構文が壊れる）。
#>
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$pubPath = Join-Path $PSScriptRoot 'id_ed25519_x13.pub'
if (-not (Test-Path $pubPath)) { throw "公開鍵が見つかりません: $pubPath（このスクリプトと同じフォルダに置いてください）" }
$pub = (Get-Content $pubPath -Raw).Trim()
if ($pub -notmatch '^ssh-ed25519 \S+') { throw "公開鍵の形ではありません: $pubPath" }

Write-Host '1/5 OpenSSH サーバーを入れて起動する'
$cap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*' | Select-Object -First 1
if (-not $cap) { throw 'この Windows には OpenSSH サーバーの追加機能が見つかりません' }
if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service sshd -StartupType Automatic
Start-Service sshd   # 初回の起動で sshd_config とホスト鍵が作られる

Write-Host '2/5 ファイアウォールを Tailscale の中だけに絞る'
$ruleName = 'OpenSSH-Server-In-TCP'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName -DisplayName 'OpenSSH Server (sshd)' -Direction Inbound `
        -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
}
Set-NetFirewallRule -Name $ruleName -RemoteAddress '100.64.0.0/10' -Profile Any -Enabled True

Write-Host '3/5 パスワードでのログインを止める'
$cfg = Join-Path $env:ProgramData 'ssh\sshd_config'
$bak = "$cfg.before_field_remote"
if (-not (Test-Path $bak)) { Copy-Item $cfg $bak }
$lines = @(Get-Content $cfg)
$hit = $false
for ($i = 0; $i -lt $lines.Count; $i++) {
    # 最初の 1 行だけ書き換える＝末尾の Match ブロックの中へは入れない
    if (-not $hit -and $lines[$i] -match '^\s*#?\s*PasswordAuthentication\s') {
        $lines[$i] = 'PasswordAuthentication no'
        $hit = $true
    }
}
if (-not $hit) { $lines = @('PasswordAuthentication no') + $lines }
Set-Content -Path $cfg -Value $lines -Encoding ascii

Write-Host '4/5 公開鍵を登録する'
# 管理者の口（sshd_config の Match Group administrators が読む）
$adminKeys = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
if (-not (Test-Path $adminKeys) -or -not (Select-String -Path $adminKeys -SimpleMatch $pub -Quiet)) {
    Add-Content -Path $adminKeys -Value $pub -Encoding ascii
}
# 権限は名前でなく SID で書く＝日本語版の Windows では「Administrators」の名前が通らない
icacls $adminKeys /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
# 一般ユーザーの口
$userSsh = Join-Path $env:USERPROFILE '.ssh'
New-Item -ItemType Directory -Force $userSsh | Out-Null
$userKeys = Join-Path $userSsh 'authorized_keys'
if (-not (Test-Path $userKeys) -or -not (Select-String -Path $userKeys -SimpleMatch $pub -Quiet)) {
    Add-Content -Path $userKeys -Value $pub -Encoding ascii
}

Write-Host '5/5 SSH の既定のシェルを PowerShell にする'
New-Item -Path 'HKLM:\SOFTWARE\OpenSSH' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\OpenSSH' -Name DefaultShell -PropertyType String -Force `
    -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" | Out-Null

Restart-Service sshd
if (-not (Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue)) {
    throw 'sshd が 22 番で待ち受けていません'
}

Write-Host ''
Write-Host '完了しました。次の 2 行を Claude に伝えてください:' -ForegroundColor Green
Write-Host "  ユーザー名: $env:USERNAME"
Write-Host "  コンピューター名: $env:COMPUTERNAME"
