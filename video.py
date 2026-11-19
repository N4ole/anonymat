"""Vidéos : lecture image par image, détection + suivi des visages, passages à risque, export.

Principes de sécurité (en plus de ceux de core.py) :
  * Hystérésis : un visage est « accroché » au seuil normal, puis suivi tant que le
    détecteur le voit, même faiblement (tête tournée, flou de bougé). Le suivi est
    aussi prolongé vers l'arrière (un visage est souvent détecté un peu tard).
  * Le détecteur ne regarde qu'une image sur `step` : entre deux analyses, la zone
    cachée est l'UNION des deux positions (elle couvre le mouvement), un visage perdu
    puis retrouvé reste caché pendant le trou, et chaque piste est prolongée avant et
    après (au moins un intervalle d'analyse).
  * Seuil plus strict qu'en photo (core.VIDEO_SAFE_BLOCKS).
  * Le fichier est réécrit image par image : aucune métadonnée (GPS, appareil, date)
    n'est recopiée. Le son est supprimé, ou remplacé par la voix déformée (audio.py).
  * Ce qui reste incertain est signalé comme « passage à risque » pour la revue.
"""

from __future__ import annotations

import bisect
import os
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

import audio
import core

LOW_SCORE = 0.30            # score mini pour PROLONGER une piste déjà accrochée
MAX_GAP_S = 1.0             # trou maximal (secondes) comblé entre deux vues d'un même visage
HOLD_S = 0.5                # prolongation de chaque piste avant / après (secondes)
RATES = {"precise": 10.0, "rapide": 4.0}   # analyses par seconde de vidéo
AUTO_PRECISE_MAX_S = 180    # réglage « auto » : analyse précise jusqu'à 3 minutes
DOUBT_SCORE = 0.6           # piste dont le meilleur score est plus bas : « vraiment un visage ? »
EDGE = 0.03                 # visage à moins de 3 % du bord : il entre / sort du champ (normal)
RISK_MERGE_S = 1.0          # passages à risque plus proches : regroupés
FOURCC = "mp4v"             # seul encodeur MP4 du FFmpeg (LGPL) intégré à OpenCV


class Cancelled(Exception):
    """Analyse ou export interrompu par l'utilisateur."""


# --------------------------------------------------------------------------- #
#  Lecture
# --------------------------------------------------------------------------- #
@dataclass
class VideoInfo:
    fps: float
    frames: int
    width: int
    height: int

    @property
    def duration(self) -> float:
        return self.frames / self.fps


def open_capture(path: str) -> cv2.VideoCapture:
    """Ouvre une vidéo (FFmpeg) en appliquant la rotation des vidéos de téléphone."""
    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise OSError("vidéo illisible (format non pris en charge ou fichier abîmé)")
    cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    return cap


def _fps(cap: cv2.VideoCapture) -> float:
    fps = cap.get(cv2.CAP_PROP_FPS)
    return float(fps) if 1 <= fps <= 1000 else 25.0  # valeur absurde (NaN, 0, 90000…) : 25 i/s


def probe(path: str) -> VideoInfo:
    """Cadence, nombre d'images (estimé) et taille APRÈS rotation (lue sur la 1re image)."""
    cap = open_capture(path)
    try:
        ok, bgr = cap.read()
        if not ok:
            raise OSError("aucune image lisible dans cette vidéo")
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        return VideoInfo(_fps(cap), max(1, int(n) if n == n and n > 0 else 1), bgr.shape[1], bgr.shape[0])
    finally:
        cap.release()


def fit(rgb: np.ndarray, max_side: int | None) -> np.ndarray:
    h, w = rgb.shape[:2]
    if not max_side or max(h, w) <= max_side:
        return rgb
    s = max_side / max(h, w)
    return cv2.resize(rgb, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


class Reader:
    """Accès à une image quelconque (aperçu) : lecture suivie rapide, saut sinon."""

    def __init__(self, path: str, max_side: int | None = None) -> None:
        self.cap = open_capture(path)
        self.max_side = max_side
        self.pos = -1
        self.last: np.ndarray | None = None

    def frame(self, idx: int) -> np.ndarray | None:
        if idx == self.pos and self.last is not None:
            return self.last
        if self.pos < idx <= self.pos + 15:  # petit saut en avant (lecture en retard) : on passe les images
            for _ in range(idx - self.pos - 1):
                if not self.cap.grab():
                    return None
        elif idx != self.pos + 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, bgr = self.cap.read()
        if not ok:
            return None
        self.pos = idx
        self.last = fit(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), self.max_side)
        return self.last

    def close(self) -> None:
        self.cap.release()


