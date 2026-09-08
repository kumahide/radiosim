; RadioSim Pro - Inno Setup インストーラスクリプト（3.1 段6）
;
; コンパイラ: Inno Setup 7（3.1RC2 以降・I-125 の選定検証の結論）。このファイルは
;   7 で廃止された機能（EnableFsRedirection / {sysnative} / 32bit からの 64bit
;   type library 登録 / WizardResizable）を 1 つも使っていないので 6.7 系でも
;   そのままコンパイルできるが、上流の更新は 6.7.3（2026-05-26）を最後に 7 系
;   へ移っているため、配布ビルドは 7 系で揃える。
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

[LangOptions]
; B-192: 日本語のダイアログを**日本語グリフを自前で持つフォント**で描く。
;
;   同梱の Japanese.isl は DialogFontName を設定していない＝Inno の既定
;   （9pt Segoe UI）で日本語を描くことになる。Segoe UI に日本語のグリフは無く、
;   普段それが読めているのは GDI のフォントリンク
;     HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\FontLink\SystemLink
;   が Segoe UI → Yu Gothic UI / Meiryo UI へ橋渡ししているからにすぎない。
;
;   ⚠️ この橋は環境によって無い。実測（2026-09-07・Windows Sandbox 内）:
;     - 開発機の SystemLink は 83 項目（Segoe UI・Tahoma などを含む）
;     - Sandbox の SystemLink は **1 項目だけ**（SimSun-ExtG のみ）
;     - 一方 meiryo.ttc / msgothic.ttc / YuGoth*.ttc の実体と登録はどちらにも在る
;   ＝フォントが無いのではなく**橋が無い**。結果、Sandbox ではウィザードの
;   日本語がすべてトーフになる（タイトルバーだけ読めるのは、そこを描くのが
;   Inno ではなく OS＝ja-JP のキャプションフォントが Yu Gothic UI 直だから）。
;
;   🔑 アプリ本体は同じ Sandbox で正常に出る（Tk が OS の UI フォント＝
;   Yu Gothic UI を直に使い、フォントリンクに依存しないため）。ここで
;   Yu Gothic UI を選ぶのは、その本体と同じフォントに揃える意味もある。
;
;   フォントが無い環境（日本語補助フォントを入れていない英語 Windows 等）では
;   Inno が 9pt Segoe UI へ差し戻す＝指定しなかった場合と同じ挙動に落ちるだけで、
;   新たに壊れるものは無い。
;   ⛔ B-193: **フォント指定は 1 つではない**。Inno Setup 7 の Default.isl が
;   挙げている font 名の指定は 2 つあり、描く場所が違う:
;     - DialogFontName  (既定  9pt Segoe UI) … 通常のダイアログの文字
;     - WelcomeFontName (既定 12pt Segoe UI) … Welcome ページと**完了ページ**の見出し
;   WizardStyle=modern では Welcome ページは既定で出ないが、**完了ページは
;   DisableFinishedPage を切っていない限り必ず出る**＝DialogFontName だけを
;   指定しても、最後の 1 画面の見出しがトーフのまま残る（B-192 の取り残し。
;   独立レビュー round84 が指摘）。⇒ **2 つとも指定して初めてこのクラスが閉じる。**
;   （*FontSize / *BaseScale* は寸法であってグリフの有無には効かないので対象外。）
japanese.DialogFontName=Yu Gothic UI
japanese.WelcomeFontName=Yu Gothic UI

