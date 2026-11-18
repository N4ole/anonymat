"""Cœur du programme : détection des visages et anonymisation.

Principes de sécurité :
  * Méthodes « destructives » : la zone est d'abord réduite à une poignée de blocs
    + bruit aléatoire (l'information du visage est DÉTRUITE). Un flou gaussien
    classique, lui, peut être en partie inversé (déconvolution / IA).
  * L'image est reconstruite à partir des pixels : aucune métadonnée (EXIF, GPS,
    XMP, IPTC, miniature EXIF qui contient souvent l'original) n'est recopiée.
  * Les zones sont élargies (cheveux, oreilles, menton) ; l'ellipse est agrandie
    pour toujours couvrir entièrement le cadre du visage.
  * Noms de sortie neutres par défaut (le nom d'origine contient souvent date/heure).
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # photos iPhone (.heic)
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover - dépend de l'installation
    HEIF_OK = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif"}
if HEIF_OK:
    IMAGE_EXTS |= {".heic", ".heif"}

OUTPUT_FOLDER = "anonymise"
NEUTRAL_PREFIX = "anonyme_"

YUNET_FILE = "face_detection_yunet_2023mar.onnx"
# Empreinte officielle (OpenCV Zoo / huggingface.co/opencv/face_detection_yunet)
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"

# --------------------------------------------------------------------------- #
#  Méthodes
# --------------------------------------------------------------------------- #
# clé -> (libellé, sûre par nature ?)
MODES = {
    "flou": "🔒 Flou (destructif)",
    "rond_flou": "🔒 Rond flou",
    "pixel": "🔒 Pixels (destructif)",
    "emoji": "🔒 Émoji 🙂",
    "noir": "🔒 Carré noir",
    "pixel_classique": "⚠ Pixelisation classique (sans bruit)",
    "flou_classique": "⚠ Flou gaussien classique",
    "bandeau": "⛔ Bandeau sur les yeux",
}
SIMPLE_MODES = ["flou", "rond_flou", "pixel", "emoji", "noir"]
SAFE_MODES = SIMPLE_MODES
SIMPLE_STRENGTH = 0.75          # force utilisée en mode simple (toujours sûre)

SAFE_BLOCKS = 12   # <= 12 blocs sur la largeur de la zone : anonymat total
WEAK_BLOCKS = 20   # 13-20 : reconnaissable par un proche / une IA
MIN_SAFE_MARGIN = 0.10
ELLIPSE_MARGIN = 0.30           # marge mini pour qu'une ellipse couvre tout le visage
ROUND_MARGIN = 0.35             # idem avec bord adouci (rond flou)
MANUAL_MARGIN = 0.10            # marge appliquée aux zones dessinées à la main


def blocks_for(strength: float, lo: int = 64, hi: int = 3) -> int:
    """Force 0 -> `lo` blocs (très léger), 1 -> `hi` blocs. Échelle logarithmique."""
    s = min(1.0, max(0.0, strength))
    return max(hi, int(round(lo * (hi / lo) ** s)))


def min_safe_strength() -> float:
    """Plus petite force (au centième) qui donne l'anonymat total en mode destructif."""
    for i in range(101):
        if blocks_for(i / 100) <= SAFE_BLOCKS:
            return i / 100
    return 1.0


def safety(mode: str, strength: float, margin: float = 0.3) -> tuple[str, str]:
    """Renvoie (niveau, message clair) ; niveau ∈ ok | moyen | danger."""
    b = blocks_for(strength)
    if mode in ("noir", "emoji"):
        level, msg = "ok", "Anonymat total : le visage est entièrement recouvert."
    elif mode in ("pixel", "flou", "rond_flou"):
        if b <= SAFE_BLOCKS:
            level, msg = "ok", "Anonymat total : le visage est détruit, impossible de le retrouver."
        elif b <= WEAK_BLOCKS:
            level, msg = "moyen", ("Effet trop faible : un proche ou une IA pourrait reconnaître la personne. "
                                   "Pousse le curseur « Force » vers la droite.")
        else:
            level, msg = "danger", "Effet léger : le visage reste reconnaissable. Pousse le curseur « Force » vers la droite."
    elif mode == "pixel_classique":
        if b <= SAFE_BLOCKS:
            level, msg = "moyen", "Pixelisation classique : correcte, mais des outils d'IA savent parfois retrouver l'identité."
        else:
            level, msg = "danger", "Pixelisation classique légère : visage reconnaissable ou reconstructible par IA."
    elif mode == "flou_classique":
        level, msg = "danger", "Flou gaussien : peut être en partie annulé (logiciels, IA), même fort. Ne protège pas vraiment."
    elif mode == "bandeau":
        level, msg = "danger", "Bandeau sur les yeux : la personne reste facilement reconnaissable (bouche, nez, cheveux…)."
    else:
        return "danger", "Méthode inconnue."
    if margin < MIN_SAFE_MARGIN and level == "ok" and mode != "bandeau":
        level, msg = "moyen", "Marge trop faible : cheveux, oreilles ou menton peuvent dépasser. Augmente la marge."
    return level, msg


