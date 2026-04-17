# -*- mode: python ; coding: utf-8 -*-
# Build with: pyinstaller iRacingEnduro.spec

import os

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[os.getcwd()],
    binaries=[],
    datas=[
        ('assets/*', 'assets'),    # include any icon files etc
    ],
    hiddenimports=[
        'irsdk',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'requests',
        'packaging',
    ],
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
    name='iRacingEnduro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',  # optional: add your .ico file to assets/
)
