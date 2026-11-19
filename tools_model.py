"""Télécharge les composants embarqués et vérifie leur empreinte SHA-256 (build.bat / la CI).

  python tools_model.py           -> tout (modèle IA YuNet + ffmpeg)
  python tools_model.py yunet     -> modèle IA YuNet seulement
  python tools_model.py ffmpeg    -> ffmpeg seulement

ffmpeg.exe : build MINIMALE (son seulement, LGPL-2.1) compilée par la CI de ce dépôt depuis
les sources officielles de FFmpeg (.github/workflows/ffmpeg.yml), publiée dans une
pré-release figée. Code de sortie 1 si un composant demandé n'a pas pu être obtenu.
"""
import hashlib
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from core import YUNET_FILE, YUNET_SHA256  # noqa: E402
from version import REPO_URL  # noqa: E402

YUNET_URLS = [
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + YUNET_FILE,
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/" + YUNET_FILE,
]

# Changer de build ffmpeg : augmenter REVISION dans build_ffmpeg.sh, attendre la pré-release
# publiée par la CI, puis reporter ici son étiquette et l'empreinte de ffmpeg.exe.
FFMPEG_TAG = "ffmpeg-9.0.2-1"
FFMPEG_SHA256 = ""
FFMPEG_BASE = f"{REPO_URL}/releases/download/{FFMPEG_TAG}/"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(urls: list[str], expected: str, dst: str, name: str) -> bool:
    """Télécharge `dst` depuis la première URL qui donne le bon fichier (empreinte vérifiée)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(dst) and sha256(dst) == expected:
        print(f"{name} déjà présent et vérifié.")
        return True
    for url in urls:
        try:
            print("Téléchargement :", url)
            data = urllib.request.urlopen(url, timeout=120).read()
            if hashlib.sha256(data).hexdigest() != expected:
                print("  ! empreinte incorrecte, fichier rejeté")
                continue
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dst)
            print("  OK, empreinte vérifiée.")
            return True
        except Exception as exc:
            print("  ! échec :", exc)
    print(f"ERREUR : impossible d'obtenir {name} valide.")
    return False


def get_yunet() -> bool:
    return fetch(YUNET_URLS, YUNET_SHA256, os.path.join(ROOT, "models", YUNET_FILE), "Modèle YuNet")


def get_ffmpeg() -> bool:
    if len(FFMPEG_SHA256) != 64:
        print("ERREUR : empreinte de ffmpeg non renseignée dans tools_model.py.")
        return False
    folder = os.path.join(ROOT, "ffmpeg")
    exe = os.path.join(folder, "ffmpeg.exe")
    if not fetch([FFMPEG_BASE + "ffmpeg.exe"], FFMPEG_SHA256, exe, "ffmpeg (son des vidéos)"):
        return False
    # Empreinte attendue, relue par l'application au lancement (audio.Ffmpeg).
    with open(exe + ".sha256", "w", encoding="ascii") as f:
        f.write(FFMPEG_SHA256 + "\n")
    lic = os.path.join(folder, "LICENSE-ffmpeg.txt")
    if not os.path.isfile(lic):
        try:
            data = urllib.request.urlopen(FFMPEG_BASE + "LICENSE-ffmpeg.txt", timeout=60).read()
            with open(lic, "wb") as f:
                f.write(data)
        except Exception as exc:
            print("  ! licence de ffmpeg non téléchargée :", exc)
            return False
    return True


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "tout"
    ok = True
    if what in ("tout", "yunet"):
        ok &= get_yunet()
    if what in ("tout", "ffmpeg"):
        ok &= get_ffmpeg()
    sys.exit(0 if ok else 1)