# --------------------------------------------------------------------------- #
#  Utilitaires
# --------------------------------------------------------------------------- #
def resource_path(rel: str) -> str:
    """Chemin d'une ressource, que l'on soit en .py ou dans le .exe PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def read_bytes(path: str) -> bytes:
    """Lecture via Python (gère les chemins accentués, contrairement à OpenCV sous Windows)."""
    with open(path, "rb") as f:
        return f.read()


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    score: float = 1.0
    manual: bool = False
    kind: str = "visage"  # visage | objet

    def expanded(self, margin: float, img_w: int, img_h: int) -> Box:
        """Agrandit la zone de `margin` (0.3 = +30 %) en restant dans l'image."""
        dx, dy_top, dy_bot = self.w * margin, self.h * margin * 1.3, self.h * margin
        x0 = max(0, int(self.x - dx))
        y0 = max(0, int(self.y - dy_top))  # plus de marge en haut (cheveux)
        x1 = min(img_w, int(round(self.x + self.w + dx)))
        y1 = min(img_h, int(round(self.y + self.h + dy_bot)))
        return Box(x0, y0, max(0, x1 - x0), max(0, y1 - y0), self.score, self.manual, self.kind)

    def raw_expanded(self, margin: float) -> tuple[int, int, int, int]:
        """Comme `expanded`, mais SANS rogner aux bords de l'image (peut être négatif).
        Indispensable pour qu'une ellipse garde sa vraie forme près d'un bord."""
        x0 = int(np.floor(self.x - self.w * margin))
        y0 = int(np.floor(self.y - self.h * margin * 1.3))
        x1 = int(np.ceil(self.x + self.w + self.w * margin))
        y1 = int(np.ceil(self.y + self.h + self.h * margin))
        return x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)

    def scaled(self, s: float) -> Box:
        return Box(int(self.x * s), int(self.y * s), max(1, int(round(self.w * s))),
                   max(1, int(round(self.h * s))), self.score, self.manual, self.kind)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.w and self.y <= y <= self.y + self.h


def effective_margin(box: Box, margin: float, shape: str, mode: str = "") -> float:
    """Marge réellement appliquée : jamais < MANUAL_MARGIN pour une zone manuelle,
    et agrandie pour qu'une ellipse recouvre entièrement le cadre du visage."""
    m = max(margin, MANUAL_MARGIN) if box.manual else margin
    if mode == "rond_flou":
        m = max(m, ROUND_MARGIN)
    elif shape == "ellipse" or mode == "emoji":
        m = max(m, ELLIPSE_MARGIN)
    return m


# --------------------------------------------------------------------------- #
#  Chargement / sauvegarde
# --------------------------------------------------------------------------- #
def _to_8bit(im: Image.Image) -> Image.Image:
    """Images 16 bits / 32 bits / flottantes -> 8 bits (Pillow les sature sinon)."""
    if im.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        a = np.asarray(im, dtype=np.float64)
        if im.mode.startswith("I;16"):
            a = a / 257.0
        elif a.size:
            lo, hi = float(a.min()), float(a.max())
            if hi > 255 or lo < 0:
                a = (a - lo) / max(hi - lo, 1e-9) * 255.0
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L")
    return im


