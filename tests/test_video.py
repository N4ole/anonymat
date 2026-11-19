"""Tests des vidéos (pytest). Vidéos générées : aucune vraie personne."""

import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import video  # noqa: E402

HAAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "haar")
RNG = np.random.default_rng(7)
INFO = video.VideoInfo(fps=30.0, frames=300, width=1000, height=800)


def B(x, y, s=100, score=0.9):
    return core.Box(x, y, s, s, score)


def make_video(path, n=20, w=320, h=240, fps=25):
    """Vidéo de bruit (chaque image différente) : tout pixel intact se voit."""
    wr = cv2.VideoWriter(path, cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert wr.isOpened()
    frames = []
    for _ in range(n):
        f = cv2.GaussianBlur(RNG.integers(0, 255, (h, w, 3), dtype=np.uint8), (3, 3), 0)
        frames.append(f)
        wr.write(f)
    wr.release()
    return frames


def read_all(path):
    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG)
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            return out
        out.append(f)


# ------------------------------------------------------------------ suivi --
def test_trou_de_detection_comble():
    """Visage non détecté pendant ~0,4 s : il reste caché pendant le trou, et c'est signalé."""
    cands = {f: [B(400, 300)] for f in range(0, 300, 3) if not 30 < f < 42}
    t = video.build_tracks(cands, 0.6, 3, INFO)
    assert len(t) == 1
    assert all(t[0].box_at(f) is not None for f in range(0, 300))
    assert t[0].gaps == [(30, 42)]
    assert any("perdu" in r.reason for r in video.find_risks(t, INFO, 3))


def test_long_trou_non_comble():
    cands = {f: [B(400, 300)] for f in range(0, 300, 3) if not 30 < f < 150}
    assert len(video.build_tracks(cands, 0.6, 3, INFO)) == 2


def test_hysteresis_suivi_meme_si_score_baisse():
    """Accroché à 0,9 puis vu à 0,35 (tête tournée) : toujours suivi."""
    cands = {f: [B(400, 300, score=0.9 if f == 0 else 0.35)] for f in range(0, 300, 3)}
    t = video.build_tracks(cands, 0.6, 3, INFO)
    assert len(t) == 1 and t[0].end == 299


def test_detection_faible_isolee_non_accrochee():
    cands = {f: [B(400, 300, score=0.35)] for f in range(0, 300, 3)}
    assert video.build_tracks(cands, 0.6, 3, INFO) == []


def test_suivi_prolonge_vers_l_arriere():
    """Visage vu faiblement de 0 à 87, puis net à 90 : caché dès l'image 0."""
    cands = {f: [B(400, 300, score=0.35 if f < 90 else 0.9)] for f in range(0, 300, 3)}
    t = video.build_tracks(cands, 0.6, 3, INFO)
    assert len(t) == 1 and t[0].start == 0


def test_union_entre_deux_analyses():
    """Visage qui bouge : entre deux analyses, les deux positions sont couvertes."""
    cands = {0: [B(100, 100)], 6: [B(160, 100)]}
    info = video.VideoInfo(30.0, 7, 1000, 800)
    b = video.build_tracks(cands, 0.6, 6, info)[0].box_at(3)
    assert b.x <= 100 and b.x + b.w >= 260


def test_prolongation_avant_et_apres():
    cands = {f: ([B(400, 300)] if 90 <= f <= 150 else []) for f in range(0, 300, 6)}
    t = video.build_tracks(cands, 0.6, 6, INFO)[0]
    assert t.start <= 90 - 6 and t.end >= 150 + 6


def test_piste_supprimee_ne_revient_pas():
    cands = {f: [B(400, 300), B(50, 50, 40)] for f in range(0, 300, 3)}
    rejected = [(0, B(50, 50, 40))]
    t = video.build_tracks(cands, 0.6, 3, INFO, rejected)
    assert len(t) == 1 and t[0].samples[0].x == 400


def test_deux_visages_deux_pistes():
    cands = {f: [B(100, 100), B(600, 300)] for f in range(0, 300, 3)}
    assert len(video.build_tracks(cands, 0.6, 3, INFO)) == 2


def test_zone_manuelle_jusqu_a_la_fin_et_interpolee():
    t = video.manual_track(30, core.Box(0, 0, 100, 100), "visage", INFO)
    assert t.box_at(29) is None and t.box_at(299) is not None
    t.set(60, core.Box(100, 0, 100, 100))
    assert t.box_at(45).x == 50 and t.box_at(45).manual


# ------------------------------------------------------- passages à risque --
def test_risques_detection_peu_sure_et_apparition():
    cands = {f: ([B(400, 300, score=0.5)] if 60 <= f <= 120 else []) for f in range(0, 300, 3)}
    risks = video.find_risks(video.build_tracks(cands, 0.45, 3, INFO), INFO, 3)
    text = " ".join(r.reason for r in risks)
    assert "peu sûre" in text and "apparaît" in text and "disparaître" in text


