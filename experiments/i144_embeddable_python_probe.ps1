<#
.SYNOPSIS
    I-144 段階1（検証スクリプト）＝ python.org 公式の embeddable python 配布に
    tkinter・numpy・matplotlib 等を持ち込み、RadioSim の GUI が実際に起動するか
    を確かめる POC。既存の PyInstaller + Inno Setup 配布は一切変更しない
    （並行実験・ISSUES.md I-144 の意見1「まず小さく」に対応）。

.DESCRIPTION
    embeddable python は配布サイズを削るため tcl/tk（＝tkinter）を同梱しない。
    RadioSim は tkinter 製 GUI なので、これが成立するかどうかが最大の未知数
    だった（意見1で書いていなかった落とし穴）。このスクリプトは:
      1. 開発機にインストール済みの RADIOSIM_PYTHON と同じバージョンの
         embeddable zip を python.org から取得・展開
      2. そのフル install（sys.base_prefix）から tkinter/_tkinter.pyd と
         Tcl/Tk の DLL（Tk 8.6 なら tcl8.6/tk8.6 のスクリプト一式も）をコピー
      3. venv の site-packages を丸ごと vendor/ へコピー（pip/setuptools/
         wheel は除く）＝numpy 等の .pyd は同じインタプリタ ABI からの
         コピーなので pip 実行やビルドツールチェーンが不要
      4. ._pth を編集して site-packages を有効化＋vendor とリポジトリ本体を
         パスへ追加
      5. リポジトリの main.py をそのまま（コピーせず）起動し、
         RADIOSIM_PROFILE=1 で "first paint" まで進んだかログで確認

    出力は $env:RADIOSIM_BUILD_ROOT\embeddable_poc\ 配下（リポジトリの外＝
    git 管理外）。毎回作り直す（冪等）。

.USAGE
    powershell -File experiments\i144_embeddable_python_probe.ps1
    powershell -File experiments\i144_embeddable_python_probe.ps1 -KeepRunning
#>