def load_image_full(path: str, max_side: int | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    """Charge une image -> (RGB uint8, alpha uint8 ou None). Applique l'orientation EXIF.

    `max_side` : réduction rapide (aperçu) - le décodage JPEG est alors accéléré.
    """
    with Image.open(path) as im:
        if max_side and im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))
        im.seek(0)  # GIF / WebP animés : première image
        im = ImageOps.exif_transpose(im)
        im = _to_8bit(im)
        alpha = None
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            alpha = np.array(rgba.getchannel("A"))
            rgb = rgba.convert("RGB")
        else:
            rgb = im.convert("RGB")
        if max_side and max(rgb.size) > max_side:
            s = max_side / max(rgb.size)
            size = (max(1, round(rgb.width * s)), max(1, round(rgb.height * s)))
            rgb = rgb.resize(size, Image.BILINEAR)
            if alpha is not None:
                alpha = np.array(Image.fromarray(alpha).resize(size, Image.BILINEAR))
        return np.array(rgb), alpha


def load_image(path: str) -> np.ndarray:
    return load_image_full(path)[0]


def image_size(path: str) -> tuple[int, int]:
    """Taille (largeur, hauteur) après orientation EXIF, sans décoder l'image."""
    with Image.open(path) as im:
        w, h = im.size
        try:
            if im.getexif().get(0x0112) in (5, 6, 7, 8):
                w, h = h, w
        except Exception:
            pass
        return w, h


FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def output_ext(src: str) -> str:
    ext = os.path.splitext(src)[1].lower()
    if ext in (".jpg", ".jpeg", ".webp"):
        return ext
    if ext in (".heic", ".heif"):
        return ".jpg"
    return ".png"  # png, tif, bmp, gif -> png (sans perte)


def save_clean(rgb: np.ndarray, path: str, alpha: np.ndarray | None = None) -> None:
    """Enregistre SANS aucune métadonnée, de façon atomique (pas de fichier tronqué)."""
    ext = os.path.splitext(path)[1].lower()
    fmt = FORMATS.get(ext, "PNG")
    if alpha is not None:
        # Les couleurs sous les pixels 100 % transparents sont effacées : sinon un visage
        # « gommé » par transparence réapparaît dès qu'on retire le canal alpha.
        rgb = rgb.copy()
        rgb[alpha == 0] = 0
    if alpha is not None and fmt in ("PNG", "WEBP"):
        img = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    else:
        img = Image.fromarray(rgb, "RGB")
    tmp = path + ".part"
    try:
        with open(tmp, "wb") as f:
            if fmt == "JPEG":
                img.save(f, fmt, quality=92, optimize=True)
            elif fmt == "WEBP":
                img.save(f, fmt, quality=92)
            else:
                img.save(f, fmt)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def plan_outputs(paths: list[str], out_dir: str | None = None, keep_names: bool = False) -> dict[str, str]:
    """Calcule un chemin de sortie UNIQUE pour chaque image (jamais d'écrasement).

    * keep_names=False (défaut) : noms neutres « anonyme_0001.jpg » (le nom d'origine
      contient souvent la date/heure de prise de vue).
    * keep_names=True : « photo_anonyme.jpg », avec suffixe _2, _3… en cas de doublon.
    """
    taken: set[str] = set()
    counters: dict[str, int] = {}
    plan: dict[str, str] = {}
    for src in paths:
        folder = out_dir or os.path.join(os.path.dirname(os.path.abspath(src)), OUTPUT_FOLDER)
        ext = output_ext(src)
        if keep_names:
            stem = os.path.splitext(os.path.basename(src))[0] + "_anonyme"
            cand, n = stem, 1
            while True:
                p = os.path.join(folder, cand + ext)
                if os.path.normcase(p) not in taken and not os.path.exists(p):
                    break
                n += 1
                cand = f"{stem}_{n}"
        else:
            key = os.path.normcase(folder)
            if key not in counters:  # numéro le plus haut déjà présent, toutes extensions confondues
                existing = os.listdir(folder) if os.path.isdir(folder) else []
                nums = [int(f[len(NEUTRAL_PREFIX):len(NEUTRAL_PREFIX) + 4]) for f in existing
                        if f.lower().startswith(NEUTRAL_PREFIX) and f[len(NEUTRAL_PREFIX):len(NEUTRAL_PREFIX) + 4].isdigit()]
                counters[key] = max(nums, default=0)
            n = counters[key]
            while True:
                n += 1
                p = os.path.join(folder, f"{NEUTRAL_PREFIX}{n:04d}{ext}")
                if os.path.normcase(p) not in taken and not os.path.exists(p):
                    break
            counters[key] = n
        taken.add(os.path.normcase(p))
        plan[src] = p
    return plan


