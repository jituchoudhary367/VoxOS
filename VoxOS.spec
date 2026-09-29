# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_data_files

sys.setrecursionlimit(sys.getrecursionlimit() * 5)

# Collect data files for openwakeword models
oww_datas = collect_data_files('openwakeword')

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('voxos/ui/assets', 'voxos/ui/assets')] + oww_datas,
    hiddenimports=[
        'pipecat', 'qasync', 'keyboard', 'sqlalchemy', 'pydantic', 'voxos', 
        'uvicorn', 'fastapi', 'openwakeword', 'asyncpg', 'passlib', 'jose'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'torch', 'transformers', 'xformers', 'accelerate',
        'django', 'pandas', 'scipy', 'spacy', 'altair', 'duckdb', 
        'playwright', 'shapely', 'matplotlib', 'Crypto', 'IPython',
        'notebook', 'cv2', 'pytest', 'jupyter', 'datasets',
        'google', 'pdfminer', 'pypdfium2'
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoxOS',
    icon='voxos/ui/assets/vox.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VoxOS',
)
