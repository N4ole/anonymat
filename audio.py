"""Son des vidéos : extraction, déformation de la voix, remontage (via ffmpeg.exe, LGPL).

ffmpeg.exe est une build MINIMALE compilée par la CI depuis les sources officielles
(.github/scripts/build_ffmpeg.sh : son seulement, LGPL-2.1, aucun encodeur vidéo).
Son empreinte SHA-256 (ffmpeg.exe.sha256, écrite à la compilation) est vérifiée au lancement.

Déformation de la voix (`disguise`) – conçue pour être NON réversible :
  1. l'enveloppe spectrale lissée (les formants : voyelles, consonnes) est gardée,
     pour que les paroles restent compréhensibles ;
  2. elle est décalée en fréquence d'un facteur tiré au hasard, qui dérive lentement
     dans le temps : le timbre change, et aucun facteur unique ne l'annule ;
  3. la vibration des cordes vocales est remplacée par un bruit aléatoire (voix
     chuchotée) : la hauteur et la mélodie de la voix sont DÉTRUITES, et la phase
     d'origine est jetée. Un simple changement de hauteur, lui, s'annule à l'envers.

Limite assumée (bandeau orange) : le rythme, l'accent, les mots prononcés (noms,
lieux) et les bruits de fond restent. Seule la suppression du son est « anonymat total ».
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass

import numpy as np

import core

SR = 24000                      # fréquence d'échantillonnage de travail (voix : largement suffisant)
FFMPEG_FILE = "ffmpeg.exe"
SOUND_MODES = {"supprimer": "🔇 Son supprimé", "voix": "🗣 Voix déformée"}


# --------------------------------------------------------------------------- #
#  Déformation de la voix
# --------------------------------------------------------------------------- #
def _smooth(v: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(v) < 2:
        return v
    k = np.ones(n) / n
    return np.convolve(np.pad(v, (n // 2, n - 1 - n // 2), mode="edge"), k, mode="valid")


@dataclass(frozen=True)
class VoiceOptions:
    """Réglages de la voix déformée (combinables). Au moins une des deux premières est
    obligatoire : ce sont elles qui DÉTRUISENT la hauteur de la voix d'origine."""

    chuchote: bool = True       # cordes vocales -> bruit (voix chuchotée)
    robot: bool = False         # cordes vocales -> bourdonnement à hauteur fixe (sans mélodie)
    timbre: str = "hasard"      # hasard | grave | aigu
    fort: bool = False          # décalage du timbre plus marqué
    telephone: bool = False     # seulement 300-3 400 Hz (moins d'indices sur la personne)

    def check(self) -> None:
        if not (self.chuchote or self.robot):
            raise ValueError("choisis au moins « Chuchotée » ou « Robot » (sinon la hauteur de voix reste)")
        if self.timbre not in TIMBRES:
            raise ValueError(f"timbre inconnu : {self.timbre}")


TIMBRES = {"hasard": "Au hasard", "grave": "Plus grave", "aigu": "Plus aigu"}
DEFAULT_VOICE = VoiceOptions()
# Décalage des formants : (grave), (aigu) ; « fort » plus marqué.
SHIFTS = {False: ((0.80, 0.87), (1.15, 1.25)), True: ((0.66, 0.75), (1.28, 1.40))}


def pick_shift(rng: np.random.Generator, opts: VoiceOptions = DEFAULT_VOICE) -> float:
    """Facteur de décalage des formants : nettement plus grave OU plus aigu."""
    grave, aigu = SHIFTS[opts.fort]
    down = rng.random() < 0.5 if opts.timbre == "hasard" else opts.timbre == "grave"
    return rng.uniform(*(grave if down else aigu))


def pick_f0(rng: np.random.Generator) -> float:
    """Hauteur fixe de la voix robot (Hz), sans rapport avec la voix d'origine."""
    return rng.uniform(95.0, 135.0)


