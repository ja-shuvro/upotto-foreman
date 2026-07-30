# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — build single-folder or onefile Windows app.

Usage (from upotto-foreman/):
  .\.venv\Scripts\pip install pyinstaller
  .\.venv\Scripts\pyinstaller upotto_foreman.spec
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ("templates", "templates"),
    ("static", "static"),
    ("assets", "assets"),
    (".env.example", "."),
]

hiddenimports = [
    "customtkinter",
    "flask",
    "dotenv",
    "crewai",
    "crewai_tools",
    "langchain_anthropic",
    "langchain_community",
    "langchain_community.tools",
    "requests",
    "tenacity",
    "pydantic",
]

# Pull customtkinter assets
tmp_ret = collect_all("customtkinter")
datas += tmp_ret[0]
hiddenimports += tmp_ret[1]
hiddenimports += collect_submodules("customtkinter")

a = Analysis(
    ["app_main.py"],
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
    name="UpottoForeman",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/upotto-foreman.ico",
)