# --------------------------------------------------------------------------- #
#  Analyse
# --------------------------------------------------------------------------- #
def analysis_step(info: VideoInfo, speed: str = "auto") -> int:
    """Nombre d'images entre deux analyses."""
    if speed not in RATES:
        speed = "precise" if info.duration <= AUTO_PRECISE_MAX_S else "rapide"
    return max(1, round(info.fps / RATES[speed]))


def analyse(path: str, detector: core.FaceDetector, speed: str = "auto",
            progress: Callable[[float], None] | None = None,
            cancel: Callable[[], bool] | None = None) -> tuple[VideoInfo, int, dict[int, list[core.Box]]]:
    """Passe sur toute la vidéo -> (infos, pas d'analyse, {image: candidats}).

    Les candidats (même peu sûrs) sont gardés : changer la sensibilité ne demande pas
    de relire la vidéo (voir build_tracks). Le nombre d'images réel est recompté."""
    info = probe(path)
    step = analysis_step(info, speed)
    cap = open_capture(path)
    cands: dict[int, list[core.Box]] = {}
    f = 0
    try:
        while True:
            if cancel and cancel():
                raise Cancelled
            if f % step == 0:
                ok, bgr = cap.read()
                if not ok:
                    break
                cands[f] = detector.candidates(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), fast=True)
            elif not cap.grab():
                break
            f += 1
            if progress and f % 15 == 0:
                progress(min(1.0, f / info.frames))
    finally:
        cap.release()
    if not f:
        raise OSError("aucune image lisible dans cette vidéo")
    info.frames = f
    return info, step, cands


# --------------------------------------------------------------------------- #
#  Suivi
# --------------------------------------------------------------------------- #
@dataclass(eq=False)
class Track:
    """Un visage (ou objet) au fil du temps : positions connues à certaines images.

    Piste auto : positions = détections ; entre deux, union des deux cadres.
    Piste manuelle : positions = images-clés posées à la main ; entre deux, interpolation."""

    samples: dict[int, core.Box] = field(default_factory=dict)
    manual: bool = False
    kind: str = "visage"
    start: int = 0                      # première image couverte
    end: int = 0                        # dernière image couverte (incluse)
    gaps: list[tuple[int, int]] = field(default_factory=list)  # trous comblés (auto)
    _keys: list[int] | None = field(default=None, repr=False)

    def set(self, f: int, b: core.Box) -> None:
        self.samples[f] = b
        self._keys = None

    def keys(self) -> list[int]:
        if self._keys is None:
            self._keys = sorted(self.samples)
        return self._keys

    @property
    def score(self) -> float:
        return max((b.score for b in self.samples.values()), default=1.0)

    def typical(self) -> core.Box:
        """Cadre « typique » (médian), pour l'apprentissage."""
        bs = list(self.samples.values())
        w = int(np.median([b.w for b in bs]))
        h = int(np.median([b.h for b in bs]))
        return core.Box(0, 0, max(1, w), max(1, h), self.score)

    def box_at(self, f: int) -> core.Box | None:
        if not self.samples or not (self.start <= f <= self.end):
            return None
        ks = self.keys()
        i = bisect.bisect_right(ks, f)
        prev = ks[i - 1] if i else None
        nxt = ks[i] if i < len(ks) else None
        if prev == f or nxt is None:
            b = self.samples[prev]
        elif prev is None:
            b = self.samples[nxt]
        else:
            a, c = self.samples[prev], self.samples[nxt]
            if self.manual:
                t = (f - prev) / (nxt - prev)
                b = core.Box(round(a.x + (c.x - a.x) * t), round(a.y + (c.y - a.y) * t),
                             round(a.w + (c.w - a.w) * t), round(a.h + (c.h - a.h) * t))
            else:
                x0, y0 = min(a.x, c.x), min(a.y, c.y)
                x1, y1 = max(a.x + a.w, c.x + c.w), max(a.y + a.h, c.y + c.h)
                b = core.Box(x0, y0, x1 - x0, y1 - y0, max(a.score, c.score))
        return core.Box(b.x, b.y, b.w, b.h, b.score, self.manual, self.kind)