def test_visage_qui_entre_par_le_bord_pas_signale():
    cands = {f: ([B(0, 300)] if f >= 60 else []) for f in range(0, 300, 3)}
    assert video.find_risks(video.build_tracks(cands, 0.6, 3, INFO), INFO, 3) == []


def test_risques_proches_regroupes():
    cands = {f: [B(400, 300, score=0.55 if f == 39 else 0.5)] for f in range(0, 300, 3) if not 30 < f < 36}
    risks = video.find_risks(video.build_tracks(cands, 0.45, 3, INFO), INFO, 3)
    assert len([r for r in risks if r.frame < 60]) == 1


# ----------------------------------------------------------------- export --
@pytest.mark.parametrize("mode", core.SAFE_MODES)
def test_export_visage_detruit_sur_chaque_image(tmp_path, mode):
    src, dst = str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4")
    frames = make_video(src)
    t = video.manual_track(0, core.Box(100, 80, 80, 80), "visage", video.VideoInfo(25, 20, 320, 240))
    assert video.export(src, dst, [t], mode, strength=core.SIMPLE_STRENGTH) == 20
    out = read_all(dst)
    assert len(out) == 20
    for a, b in zip(frames, out, strict=True):
        face_a = a[80:160, 100:180].astype(int)
        face_b = b[80:160, 100:180].astype(int)
        if face_b.std() < 1:  # zone uniforme (carré noir) : rien du visage
            continue
        corr = np.corrcoef(face_a.ravel(), face_b.ravel())[0, 1]
        assert abs(corr) < 0.3, "le visage d'origine reste lisible"
    # hors de la zone, l'image est conservée (au bruit de compression près)
    assert np.corrcoef(frames[5][:, 260:].astype(int).ravel(), out[5][:, 260:].astype(int).ravel())[0, 1] > 0.6


def test_export_dimensions_impaires_et_chemin_accentue(tmp_path):
    d = tmp_path / "Vidéos été"
    d.mkdir()
    src = str(d / "clip à moi.avi")
    wr = cv2.VideoWriter(src, cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"MJPG"), 25, (321, 241))
    for _ in range(5):
        wr.write(RNG.integers(0, 255, (241, 321, 3), dtype=np.uint8))
    wr.release()
    dst = str(d / "anonyme_0001.mp4")
    assert video.export(src, dst, []) == 5
    out = read_all(dst)
    assert len(out) == 5 and out[0].shape[0] % 2 == 0 and out[0].shape[1] % 2 == 0
    assert sorted(os.listdir(d)) == ["anonyme_0001.mp4", "clip à moi.avi"]  # pas de .part oublié


def test_export_annule_ne_laisse_rien(tmp_path):
    src, dst = str(tmp_path / "in.mp4"), str(tmp_path / "out.mp4")
    make_video(src)
    with pytest.raises(video.Cancelled):
        video.export(src, dst, [], cancel=lambda: True)
    assert sorted(os.listdir(tmp_path)) == ["in.mp4"]


def test_video_illisible(tmp_path):
    p = tmp_path / "faux.mp4"
    p.write_bytes(b"pas une video")
    with pytest.raises(OSError):
        video.probe(str(p))


# ---------------------------------------------------------------- analyse --
def test_analyse_compte_les_images_et_respecte_le_pas(tmp_path):
    src = str(tmp_path / "in.mp4")
    make_video(src, n=23)
    det = core.FaceDetector(haar_dir=HAAR, models_dir="/nulle/part")
    info, step, cands = video.analyse(src, det, "precise")
    assert info.frames == 23 and step == round(25 / video.RATES["precise"])
    assert sorted(cands) == list(range(0, 23, step))


def test_pas_d_analyse_selon_la_duree():
    court = video.VideoInfo(30.0, 30 * 60, 1920, 1080)
    long = video.VideoInfo(30.0, 30 * 600, 1920, 1080)
    assert video.analysis_step(court) == 3 and video.analysis_step(long) == 8


# ------------------------------------------------------- sécurité / fichiers --
def test_seuil_video_plus_strict():
    s = 0.6  # ~ 10 blocs : sûr pour une photo, pas pour une vidéo
    assert 8 < core.blocks_for(s) <= 12
    assert core.safety("flou", s)[0] == "ok"
    assert core.safety("flou", s, video=True)[0] == "moyen"
    assert core.min_safe_strength(video=True) > core.min_safe_strength()


@pytest.mark.parametrize("mode", core.SIMPLE_MODES)
def test_mode_simple_sur_aussi_en_video(mode):
    assert core.safety(mode, core.SIMPLE_STRENGTH, 0.3, video=True)[0] == "ok"


def test_videos_trouvees_et_sorties_ignorees(tmp_path):
    (tmp_path / "clip.MOV").write_bytes(b"x")
    (tmp_path / "anonyme_0001.mp4").write_bytes(b"x")
    files, skipped = core.scan_images(str(tmp_path))
    assert [os.path.basename(f) for f in files] == ["clip.MOV"] and skipped == 1
    assert core.output_ext("clip.MOV") == ".mp4" and core.is_video("a.mkv") and not core.is_video("a.jpg")
