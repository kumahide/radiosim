# -*- mode: python ; coding: utf-8 -*-
#
# RadioSim Pro 2.3 - PyInstaller spec ファイル
#
# ビルド方法:
#   pyinstaller radiosim.spec
#
# 出力先: dist/RadioSimPro/
#

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))  # version.py を import するため

block_cipher = None

# ============================================================
# 追加データ（バイナリに同梱するファイル）
# ============================================================
import matplotlib
import sv_ttk as _sv_ttk
from PIL import Image as _Image

mpl_data_path = Path(matplotlib.get_data_path())
sv_ttk_path   = Path(_sv_ttk.__file__).parent

# icon.png が存在すれば icon.ico へ変換してビルドに使用する
_icon_png = Path("icon.png")
_icon_ico = Path("icon.ico")
if _icon_png.exists():
    _img = _Image.open(_icon_png).convert("RGBA")
    _img.save(str(_icon_ico), format="ICO",
              sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
_exe_icon = str(_icon_ico) if _icon_ico.exists() else None

datas = [
    # matplotlib のデータディレクトリ（フォント・スタイル等）
    (str(mpl_data_path), "matplotlib/mpl-data"),
    # sv-ttk の TCL テーマファイル
    (str(sv_ttk_path), "sv_ttk"),
    # README（ヘルプメニューで表示）
    ("README_binary_ja.md", "."),
    ("README_binary_en.md", "."),
    # ロゴ画像
    ("logo.png", "."),
]

# icon.png が存在する場合のみ datas に追加する
if Path("icon.png").exists():
    datas.append(("icon.png", "."))

# ============================================================
# EXE ファイルプロパティ（エクスプローラー → プロパティ → 詳細）
# version.py から自動生成。バージョン変更は version.py だけでよい。
# ============================================================
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo,
    StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

import version as _ver

def _to_ver_tuple(v: str) -> tuple:
    """'2.0' → (2, 0, 0, 0)、'2.1.3' → (2, 1, 3, 0)、'2.0RC2' → (2, 0, 0, 2)"""
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?(?:RC(\d+))?", v)
    if not m:
        return (0, 0, 0, 0)
    return (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3) or 0),
        int(m.group(4) or 0),
    )

_ver_tuple = _to_ver_tuple(_ver.APP_VERSION)

