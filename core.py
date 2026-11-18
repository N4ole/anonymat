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
    "caviardage": "🔒 Caviardage (noir plein)",
    "pixel": "🔒 Pixelisation destructive (+ bruit)",
    "flou": "🔒 Flou destructif",
    "pixel_classique": "⚠ Pixelisation classique",
    "flou_classique": "⚠ Flou gaussien classique",
    "bandeau": "⛔ Bandeau sur les yeux",
}

# Méthodes « destructives » : sous le seuil (nb de blocs en largeur) le visage
# n'est plus identifiable. Au-dessus, on prévient l'utilisateur.
SAFE_BLOCKS = 12     # <= 12 blocs sur la largeur de la zone : anonymat total
WEAK_BLOCKS = 20     # 13-20 : reconnaissable par un proche / une IA


def blocks_for(strength: float, lo: int = 64, hi: int = 3) -> int:
    """Force 0 -> `lo` blocs (effet très léger), 1 -> `hi` blocs. Échelle logarithmique
    pour que le curseur soit progressif sur toute sa course."""
    s = min(1.0, max(0.0, strength))
    return max(hi, int(round(lo * (hi / lo) ** s)))


def safety(mode: str, strength: float) -> tuple[str, str]:
    """Renvoie (niveau, message) ; niveau ∈ ok | moyen | danger."""
    if mode == "caviardage":
        return "ok", "Anonymat total : aucune information du visage n'est conservée."
    if mode in ("pixel", "flou"):
        b = blocks_for(strength)
        if b <= SAFE_BLOCKS:
            return "ok", f"Anonymat total : visage réduit à {b} blocs, information détruite."
        if b <= WEAK_BLOCKS:
            return "moyen", f"Sous le seuil d'anonymat ({b} blocs) : un proche ou une IA peut reconnaître la personne."
        return "danger", f"Effet léger ({b} blocs) : le visage reste reconnaissable."
    if mode == "pixel_classique":
        b = blocks_for(strength)
        if b <= SAFE_BLOCKS:
            return "moyen", f"Pixelisation sans bruit ({b} blocs) : correcte, mais des outils d'IA savent parfois retrouver l'identité."
        return "danger", f"Pixelisation classique ({b} blocs) : visage reconnaissable / reconstructible par IA."
    if mode == "flou_classique":
        return "danger", "Flou gaussien : réversible en partie (déconvolution, IA), même fort. Ne protège pas vraiment."
    if mode == "bandeau":
        return "danger", "Bandeau sur les yeux : la personne reste facilement reconnaissable (bouche, nez, cheveux…)."
    return "danger", ""


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
def _destroy(region: np.ndarray, blocks: int, rng: np.random.Generator, noise: int = 18) -> np.ndarray:
    """Réduit la zone à `blocks` blocs de large (+ bruit aléatoire) : l'info d'origine est perdue."""
    h, w = region.shape[:2]
    bw = max(1, min(blocks, w))
    bh = max(1, int(round(bw * h / max(w, 1))))
    small = cv2.resize(region, (bw, bh), interpolation=cv2.INTER_AREA).astype(np.int16)
    if noise:
        small += rng.integers(-noise, noise + 1, small.shape, dtype=np.int16)
    return np.clip(small, 0, 255).astype(np.uint8)


def anonymize(rgb: np.ndarray, boxes: list[Box], mode: str = "caviardage",
              shape: str = "rectangle", margin: float = 0.25,
              color: tuple[int, int, int] = (0, 0, 0), strength: float = 0.7) -> np.ndarray:
    """strength : 0 = effet à peine visible ... 1 = effet maximal (voir `safety`)."""
    s = min(1.0, max(0.0, strength))
    out = rgb.copy()
    H, W = out.shape[:2]
    rng = np.random.default_rng()  # graine aléatoire : rien de reproductible
    for b in boxes:
        e = b.expanded(0 if b.manual else margin, W, H)
        if e.w < 2 or e.h < 2:
            continue
        region = out[e.y:e.y + e.h, e.x:e.x + e.w]
        mask = None

        if mode == "caviardage":
            patch = np.empty_like(region)
            patch[:] = color

        elif mode in ("pixel", "pixel_classique"):
            noise = int(8 + 22 * s) if mode == "pixel" else 0
            small = _destroy(region, blocks_for(s), rng, noise)
            patch = cv2.resize(small, (e.w, e.h), interpolation=cv2.INTER_NEAREST)

        elif mode == "flou":  # on détruit d'abord, puis on lisse pour l'esthétique
            small = _destroy(region, blocks_for(s), rng, int(8 + 22 * s))
            patch = cv2.resize(small, (e.w, e.h), interpolation=cv2.INTER_CUBIC)
            k = max(3, int(min(e.w, e.h) / blocks_for(s) * 1.5) | 1)
            patch = cv2.GaussianBlur(patch, (k, k), 0)

        elif mode == "flou_classique":  # flou gaussien simple (réversible en partie)
            sigma = max(0.5, min(e.w, e.h) * (0.01 + 0.15 * s))
            patch = cv2.GaussianBlur(region, (0, 0), sigma)

        elif mode == "bandeau":  # bande noire sur les yeux (pas d'élargissement)
            r = b.expanded(0, W, H)
            thick = max(2, int(r.h * (0.12 + 0.30 * s)))
            cy = r.y + int(r.h * 0.40)  # les yeux sont vers 40 % de la hauteur du visage
            y0, y1 = max(0, cy - thick // 2), min(H, cy + thick // 2)
            x0, x1 = max(0, r.x - int(r.w * 0.08)), min(W, r.x + r.w + int(r.w * 0.08))
            out[y0:y1, x0:x1] = color
            continue

        else:
            raise ValueError(f"Méthode inconnue : {mode}")

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
