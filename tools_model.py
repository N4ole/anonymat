"""Télécharge le modèle YuNet et vérifie son empreinte SHA-256 (utilisé par build.bat / la CI)."""
import hashlib
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import YUNET_FILE, YUNET_SHA256  # noqa: E402

URLS = [
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + YUNET_FILE,
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/" + YUNET_FILE,
]
dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", YUNET_FILE)
os.makedirs(os.path.dirname(dst), exist_ok=True)


def ok(path):
    return os.path.isfile(path) and hashlib.sha256(open(path, "rb").read()).hexdigest() == YUNET_SHA256


if ok(dst):
    print("Modèle YuNet déjà présent et vérifié.")
    sys.exit(0)
for url in URLS:
    try:
        print("Téléchargement :", url)
        data = urllib.request.urlopen(url, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != YUNET_SHA256:
            print("  ! empreinte incorrecte, fichier rejeté")
            continue
        with open(dst, "wb") as f:
            f.write(data)
        print("  OK, empreinte vérifiée.")
        sys.exit(0)
    except Exception as exc:
        print("  ! échec :", exc)
print("ERREUR : impossible d'obtenir un modèle YuNet valide.")
sys.exit(1)