def disguise(x: np.ndarray, sr: int = SR, rng: np.random.Generator | None = None,
             base: float | None = None, opts: VoiceOptions = DEFAULT_VOICE,
             f0: float | None = None, offset: int = 0) -> np.ndarray:
    """Voix déformée (mono, flottants -1..1) ; même longueur en sortie.
    `offset` : position du morceau dans la piste (voix robot continue d'un morceau à l'autre)."""
    opts.check()
    rng = rng or np.random.default_rng()  # graine aléatoire : rien de reproductible
    x = np.asarray(x, dtype=np.float64).ravel()
    n = len(x)
    if n == 0:
        return x.astype(np.float32)
    n_fft = 1024 if sr >= 32000 else 512
    hop = n_fft // 4
    win = np.hanning(n_fft + 1)[:-1]
    xp = np.pad(x, (n_fft, n_fft + hop))
    frames = np.lib.stride_tricks.sliding_window_view(xp, n_fft)[::hop]
    X = np.fft.rfft(frames * win, axis=1)
    T, F = X.shape

    # 1. enveloppe spectrale (cepstre lissé : la structure harmonique, donc la hauteur, disparaît)
    cep = np.fft.irfft(np.log(np.abs(X) + 1e-7), n=n_fft, axis=1)
    lift = max(8, int(0.0018 * sr))      # < période de la voix la plus aiguë (~2,5 ms)
    cep[:, lift:n_fft - lift + 1] = 0.0
    env = np.exp(np.fft.rfft(cep, axis=1).real)

    # 2. décalage des formants : facteur tiré au hasard, qui dérive
    base = pick_shift(rng, opts) if base is None else base
    drift = _smooth(rng.standard_normal(T), max(1, int(0.6 * sr / hop)))
    drift = drift / (np.abs(drift).max() + 1e-9) * 0.05
    alpha = base * (1.0 + drift)
    src = np.arange(F)[None, :] / alpha[:, None]
    i0 = np.clip(np.floor(src).astype(int), 0, F - 2)
    fr = np.clip(src - i0, 0.0, 1.0)
    rows = np.arange(T)[:, None]
    env_w = env[rows, i0] * (1 - fr) + env[rows, i0 + 1] * fr
    if opts.telephone:
        f = np.fft.rfftfreq(n_fft, 1 / sr)
        band = np.clip((f - 200) / 100, 0, 1) * np.clip((3600 - f) / 200, 0, 1)
        env_w = env_w * band[None, :]

    # 3. nouvelle excitation (la vibration d'origine est jetée) ; même énergie, image par image
    E = np.zeros((T, F), complex)
    if opts.chuchote:
        E += np.exp(1j * rng.uniform(0, 2 * np.pi, (T, F)))
    if opts.robot:
        period = sr / (pick_f0(rng) if f0 is None else f0)
        k = np.arange(len(xp)) + offset - n_fft
        pulses = (np.diff(np.floor(k / period), prepend=np.floor((k[0] - 1) / period)) > 0).astype(float)
        P = np.fft.rfft(np.lib.stride_tricks.sliding_window_view(pulses, n_fft)[::hop] * win, axis=1)
        E += P / (np.sqrt((np.abs(P) ** 2).mean(1, keepdims=True)) + 1e-9)
    Y = env_w * E
    e_in = (np.abs(X) ** 2).sum(1)
    e_out = (np.abs(Y) ** 2).sum(1) + 1e-12
    gain = _smooth(np.sqrt(e_in / e_out), 3)
    Y *= gain[:, None]

    # reconstruction (addition-recouvrement)
    y_frames = np.fft.irfft(Y, n=n_fft, axis=1) * win
    out = np.zeros(len(xp))
    norm = np.zeros(len(xp))
    for i in range(T):
        out[i * hop:i * hop + n_fft] += y_frames[i]
        norm[i * hop:i * hop + n_fft] += win ** 2
    out = out / np.maximum(norm, 1e-6)
    y = out[n_fft:n_fft + n]
    peak = np.abs(y).max()
    if peak > 0.98:
        y *= 0.98 / peak
    return y.astype(np.float32)


