"""Tests du moteur (pytest). Chaque test reprend un bug ou une exigence remontés en revue."""

import os
import shutil
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402

HAAR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "haar")
RNG = np.random.default_rng(42)


def noise_img(h=400, w=400):
    return RNG.integers(0, 255, (h, w, 3), dtype=np.uint8)


# --------------------------------------------------------------- sécurité --
@pytest.mark.parametrize("mode", core.SAFE_MODES)
@pytest.mark.parametrize("shape", ["rectangle", "ellipse"])
@pytest.mark.parametrize("manual", [False, True])
def test_aucun_pixel_du_visage_intact(mode, shape, manual):
    img = noise_img()
    b = core.Box(150, 150, 100, 100, manual=manual)
    out = core.anonymize(img, [b], mode, shape, margin=0.0, strength=core.SIMPLE_STRENGTH)
    same = (out[150:250, 150:250] == img[150:250, 150:250]).all(axis=2)
    assert same.mean() < 0.01


@pytest.mark.parametrize("mode,shape", [("rond_flou", "ellipse"), ("emoji", "ellipse"), ("noir", "ellipse")])
def test_ellipse_couvre_tout_le_cadre(mode, shape):
    b = core.Box(150, 150, 100, 100)
    e = b.expanded(core.effective_margin(b, 0.0, shape, mode), 1000, 1000)
    mask = core._ellipse_mask(e.w, e.h, 0.12 if mode == "rond_flou" else 0)
    fx, fy = b.x - e.x, b.y - e.y
    assert mask[fy:fy + b.h, fx:fx + b.w].min() >= 0.999


def test_zone_manuelle_a_une_marge():
    b = core.Box(100, 100, 50, 50, manual=True)
    assert core.effective_margin(b, 0.0, "rectangle") >= core.MANUAL_MARGIN


def test_mode_simple_toujours_sur():
    for m in core.SIMPLE_MODES:
        assert core.safety(m, core.SIMPLE_STRENGTH, 0.3)[0] == "ok"


def test_niveaux_de_securite():
    assert core.safety("flou_classique", 1.0)[0] == "danger"
    assert core.safety("bandeau", 1.0)[0] == "danger"
    assert core.safety("pixel", 0.0)[0] == "danger"
    assert core.safety("pixel", 1.0, margin=0.0)[0] == "moyen"  # marge trop faible
    assert core.blocks_for(core.min_safe_strength()) <= core.SAFE_BLOCKS


def test_vraie_difference_entre_0_et_100():
    assert core.blocks_for(0) >= 50 and core.blocks_for(1) <= 3


def test_zone_minuscule_toujours_reduite():
    r = core._destroy(noise_img(6, 6), 12, np.random.default_rng(), 0)
    assert r.shape[0] <= 3 and r.shape[1] <= 3


def test_bruit_non_reproductible():
    img = noise_img()
    b = [core.Box(100, 100, 100, 100)]
    assert not np.array_equal(core.anonymize(img, b, "pixel"), core.anonymize(img, b, "pixel"))


# -------------------------------------------------------- fichiers / I/O --
def test_metadonnees_supprimees(tmp_path):
    src = tmp_path / "exif.jpg"
    ex = Image.Exif()
    ex[0x0110] = "iPhone"
    ex[0x0112] = 6
    ex.get_ifd(0x8825)[2] = (48.0, 51.0, 0.0)
    Image.fromarray(noise_img(200, 300)).save(src, exif=ex)
    rgb = core.load_image(str(src))
    assert rgb.shape[:2] == (300, 200)  # orientation appliquée
    dst = tmp_path / "out.jpg"
    core.save_clean(rgb, str(dst))
    im = Image.open(dst)
    assert dict(im.getexif()) == {} and "exif" not in im.info
    assert not os.path.exists(str(dst) + ".part")


def test_png_16_bits(tmp_path):
    g = np.linspace(0, 65535, 256 * 256).reshape(256, 256).astype(np.uint16)
    Image.fromarray(g).save(tmp_path / "g.png")
    assert abs(core.load_image(str(tmp_path / "g.png")).mean() - 127) < 3


