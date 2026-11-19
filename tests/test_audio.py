"""Tests du son des vidéos (pytest). Voix de synthèse : aucune vraie personne."""

import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audio  # noqa: E402
import core  # noqa: E402
import video  # noqa: E402

SR = audio.SR
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FF = audio.Ffmpeg(os.path.join(ROOT, "ffmpeg"))
needs_ffmpeg = pytest.mark.skipif(not FF.ok, reason="ffmpeg.exe absent (python tools_model.py)")


def voyelle(seconds=1.0, f0=140.0, sr=SR):
    """« aaa » de synthèse : impulsions des cordes vocales (avec vibrato) filtrées par 3 formants."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    phase = np.cumsum(f0 * (1 + 0.02 * np.sin(2 * np.pi * 5 * t)) / sr)
    src = (np.diff(np.floor(phase), prepend=0) > 0).astype(float)
    y = np.zeros(n)
    for fc, bw in ((700, 90), (1200, 110), (2500, 160)):
        r = np.exp(-np.pi * bw / sr)
        a1, a2 = -2 * r * np.cos(2 * np.pi * fc / sr), r * r
        out, y1, y2 = np.zeros(n), 0.0, 0.0
        for i, v in enumerate(src):
            y0 = v - a1 * y1 - a2 * y2
            out[i], y2, y1 = y0, y1, y0
        y += out
    return (0.5 * y / np.abs(y).max()).astype(np.float32)


def periodicite(x, sr=SR):
    """Force de la hauteur de voix : autocorrélation maximale entre 2,5 et 14 ms (1 = voix pure)."""
    vals = []
    n = int(0.04 * sr)
    for s in range(0, len(x) - n, n):
        f = x[s:s + n] - x[s:s + n].mean()
        ac = np.correlate(f, f, "full")[n - 1:]
        if ac[0] > 1e-9:
            vals.append((ac[int(sr / 400):int(sr / 70)] / ac[0]).max())
    return float(np.median(vals))


def centroide(x, sr=SR):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    m = f < 5000
    return float((f[m] * spec[m]).sum() / spec[m].sum())


VOIX = voyelle()


def test_hauteur_de_voix_detruite():
    assert periodicite(VOIX) > 0.7
    assert periodicite(audio.disguise(VOIX)) < 0.35  # niveau d'un bruit pur (~0,2)


@pytest.mark.parametrize("seed", range(6))
def test_timbre_decale(seed):
    ratio = centroide(audio.disguise(VOIX, rng=np.random.default_rng(seed))) / centroide(VOIX)
    assert abs(ratio - 1) > 0.08


def test_non_reproductible_et_sans_lien_avec_l_onde_d_origine():
    a, b = audio.disguise(VOIX), audio.disguise(VOIX)
    assert abs(np.corrcoef(a, b)[0, 1]) < 0.2
    assert abs(np.corrcoef(a, VOIX)[0, 1]) < 0.2


def test_silences_gardes_longueur_et_amplitude():
    x = np.concatenate([VOIX, np.zeros(SR, np.float32), VOIX])
    y = audio.disguise(x)
    assert len(y) == len(x) and np.isfinite(y).all() and np.abs(y).max() <= 1
    parle = np.sqrt((y[:SR] ** 2).mean())
    silence = np.sqrt((y[SR + 2000:2 * SR - 2000] ** 2).mean())
    assert silence < 0.02 * parle
    assert 0.3 < parle / np.sqrt((VOIX ** 2).mean()) < 3  # volume comparable


def test_son_vide_ou_tres_court():
    assert len(audio.disguise(np.zeros(0))) == 0
    assert len(audio.disguise(np.ones(10, np.float32) * 0.1)) == 10


# ------------------------------------------------------------------ ffmpeg --
def test_ffmpeg_absent_signale(tmp_path):
    f = audio.Ffmpeg(str(tmp_path))
    assert not f.ok and not f.tampered and "absent" in f.problem


def test_ffmpeg_modifie_refuse(tmp_path):
    (tmp_path / audio.FFMPEG_FILE).write_bytes(b"programme pirate")
    (tmp_path / (audio.FFMPEG_FILE + ".sha256")).write_text("0" * 64)
    f = audio.Ffmpeg(str(tmp_path))
    assert not f.ok and f.tampered


def test_ffmpeg_sans_empreinte_refuse(tmp_path):
    (tmp_path / audio.FFMPEG_FILE).write_bytes(b"programme inconnu")
    f = audio.Ffmpeg(str(tmp_path))
    assert not f.ok and f.tampered and "absente" in f.problem


def test_ffmpeg_empreinte_correcte_accepte(tmp_path):
    import hashlib
    data = b"faux ffmpeg"
    (tmp_path / audio.FFMPEG_FILE).write_bytes(data)
    (tmp_path / (audio.FFMPEG_FILE + ".sha256")).write_text(hashlib.sha256(data).hexdigest() + "\n")
    assert audio.Ffmpeg(str(tmp_path)).ok


def test_wav_extrait():
    w = audio.wav_bytes(VOIX)
    assert w[:4] == b"RIFF" and len(w) == 44 + 2 * len(VOIX)


def _video_avec_son(path, seconds=2.0, fps=25):
    muette = str(path) + ".muette.mp4"
    wr = cv2.VideoWriter(muette, cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
    for k in range(int(seconds * fps)):
        wr.write(np.random.default_rng(k).integers(0, 255, (240, 320, 3), dtype=np.uint8))
    wr.release()
    FF.mux(muette, np.tile(VOIX, int(np.ceil(seconds))), str(path))
    os.remove(muette)


@needs_ffmpeg
def test_video_sans_son(tmp_path):
    src = str(tmp_path / "muette.mp4")
    cv2.VideoWriter(src, cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), 25, (64, 48)).release()
    video_ok = tmp_path / "v.mp4"
    wr = cv2.VideoWriter(str(video_ok), cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), 25, (64, 48))
    for _ in range(5):
        wr.write(np.zeros((48, 64, 3), np.uint8))
    wr.release()
    assert FF.extract(str(video_ok)) is None


@needs_ffmpeg
def test_export_voix_deformee(tmp_path):
    src, dst = tmp_path / "clip été.mp4", tmp_path / "anonyme_0001.mp4"
    _video_avec_son(src)
    orig = FF.extract(str(src))
    assert orig is not None and periodicite(orig) > 0.6
    t = video.manual_track(0, core.Box(100, 80, 80, 80), "visage", video.VideoInfo(25, 50, 320, 240))
    assert video.export(str(src), str(dst), [t], sound="voix", ffmpeg=FF) == 50
    out = FF.extract(str(dst))
    assert out is not None and abs(len(out) - len(orig)) < 0.1 * SR
    assert periodicite(out) < 0.35, "la hauteur de la voix d'origine reste audible"
    assert sorted(os.listdir(tmp_path)) == ["anonyme_0001.mp4", "clip été.mp4"]  # aucun fichier temporaire


@needs_ffmpeg
def test_export_son_supprime_par_defaut(tmp_path):
    src, dst = tmp_path / "clip.mp4", tmp_path / "out.mp4"
    _video_avec_son(src)
    video.export(str(src), str(dst), [], ffmpeg=FF)
    assert FF.extract(str(dst)) is None


@needs_ffmpeg
def test_extrait_pour_ecoute(tmp_path):
    src = tmp_path / "clip.mp4"
    _video_avec_son(src)
    part = FF.extract(str(src), start=0.5, duration=1.0)
    assert abs(len(part) - SR) < 0.05 * SR


def test_longue_piste_par_tranches():
    x = np.tile(VOIX, 5)
    y = audio.disguise_long(x, chunk_s=1.5, fade_s=0.1)
    assert len(y) == len(x) and np.isfinite(y).all()
    assert periodicite(y) < 0.35
    # pas de « trou » ni de saut de volume aux raccords
    env = np.sqrt(np.convolve(y ** 2, np.ones(480) / 480, "same"))[2400:-2400]
    assert env.min() > 0.2 * np.median(env)


@pytest.mark.parametrize("mode", core.SIMPLE_MODES)
def test_voix_deformee_jamais_verte(mode):
    assert core.safety(mode, core.SIMPLE_STRENGTH, 0.3, video=True)[0] == "ok"
    level, msg = core.safety(mode, core.SIMPLE_STRENGTH, 0.3, video=True, voice=True)
    assert level == "moyen" and "Son supprimé" in msg


# ---------------------------------------------------------------- options --
def hauteur(x, sr=SR):
    """Hauteur dominante (Hz) par autocorrélation sur toute la piste."""
    x = x - x.mean()
    ac = np.correlate(x[:sr // 2], x[:sr // 2], "full")[sr // 2 - 1:]
    lo, hi = int(sr / 400), int(sr / 70)
    return sr / (lo + int(np.argmax(ac[lo:hi])))


def test_robot_hauteur_fixe_sans_rapport_avec_la_voix():
    """Voix robot : deux voix de hauteurs différentes donnent la MÊME hauteur (mélodie détruite)."""
    opts = audio.VoiceOptions(chuchote=False, robot=True)
    grave = audio.disguise_long(voyelle(f0=110), rng=np.random.default_rng(3), opts=opts)
    aigue = audio.disguise_long(voyelle(f0=210), rng=np.random.default_rng(3), opts=opts)
    assert abs(hauteur(grave) - hauteur(aigue)) < 3
    assert 90 < hauteur(grave) < 140


def test_robot_et_chuchote_combinables():
    y = audio.disguise(VOIX, opts=audio.VoiceOptions(chuchote=True, robot=True))
    assert np.isfinite(y).all() and len(y) == len(VOIX)


def test_aucune_excitation_refusee():
    with pytest.raises(ValueError):
        audio.disguise(VOIX, opts=audio.VoiceOptions(chuchote=False, robot=False))


def test_effet_telephone_coupe_les_aigus():
    y = audio.disguise(VOIX, opts=audio.VoiceOptions(telephone=True))
    spec = np.abs(np.fft.rfft(y)) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    assert spec[f > 4000].sum() < 0.02 * spec.sum()
    assert spec[f < 150].sum() < 0.02 * spec.sum()


@pytest.mark.parametrize("seed", range(4))
def test_timbre_grave_aigu_et_fort(seed):
    def ratio(**kw):
        y = audio.disguise(VOIX, rng=np.random.default_rng(seed), opts=audio.VoiceOptions(**kw))
        return centroide(y) / centroide(VOIX)
    assert ratio(timbre="grave") < 0.92 and ratio(timbre="aigu") > 1.08
    assert ratio(timbre="grave", fort=True) < ratio(timbre="grave")
    assert ratio(timbre="aigu", fort=True) > ratio(timbre="aigu")


def test_meme_graine_meme_son():
    """L'aperçu et l'export partagent une graine (gardée en mémoire seulement) : même résultat."""
    x = np.tile(VOIX, 3)
    a = audio.disguise_long(x, rng=np.random.default_rng(9), chunk_s=1.0, fade_s=0.1)
    b = audio.disguise_long(x, rng=np.random.default_rng(9), chunk_s=1.0, fade_s=0.1)
    assert np.array_equal(a, b)