def disguise_long(x: np.ndarray, sr: int = SR, rng: np.random.Generator | None = None,
                  opts: VoiceOptions = DEFAULT_VOICE, chunk_s: float = 30.0, fade_s: float = 0.25) -> np.ndarray:
    """Comme `disguise`, par tranches (mémoire bornée pour les longues vidéos), même timbre
    et même hauteur robot partout, avec un fondu enchaîné entre deux tranches.
    Avec la même graine (`rng`), le résultat est identique : l'aperçu = l'export."""
    opts.check()
    rng = rng or np.random.default_rng()
    x = np.asarray(x, dtype=np.float32).ravel()
    base, f0 = pick_shift(rng, opts), pick_f0(rng)
    chunk, fade = int(chunk_s * sr), int(fade_s * sr)
    if len(x) <= chunk + fade:
        return disguise(x, sr, rng, base, opts, f0)
    out = np.zeros(len(x), np.float32)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    for s in range(0, len(x), chunk):
        a, b = max(0, s - fade), min(len(x), s + chunk)
        y = disguise(x[a:b], sr, rng, base, opts, f0, offset=a)
        if a < s:  # fondu avec la fin de la tranche précédente
            y[:fade] *= ramp
            out[a:s] *= ramp[::-1]
        out[a:b] += y
    return out


# --------------------------------------------------------------------------- #
#  ffmpeg.exe (LGPL, empreinte vérifiée)
# --------------------------------------------------------------------------- #
class Ffmpeg:
    """ffmpeg.exe embarqué, refusé si son empreinte ne correspond pas (programme altéré)."""

    def __init__(self, folder: str | None = None, sha256: str | None = None) -> None:
        self.path: str | None = None
        self.tampered = False
        self.problem = ""
        p = os.path.join(folder or core.resource_path("ffmpeg"), FFMPEG_FILE)
        if not os.path.isfile(p):
            self.problem = "ffmpeg absent de ce programme : le son des vidéos ne peut qu'être supprimé."
            return
        expected = sha256
        if expected is None:
            try:
                with open(p + ".sha256", encoding="ascii") as f:
                    expected = f.read().strip().lower()
            except OSError:
                expected = ""
        if len(expected) != 64:
            self.tampered = True
            self.problem = "ffmpeg refusé : empreinte de sécurité absente."
            return
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != expected:
            self.tampered = True
            self.problem = "ffmpeg refusé : empreinte SHA-256 incorrecte (fichier corrompu ou modifié)."
            return
        self.path = p

    @property
    def ok(self) -> bool:
        return self.path is not None

    def _run(self, args: list[str], data: bytes | None = None) -> subprocess.CompletedProcess:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # pas de console qui clignote
        return subprocess.run([self.path, "-nostdin", "-hide_banner", "-v", "error", *args],
                              input=data, capture_output=True, creationflags=flags, check=False)

    def extract(self, src: str, start: float = 0.0, duration: float | None = None, sr: int = SR) -> np.ndarray | None:
        """Première piste son, en mono -> flottants ; None si la vidéo n'a pas de son."""
        args = ["-ss", f"{start:.3f}"] if start > 0 else []
        args += ["-i", src, "-map", "0:a:0?", "-vn", "-sn", "-dn"]
        if duration is not None:
            args += ["-t", f"{duration:.3f}"]
        r = self._run(args + ["-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"])
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", "replace")
            if "does not contain any stream" in err or "matches no streams" in err:
                return None
            raise OSError(f"lecture du son impossible : {err.strip()[-300:]}")
        if not r.stdout:
            return None
        return np.frombuffer(r.stdout, dtype="<f4").copy()

    def mux(self, video_only: str, pcm: np.ndarray, dst: str, sr: int = SR) -> None:
        """Remonte la vidéo (copiée telle quelle) avec le son fourni, sans aucune métadonnée."""
        r = self._run(["-y", "-i", video_only, "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
                       "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                       "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
                       "-flags:a", "+bitexact", "-movflags", "+faststart", dst],
                      np.asarray(pcm, dtype="<f4").tobytes())
        if r.returncode != 0 or not os.path.isfile(dst):
            raise OSError(f"remontage du son impossible : {r.stderr.decode('utf-8', 'replace').strip()[-300:]}")


def wav_bytes(pcm: np.ndarray, sr: int = SR) -> bytes:
    """WAV 16 bits mono (pour écouter un extrait de voix déformée)."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()
