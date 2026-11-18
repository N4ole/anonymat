# -*- mode: python ; coding: utf-8 -*-
# Spécification PyInstaller unique, utilisée par build.bat ET la CI (mêmes options partout).
import os
import sys

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

sys.path.insert(0, os.path.abspath("."))
from version import __version__  # noqa: E402

heif_datas, heif_bins, heif_hidden = collect_all("pillow_heif")

# Ressource « Version » Windows (clic droit > Propriétés > Détails sur le .exe)
nums = tuple(int(x) for x in (__version__.split("+")[0].split("-")[0].split(".") + ["0"] * 4)[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("040C04B0", [
            StringStruct("CompanyName", "N4ole"),
            StringStruct("FileDescription", "Anonymiseur de visages"),
            StringStruct("FileVersion", __version__),
            StringStruct("InternalName", "AnonymiseurVisages"),
            StringStruct("OriginalFilename", "AnonymiseurVisages.exe"),
            StringStruct("ProductName", "Anonymat"),
            StringStruct("ProductVersion", __version__),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
    ],
)

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
    version=version_info,
    console=False,
    upx=False,
)
