"""Apprentissage local à partir des corrections de l'utilisateur.

Pourquoi pas un ré-entraînement du réseau YuNet ? Il faudrait garder des photos de
visages pour s'entraîner dessus (contraire au but du logiciel), embarquer PyTorch
(+ plusieurs centaines de Mo) et des heures de calcul. À la place, une couche légère
au-dessus du détecteur apprend de chaque correction :

  * « ok »    : zone détectée automatiquement, conservée sur une photo vérifiée ;
  * « faux »  : zone détectée automatiquement, supprimée par l'utilisateur ;
  * « raté »  : visage ajouté à la main ; on mémorise le score que le détecteur lui
                donnait (0 s'il ne l'avait pas vu du tout).

Ce qui est mémorisé : UNIQUEMENT des nombres (score de confiance, taille relative,
proportions, détecteur utilisé). Jamais d'image, de position, de nom de fichier ni de date.

Ce qui est appris :
  1. le SEUIL de détection, qui ne peut que DESCENDRE (on trouve plus de visages,
     jamais moins) quand des visages avaient été vus mais jugés trop peu sûrs ;
  2. un petit classifieur (régression logistique) qui reconnaît les fausses détections
     typiques de l'utilisateur. Il ne fait que les SIGNALER (« ? ») : il ne cache
     jamais une zone tout seul, car un visage oublié est bien pire qu'un cadre en trop.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np

VERSION = 1
MAX_EVENTS = 3000
THRESHOLD_FLOOR = 0.20       # le seuil appris ne descend jamais en dessous
THRESHOLD_MARGIN = 0.05      # marge sous le score du visage raté le plus faible
MIN_MISSES = 2               # nb de visages ratés « rattrapables » avant d'ajuster le seuil
MIN_PER_CLASS = 5            # nb mini de « ok » ET de « faux » avant d'entraîner le classifieur
FALSE_PROBA = 0.5            # au-dessus : la zone est signalée « ? »


def features(score: float, rel: float, aspect: float) -> np.ndarray:
    """Caractéristiques NON identifiantes d'une zone."""
    lr = math.log(max(rel, 1e-4))
    la = math.log(max(aspect, 1e-2))
    return np.array([score, lr, la, score * lr, score * score], dtype=np.float64)


class Learner:
    def __init__(self, path: str | None) -> None:
        self.path = path
        self.events: list[dict] = []
        self.read_only = False
        self._models: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray] | None] = {}
        self._load()

    # ------------------------------------------------------------ stockage --
    def _load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == VERSION:
                self.events = [e for e in data.get("events", []) if self._valid(e)][-MAX_EVENTS:]
        except Exception:
            self.events = []

    @staticmethod
    def _valid(e: dict) -> bool:
        try:
            return (e["k"] in ("ok", "faux", "rate") and isinstance(e["b"], str)
                    and 0 <= float(e["s"]) <= 1 and 0 < float(e["r"]) <= 1 and 0 < float(e["a"]) < 20)
        except Exception:
            return False

    def save(self) -> None:
        if not self.path or self.read_only:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": VERSION, "events": self.events[-MAX_EVENTS:]}, f)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def reset(self) -> None:
        self.events = []
        self._models = {}
        if self.path and os.path.exists(self.path):
            try:
                os.remove(self.path)
            except OSError:
                pass

    # ------------------------------------------------------------- collecte --
    def record(self, kind: str, backend: str, score: float, w: int, h: int, img_w: int, img_h: int) -> None:
        """Enregistre une correction. w, h : taille de la zone ; img_w, img_h : taille de la photo."""
        if self.read_only or kind not in ("ok", "faux", "rate") or w <= 0 or h <= 0:
            return
        e = {"k": kind, "b": backend, "s": round(float(min(1.0, max(0.0, score))), 4),
             "r": round(min(1.0, max(w, h) / max(img_w, img_h, 1)), 5),
             "a": round(h / w, 4)}
        if self._valid(e):
            self.events.append(e)
            self.events = self.events[-MAX_EVENTS:]
            self._models.pop(backend, None)

    # ---------------------------------------------------------- statistiques --
    def counts(self, backend: str | None = None) -> dict[str, int]:
        ev = [e for e in self.events if backend is None or e["b"] == backend]
        return {k: sum(e["k"] == k for e in ev) for k in ("ok", "faux", "rate")}

    # ------------------------------------------------------- 1. seuil appris --
    def threshold(self, backend: str, base: float) -> float:
        """Seuil à utiliser : jamais au-dessus de `base` (on ne perd jamais de visages)."""
        caught = [e["s"] for e in self.events if e["b"] == backend and e["k"] == "rate"
                  and e["s"] >= THRESHOLD_FLOOR]
        if len(caught) < MIN_MISSES:
            return base
        # Seuil juste sous le visage raté le plus faible, en ignorant 10 % de valeurs extrêmes.
        target = float(np.quantile(caught, 0.1)) - THRESHOLD_MARGIN
        return max(THRESHOLD_FLOOR, min(base, target))

    def unreachable_misses(self, backend: str) -> int:
        """Visages ajoutés à la main que le détecteur ne voyait pas du tout."""
        return sum(e["b"] == backend and e["k"] == "rate" and e["s"] < THRESHOLD_FLOOR for e in self.events)

    # ---------------------------------------- 2. classifieur fausses alertes --
    def _model(self, backend: str):
        if backend in self._models:
            return self._models[backend]
        ev = [e for e in self.events if e["b"] == backend and e["k"] in ("ok", "faux")]
        n_ok = sum(e["k"] == "ok" for e in ev)
        n_faux = len(ev) - n_ok
        model = None
        if n_ok >= MIN_PER_CLASS and n_faux >= MIN_PER_CLASS:
            X = np.array([features(e["s"], e["r"], e["a"]) for e in ev])
            y = np.array([e["k"] == "faux" for e in ev], dtype=np.float64)
            mu, sd = X.mean(0), X.std(0) + 1e-6
            Xn = (X - mu) / sd
            # poids équilibrés : autant d'importance aux deux classes
            sw = np.where(y == 1, 0.5 / n_faux, 0.5 / n_ok) * len(y)
            w = np.zeros(X.shape[1])
            b = 0.0
            for _ in range(800):  # descente de gradient, régularisation L2
                p = 1 / (1 + np.exp(-(Xn @ w + b)))
                g = (p - y) * sw
                w -= 0.3 * (Xn.T @ g / len(y) + 0.05 * w)
                b -= 0.3 * g.mean()
            model = (w, np.array([b]), np.vstack([mu, sd]))
        self._models[backend] = model
        return model

    def trained(self, backend: str) -> bool:
        return self._model(backend) is not None

    def p_false(self, backend: str, score: float, w: int, h: int, img_w: int, img_h: int) -> float | None:
        """Probabilité que la zone soit une fausse détection (None si pas encore assez appris)."""
        m = self._model(backend)
        if m is None or w <= 0 or h <= 0:
            return None
        wts, b, ms = m
        x = (features(score, max(w, h) / max(img_w, img_h, 1), h / w) - ms[0]) / ms[1]
        return float(1 / (1 + np.exp(-(x @ wts + b[0]))))
