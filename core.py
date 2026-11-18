"""Cœur du programme : détection des visages et anonymisation irréversible.

Principes de sécurité :
  * On ne « floute » jamais directement l'original : la zone est d'abord réduite
    à une poignée de pixels (l'information du visage est DÉTRUITE), puis on
    ajoute du bruit aléatoire. Un flou gaussien simple peut être en partie
    inversé (déconvolution / IA), une zone écrasée à 6x6 pixels non.
  * Le mode « caviardage » remplit la zone d'une couleur unie : 0 information.
  * L'image est ré-encodée à partir des pixels : aucune métadonnée EXIF/XMP/IPTC
    n'est recopiée (GPS, appareil, et surtout la MINIATURE EXIF qui contient
    souvent l'image originale non floutée).
  * Les zones sont élargies (cheveux, oreilles, menton) pour ne rien laisser.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

MODES = {
    "caviardage": "Caviardage (noir plein) - le plus sûr",
    "pixel": "Pixelisation destructive",
    "flou": "Flou destructif",
}


def resource_path(rel: str) -> str:
    """Chemin d'une ressource, que l'on soit en .py ou dans le .exe PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    score: float = 1.0
    manual: bool = False

    def expanded(self, margin: float, img_w: int, img_h: int) -> "Box":
        """Agrandit la zone de `margin` (0.3 = +30 %) en restant dans l'image."""
        dx, dy_top, dy_bot = self.w * margin, self.h * margin * 1.3, self.h * margin
        x0 = max(0, int(self.x - dx))
        y0 = max(0, int(self.y - dy_top))  # plus de marge en haut (cheveux)
        x1 = min(img_w, int(self.x + self.w + dx))
        y1 = min(img_h, int(self.y + self.h + dy_bot))
        return Box(x0, y0, x1 - x0, y1 - y0, self.score, self.manual)