_OUTPUT_RE = re.compile(r"^(anonyme_\d{4}|.+_anonyme(_\d+)?)$", re.IGNORECASE)


def is_output_file(path: str) -> bool:
    """Vrai uniquement pour les noms produits par ce programme :
    « anonyme_0001 » ou « photo_anonyme » / « photo_anonyme_2 »."""
    return bool(_OUTPUT_RE.match(os.path.splitext(os.path.basename(path))[0]))


def scan_images(folder: str, exclude_dirs: list[str] | None = None) -> tuple[list[str], int]:
    """Images d'un dossier (récursif) -> (fichiers, nb de sorties du programme ignorées)."""
    excl = {os.path.normcase(os.path.abspath(d)) for d in (exclude_dirs or [])}
    res, skipped = [], 0
    for root, dirs, files in os.walk(folder):
        kept = []
        for d in dirs:
            full = os.path.join(root, d)
            if d.lower() == OUTPUT_FOLDER or os.path.normcase(os.path.abspath(full)) in excl:
                skipped += sum(os.path.splitext(f)[1].lower() in IMAGE_EXTS
                               for _, _, fs in os.walk(full) for f in fs)
            else:
                kept.append(d)
        dirs[:] = kept
        if os.path.normcase(os.path.abspath(root)) in excl:
            continue
        for f in sorted(files):
            p = os.path.join(root, f)
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                if is_output_file(p):
                    skipped += 1
                else:
                    res.append(p)
    return res, skipped


def list_images(folder: str, exclude_dirs: list[str] | None = None) -> list[str]:
    return scan_images(folder, exclude_dirs)[0]


def secure_delete(path: str) -> None:
    """Écrase le fichier avec des données aléatoires, le renomme puis le supprime.

    Limite : sur SSD, clé USB, disque synchronisé (OneDrive…) ou avec des sauvegardes,
    des copies peuvent subsister ailleurs. C'est un « mieux que rien », pas une garantie.
    """
    size = os.path.getsize(path)
    with open(path, "r+b", buffering=0) as f:
        remaining = size
        while remaining > 0:
            chunk = min(remaining, 1 << 20)
            f.write(secrets.token_bytes(chunk))
            remaining -= chunk
        f.flush()
        os.fsync(f.fileno())
    tmp = os.path.join(os.path.dirname(path), secrets.token_hex(8))
    os.replace(path, tmp)
    os.remove(tmp)