def test_transparence_conservee(tmp_path):
    rgba = np.dstack([noise_img(), np.full((400, 400), 100, np.uint8)])
    Image.fromarray(rgba, "RGBA").save(tmp_path / "t.png")
    rgb, alpha = core.load_image_full(str(tmp_path / "t.png"))
    core.save_clean(core.anonymize(rgb, [core.Box(10, 10, 50, 50)]), str(tmp_path / "o.png"), alpha)
    assert Image.open(tmp_path / "o.png").mode == "RGBA"


def test_image_tres_allongee():
    det = core.FaceDetector(haar_dir=HAAR, models_dir="/nulle/part")
    assert det.detect(np.zeros((1, 5000, 3), np.uint8)) == []
    assert det.detect(np.zeros((5000, 1, 3), np.uint8)) == []


@pytest.mark.parametrize("keep", [False, True])
def test_pas_de_collision_de_noms(tmp_path, keep):
    for p in ["x.tif", "x.png", "a/p.jpg", "b/p.jpg"]:
        (tmp_path / p).parent.mkdir(exist_ok=True)
        Image.fromarray(noise_img(10, 10)).save(tmp_path / p)
    srcs = [str(tmp_path / p) for p in ["x.tif", "x.png", "a/p.jpg", "b/p.jpg"]]
    out = tmp_path / "out"
    out.mkdir()
    (out / "anonyme_0001.png").write_bytes(b"deja la")
    (out / "x_anonyme.png").write_bytes(b"deja la")
    plan = core.plan_outputs(srcs, str(out), keep)
    assert len(set(plan.values())) == 4
    assert not any(os.path.exists(p) for p in plan.values())  # jamais d'écrasement


def test_noms_neutres_sans_date():
    p = core.plan_outputs([os.path.abspath("IMG_20261117_143022.jpg")])
    assert "2026" not in os.path.basename(list(p.values())[0])


def test_sorties_non_reimportees(tmp_path):
    Image.fromarray(noise_img(10, 10)).save(tmp_path / "photo.jpg")
    for n in ["anonyme_0001.jpg", "photo_anonyme.JPG", "anonyme_0002.webp"]:
        Image.fromarray(noise_img(10, 10)).save(tmp_path / n, format="PNG")
    (tmp_path / "anonymise").mkdir()
    Image.fromarray(noise_img(10, 10)).save(tmp_path / "anonymise" / "y.jpg")
    assert [os.path.basename(p) for p in core.list_images(str(tmp_path))] == ["photo.jpg"]


def test_effacement_securise(tmp_path):
    f = tmp_path / "secret.jpg"
    f.write_bytes(b"secret" * 1000)
    core.secure_delete(str(f))
    assert os.listdir(tmp_path) == []


@pytest.mark.skipif(not core.HEIF_OK, reason="pi-heif absent")
def test_heic():
    sample = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "synthetique.heic")
    assert core.load_image(sample).shape == (64, 64, 3)
    assert core.output_ext("x.heic") == ".jpg"


# ------------------------------------------------------------- détecteur --
def test_chemins_accentues(tmp_path):
    d = tmp_path / "Hélène é ü" / "haar"
    shutil.copytree(HAAR, d)
    det = core.FaceDetector(models_dir=str(tmp_path / "rien"), haar_dir=str(d))
    assert det.backend == "haar"


def test_aucun_detecteur_signale():
    det = core.FaceDetector(models_dir="/nulle/part", haar_dir="/nulle/part")
    assert not det.ok and det.problems


def test_modele_altere_refuse(tmp_path):
    (tmp_path / core.YUNET_FILE).write_bytes(b"x" * 300_000)
    det = core.FaceDetector(models_dir=str(tmp_path), haar_dir=HAAR)
    assert det.backend == "haar" and "SHA-256" in det.problems[0]


