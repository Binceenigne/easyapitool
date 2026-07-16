# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

project_root = Path(SPECPATH)
datas = [
    (str(project_root / 'API_TOOLS_响应式悬浮窗完整版_v3.html'), '.'),
    (str(project_root / 'initialize.html'), '.'),
    (str(project_root / 'CHANGELOG.md'), '.'),
    (str(project_root / 'assets' / 'app.css'), 'assets'),
    (str(project_root / 'assets' / 'title_logo.png'), 'assets'),
    (str(project_root / 'assets' / 'api_tools_icon.ico'), 'assets'),
    (str(project_root / 'assets' / 'api_tools_icon.png'), 'assets'),
    (str(project_root / 'assets' / 'icons'), 'assets/icons'),
]
binaries = []
hiddenimports = ['pystray._win32', 'winotify']
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    [str(project_root / 'app.py')],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='API_TOOLS',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(project_root / 'assets' / 'api_tools_icon.ico')],
)