# --------------------------------------------------------------------------- #
#  Détection
# --------------------------------------------------------------------------- #
def load_cascade(path: str) -> cv2.CascadeClassifier:
    """Charge une cascade Haar depuis la mémoire (OpenCV ne gère pas les chemins
    accentués sous Windows : un nom d'utilisateur « Hélène » suffit à tout casser)."""
    text = read_bytes(path).decode("utf-8")
    fs = cv2.FileStorage(text, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
    c = cv2.CascadeClassifier()
    if not c.read(fs.getFirstTopLevelNode()) or c.empty():
        raise RuntimeError(f"cascade illisible : {os.path.basename(path)}")
    return c


class FaceDetector:
    """YuNet (IA, précis) si le modèle est présent et intègre, sinon Haar (basique).

    backend ∈ "yunet" | "haar" | "aucun" ; `problems` liste ce qui n'a pas marché.
    """

    def __init__(self, models_dir: str | None = None, haar_dir: str | None = None) -> None:
        self.problems: list[str] = []
        self.tampered = False  # modèle présent mais empreinte incorrecte : alerte forte
        self._yunet = None
        self._cascades: list[tuple[cv2.CascadeClassifier, bool, tuple[float, float]]] = []

        model = os.path.join(models_dir or resource_path("models"), YUNET_FILE)
        if os.path.isfile(model):
            try:
                data = read_bytes(model)
                if hashlib.sha256(data).hexdigest() != YUNET_SHA256:
                    self.tampered = True
                    raise RuntimeError("empreinte SHA-256 incorrecte (fichier corrompu ou modifié)")
                buf = np.frombuffer(data, np.uint8)
                self._yunet = cv2.FaceDetectorYN.create("onnx", buf, np.empty(0, np.uint8),
                                                        (320, 320), 0.5, 0.3, 5000)
            except Exception as exc:
                self.problems.append(f"Modèle IA YuNet inutilisable : {exc}")
                self._yunet = None
        else:
            self.problems.append("Modèle IA YuNet absent de ce programme.")

        hd = haar_dir or resource_path("haar")
        for name, profile in (("haarcascade_frontalface_default.xml", False),
                              ("haarcascade_frontalface_alt2.xml", False),
                              ("haarcascade_profileface.xml", True)):
            try:
                self._cascades.append((load_cascade(os.path.join(hd, name)), profile, self.HAAR_NORM[name]))
            except Exception as exc:
                self.problems.append(f"Détecteur Haar {name} : {exc}")

        self.backend = "yunet" if self._yunet is not None else ("haar" if self._cascades else "aucun")

    @property
    def ok(self) -> bool:
        return self.backend != "aucun"

    # Les deux détecteurs produisent des « candidats » avec un score de confiance 0..1 ;
    # le seuil est appliqué ensuite (et peut être ajusté par l'apprentissage local).
    CANDIDATE_FLOOR = 0.15
    # Normalisation des poids Haar (levelWeights) : centre / échelle par cascade,
    # étalonnés sur des vrais visages (≈ 0.9) et des faux positifs typiques (≈ 0.25).
    HAAR_NORM = {"haarcascade_frontalface_default.xml": (2.5, 1.5),
                 "haarcascade_frontalface_alt2.xml": (55.3, 0.8),
                 "haarcascade_profileface.xml": (1.0, 0.8)}

    # -- YuNet, multi-échelle pour attraper petits ET grands visages ----------
    def _yunet_candidates(self, rgb: np.ndarray) -> list[Box]:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        self._yunet.setScoreThreshold(self.CANDIDATE_FLOOR)
        boxes: list[Box] = []
        long_side = max(h, w)
        for t in sorted({min(long_side, t) for t in (640, 1280, 2560)}):
            s = t / long_side
            img = bgr if s >= 1 else cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                                                interpolation=cv2.INTER_AREA)
            if min(img.shape[:2]) < 10:
                continue
            self._yunet.setInputSize((img.shape[1], img.shape[0]))
            _, faces = self._yunet.detect(img)
            if faces is None:
                continue
            for f in faces:
                x, y, fw, fh = (f[:4] / s).tolist()
                boxes.append(Box(int(x), int(y), int(fw), int(fh), float(f[-1])))
        return boxes

    # -- Haar (secours), de face + de profil des deux côtés -------------------
    def _haar_candidates(self, rgb: np.ndarray) -> list[Box]:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape
        s = min(1.0, 1600 / max(h, w))
        if s < 1:
            gray = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        if min(gray.shape) < 20:
            return []
        gray = cv2.equalizeHist(gray)
        boxes: list[Box] = []
        for casc, profile, (c, k) in self._cascades:
            imgs = [(gray, False)] + ([(cv2.flip(gray, 1), True)] if profile else [])
            for img, flip in imgs:
                rects, _, weights = casc.detectMultiScale3(img, 1.1, 1, minSize=(20, 20), outputRejectLevels=True)
                for (x, y, fw, fh), wt in zip(np.asarray(rects).reshape(-1, 4), np.ravel(weights), strict=True):
                    score = float(1 / (1 + np.exp(-(float(wt) - c) / k)))
                    if score < self.CANDIDATE_FLOOR:
                        continue
                    if flip:
                        x = img.shape[1] - x - fw
                    boxes.append(Box(int(x / s), int(y / s), int(fw / s), int(fh / s), score))
        return boxes

    def candidates(self, rgb: np.ndarray) -> list[Box]:
        """Toutes les détections possibles (même peu sûres), avec leur score, non fusionnées."""
        if self._yunet is not None:
            boxes = self._yunet_candidates(rgb)
        elif self._cascades:
            boxes = self._haar_candidates(rgb)
        else:
            boxes = []
        H, W = rgb.shape[:2]
        out = []
        for b in boxes:
            x0, y0 = max(0, b.x), max(0, b.y)
            x1, y1 = min(W, b.x + b.w), min(H, b.y + b.h)
            if x1 - x0 > 1 and y1 - y0 > 1:
                out.append(Box(x0, y0, x1 - x0, y1 - y0, b.score))
        return out

    @staticmethod
    def base_threshold(sensitivity: float) -> float:
        """sensitivity : 0 = prudent ... 1 = attrape tout."""
        return 0.85 - 0.6 * min(1.0, max(0.0, sensitivity))

    def detect(self, rgb: np.ndarray, sensitivity: float = 0.5, threshold: float | None = None) -> list[Box]:
        """Visages retenus. `threshold` (si fourni) remplace le seuil déduit de la sensibilité."""
        t = self.base_threshold(sensitivity) if threshold is None else threshold
        return merge_boxes([b for b in self.candidates(rgb) if b.score >= t])

    def best_score_near(self, rgb: np.ndarray, box: Box) -> float:
        """Score du meilleur candidat qui recouvre `box` (0 si le détecteur n'a rien vu là).
        Sert à savoir si un visage ajouté à la main avait été « presque » détecté."""
        best = 0.0
        for c in self.candidates(rgb):
            if _iou(c, box) > 0.3 or (_overlap(c, box) > 0.6 and 0.4 < (c.w * c.h) / max(1, box.w * box.h) < 2.5):
                best = max(best, c.score)
        return best


