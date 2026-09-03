; RadioSim Pro - Inno Setup インストーラスクリプト（3.1 段6）
;
; 起動方法: build.bat installer から呼ぶ（AppVersion を core/version.py の
; APP_VERSION から /DAppVersion=... で渡す。単体で ISCC にかける場合は
; 下の #ifndef で "0.0.0-dev" が使われる）。
;
; 配置方針（3.1 段1の保存先移設と対）:
;   - このインストーラは既定で Program Files（管理者権限が無ければユーザー配下）
;     へインストールする＝インストール先は書込禁止になり得る前提。
;   - portable.txt はビルド側（build.bat installer）が最初から作らないので、
;     core.config.is_portable() は False を返し、設定/キャッシュ/ログ/結果は
;     すべて OS 標準フォルダ（%APPDATA%・%LOCALAPPDATA%・ドキュメント）へ書く。
;   - terrain_cache / results の空フォルダは同梱しない（下の [Files] の
;     Excludes）＝インストール先の直下に「書けそうで書けない」フォルダを
;     残さない。

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

#define AppName "RadioSim Pro"
#define AppExeName "RadioSimPro.exe"
#define AppPublisher "BearValley AI Craftworks"
#define AppURL "https://github.com/kumahide/radiosim"

[Setup]
; 固定 GUID（版が変わっても同じ値のまま＝アップグレードインストールの同一性判定に使う）
AppId={{8C9E9E0B-7C6E-4B7A-9C7B-6D5E8F0A1C2D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\RadioSim Pro
DefaultGroupName=RadioSim Pro
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=RadioSimPro-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
; 署名レディ化（3.1 段6・[[project-code-signing]]）: 証明書を導入したら
; SignTool= をここに 1 行足すだけで、ISCC がインストーラ自身にも署名する。
; 今は未署名のまま（署名の是非はトリガー駆動で保留＝メモリ project-code-signing）。
;SignTool=signtool
; 管理者権限が無い環境でも Program Files 以外へインストールできるようにする
; （3.1 段1で「書込禁止フォルダに置かれる」想定を実機検証済み＝その経路と一致させる）。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; dist\RadioSimPro\ の一式をそのまま同梱する。ただし:
;  - portable.txt は build.bat installer が最初から作らないので通常は存在しない
;    （念のため除外リストにも入れておく＝手元ビルドの取り違え対策）
;  - terrain_cache / results は空フォルダのまま同梱しない（上の配置方針を参照）
; createallsubdirs は使わない: それを付けると Excludes で中身を空にした
; フォルダ（terrain_cache / results）も空のまま作られてしまうと実機検証で
; 判明した（2026-09-04）。recursesubdirs だけなら、ファイルが1つも
; マッチしないサブフォルダはインストール先に作られない。
Source: "..\dist\RadioSimPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs; Excludes: "portable.txt,terrain_cache\*,results\*"

[Icons]
Name: "{group}\RadioSim Pro"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,RadioSim Pro}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\RadioSim Pro"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,RadioSim Pro}"; Flags: nowait postinstall skipifsilent