[CustomMessages]
; I-139: 削除対象を 4 種に割り、それぞれチェックボックスで選ばせる。
; ⚠️ 見出し（UninstData*）は下の [Code] が **CustomMessages のキー名** として
; 文字列で参照する（DataNames に入る）＝キーを改名したら [Code] も直すこと。
japanese.UninstDataTitle=RadioSim Pro のデータの削除
japanese.UninstDataIntro=下のデータはアンインストールしても残ります。この PC から消したいものだけチェックしてください。
japanese.UninstDataSettings=UI 設定・最後に使用した入力値
japanese.UninstDataCache=DEM タイルのディスクキャッシュ・ログ
japanese.UninstDataResults=保存パッケージの出力先
japanese.UninstDataLang=追加した表示言語ファイル
japanese.UninstDataHint=チェックを外したものはそのまま残り、次回インストール時に引き継がれます。
; B-188: 管理者へ昇格したアンインストールでは、この案内だけ出して 1 件も消さない。
japanese.UninstDataElevated=このアンインストールは管理者として実行されているため、RadioSim Pro を使っていた利用者のデータの場所を特定できません（この PC から誤って別の人のデータを消さないよう、ここでは何も削除しません）。%n%n残るデータを消したい場合は、そのユーザーでサインインしてから次の場所を手動で削除してください:%n%n  %1
; B-189: 消せなかったものを黙って成功にしない。
japanese.UninstDataFailed=次のデータは削除できませんでした（ファイルが開かれている・アクセス権が無い等）。手動で削除してください:%n%n%1
english.UninstDataElevated=This uninstall is running as an administrator, so it cannot tell where the data of the person who used RadioSim Pro is stored. Nothing has been deleted, so that another user's data is not removed by mistake.%n%nTo remove the remaining data, sign in as that user and delete these locations by hand:%n%n  %1
english.UninstDataFailed=The following data could not be deleted (a file is open, permissions are missing, and so on). Please delete it by hand:%n%n%1
english.UninstDataTitle=Remove RadioSim Pro data
english.UninstDataIntro=The data below is kept when you uninstall. Tick only what you want removed from this PC.
english.UninstDataSettings=UI settings and last used input values
english.UninstDataCache=DEM tile disk cache and logs
english.UninstDataResults=Saved result packages
english.UninstDataLang=Display language files you added
english.UninstDataHint=Anything left unticked stays on this PC and is picked up again by the next install.

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

[UninstallDelete]
; install_lang.txt は [Files] ではなく [Code] が作るので uninsdeletefile が使えない。
; アンインストールで消し残さないよう、ここで明示的に消す。
Type: files; Name: "{app}\install_lang.txt"

[Code]
{ 3.2・I-127: ウィザードで選ばれた言語をアプリへ引き継ぐ。
  値は [Languages] の Name（japanese / english）をそのまま書く＝アプリ側
  （core/config.py の _INSTALLER_LANG_CODES）が言語コードへ写す。

  ⛔ %APPDATA%\RadioSim\radiosim_conf.json を直接書かない:
     PrivilegesRequiredOverridesAllowed=dialog で管理者へ昇格され得るため、
     書き込む %APPDATA% が別ユーザー（管理者）のものになり得る。加えて
     上書きインストールで既存の設定を壊す。⇒ **アプリが読むだけの種**を置く。

  アプリ側はこれを「設定ファイルがまだ無いとき」しか見ないので、上書き
  インストールで言語を選び直しても既存利用者の選択は変わらない。 }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\install_lang.txt'), ActiveLanguage(), False);
end;

{ I-137 → I-139: アンインストールで設定/キャッシュ/結果/追加言語を消せるようにする。
  上の UninstallDelete セクションは固定パスの無条件削除しか書けず確認を
  挟めないので、usPostUninstall で自前のダイアログを出してから消す。

  I-137 の初版は「全部まとめて消すか / 何も消さないか」の 2 択（MsgBox）だった。
  4 種は消したい理由がまるで違う（キャッシュは再取得できるが保存パッケージは
  作り直せない）ので、種別ごとのチェックボックスに割った＝ I-139。

  ⛔ 既定は全部オフ（＝残す）。サイレントアンインストールでは**ダイアログを
     出さずに何も消さない**（モーダルを出すと無人実行が固まるので、既定ボタン
     任せにせず UninstallSilent で明示的に抜ける）。 }

var
  { 削除候補。DataNames[i] は [CustomMessages] のキー名、DataPaths[i] は実体
    （フォルダでもファイルでもよい）。CollectRemovableData が**実在するものだけ**
    を積むので、この 2 本の長さがそのまま画面に出る行数になる。 }
  DataNames: TArrayOfString;
  DataPaths: TArrayOfString;

