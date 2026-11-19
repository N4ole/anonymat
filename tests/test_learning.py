"""Tests de l'apprentissage local."""

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import learning  # noqa: E402

RNG = np.random.default_rng(0)


def _face():
    return float(np.clip(RNG.normal(0.85, 0.08), 0, 1)), int(RNG.uniform(60, 300))


def _fp():
    return float(np.clip(RNG.normal(0.55, 0.1), 0, 1)), int(RNG.uniform(15, 45))


def test_le_seuil_ne_monte_jamais():
    L = learning.Learner(None)
    for s in (0.95, 0.9, 0.99):  # visages « ratés » mais très sûrs : aucune raison de monter
        L.record("rate", "yunet", s, 80, 90, 1600, 1200)
    assert L.threshold("yunet", 0.49) == 0.49


def test_le_seuil_descend_pour_les_visages_rates():
    L = learning.Learner(None)
    for s in (0.41, 0.38):
        L.record("rate", "yunet", s, 80, 90, 1600, 1200)
    t = L.threshold("yunet", 0.49)
    assert learning.THRESHOLD_FLOOR <= t < 0.38


def test_plancher_et_visages_introuvables():
    L = learning.Learner(None)
    for s in (0.21, 0.22, 0.05, 0.0):
        L.record("rate", "yunet", s, 80, 90, 1600, 1200)
    assert L.threshold("yunet", 0.49) >= learning.THRESHOLD_FLOOR
    assert L.unreachable_misses("yunet") == 2


def test_classifieur_repere_les_fausses_detections():
    L = learning.Learner(None)
    assert L.p_false("yunet", 0.5, 30, 30, 1600, 1200) is None  # rien appris
    for _ in range(30):
        s, w = _face()
        L.record("ok", "yunet", s, w, w, 1600, 1200)
        s, w = _fp()
        L.record("faux", "yunet", s, w, w, 1600, 1200)
    assert L.trained("yunet")
    assert L.p_false("yunet", 0.55, 25, 25, 1600, 1200) > 0.5
    assert L.p_false("yunet", 0.9, 200, 220, 1600, 1200) < 0.5


def test_detecteurs_separes():
    L = learning.Learner(None)
    for s in (0.3, 0.32):
        L.record("rate", "haar", s, 80, 90, 1600, 1200)
    assert L.threshold("yunet", 0.49) == 0.49


def test_stockage_uniquement_des_nombres(tmp_path):
    p = tmp_path / "a.json"
    L = learning.Learner(str(p))
    L.record("faux", "haar", 0.3, 20, 22, 4000, 3000)
    L.save()
    data = json.loads(p.read_text())
    assert set(data["events"][0]) == {"k", "b", "s", "r", "a"}
    assert all(isinstance(v, (int, float)) for k, v in data["events"][0].items() if k not in ("k", "b"))
    assert learning.Learner(str(p)).events == L.events  # relecture


def test_lecture_seule_et_plafond(tmp_path):
    p = tmp_path / "a.json"
    L = learning.Learner(str(p))
    L.read_only = True
    L.record("faux", "haar", 0.3, 20, 22, 4000, 3000)
    L.save()
    assert not p.exists() and L.events == []
    L.read_only = False
    for _ in range(learning.MAX_EVENTS + 50):
        L.record("ok", "haar", 0.9, 100, 100, 1000, 1000)
    assert len(L.events) == learning.MAX_EVENTS


def test_fichier_corrompu_ignore(tmp_path):
    p = tmp_path / "a.json"
    p.write_text('{"version": 1, "events": [{"k": "rm -rf", "b": 1}, "x", {"k": "ok", "b": "haar", "s": 9, "r": 1, "a": 1}]}')
    assert learning.Learner(str(p)).events == []
    p.write_text("pas du json")
    assert learning.Learner(str(p)).events == []


def test_oubli(tmp_path):
    p = tmp_path / "a.json"
    L = learning.Learner(str(p))
    L.record("ok", "haar", 0.9, 100, 100, 1000, 1000)
    L.save()
    L.reset()
    assert not p.exists() and L.events == []


def test_oublier_meme_si_fichier_verrouille(tmp_path, monkeypatch):
    """« Oublier l'apprentissage » doit tenir même si le fichier ne peut pas être supprimé."""
    p = str(tmp_path / "a.json")
    L = learning.Learner(p)
    L.record("faux", "yunet", 0.5, 30, 30, 1000, 800)
    L.save()

    def refuse(_path):
        raise PermissionError("fichier verrouillé")

    monkeypatch.setattr(learning.os, "remove", refuse)
    L.read_only = True  # même en mode terrain, l'oubli doit être effectif
    L.reset()
    assert L.read_only
    assert learning.Learner(p).events == []
