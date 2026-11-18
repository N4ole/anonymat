# Spécification PyInstaller unique, utilisée par build.bat ET la CI (mêmes options partout).
import os
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, os.path.abspath("."))
from version import __version__  # noqa: E402

heif_datas, heif_bins, heif_hidden = collect_all("pi_heif")  # lecture HEIC seule (LGPL), sans x265

# Ressource « Version » Windows (clic droit > Propriétés > Détails sur le .exe).
# Le module n'existe que sous Windows : ailleurs, la ressource est simplement omise.
try:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )
except ImportError:
    version_info = None
else:
    nums = tuple(int(x) for x in (__version__.split("+")[0].split("-")[0].split(".") + ["0"] * 4)[:4])
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040C04B0", [
                StringStruct("CompanyName", "N4ole"),
                StringStruct("FileDescription", "Anonymiseur de visages"),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "AnonymiseurVisages"),
                StringStruct("LegalCopyright", "Licence MIT – voir LICENSE et THIRD_PARTY_NOTICES"),
                StringStruct("OriginalFilename", "AnonymiseurVisages.exe"),
                StringStruct("ProductName", "Anonymat"),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
        ],
    )

a = Analysis(
    ["app.py"],
    datas=[("icon.ico", "."), ("haar", "haar"), ("models", "models"),
           ("LICENSE", "."), ("THIRD_PARTY_NOTICES.md", ".")] + heif_datas,
    binaries=heif_bins,
    hiddenimports=["PIL._tkinter_finder"] + heif_hidden,
    excludes=["matplotlib", "scipy", "pandas", "pytest", "pillow_heif"],
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