procedure AddRemovableData(const Name, Path: string);
var
  N: Integer;
begin
  if not (FileExists(Path) or DirExists(Path)) then
    Exit;
  N := GetArrayLength(DataNames);
  SetArrayLength(DataNames, N + 1);
  SetArrayLength(DataPaths, N + 1);
  DataNames[N] := Name;
  DataPaths[N] := Path;
end;

{ 4 種の実体は core/config.py の保存先解決と 1 対 1 で対応する:
    設定       %APPDATA%\RadioSim\radiosim_conf.json  … _config_base_dir()
    キャッシュ %LOCALAPPDATA%\RadioSim\               … cache_log_base_dir()
                 （terrain_cache\ と radiosim*.log しか置かないので丸ごと消す）
    保存結果   ドキュメント\RadioSim\                  … _results_dir()
    追加言語   %APPDATA%\RadioSim\lang\               … _user_lang_dir()
  ⚠️ 設定と追加言語は**同じ親フォルダの中**なので、親ごと消してはいけない
     （片方だけ選んだときにもう片方まで消える）。
  ⛔ このコメントに Inno の定数を波括弧つきで書かない: 波括弧はコメントの
     終端そのものなので、途中で閉じて残りが構文エラーになる（1710b9f と同型）。 }
procedure CollectRemovableData;
var
  ConfigBase: string;
begin
  SetArrayLength(DataNames, 0);
  SetArrayLength(DataPaths, 0);
  ConfigBase := ExpandConstant('{userappdata}\RadioSim');
  AddRemovableData('UninstDataSettings', ConfigBase + '\radiosim_conf.json');
  AddRemovableData('UninstDataCache',    ExpandConstant('{localappdata}\RadioSim'));
  AddRemovableData('UninstDataResults',  ExpandConstant('{userdocs}\RadioSim'));
  AddRemovableData('UninstDataLang',     ConfigBase + '\lang');
end;

{ 折り返すラベルの中身と幅を、この順番でしか入れられないようにする（B-200）。
  ⛔ TLabel は AutoSize を True にした**その瞬間**に今の Caption で自分の幅を測り直す。
     Caption がまだ空だと「空白 1 個ぶん」に潰れ（実測 20px＝1 文字ぶん）、あとから
     入れた日本語はその 20px の中で折り返される＝1 行 1 文字の縦書きになる。
     実測（Inno 7・DPI 125%・ClientWidth 564）:
       Width→WordWrap→AutoSize→Caption の順  … W=20  H=450
       WordWrap→Caption→Width→AutoSize の順  … W=472 H=15
  ⚠️ 英語だと目立たない: DT_WORDBREAK は単語の途中で折らないので、幅が潰れていても
     1 行 1 単語になるだけで「少し縦長」に見える。日本語は文字単位で折れるので
     全部が縦一列になる＝**同じ欠陥が言語で違う顔をする**。
  🔑 幅は AutoSize が最長行まで縮める（＝返ってくる Width は指定値以下）。
     以後この窓に折り返すラベルを足すときは、直に組まずここを通すこと。 }
procedure SetWrappedCaption(L: TLabel; const Text: string; const W: Integer);
begin
  L.WordWrap := True;
  L.AutoSize := False;
  L.Caption  := Text;
  L.Width    := W;
  L.AutoSize := True;
end;

{ 選択ダイアログ。戻り値 True＝OK が押された。Chosen[i] は 1 なら削除する。
  アンインストール側にはウィザードが無いので CreateCustomForm で自前に建てる。 }
function AskRemovableData(var Chosen: TArrayOfInteger): Boolean;
var
  Form: TSetupForm;
  Intro, Hint, PathLabel: TLabel;
  { 上限は CollectRemovableData が積む種類の数。増やすときは**両方**直すこと
    （足りないと実行時に添字が飛ぶ＝コンパイルでは分からない）。
    ずれの検出は tests/test_config.py::test_choice_count_fits_the_checkbox_array。 }
  Boxes: array[0..3] of TNewCheckBox;
  OkButton, CancelButton: TNewButton;
  I, Count, Y, ButtonWidth: Integer;