# --------------------------------------------------------------------------- #
#  Chargement / sauvegarde
# --------------------------------------------------------------------------- #
def load_image(path: str) -> np.ndarray:
    """Charge une image en RGB en appliquant l'orientation EXIF (photos de tel)."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return np.array(im.convert("RGB"))


def save_clean(rgb: np.ndarray, path: str) -> None:
    """Enregistre SANS aucune métadonnée (nouvelle image construite depuis les pixels)."""
    clean = Image.fromarray(rgb, "RGB")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        clean.save(path, "JPEG", quality=92, optimize=True)
    elif ext == ".webp":
        clean.save(path, "WEBP", quality=92)
    else:
        clean.save(path)


def output_path(src: str, out_dir: str | None = None) -> str:
    folder = out_dir or os.path.join(os.path.dirname(src), "anonymise")
    os.makedirs(folder, exist_ok=True)
    name, ext = os.path.splitext(os.path.basename(src))
    if ext.lower() in (".tif", ".tiff", ".bmp"):
        ext = ".png"
    return os.path.join(folder, f"{name}_anonyme{ext}")


# --------------------------------------------------------------------------- #
#  Détection
# --------------------------------------------------------------------------- #
class FaceDetector:
    """YuNet (réseau de neurones, précis) si le modèle est présent, sinon Haar."""

    YUNET_FILE = "face_detection_yunet_2023mar.onnx"

    def __init__(self) -> None:
        self.backend = "haar"
        model = resource_path(os.path.join("models", self.YUNET_FILE))
        self._yunet = None
        if os.path.isfile(model) and os.path.getsize(model) > 100_000:
            try:
                self._yunet = cv2.FaceDetectorYN.create(model, "", (320, 320), 0.5, 0.3, 5000)
                self.backend = "yunet"
            except cv2.error:
                self._yunet = None
        haar_dir = resource_path("haar")  # copie embarquée dans le .exe
        if not os.path.isdir(haar_dir) and getattr(cv2, "data", None):
            haar_dir = cv2.data.haarcascades
        self._frontal = cv2.CascadeClassifier(os.path.join(haar_dir, "haarcascade_frontalface_default.xml"))
        self._frontal2 = cv2.CascadeClassifier(os.path.join(haar_dir, "haarcascade_frontalface_alt2.xml"))
        self._profile = cv2.CascadeClassifier(os.path.join(haar_dir, "haarcascade_profileface.xml"))

    # -- YuNet, multi-échelle pour attraper petits ET grands visages ----------
    def _yunet_detect(self, rgb: np.ndarray, threshold: float) -> list[Box]:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        self._yunet.setScoreThreshold(threshold)
        boxes: list[Box] = []
        long_side = max(h, w)
        targets = sorted({min(long_side, t) for t in (640, 1280, 2560)})
        for t in targets:
            s = t / long_side
            img = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else bgr
            self._yunet.setInputSize((img.shape[1], img.shape[0]))
            _, faces = self._yunet.detect(img)
            if faces is None:
                continue
            for f in faces:
                x, y, fw, fh = (f[:4] / s).tolist()
                boxes.append(Box(int(x), int(y), int(fw), int(fh), float(f[-1])))
        return boxes

    # -- Haar (secours), de face + de profil des deux côtés -------------------
    def _haar_detect(self, rgb: np.ndarray, threshold: float) -> list[Box]:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape
        s = min(1.0, 1600 / max(h, w))
        if s < 1:
            gray = cv2.resize(gray, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        gray = cv2.equalizeHist(gray)
        neighbors = max(2, int(round(2 + threshold * 6)))  # sensibilité -> minNeighbors
        boxes: list[Box] = []

        def run(casc, img, flip=False):
            if casc.empty():
                return
            for (x, y, fw, fh) in casc.detectMultiScale(img, 1.1, neighbors, minSize=(20, 20)):
                if flip:
                    x = img.shape[1] - x - fw
                boxes.append(Box(int(x / s), int(y / s), int(fw / s), int(fh / s), 0.9))

        run(self._frontal, gray)
        run(self._frontal2, gray)
        run(self._profile, gray)
        run(self._profile, cv2.flip(gray, 1), flip=True)
        return boxes

    def detect(self, rgb: np.ndarray, sensitivity: float = 0.5) -> list[Box]:
        """sensitivity : 0 = prudent (peu de faux positifs) ... 1 = attrape tout."""
        threshold = 0.85 - 0.6 * sensitivity  # 0.85 -> 0.25
        if self._yunet is not None:
            boxes = self._yunet_detect(rgb, threshold)
        else:
            boxes = self._haar_detect(rgb, 1 - sensitivity)
        return merge_boxes(boxes)


def _iou_or_contained(a: Box, b: Box) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    if inter == 0:
        return 0.0
    smaller = min(a.w * a.h, b.w * b.h)
    return inter / smaller


def merge_boxes(boxes: list[Box], thr: float = 0.5) -> list[Box]:
    """Fusionne les doublons (même visage trouvé à plusieurs échelles)."""
    out: list[Box] = []
    for b in sorted(boxes, key=lambda b: -b.w * b.h):
        for i, o in enumerate(out):
            if _iou_or_contained(b, o) > thr:
                x0, y0 = min(b.x, o.x), min(b.y, o.y)
                x1, y1 = max(b.x + b.w, o.x + o.w), max(b.y + b.h, o.y + o.h)
                out[i] = Box(x0, y0, x1 - x0, y1 - y0, max(b.score, o.score), o.manual or b.manual)
                break
        else:
            out.append(b)
    return out


# --------------------------------------------------------------------------- #
#  Anonymisation
# --------------------------------------------------------------------------- #
def _destroy(region: np.ndarray, blocks: int, rng: np.random.Generator) -> np.ndarray:
    """Écrase la zone à `blocks` x `blocks` pixels + bruit : l'info d'origine est perdue."""
    h, w = region.shape[:2]
    bw = max(1, min(blocks, w))
    bh = max(1, int(round(bw * h / max(w, 1))))
    small = cv2.resize(region, (bw, bh), interpolation=cv2.INTER_AREA).astype(np.int16)
    small += rng.integers(-18, 19, small.shape, dtype=np.int16)  # bruit aléatoire
    return np.clip(small, 0, 255).astype(np.uint8)


def anonymize(rgb: np.ndarray, boxes: list[Box], mode: str = "caviardage",
              shape: str = "rectangle", margin: float = 0.25,
              color: tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    out = rgb.copy()
    H, W = out.shape[:2]
    rng = np.random.default_rng()  # graine aléatoire : rien de reproductible
    for b in boxes:
        e = b.expanded(0 if b.manual else margin, W, H)
        if e.w < 2 or e.h < 2:
            continue
        region = out[e.y:e.y + e.h, e.x:e.x + e.w]

        if mode == "caviardage":
            patch = np.empty_like(region)
            patch[:] = color
        elif mode == "pixel":
            small = _destroy(region, 7, rng)
            patch = cv2.resize(small, (e.w, e.h), interpolation=cv2.INTER_NEAREST)
        else:  # flou destructif : on détruit d'abord, puis on lisse pour l'esthétique
            small = _destroy(region, 5, rng)
            patch = cv2.resize(small, (e.w, e.h), interpolation=cv2.INTER_LINEAR)
            k = max(3, (min(e.w, e.h) // 3) | 1)
            patch = cv2.GaussianBlur(patch, (k, k), 0)

        if shape == "ellipse":
            mask = np.zeros((e.h, e.w), np.uint8)
            cv2.ellipse(mask, (e.w // 2, e.h // 2), (e.w // 2, e.h // 2), 0, 0, 360, 255, -1)
            m = mask.astype(bool)
            region[m] = patch[m]
        else:
            region[:] = patch
    return out


def list_images(folder: str) -> list[str]:
    res = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "anonymise"]
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS and not f.endswith(("_anonyme.jpg", "_anonyme.png")):
                res.append(os.path.join(root, f))
    return res