def _match(a: core.Box, b: core.Box) -> float:
    """Ressemblance de deux cadres à deux instants proches (0 = aucun rapport)."""
    iou = core._iou(a, b)
    if iou > 0.2:
        return iou
    ratio = (a.w * a.h) / max(1, b.w * b.h)
    if core._overlap(a, b) > 0.5 and 0.4 < ratio < 2.5:
        return 0.1
    return 0.0


def build_tracks(cands: dict[int, list[core.Box]], threshold: float, step: int, info: VideoInfo,
                 rejected: list[tuple[int, core.Box]] | None = None) -> list[Track]:
    """Relie les détections en pistes (hystérésis : accroche à `threshold`, suivi jusqu'à LOW_SCORE)."""
    frames = sorted(cands)
    low = min(threshold, LOW_SCORE)
    det = {f: core.merge_boxes([b for b in cands[f] if b.score >= low]) for f in frames}
    used: dict[int, set[int]] = {f: set() for f in frames}
    max_gap = max(step, round(MAX_GAP_S * info.fps))   # en images
    tracks: list[Track] = []
    active: list[Track] = []

    for f in frames:
        active = [t for t in active if f - t.keys()[-1] <= max_gap]  # trou trop long : nouvelle piste
        boxes = det[f]
        pairs = sorted(((_match(t.samples[t.keys()[-1]], b), ti, bi)
                        for ti, t in enumerate(active) for bi, b in enumerate(boxes)), reverse=True)
        taken_t: set[int] = set()
        for sc, ti, bi in pairs:
            if sc <= 0:
                break
            if ti in taken_t or bi in used[f]:
                continue
            t = active[ti]
            last = t.keys()[-1]
            if f - last > step:
                t.gaps.append((last, f))
            t.set(f, boxes[bi])
            taken_t.add(ti)
            used[f].add(bi)
        for bi, b in enumerate(boxes):
            if bi not in used[f] and b.score >= threshold:
                t = Track()
                t.set(f, b)
                tracks.append(t)
                active.append(t)
                used[f].add(bi)

    # Prolongation vers l'arrière avec les détections faibles restées libres.
    for t in tracks:
        for f in reversed(frames[:frames.index(t.keys()[0])]):
            first = t.keys()[0]
            if first - f > max_gap:
                break
            best = max(((_match(t.samples[first], b), bi) for bi, b in enumerate(det[f]) if bi not in used[f]),
                       default=(0.0, -1))
            if best[0] > 0:
                if first - f > step:
                    t.gaps.append((f, first))
                t.set(f, det[f][best[1]])
                used[f].add(best[1])

    if rejected:
        tracks = [t for t in tracks if not any(
            rf in t.samples and core._overlap(t.samples[rf], rb) > 0.5 for rf, rb in rejected)]

    hold = max(step, round(HOLD_S * info.fps))
    for t in tracks:
        ks = t.keys()
        t.start, t.end = max(0, ks[0] - hold), min(info.frames - 1, ks[-1] + hold)
        t.gaps.sort()
    return tracks


def boxes_at(tracks: list[Track], f: int) -> list[core.Box]:
    return [b for t in tracks if (b := t.box_at(f)) is not None]


def manual_track(f: int, box: core.Box, kind: str, info: VideoInfo) -> Track:
    """Zone ajoutée à la main à l'image f : par sécurité, jusqu'à la fin de la vidéo."""
    t = Track(manual=True, kind=kind, start=f, end=info.frames - 1)
    t.set(f, core.Box(box.x, box.y, box.w, box.h, 1.0, True, kind))
    return t


# --------------------------------------------------------------------------- #
#  Passages à risque
# --------------------------------------------------------------------------- #
@dataclass
class Risk:
    frame: int
    reason: str


def _near_edge(b: core.Box, info: VideoInfo) -> bool:
    mx, my = EDGE * info.width, EDGE * info.height
    return b.x <= mx or b.y <= my or b.x + b.w >= info.width - mx or b.y + b.h >= info.height - my