begin
  Count := GetArrayLength(DataPaths);
  SetArrayLength(Chosen, Count);
  for I := 0 to Count - 1 do
    Chosen[I] := 0;

  { ⚠️ CreateCustomForm は**引数を取る**（古い資料にある引数なしの版で書くと
    「Invalid number of parameters」でコンパイルが止まる＝実際に踏んだ）。
    引数は 幅・高さ・横リサイズ可否・縦リサイズ可否。高さは行数で決まるので
    ここでは仮置きし、最後に ClientHeight で確定させる。
    ※ この 4 引数版は手元の Inno 6 / 7 の両方でコンパイルを確認済み。 }
  Form := CreateCustomForm(ScaleX(470), ScaleY(320), False, False);
  try
    Form.Caption := CustomMessage('UninstDataTitle');

    Intro := TLabel.Create(Form);
    Intro.Parent   := Form;
    Intro.Left     := ScaleX(16);
    Intro.Top      := ScaleY(16);
    SetWrappedCaption(Intro, CustomMessage('UninstDataIntro'),
                      Form.ClientWidth - ScaleX(32));

    Y := Intro.Top + Intro.Height + ScaleY(14);
    for I := 0 to Count - 1 do
    begin
      Boxes[I] := TNewCheckBox.Create(Form);
      Boxes[I].Parent  := Form;
      Boxes[I].Left    := ScaleX(20);
      Boxes[I].Top     := Y;
      Boxes[I].Width   := Form.ClientWidth - ScaleX(36);
      Boxes[I].Height  := ScaleY(17);
      Boxes[I].Checked := False;
      Boxes[I].Caption := CustomMessage(DataNames[I]);

      { 実体のパスを添える。I-137 の起票にあったとおり、利用者には
        %APPDATA% などの保存先が分かりにくい＝何が消えるのかを名前だけで
        判断させない。 }
      PathLabel := TLabel.Create(Form);
      PathLabel.Parent     := Form;
      PathLabel.Left       := ScaleX(38);
      PathLabel.Top        := Y + ScaleY(19);
      PathLabel.Width      := Form.ClientWidth - ScaleX(54);
      PathLabel.Height     := ScaleY(14);
      PathLabel.AutoSize   := False;
      PathLabel.Font.Color := clGray;
      PathLabel.Caption    := DataPaths[I];

      Y := Y + ScaleY(42);
    end;

    Hint := TLabel.Create(Form);
    Hint.Parent   := Form;
    Hint.Left     := ScaleX(16);
    Hint.Top      := Y;
    SetWrappedCaption(Hint, CustomMessage('UninstDataHint'),
                      Form.ClientWidth - ScaleX(32));

    Y := Hint.Top + Hint.Height + ScaleY(16);

    OkButton := TNewButton.Create(Form);
    OkButton.Parent      := Form;
    OkButton.Height      := ScaleY(23);
    OkButton.Top         := Y;
    OkButton.Caption     := SetupMessage(msgButtonOK);
    OkButton.ModalResult := mrOk;
    OkButton.Default     := True;

    CancelButton := TNewButton.Create(Form);
    CancelButton.Parent      := Form;
    CancelButton.Height      := ScaleY(23);
    CancelButton.Top         := Y;
    CancelButton.Caption     := SetupMessage(msgButtonCancel);
    CancelButton.ModalResult := mrCancel;
    CancelButton.Cancel      := True;

    { 訳の長さでボタン幅が足りなくならないよう、両方の見出しから幅を出す
      （日本語の「キャンセル」と英語の "Cancel" で必要幅が違う）。 }
    ButtonWidth := Form.CalculateButtonWidth([OkButton.Caption, CancelButton.Caption]);
    OkButton.Width     := ButtonWidth;
    CancelButton.Width := ButtonWidth;
    CancelButton.Left  := Form.ClientWidth - ScaleX(16) - ButtonWidth;
    OkButton.Left      := CancelButton.Left - ScaleX(8) - ButtonWidth;

    Form.ClientHeight := Y + OkButton.Height + ScaleY(16);

    Result := Form.ShowModal = mrOk;
    if Result then
      for I := 0 to Count - 1 do
        if Boxes[I].Checked then
          Chosen[I] := 1;
  finally
    Form.Free;
  end;
