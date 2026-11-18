# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller unique, utilisée par build.bat ET la CI (mêmes options partout).
from PyInstaller.utils.hooks import collect_all

heif_datas, heif_bins, heif_hidden = collect_all("pillow_heif")

a = Analysis(
    ["app.py"],
    datas=[("icon.ico", "."), ("haar", "haar"), ("models", "models")] + heif_datas,
    binaries=heif_bins,
    hiddenimports=["PIL._tkinter_finder"] + heif_hidden,
    excludes=["matplotlib", "scipy", "pandas", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="AnonymiseurVisages",
    icon="icon.ico",
    console=False,
    upx=False,
)