def find_risks(tracks: list[Track], info: VideoInfo, step: int) -> list[Risk]:
    """Moments à vérifier en priorité, triés et regroupés."""
    raw: list[Risk] = []
    for t in tracks:
        if t.manual:
            continue
        ks = t.keys()
        for a, b in t.gaps:
            raw.append(Risk((a + b) // 2, "visage perdu un instant puis retrouvé : reste-t-il caché entre les deux ?"))
        if t.score < DOUBT_SCORE:
            best = max(ks, key=lambda k: t.samples[k].score)
            raw.append(Risk(best, "détection peu sûre : est-ce vraiment un visage ?"))
        if ks[0] > step and not _near_edge(t.samples[ks[0]], info):
            raw.append(Risk(max(0, t.start - 1), "un visage apparaît juste après : est-il visible ici, sans cadre ?"))
        if ks[-1] < info.frames - 1 - step and not _near_edge(t.samples[ks[-1]], info):
            raw.append(Risk(min(info.frames - 1, t.end + 1),
                            "un visage vient de disparaître : est-il encore visible ici, sans cadre ?"))
    raw.sort(key=lambda r: r.frame)
    out: list[Risk] = []
    gap = RISK_MERGE_S * info.fps
    for r in raw:
        if out and r.frame - out[-1].frame <= gap:
            if r.reason not in out[-1].reason:
                out[-1].reason += "\n" + r.reason
        else:
            out.append(Risk(r.frame, r.reason))
    return out


# --------------------------------------------------------------------------- #
#  Export
# --------------------------------------------------------------------------- #
def export(src: str, dst: str, tracks: list[Track], mode: str = "flou", shape: str = "rectangle",
           margin: float = 0.3, strength: float = core.SIMPLE_STRENGTH,
           progress: Callable[[float], None] | None = None,
           cancel: Callable[[], bool] | None = None,
           sound: str = "supprimer", ffmpeg: audio.Ffmpeg | None = None,
           voice: audio.VoiceOptions = audio.DEFAULT_VOICE, voice_seed: int | None = None) -> int:
    """Réécrit la vidéo image par image (sans métadonnées), de façon atomique.
    `sound` : « supprimer » (défaut, le plus sûr) ou « voix » (voix déformée selon `voice`, via ffmpeg).
    `voice_seed` : graine du tirage aléatoire, partagée avec l'aperçu (jamais enregistrée).
    Renvoie le nombre d'images écrites."""
    pcm = None
    if sound == "voix":
        voice.check()
        if ffmpeg is None or not ffmpeg.ok:
            raise OSError("voix déformée impossible : " + (ffmpeg.problem if ffmpeg else "ffmpeg indisponible"))
        orig = ffmpeg.extract(src)
        if orig is not None:  # vidéo sans son : rien à déformer
            pcm = audio.disguise_long(orig, rng=np.random.default_rng(voice_seed), opts=voice)
        del orig
    cap = open_capture(src)
    total = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
    fps = _fps(cap)
    root, ext = os.path.splitext(dst)
    tmp = root + ".part" + ext          # l'extension finale choisit le conteneur MP4
    tmp2 = root + ".part2" + ext        # vidéo + son déformé
    writer = None
    n = 0
    try:
        ok, bgr = cap.read()
        if not ok:
            raise OSError("aucune image lisible dans cette vidéo")
        h, w = bgr.shape[:2]
        pad_b, pad_r = h % 2, w % 2      # l'encodeur MPEG-4 veut des dimensions paires
        writer = cv2.VideoWriter(tmp, cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*FOURCC), fps, (w + pad_r, h + pad_b))
        if not writer.isOpened():
            raise OSError("impossible de créer la vidéo de sortie")
        while ok:
            if cancel and cancel():
                raise Cancelled
            if bgr.shape[:2] != (h, w):  # taille qui change en cours de route (rare)
                bgr = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
            boxes = boxes_at(tracks, n)
            if boxes:
                rgb = core.anonymize(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), boxes, mode, shape, margin, strength=strength)
                bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            if pad_b or pad_r:
                bgr = cv2.copyMakeBorder(bgr, 0, pad_b, 0, pad_r, cv2.BORDER_REPLICATE)
            writer.write(bgr)
            n += 1
            if progress and n % 10 == 0:
                progress(min(1.0, n / total))
            ok, bgr = cap.read()
        writer.release()
        writer = None
        check = cv2.VideoCapture(tmp, cv2.CAP_FFMPEG)
        good = check.isOpened() and check.read()[0]
        check.release()
        if not good:
            raise OSError("la vidéo écrite est illisible")
        if pcm is not None:
            if cancel and cancel():
                raise Cancelled
            ffmpeg.mux(tmp, pcm, tmp2)
            os.replace(tmp2, dst)
        else:
            os.replace(tmp, dst)
        return n
    finally:
        if writer is not None:
            writer.release()
        cap.release()
        for p in (tmp, tmp2):
            if os.path.exists(p):
                os.remove(p)