end;

{ 消せなかったものを黙って成功にしない（B-189）。Inno の削除 API は例外を投げず
  **戻り値で失敗を返す**ので、見ないと失敗が消える。⇒ 実際に消えたかを DirExists /
  FileExists で確かめ直し、残ったパスを積んで最後に見せる。⚠️ 戻り値ではなく実体を
  見るのは、DelTree が「一部だけ消せた」場合も True を返し得るため。 }
procedure DeleteSelectedData(const Chosen: TArrayOfInteger);
var
  I: Integer;
  Path, Failed: string;
begin
  Failed := '';
  for I := 0 to GetArrayLength(DataPaths) - 1 do
  begin
    if Chosen[I] <> 0 then
    begin
      Path := DataPaths[I];
      if DirExists(Path) then
        DelTree(Path, True, True, True)
      else
        DeleteFile(Path);
      if DirExists(Path) or FileExists(Path) then
        Failed := Failed + '  ' + Path + #13#10;
    end;
  end;
  { 設定と追加言語を両方消すと %APPDATA%\RadioSim が空殻で残る。RemoveDir は
    空のときしか成功しないので、片方だけ消した場合は何も起きない。 }
  RemoveDir(ExpandConstant('{userappdata}\RadioSim'));
  if Failed <> '' then
    MsgBox(FmtMessage(CustomMessage('UninstDataFailed'), [Failed]),
           mbError, MB_OK);
end;

{ 昇格時に案内する場所（B-188）。**実体のパスへ展開しない**＝展開すると管理者の
  プロファイルが出てしまい、案内としてまさに間違ったものを見せることになる。
  環境変数の書き方のまま出して、読む人が自分のアカウントで開けるようにする。
  ⛔ ここも波括弧を書かない: 定数の展開に見えるうえ、コメントなら途中で閉じる。 }
function ManualCleanupPaths: string;
begin
  Result := '%APPDATA%\RadioSim' + #13#10
          + '  %LOCALAPPDATA%\RadioSim' + #13#10
          + '  %USERPROFILE%\Documents\RadioSim';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Chosen: TArrayOfInteger;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  if UninstallSilent then
    Exit;
  { ⛔ 昇格したアンインストールでは 1 件も消さない（B-188）。プロファイル系の定数は
    **アンインストーラを実行している側**を指すので、標準ユーザーが管理者の資格情報を
    入れて消した場合、見ているのはアプリを使っていた人ではなく管理者のプロファイル。
    ⇒ 候補が 1 件も出ない（「もう残っていない」と読める）か、**その管理者自身の
    無関係な RadioSim のデータを消す**。どちらへ外れても取り返しがつかないので、
    場所だけ案内して手を出さない。🔑 同じ危険はインストール側で既に認識済み＝
    上の CurStepChanged が設定を直接書かず種を置いているのがそれ。 }
  { ⛔ ここは IsAdmin（実行中のプロセスが管理者権限を持つか）であって
    IsAdminInstallMode（インストールが全ユーザー向けモードだったか）ではない。
    取り違えると、ユーザー向けに入れたアンインストーラを「管理者として実行」した
    ときに条件が偽のまま素通りし、**防ごうとしている誤削除がそのまま起きる**
    （Codex round81 の指摘・2026-09-07）。 }
  if IsAdmin then
  begin
    { ⛔ 配列リテラルの `[` を**行頭に置かない**（前の空白は関係ない）＝ISCC は
      行頭の角括弧を**セクションタグ**として読むので "Invalid section tag" で落ちる。
      1710b9f と同じクラス（あれはコメントの中、これはコードの中）。 }
    MsgBox(FmtMessage(CustomMessage('UninstDataElevated'), [ManualCleanupPaths]),
           mbInformation, MB_OK);
    Exit;
  end;
  CollectRemovableData;
  if GetArrayLength(DataPaths) = 0 then
    Exit;
  if AskRemovableData(Chosen) then
    DeleteSelectedData(Chosen);
end;