# ------------------------------------------------- revue v2 (bords, alpha) --
@pytest.mark.parametrize("mode", core.SAFE_MODES)
@pytest.mark.parametrize("shape", ["rectangle", "ellipse"])
@pytest.mark.parametrize("pos", [(0, 150), (320, 0), (0, 0), (320, 320), (395, 395)])
def test_visage_au_bord_entierement_couvert(mode, shape, pos):
    img = noise_img()
    b = core.Box(pos[0], pos[1], min(80, 400 - pos[0]), min(80, 400 - pos[1]))
    out = core.anonymize(img, [b], mode, shape, margin=0.3)
    same = (out[b.y:b.y + b.h, b.x:b.x + b.w] == img[b.y:b.y + b.h, b.x:b.x + b.w]).all(axis=2)
    # Le bruit aléatoire peut, par hasard, redonner la couleur d'origine à un pixel isolé :
    # on tolère 0,2 % (la fuite corrigée en 5.0.0 laissait 4 à 12 % du visage intact).
    assert same.mean() < 0.002


@pytest.mark.parametrize("pos", [(0, 0), (320, 320), (0, 150)])
def test_ellipse_au_bord_alpha_plein_sur_le_visage(pos):
    b = core.Box(pos[0], pos[1], 80, 80)
    for mode, fe in (("rond_flou", 0.12), ("emoji", 0), ("noir", 0)):
        x0, y0, x1, y1 = b.raw_expanded(core.effective_margin(b, 0.3, "ellipse", mode))
        m = core._ellipse_mask(x1 - x0, y1 - y0, fe)
        assert m[b.y - y0:b.y - y0 + b.h, b.x - x0:b.x - x0 + b.w].min() >= 0.999


def test_couleurs_sous_transparence_effacees(tmp_path):
    rgb = noise_img()
    alpha = np.full((400, 400), 255, np.uint8)
    alpha[100:200, 100:200] = 0
    core.save_clean(rgb, str(tmp_path / "a.png"), alpha)
    r = np.array(Image.open(tmp_path / "a.png").convert("RGBA"))
    assert r[100:200, 100:200, :3].max() == 0
    assert (r[300:, 300:, :3] == rgb[300:, 300:]).all()  # le reste est intact


@pytest.mark.parametrize("name,out", [
    ("soirée_anonyme_club.png", False), ("anonyme_vacances.png", False), ("anonymement.jpg", False),
    ("anonyme_0003.jpg", True), ("IMG_1_anonyme.jpg", True), ("IMG_1_anonyme_2.PNG", True)])
def test_filtre_des_sorties_precis(name, out):
    assert core.is_output_file(name) is out


def test_fichiers_ignores_comptes(tmp_path):
    for n in ["a.jpg", "anonyme_0001.jpg", "soirée_anonyme_club.jpg"]:
        Image.fromarray(noise_img(10, 10)).save(tmp_path / n)
    (tmp_path / "anonymise").mkdir()
    Image.fromarray(noise_img(10, 10)).save(tmp_path / "anonymise" / "x.jpg")
    files, skipped = core.scan_images(str(tmp_path))
    assert sorted(os.path.basename(f) for f in files) == ["a.jpg", "soirée_anonyme_club.jpg"]
    assert skipped == 2


def test_modele_altere_signale_comme_tel(tmp_path):
    (tmp_path / core.YUNET_FILE).write_bytes(b"x" * 300_000)
    assert core.FaceDetector(models_dir=str(tmp_path), haar_dir=HAAR).tampered
    assert not core.FaceDetector(models_dir="/nulle/part", haar_dir=HAAR).tampered


# ------------------------------------------------------- scores / candidats --
def test_scores_entre_0_et_1_et_seuil():
    det = core.FaceDetector(haar_dir=HAAR, models_dir="/nulle/part")
    astro = os.path.join(os.path.dirname(HAAR), "..", "test", "astronaut.jpg")
    img = core.load_image(astro) if os.path.exists(astro) else noise_img()
    cands = det.candidates(img)
    assert all(0 <= c.score <= 1 for c in cands)
    strict = det.detect(img, threshold=0.99)
    loose = det.detect(img, threshold=0.2)
    assert len(strict) <= len(loose)
