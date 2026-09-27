#define MyAppName "JmShelf"
#define MyAppVersion "1.3.4"
#define MyAppNumericVersion "1.3.4.0"
#define MyAppPublisher "UsotsukiKaze"
#define MyAppExeName "JmShelf.exe"
#define MyAppSourceDir GetEnv("JMSHELF_INSTALLER_SOURCE_DIR")
#if MyAppSourceDir == ""
  #define MyAppSourceDir "..\dist\JmShelf"
#endif

[Setup]
AppId={{B5683878-DB9F-4B01-A346-1A5D0D889CBC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=JmShelf-Setup-{#MyAppVersion}-x64
SetupIconFile=JmShelf.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
VersionInfoVersion={#MyAppNumericVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppNumericVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "JmShelf 本地书架"
Name: "{userprograms}\{#MyAppName}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "JmShelf 本地书架"
Name: "{userprograms}\{#MyAppName}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
