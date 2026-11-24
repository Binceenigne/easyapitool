# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, copy_metadata
from pathlib import Path

project_root = Path(SPECPATH)
datas = [
    (str(project_root / 'frontend'), 'frontend'),
    (str(project_root / 'resources'), 'resources'),
    (str(project_root / 'CHANGELOG.md'), '.'),
    (str(project_root / 'mirra_agents' / 'model_limits.json'), 'mirra_agents'),
]
binaries = []
hiddenimports = ['pystray._win32', 'winotify', 'resvg_py']
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# The SDK discovers optional providers and reads its distribution metadata at
# runtime; retain those modules when building the desktop executable.
tmp_ret = collect_all('agents')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
datas += copy_metadata('openai-agents')
datas += copy_metadata('mcp')


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
    icon=[str(project_root / 'resources' / 'api_tools_icon.ico')],
)