def _iou(a: Box, b: Box) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max(1, a.w * a.h + b.w * b.h - inter)


def _overlap(a: Box, b: Box) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    return inter / max(1, min(a.w * a.h, b.w * b.h))


def merge_boxes(boxes: list[Box], thr: float = 0.5) -> list[Box]:
    """Fusionne les doublons (même visage trouvé à plusieurs échelles)."""
    out: list[Box] = []
    for b in sorted(boxes, key=lambda b: -b.w * b.h):
        for i, o in enumerate(out):
            if _overlap(b, o) > thr:
                x0, y0 = min(b.x, o.x), min(b.y, o.y)
                x1, y1 = max(b.x + b.w, o.x + o.w), max(b.y + b.h, o.y + o.h)
                out[i] = Box(x0, y0, x1 - x0, y1 - y0, max(b.score, o.score))
                break
        else:
            out.append(b)
    return out


# --------------------------------------------------------------------------- #
#  Anonymisation
# --------------------------------------------------------------------------- #
def _destroy(region: np.ndarray, blocks: int, rng: np.random.Generator, noise: int = 18) -> np.ndarray:
    """Réduit la zone à `blocks` blocs de large (+ bruit) : l'info d'origine est perdue.
    Même une zone minuscule est réduite au moins de moitié."""
    h, w = region.shape[:2]
    bw = max(1, min(blocks, w // 2 if w >= 2 else 1))
    bh = max(1, min(int(round(bw * h / max(w, 1))), h // 2 if h >= 2 else 1))
    small = cv2.resize(region, (bw, bh), interpolation=cv2.INTER_AREA).astype(np.int16)
    if noise:
        small += rng.integers(-noise, noise + 1, small.shape, dtype=np.int16)
    return np.clip(small, 0, 255).astype(np.uint8)


def _ellipse_mask(w: int, h: int, feather: float = 0.0) -> np.ndarray:
    """Masque elliptique 0..1 ; `feather` = largeur du bord adouci (fraction)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = (xx + 0.5 - w / 2) / (w / 2)
    ny = (yy + 0.5 - h / 2) / (h / 2)
    d = np.sqrt(nx * nx + ny * ny)
    if feather <= 0:
        return (d <= 1.0).astype(np.float32)
    return np.clip((1.0 - d) / feather, 0, 1).astype(np.float32)


def _draw_emoji(w: int, h: int, region: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Dessine un smiley jaune (sans police : fonctionne partout)."""
    S = 4  # sur-échantillonnage pour des bords lisses
    W, H = w * S, h * S
    img = region.copy()
    img = cv2.resize(img, (W, H), interpolation=cv2.INTER_NEAREST)
    cx, cy, rx, ry = W // 2, H // 2, W // 2, H // 2
    lw = max(S, int(min(W, H) * 0.035))
    cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, (255, 204, 51), -1, cv2.LINE_AA)
    cv2.ellipse(img, (cx, cy), (rx - lw // 2, ry - lw // 2), 0, 0, 360, (230, 160, 20), lw, cv2.LINE_AA)
    ex, ey = int(rx * 0.36), int(ry * 0.28)
    er = (max(S, int(rx * 0.11)), max(S, int(ry * 0.17)))
    for sx in (-1, 1):
        cv2.ellipse(img, (cx + sx * ex, cy - ey), er, 0, 0, 360, (60, 40, 20), -1, cv2.LINE_AA)
    cv2.ellipse(img, (cx, cy + int(ry * 0.08)), (int(rx * 0.55), int(ry * 0.45)), 0, 20, 160,
                (60, 40, 20), max(S, int(min(W, H) * 0.06)), cv2.LINE_AA)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return img, _ellipse_mask(w, h)


def box_mode(b: Box, mode: str, shape: str) -> tuple[str, str]:
    """Méthode et forme réellement appliquées à une zone."""
    if b.kind == "objet" and mode == "emoji":  # un smiley sur une plaque n'a pas de sens
        return "flou", "rectangle"
    if mode in ("rond_flou", "emoji"):
        return mode, "ellipse"
    return mode, shape


def anonymize(rgb: np.ndarray, boxes: list[Box], mode: str = "flou",
              shape: str = "rectangle", margin: float = 0.3,
              color: tuple[int, int, int] = (0, 0, 0), strength: float = 0.75) -> np.ndarray:
    """Applique la méthode `mode` sur chaque zone. strength : 0 = léger ... 1 = maximal."""
    if mode not in MODES:
        raise ValueError(f"Méthode inconnue : {mode}")
    s = min(1.0, max(0.0, strength))
    out = rgb.copy()
    H, W = out.shape[:2]
    rng = np.random.default_rng()  # graine aléatoire : rien de reproductible
    base_mode, base_shape = mode, shape
    for b in boxes:
        mode, shape = box_mode(b, base_mode, base_shape)
        if mode == "bandeau" and b.kind == "visage":
            r = b.expanded(0, W, H)
            thick = max(2, int(r.h * (0.12 + 0.30 * s)))
            cy = r.y + int(r.h * 0.40)  # les yeux sont vers 40 % de la hauteur du visage
            y0, y1 = max(0, cy - thick // 2), min(H, cy + (thick + 1) // 2)
            x0, x1 = max(0, r.x - int(r.w * 0.08)), min(W, r.x + r.w + int(r.w * 0.08))
            out[y0:y1, x0:x1] = color
            continue

        # Zone complète (peut déborder de la photo) : la forme est calculée dessus,
        # puis recadrée. Sinon une ellipse près d'un bord laisse le visage à découvert.
        rx0, ry0, rx1, ry1 = b.raw_expanded(effective_margin(b, margin, shape, mode))
        cx0, cy0, cx1, cy1 = max(0, rx0), max(0, ry0), min(W, rx1), min(H, ry1)
        if cx1 - cx0 < 1 or cy1 - cy0 < 1:
            continue
        rw, rh = rx1 - rx0, ry1 - ry0
        visible = out[cy0:cy1, cx0:cx1]
        pad = (cy0 - ry0, ry1 - cy1, cx0 - rx0, rx1 - cx1)  # haut, bas, gauche, droite
        region = cv2.copyMakeBorder(visible, *pad, cv2.BORDER_REPLICATE) if any(pad) else visible.copy()
        alpha = None

        if mode == "noir" or mode == "bandeau":  # objets en mode bandeau : noir
            patch = np.empty_like(region)
            patch[:] = color
        elif mode in ("pixel", "pixel_classique"):
            noise = int(8 + 22 * s) if mode == "pixel" else 0
            small = _destroy(region, blocks_for(s), rng, noise)
            patch = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
        elif mode in ("flou", "rond_flou"):  # on détruit d'abord, puis on lisse
            small = _destroy(region, blocks_for(s), rng, int(8 + 22 * s))
            patch = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_CUBIC)
            k = max(3, int(min(rw, rh) / max(1, blocks_for(s)) * 1.5) | 1)
            patch = cv2.GaussianBlur(patch, (k, k), 0)
            if mode == "rond_flou":
                alpha = _ellipse_mask(rw, rh, feather=0.12)
        elif mode == "flou_classique":
            sigma = max(0.5, min(rw, rh) * (0.01 + 0.15 * s))
            patch = cv2.GaussianBlur(region, (0, 0), sigma)
        else:  # emoji
            patch, alpha = _draw_emoji(rw, rh, region)

        if alpha is None and shape == "ellipse":
            alpha = _ellipse_mask(rw, rh)
        top, left = pad[0], pad[2]
        vh, vw = visible.shape[:2]
        patch = patch[top:top + vh, left:left + vw]
        if alpha is None:
            visible[:] = patch
        else:
            a = alpha[top:top + vh, left:left + vw, None]
            visible[:] = (patch * a + visible * (1 - a)).round().astype(np.uint8)
    return out
