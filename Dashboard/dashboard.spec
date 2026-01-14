# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for RB3E Dashboard

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all required hidden imports
hiddenimports = [
    'googleapiclient',
    'googleapiclient.discovery',
    'googleapiclient.errors',
    'google.auth.transport.requests',
    'google.oauth2.credentials',
    'yt_dlp',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageTk',
    'pypresence',
    'screeninfo',
    'requests',
    'littlefs',
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'tkinter.scrolledtext',
    'tkinter.filedialog',
    'json',
    'sqlite3',
    'socket',
    'struct',
    'threading',
    'hashlib',
    'webbrowser',
    'ctypes',
    'collections',
    'datetime',
    're',
    'io',
]

# Collect submodules for packages that need them
hiddenimports += collect_submodules('googleapiclient')
hiddenimports += collect_submodules('yt_dlp')
hiddenimports += collect_submodules('PIL')
hiddenimports += collect_submodules('littlefs')

# Collect data files needed by packages
datas = []
datas += collect_data_files('googleapiclient')
datas += collect_data_files('yt_dlp')

a = Analysis(
    ['dashboard.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RB3E-Dashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to False to hide console window (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon path here if you have one: icon='icon.ico'
)
