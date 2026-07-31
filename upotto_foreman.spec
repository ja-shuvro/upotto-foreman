# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — onefile Windows app.

Usage (from upotto-foreman/):
  .venv\\Scripts\\pip install pyinstaller
  .venv\\Scripts\\pyinstaller upotto_foreman.spec
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ("templates", "templates"),
    ("static", "static"),
    ("web", "web"),
    ("assets", "assets"),
    (".env.example", "."),
]
binaries = []

hiddenimports = [
    "customtkinter",
    "webview",
    "flask",
    "dotenv",
    "crewai",
    "crewai_tools",
    "crewai.events",
    "crewai.events.event_bus",
    "live_events",
    "langchain_anthropic",
    "langchain_community",
    "langchain_community.tools",
    "requests",
    "tenacity",
    "pydantic",
]

# collect_all → (datas, binaries, hiddenimports)
ctk_datas, ctk_bins, ctk_hidden = collect_all("customtkinter")
datas += ctk_datas
binaries += ctk_bins
hiddenimports += list(ctk_hidden)
hiddenimports += collect_submodules("customtkinter")

try:
    wv_datas, wv_bins, wv_hidden = collect_all("webview")
    datas += wv_datas
    binaries += wv_bins
    hiddenimports += list(wv_hidden)
    # Skip android/ios submodules (not on Windows)
    hiddenimports += [
        m
        for m in collect_submodules("webview")
        if "android" not in m and "ios" not in m
    ]
except Exception:
    pass

# Deduplicate while preserving order
_seen = set()
hiddenimports = [h for h in hiddenimports if isinstance(h, str) and not (h in _seen or _seen.add(h))]

a = Analysis(
    ["app_main.py"],
    pathex=[],
    binaries=binaries,
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/upotto-foreman.ico",
)
