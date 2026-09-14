import os
import subprocess
import sys
from pathlib import Path

def build():
    """
    Builds the VoxOS desktop application using PyInstaller.
    """
    print("Starting VoxOS Build Process...")
    
    # Path to the main entry point
    entry_point = Path("voxos") / "ui" / "app.py"
    
    if not entry_point.exists():
        print(f"Error: Could not find entry point at {entry_point}")
        sys.exit(1)
        
    # We need a small wrapper script for PyInstaller since app.py expects to be run with a session_id
    # We will generate a quick launcher
    launcher_path = Path("launcher.py")
    with open(launcher_path, "w") as f:
        f.write("""
import uuid
import sys
from voxos.ui.app import run_ui
from voxos.config import get_settings

if __name__ == '__main__':
    # Initialize settings and run UI
    settings = get_settings()
    session_id = str(uuid.uuid4())
    run_ui(session_id)
""")
    
    # Write the spec file content
    spec_path = Path("VoxOS.spec")
    with open(spec_path, "w") as f:
        f.write(f"""# -*- mode: python ; coding: utf-8 -*-
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
    hooksconfig={{}},
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
""")
    
    # PyInstaller arguments
    args = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        str(spec_path)
    ]
    
    print(f"Running: {' '.join(args)}")
    try:
        subprocess.run(args, check=True)
        print("Build completed successfully! Check the 'dist' folder.")
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")
    finally:
        # Cleanup launcher
        if launcher_path.exists():
            launcher_path.unlink()

if __name__ == "__main__":
    build()