_version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_ver_tuple,
        prodvers=_ver_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,    # VOS_NT_WINDOWS32
        fileType=0x1,  # VFT_APP
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable("040904B0", [  # English (US) / Unicode
                StringStruct("CompanyName",      "BearValley Corp."),
                StringStruct("FileDescription",  _ver.APP_NAME),
                StringStruct("FileVersion",      _ver.APP_VERSION),
                StringStruct("InternalName",     "RadioSimPro"),
                StringStruct("LegalCopyright",   _ver.COPYRIGHT),
                StringStruct("OriginalFilename", "RadioSimPro.exe"),
                StringStruct("ProductName",      _ver.APP_NAME),
                StringStruct("ProductVersion",   _ver.APP_VERSION),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

# ============================================================
# 隠れた import（PyInstaller が自動検出できないもの）
# ============================================================
hiddenimports = [
    # matplotlib TkAgg バックエンド（tkinter 連携）
    "matplotlib.backends.backend_tkagg",
    "matplotlib.backends.backend_agg",
    "matplotlib.backends._backend_tk",
    # PIL / Pillow
    "PIL._tkinter_finder",
    "PIL.Image",
    "PIL.PngImagePlugin",
    # requests の内部モジュール
    "requests.adapters",
    "requests.auth",
    "requests.cookies",
    "requests.exceptions",
    "requests.models",
    "requests.sessions",
    "requests.packages",
    # tkinter 関連
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    # sv-ttk / darkdetect
    "sv_ttk",
    "darkdetect",
    # markdown (README viewer)
    "markdown",
    "markdown.extensions.tables",
    "markdown.extensions.fenced_code",
    "markdown.extensions.toc",
    # tkinter 追加モジュール
    "tkinter.scrolledtext",
    # urllib3（requests の依存）
    "urllib3",
    "urllib3.util.retry",
    "urllib3.util.ssl_",
    # certifi（requests の SSL 証明書）
    "certifi",
    # truststore（OS 証明書ストア連携・企業プロキシ SSL 対策）
    "truststore",
    "truststore._api",
    "truststore._windows",
    "truststore._ssl_constants",
    # pyparsing（matplotlib の依存）
    "pyparsing",
    "pyparsing.testing",
    "pyparsing.core",
    "pyparsing.helpers",
    "pyparsing.actions",
    "pyparsing.exceptions",
    "pyparsing.results",
    "pyparsing.unicode",
    # unittest（pyparsing の依存）
    "unittest",
    "unittest.mock",
    # numpy の内部モジュール
    "numpy.core._multiarray_umath",
    "numpy.core._multiarray_tests",
    # tkintermapview（タイルキャッシュ管理ウィンドウ）
    "tkintermapview",
    "geocoder",   # tkintermapview がトップレベルで無条件 import
    "pyperclip",  # 同上
    # ※ customtkinter は tkintermapview が hasattr ダックタイピングで参照するだけで
    #   実 import しないため hiddenimports から除外（excludes でバンドルも防ぐ）。
    # ※ "pywin32" は import 可能な実体ではない（win32api 等が実モジュール）ため no-op。削除。
]

# ============================================================
# Analysis
# ============================================================
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 開発・テスト系
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
        "scipy",
        "pandas",
        "sklearn",
        "cv2",
        # requests の診断用 optional 依存（requests/help.py が try/except で参照。
        # 実行時は不要だが ~8.7MB バンドルされるため除外）
        "cryptography",
        # tkintermapview は customtkinter を実 import しない（hasattr 判定のみ）
        "customtkinter",
        # GUI フレームワーク（tkinter のみ使用）
        "wx",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "gi",
        "gtk",
        # A: matplotlib 未使用バックエンド（TkAgg / Agg のみ使用）
        "matplotlib.backends.backend_qt5agg",
        "matplotlib.backends.backend_qt5",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qtcairo",
        "matplotlib.backends.backend_webagg",
        "matplotlib.backends.backend_webagg_core",
        "matplotlib.backends.web_backend",
        "matplotlib.backends.backend_gtk3agg",
        "matplotlib.backends.backend_gtk3cairo",
        "matplotlib.backends.backend_gtk4agg",
        "matplotlib.backends.backend_gtk4cairo",
        "matplotlib.backends.backend_wxagg",
        "matplotlib.backends.backend_wx",
        "matplotlib.backends.backend_pdf",
        "matplotlib.backends.backend_ps",
        "matplotlib.backends.backend_svg",
        "matplotlib.backends.backend_pgf",
        "matplotlib.backends.backend_cairo",
        "matplotlib.backends.backend_nbagg",
        "matplotlib.backends.backend_template",
        # B: 標準ライブラリ（本アプリで未使用）
        # ※ email / html は dem.py / batch.py で使用するため除外しない
        # "xml",
        "xmlrpc",
        "ftplib",
        "imaplib",
        "poplib",
        "smtplib",
        "telnetlib",
        "nntplib",
        "http.server",
        "doctest",
        "pdb",
        "profile",
        "cProfile",
        "timeit",
        "trace",
        "tkinter.test",
        "lib2to3",
        # "plistlib",  # matplotlib font_manager が Windows でも使用するため除外不可
        "turtle",
        "turtledemo",
        "ensurepip",
        "venv",
        "antigravity",
        "this",
        "tabnanny",
        "py_compile",
        "compileall",
        "pickletools",
        # C: numpy テスト・ビルドツール
        "numpy.testing",
        "numpy.tests",
        "numpy.distutils",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ============================================================
# 同梱データの間引き（AV スキャン面＝ファイル数削減・起動高速化）
# ------------------------------------------------------------
# Tcl の tzdata（タイムゾーンDB・約600ファイル）と msgs（locale メッセージ
# カタログ・約130ファイル）は Tcl の clock/msgcat 用で、GUI 用 tkinter では
# 未使用。時刻・多言語はすべて Python 側で処理しているため安全に除去できる。
# 合わせて全同梱ファイル数の約半分を占めるため、起動時のオンアクセス AV
# スキャン面を大きく削減する（実機ログでファイル数が起動律速と確定）。
# ============================================================
def _keep_data(dest: str) -> bool:
    p = dest.replace("\\", "/").lower()
    if "tcl" not in p:            # Tcl 同梱データ以外は一切触らない
        return True
    return ("/tzdata/" not in p) and ("/msgs/" not in p)

a.datas = [d for d in a.datas if _keep_data(d[0])]

# ============================================================
# PYZ（Python アーカイブ）
# ============================================================
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ============================================================
# EXE
# ============================================================
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir モード
    name="RadioSimPro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                       # UPX 圧縮（インストール済みの場合）
    console=False,                  # コンソールウィンドウを非表示
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
    version=_version_info,
)

# ============================================================
# COLLECT（onedir: フォルダ形式で出力）
# ============================================================
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RadioSimPro",             # dist/RadioSimPro/ に出力
)