param(
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot

$Py = $env:RADIOSIM_PYTHON
if (-not $Py) { throw "RADIOSIM_PYTHON が未設定です（build.bat と同じ環境変数を使う）" }
$BuildRoot = $env:RADIOSIM_BUILD_ROOT
if (-not $BuildRoot) { throw "RADIOSIM_BUILD_ROOT が未設定です" }

$PyVer = & $Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
$BasePrefix = & $Py -c "import sys; print(sys.base_prefix)"
$SitePkgs = & $Py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"

Write-Host "=== I-144 embeddable python 検証: Python $PyVer ==="
Write-Host "base install : $BasePrefix"
Write-Host "site-packages: $SitePkgs"

$PocDir = Join-Path $BuildRoot "embeddable_poc"
$EmbedDir = Join-Path $PocDir "python-embed"
$Zip = Join-Path $PocDir "python-$PyVer-embed-amd64.zip"
$Url = "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip"

# ---- 1. まっさらに作り直す ------------------------------------------------
if (Test-Path $PocDir) { Remove-Item -Recurse -Force $PocDir }
New-Item -ItemType Directory -Force -Path $EmbedDir | Out-Null

# ---- 2. embeddable zip を取得して展開 -------------------------------------
Write-Host "`n-- ダウンロード: $Url"
Invoke-WebRequest -Uri $Url -OutFile $Zip
Expand-Archive -Path $Zip -DestinationPath $EmbedDir -Force

# ---- 3. tkinter 一式をフル install から移植（embeddable には同梱されない）--
# DLL の名前は Tk の版で変わる（8.6＝tcl86t.dll/tk86t.dll・9.0＝tcl90.dll/tcl9tk90.dll）
# ので決め打ちせず拾う。zlib1.dll（8.6 の tcl の依存）と libtommath.dll（9.0 の tcl
# の依存）はどちらも embeddable 配布に無い＝在る方を持ち込む。
Write-Host "-- tkinter/tcl を移植"
$DllDir = Join-Path $BasePrefix "DLLs"
Copy-Item (Join-Path $DllDir "_tkinter.pyd") $EmbedDir
$TclDlls = @(Get-ChildItem $DllDir -File | Where-Object { $_.Name -match '^(tcl|tk).*\.dll$|^(zlib1|libtommath)\.dll$' })
if (-not ($TclDlls | Where-Object { $_.Name -match '^tcl' })) { throw "tcl の DLL が $DllDir に見つかりません" }
$TclDlls | ForEach-Object { Copy-Item $_.FullName $EmbedDir; Write-Host "   $($_.Name)" }
# ._pth の "." で $EmbedDir 自体が sys.path に乗る（Lib\ 経由ではない）ので、
# tkinter パッケージは $EmbedDir 直下に置く。
Copy-Item -Recurse (Join-Path $BasePrefix "Lib\tkinter") (Join-Path $EmbedDir "tkinter")
# Tcl/Tk のスクリプト一式＝8.6 は <base>\tcl\tcl8.6・tk8.6 に在り、TCL_LIBRARY/TK_LIBRARY
# で指す。9.0 は DLL の中（zipfs の //zipfs:/lib/tcl）に抱えていて <base>\tcl が無い＝
# 写すものも環境変数も要らない（指すと DLL の中ではなくそちらを探しに行く）。
$TclLibRoot = Join-Path $BasePrefix "tcl"
$TclLibDirs = @()
if (Test-Path $TclLibRoot) {
    $TclLibDirs = @(Get-ChildItem $TclLibRoot -Directory | Where-Object { $_.Name -match '^(tcl|tk)\d' })
    $TclLibDirs | ForEach-Object { Copy-Item -Recurse $_.FullName (Join-Path $EmbedDir $_.Name) }
}

# ---- 4. サードパーティ一式を venv からそのまま持ち込む（vendor）----------
Write-Host "-- site-packages を移植（pip/setuptools/wheel は除く）"
$VendorDir = Join-Path $EmbedDir "vendor"
New-Item -ItemType Directory -Force -Path $VendorDir | Out-Null
Get-ChildItem $SitePkgs -Directory | Where-Object {
    $_.Name -notmatch '^(pip|setuptools|wheel)(-|$|\.dist-info)' -and $_.Name -ne '__pycache__'
} | ForEach-Object { Copy-Item -Recurse $_.FullName (Join-Path $VendorDir $_.Name) }
Get-ChildItem $SitePkgs -File -Filter "*.py" | ForEach-Object { Copy-Item $_.FullName $VendorDir }

# ---- 5. site-packages を有効化 + パスを ._pth へ追加 ----------------------
$PthFile = Get-ChildItem $EmbedDir -Filter "python*._pth" | Select-Object -First 1
if (-not $PthFile) { throw "._pth が見つかりません（embeddable zip の構造が変わった?）" }
$content = Get-Content $PthFile.FullName
$content = $content -replace '^#\s*import site$', 'import site'
$content += "vendor"
$content += $RepoRoot
Set-Content -Path $PthFile.FullName -Value $content -Encoding ASCII

# Python 3.8+ は DLL の暗黙探索（同ディレクトリ含む）をしない＝
# Tcl/Tk の DLL が _tkinter.pyd と同じフォルダに在っても見つからない。
# site 初期化の一環として最初に実行される sitecustomize.py で
# os.add_dll_directory を呼び、tkinter の import より前に検索パスへ登録する。
@"
import os
os.add_dll_directory(os.path.dirname(os.path.abspath(__file__)))
"@ | Set-Content -Path (Join-Path $EmbedDir "sitecustomize.py") -Encoding ASCII

# ---- 6. 起動して "first paint" まで進むかログで確認 -----------------------
foreach ($d in $TclLibDirs) {
    if ($d.Name -match '^tcl\d') { $env:TCL_LIBRARY = Join-Path $EmbedDir $d.Name }
    if ($d.Name -match '^tk\d') { $env:TK_LIBRARY = Join-Path $EmbedDir $d.Name }
}
$env:RADIOSIM_PROFILE = "1"
$env:PYTHONUNBUFFERED = "1"  # リダイレクト先はパイプなので既定は全バッファ＝殺すと出力が消える

$exe = Join-Path $EmbedDir "python.exe"
$stdout = Join-Path $PocDir "stdout.log"
$stderr = Join-Path $PocDir "stderr.log"
Write-Host "`n-- 起動: $exe main.py  (cwd=$RepoRoot)"
$proc = Start-Process -FilePath $exe -ArgumentList "main.py" -WorkingDirectory $RepoRoot `
    -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr

Start-Sleep -Seconds 6
$launched = -not $proc.HasExited
if (-not $KeepRunning -and -not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
}

Write-Host ""
if ($launched) {
    Write-Host "OK: GUI プロセスは 6 秒後も生存（起動時クラッシュなし）"
} else {
    Write-Host "NG: プロセスは終了済み（exit code $($proc.ExitCode)）"
}
$profileText = (Select-String -Path $stdout -Pattern "first paint" -SimpleMatch -ErrorAction SilentlyContinue)
if ($profileText) {
    Write-Host "OK: 起動プロファイルに 'first paint' 到達を確認"
} else {
    Write-Host "確認できず: 'first paint' が stdout.log に無い（-KeepRunning で目視するか stderr.log を見よ）"
}
Write-Host "stdout: $stdout"
Write-Host "stderr: $stderr"
