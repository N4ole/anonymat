"""Anonymiseur de visages - interface graphique (Tkinter).

  * Mode simple (par défaut) : un style, toujours réglé pour un anonymat total.
  * Options avancées : force, marge, sensibilité, méthodes classiques (moins sûres),
    noms de fichiers, suppression des originaux.
  * Revue image par image : « Suivante ▶ » marque l'image comme vérifiée.

  * Vidéos : analyse + suivi des visages, frise avec « passages à risque », zones
    manuelles sur une plage de temps, export MP4 sans son ni métadonnées.

Lancement en ligne de commande : AnonymiseurVisages.exe [photos, vidéos ou dossiers…]
Auto-test (CI) : AnonymiseurVisages.exe --selftest [--require-ai] [--log fichier]
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import core
import learning
import video
from version import REPO_URL, __version__

APP_NAME = "Anonymiseur de visages"
PROXY_SIDE = 1600       # taille max de l'image d'aperçu (fluidité sur les photos 48 Mpx)
DETECT_SIDE = 2560      # taille max utilisée pour la détection
VIDEO_SIDE = 1280       # taille max de l'aperçu vidéo (lecture fluide)
SPEEDS = {"Auto": "auto", "Précise": "precise", "Rapide": "rapide"}

COL = {"auto": "#ff3b30", "manuel": "#0a84ff", "objet": "#ff9500", "sel": "#ffd60a"}

SIMPLE_LABELS = {
    "flou": "Flou (recommandé)",
    "rond_flou": "Rond flou",
    "pixel": "Pixels",
    "emoji": "Émoji 🙂",
    "noir": "Carré noir",
}
SAFETY_COLORS = {"ok": ("#e6f6ea", "#1b7a34"), "moyen": ("#fff4dc", "#9a5b00"),
                 "danger": ("#fde8e7", "#b3261e")}
SAFETY_ICONS = {"ok": "🔒", "moyen": "⚠", "danger": "⛔"}


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "AnonymiseurVisages", "reglages.json")


def load_config() -> dict:
    """Seuls des réglages sont mémorisés (jamais de nom de photo ni de dossier)."""
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(config_path()), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
class Tooltip:
    """Petite bulle d'aide au survol."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self.job = self.widget.after(450, self._show)

    def _show(self) -> None:
        if self.tip:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", background="#ffffe8", relief="solid",
                 borderwidth=1, wraplength=340, padx=6, pady=4, font=("Segoe UI", 9)).pack()

    def _hide(self, _e=None) -> None:
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Item:
    def __init__(self, path: str) -> None:
        self.path = path
        self.boxes: list[core.Box] = []
        self.state = "attente"  # attente | analyse | ok | erreur | fait
        self.error = ""
        self.reviewed = False
        self.rejected: list[core.Box] = []  # faux positifs supprimés : ne reviennent pas
        self.out_path: str | None = None     # dernière sortie (ré-export = remplacement)
        self.size: tuple[int, int] = (0, 0)  # taille réelle de la photo
        self.learned = False                 # zones déjà apprises comme « ok »
        # --- vidéo ---
        self.is_video = core.is_video(path)
        self.vinfo: video.VideoInfo | None = None
        self.vstep = 1
        self.cands: dict[int, list[core.Box]] = {}  # candidats par image analysée (sensibilité sans relecture)
        self.tracks: list[video.Track] = []         # visages suivis automatiquement
        self.mtracks: list[video.Track] = []        # zones ajoutées à la main
        self.vrejected: list[tuple[int, core.Box]] = []  # pistes supprimées : ne reviennent pas
        self.risks: list[video.Risk] = []
        self.seen: set[int] = set()                 # passages à risque déjà montrés (n° d'image)
        self.pos = 0                                # image affichée
        self.progress = 0.0
        self.gen = 0                                # change = analyse en cours abandonnée
        self.cancelled = False

    @property
    def all_tracks(self) -> list[video.Track]:
        return self.tracks + self.mtracks

    @property
    def n_zones(self) -> int:
        return len(self.all_tracks) if self.is_video else len(self.boxes)

    @property
    def n_faces(self) -> int:
        if self.is_video:
            return sum(t.kind == "visage" for t in self.all_tracks)
        return sum(b.kind == "visage" for b in self.boxes)

    @property
    def unseen_risks(self) -> list[video.Risk]:
        return [r for r in self.risks if r.frame not in self.seen]

    @property
    def label(self) -> str:
        name = ("🎬 " if self.is_video else "") + os.path.basename(self.path)
        if self.state == "attente":
            return f"⏳  {name}"
        if self.state == "analyse":
            pct = f"  ({round(self.progress * 100)} %)" if self.is_video else ""
            return f"🔍  {name}{pct}"
        if self.state == "erreur":
            return f"⚠  {name}  (illisible)"
        n = self.n_zones
        mark = "💾" if self.state == "fait" else ("✔" if self.reviewed else "•")
        txt = f"{mark}  {name}  —  {n} zone{'s' if n > 1 else ''}"
        if self.is_video and not self.reviewed and self.unseen_risks:
            txt += f" · ⚠ {len(self.unseen_risks)}"
        return txt


# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self, initial: list[str]) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self.geometry("1240x820")
        self.minsize(1040, 640)
        try:
            self.iconbitmap(core.resource_path("icon.ico"))
        except Exception:
            pass

        self.items: list[Item] = []
        self.current: Item | None = None
        self.proxy = None          # image d'aperçu (réduite)
        self.proxy_scale = 1.0     # pixels aperçu / pixels réels
        self.view_scale = 1.0      # pixels écran / pixels aperçu
        self.offset = (0, 0)
        self.drag_start = None
        self.drag_rect = None
        self.draw_kind: str | None = None   # None | "visage" | "objet" (mode ajout forcé)
        self.sel_box: core.Box | None = None
        self.tkimg = None
        self.busy = False
        self.cancel = False
        self.export_thread: threading.Thread | None = None
        self._sens_job = None
        self._seek_job = None
        self.reader: video.Reader | None = None  # vidéo affichée
        self.playing = False
        self.cur_risk: video.Risk | None = None
        self.cfg = load_config()
        self.learner = learning.Learner(os.path.join(os.path.dirname(config_path()), "apprentissage.json"))

        self.detector = core.FaceDetector()
        self.jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        # Vidéos : file et détecteur à part (une longue vidéo ne bloque pas les photos ;
        # un détecteur n'est jamais utilisé par deux fils à la fois).
        self.vdetector = core.FaceDetector()
        self.vjobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._vworker, daemon=True).start()

        self._build_ui()
        self._update_learn_label()
        self._on_mode()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll)
        if self.detector.tampered:
            self.after(300, lambda: messagebox.showerror(
                APP_NAME, "⛔ Le modèle de détection IA de ce programme a été MODIFIÉ ou est corrompu "
                          "(empreinte de sécurité incorrecte).\n\nLe programme a peut-être été altéré : "
                          "ne l'utilise pas pour des photos sensibles et retélécharge-le depuis une source sûre.\n\n"
                          "En attendant, seule la détection basique est utilisée."))
        elif not self.detector.ok:
            self.after(300, lambda: messagebox.showerror(
                APP_NAME, "Aucun détecteur de visages n'a pu être chargé :\n\n"
                + "\n".join(self.detector.problems)
                + "\n\nAucun visage ne sera trouvé automatiquement : tu devras ajouter chaque zone à la main."))
        if initial:
            self.after(200, lambda: self.add_paths(initial))

    # ================================================================== UI ==
    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)

        # --- Barre du haut ---------------------------------------------------
        top = self.top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        self.btn_add = ttk.Button(top, text="➕ Photos / vidéos…", command=self.pick_files)
        self.btn_add.pack(side="left")
        self.btn_dir = ttk.Button(top, text="📁 Dossier…", command=self.pick_folder)
        self.btn_dir.pack(side="left", padx=4)
        self.btn_clear = ttk.Button(top, text="🗑 Vider", command=self.clear)
        self.btn_clear.pack(side="left")
        Tooltip(self.btn_clear, "Retire toutes les photos de la liste (les fichiers ne sont pas supprimés).")
        Tooltip(self.btn_dir, "Ajouter toutes les photos et vidéos d'un dossier (et de ses sous-dossiers).")
        Tooltip(self.btn_add, "Choisir des photos ou des vidéos. Tu peux aussi les glisser sur l'icône du programme.")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(top, text="Style :").pack(side="left")
        self.mode = tk.StringVar()
        self.mode_cb = ttk.Combobox(top, textvariable=self.mode, state="readonly", width=23)
        self.mode_cb.pack(side="left", padx=4)
        self.mode_cb.bind("<<ComboboxSelected>>", lambda e: self._on_mode())
        Tooltip(self.mode_cb, "Comment cacher les visages.\n🔒 = anonymat total\n⚠ / ⛔ = méthodes classiques, "
                              "la personne peut rester reconnaissable (options avancées).")

        self.preview = tk.BooleanVar(value=True)
        cbp = ttk.Checkbutton(top, text="Voir le résultat", variable=self.preview, command=self.redraw)
        cbp.pack(side="left", padx=10)
        Tooltip(cbp, "Affiche la photo telle qu'elle sera enregistrée. Les cadres restent visibles par-dessus.")

        self.advanced = tk.BooleanVar(value=bool(self.cfg.get("advanced", False)))
        self.btn_adv = ttk.Checkbutton(top, text="⚙ Options avancées", variable=self.advanced,
                                       command=self._toggle_advanced)
        self.btn_adv.pack(side="right")
        b_about = ttk.Button(top, text="ⓘ", width=3, command=self.about)
        b_about.pack(side="right", padx=(0, 6))
        Tooltip(b_about, "À propos : version, confidentialité, code source.")

        # --- Panneau avancé (caché par défaut) ---------------------------------
        self.adv = ttk.Frame(self, padding=(8, 0, 8, 4))
        r1 = ttk.Frame(self.adv)
        r1.pack(fill="x")
        lbl = ttk.Label(r1, text="Force de l'effet :")
        lbl.pack(side="left")
        self.strength = tk.DoubleVar(value=core.SIMPLE_STRENGTH)
        self.strength_scale = ttk.Scale(r1, from_=0.0, to=1.0, variable=self.strength, length=170,
                                        command=lambda v: self._on_strength())
        self.strength_scale.pack(side="left", padx=4)
        self.strength_val = ttk.Label(r1, text="", width=5)
        self.strength_val.pack(side="left")
        Tooltip(self.strength_scale, "0 % = effet à peine visible, 100 % = effet maximal.\n"
                                     f"Anonymat total à partir d'environ {round(core.min_safe_strength()*100)} % "
                                     "pour le flou et les pixels.")

        ttk.Label(r1, text="   Marge :").pack(side="left")
        self.margin = tk.DoubleVar(value=0.30)
        sc = ttk.Scale(r1, from_=0.0, to=0.8, variable=self.margin, length=110,
                       command=lambda v: self._on_strength())
        sc.pack(side="left", padx=4)
        Tooltip(sc, "Agrandit la zone autour du visage détecté (cheveux, oreilles, menton). 30 % conseillé.")

        ttk.Label(r1, text="   Forme :").pack(side="left")
        self.shape = tk.StringVar(value="Rectangle")
        cbs = ttk.Combobox(r1, textvariable=self.shape, values=["Rectangle", "Ellipse"], state="readonly", width=10)
        cbs.pack(side="left", padx=4)
        cbs.bind("<<ComboboxSelected>>", lambda e: self.redraw())
        Tooltip(cbs, "Ellipse : la zone est automatiquement agrandie pour recouvrir tout le visage.")

        r1b = ttk.Frame(self.adv)
        r1b.pack(fill="x", pady=(4, 0))
        ttk.Label(r1b, text="Sensibilité de détection :").pack(side="left")
        self.sens = tk.DoubleVar(value=0.6)
        ss = ttk.Scale(r1b, from_=0.0, to=1.0, variable=self.sens, length=110, command=lambda v: self._on_sens())
        ss.pack(side="left", padx=4)
        Tooltip(ss, "À droite : trouve plus de visages, mais aussi plus de fausses détections.\n"
                    "La détection est relancée automatiquement (tes zones manuelles sont gardées).")
        ttk.Label(r1b, text="   Analyse des vidéos :").pack(side="left")
        self.vspeed = tk.StringVar(value="Auto")
        cv = ttk.Combobox(r1b, textvariable=self.vspeed, values=list(SPEEDS), state="readonly", width=8)
        cv.pack(side="left", padx=4)
        cv.bind("<<ComboboxSelected>>", lambda e: self.reanalyse_videos())
        Tooltip(cv, "Précise : 10 analyses par seconde de film. Rapide : 4 par seconde, plus de « passages à "
                    "risque » à vérifier. Auto : précise jusqu'à 3 minutes, rapide au-delà.\n"
                    "Changer ce réglage relance l'analyse des vidéos.")

        r1c = ttk.Frame(self.adv)
        r1c.pack(fill="x", pady=(4, 0))
        self.learn_on = tk.BooleanVar(value=bool(self.cfg.get("learn", True)))
        cl = ttk.Checkbutton(r1c, text="🧠 Apprendre de mes corrections", variable=self.learn_on,
                             command=self._toggle_learn)
        cl.pack(side="left")
        Tooltip(cl, "La détection s'améliore avec tes corrections :\n"
                    "• visage ajouté à la main → elle cherche plus large la fois suivante ;\n"
                    "• zone supprimée → elle apprend à repérer ce genre de fausse détection (marquée « ? »).\n\n"
                    "Seuls des NOMBRES sont gardés (score, taille relative, proportions) : jamais d'image, "
                    "de position ni de nom de fichier. Elle ne cache jamais une zone toute seule.\n"
                    "En mode terrain, rien n'est enregistré.")
        self.learn_lbl = ttk.Label(r1c, text="", foreground="#555")
        self.learn_lbl.pack(side="left", padx=8)
        ttk.Button(r1c, text="Oublier l'apprentissage", command=self._reset_learn).pack(side="left")

        r2 = ttk.Frame(self.adv)
        r2.pack(fill="x", pady=(4, 0))
        self.safe_only = tk.BooleanVar(value=bool(self.cfg.get("safe_only", False)))
        c1 = ttk.Checkbutton(r2, text="🔒 Méthodes sûres uniquement (mode terrain)", variable=self.safe_only,
                             command=self._on_safe_only)
        c1.pack(side="left")
        Tooltip(c1, "Ne propose que les méthodes 🔒, bloque la force et la marge au-dessus du seuil d'anonymat, "
                    "et refuse d'enregistrer si la détection IA n'est pas disponible. Réglage mémorisé.")
        self.keep_names = tk.BooleanVar(value=False)
        c2 = ttk.Checkbutton(r2, text="Garder les noms de fichiers d'origine", variable=self.keep_names)
        c2.pack(side="left", padx=12)
        Tooltip(c2, "Par défaut les photos sont renommées « anonyme_0001.jpg » : le nom d'origine contient "
                    "souvent la date et l'heure de la prise de vue (ex. IMG_20261117_143022).")
        self.delete_orig = tk.BooleanVar(value=False)
        c3 = ttk.Checkbutton(r2, text="⚠ Effacer les originaux après", variable=self.delete_orig)
        c3.pack(side="left")
        Tooltip(c3, "Écrase les photos d'origine avec des données aléatoires puis les supprime.\n"
                    "Limite : sur SSD, clé USB, OneDrive/Google Drive ou sauvegardes, des copies "
                    "peuvent subsister. Windows garde aussi des miniatures en cache.")
        b = ttk.Button(r1b, text="🔄 Relancer la détection", command=self.redetect_all)
        b.pack(side="left", padx=8)

        # --- Bandeaux d'information --------------------------------------------
        self.engine_lbl = tk.Label(self, anchor="w", padx=10, pady=3, font=("Segoe UI", 9, "bold"))
        if self.detector.backend == "haar":
            if self.detector.tampered:
                txt = ("⛔ Attention : le programme semble avoir été modifié (modèle IA refusé). "
                       "Ne l'utilise pas pour des photos sensibles.")
            else:
                txt = ("⚠ Cette version n'a pas la détection intelligente : regarde bien chaque photo "
                       "et ajoute les visages oubliés avec « ➕ Visage oublié ».")
            self.engine_lbl.configure(text=txt, bg="#fde8e7", fg="#b3261e")
        elif self.detector.backend == "aucun":
            self.engine_lbl.configure(text="⛔ Aucune détection automatique : ajoute chaque visage à la main.",
                                      bg="#b3261e", fg="white")
        self.safety_lbl = tk.Label(self, anchor="w", padx=10, pady=4, font=("Segoe UI", 10, "bold"))
        self.safety_lbl.pack(fill="x", padx=8, pady=(2, 4))
        if self.detector.backend != "yunet":
            self.engine_lbl.pack(fill="x", padx=8, pady=(0, 2), before=self.safety_lbl)

        # --- Corps : liste + photo ---------------------------------------------
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8)

        left = ttk.Frame(body)
        self.listbox = tk.Listbox(left, activestyle="none", font=("Segoe UI", 10), width=34,
                                  exportselection=False, selectbackground="#0a84ff",
                                  borderwidth=0, highlightthickness=1)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Delete>", lambda e: (self.remove_selected(), "break")[1])
        self.listbox.bind("<Left>", lambda e: (self.step(-1), "break")[1])
        self.listbox.bind("<Right>", lambda e: (self.step(1, mark=True), "break")[1])
        body.add(left, weight=1)

        right = ttk.Frame(body)
        review = ttk.Frame(right, padding=(0, 0, 0, 4))
        review.pack(fill="x")
        self.review_lbl = ttk.Label(review, text="", font=("Segoe UI", 10, "bold"), wraplength=560)
        self.review_lbl.pack(side="left")
        self.btn_next = ttk.Button(review, text="Suivante ▶", command=lambda: self.step(1, mark=True))
        self.btn_next.pack(side="right")
        ttk.Button(review, text="◀ Précédente", command=lambda: self.step(-1)).pack(side="right", padx=4)
        Tooltip(self.btn_next, "Passe à la photo suivante et marque celle-ci comme vérifiée (✔).")

        self.canvas = tk.Canvas(right, background="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        # --- Lecteur vidéo (affiché seulement pour une vidéo) ---------------------
        self.vbar = ttk.Frame(right)
        self.timeline = tk.Canvas(self.vbar, height=50, background="#1e1e1e", highlightthickness=0, cursor="hand2")
        self.timeline.pack(fill="x", pady=(4, 2))
        Tooltip(self.timeline, "Frise de la vidéo : une barre par visage suivi (rouge), zone manuelle (bleu), "
                               "objet (orange). ▼ orange = passage à risque à vérifier (gris : déjà vu). "
                               "Clique ou fais glisser pour te déplacer.")
        self.timeline.bind("<Configure>", lambda e: self._draw_timeline())
        self.timeline.bind("<Button-1>", self._on_timeline)
        self.timeline.bind("<B1-Motion>", self._on_timeline)
        ctl = ttk.Frame(self.vbar)
        ctl.pack(fill="x")
        self.btn_play = ttk.Button(ctl, text="▶ Lire", width=9, command=self.toggle_play)
        self.btn_play.pack(side="left")
        for txt, d, sec, tip in (("◀◀", -1, True, "Reculer d'une seconde"), ("◀", -1, False, "Image précédente"),
                                 ("▶", 1, False, "Image suivante"), ("▶▶", 1, True, "Avancer d'une seconde")):
            bb = ttk.Button(ctl, text=txt, width=4, command=lambda d=d, s=sec: self.seek_rel(d, s))
            bb.pack(side="left", padx=1)
            Tooltip(bb, tip)
        self.time_lbl = ttk.Label(ctl, text="", width=18, anchor="center")
        self.time_lbl.pack(side="left", padx=6)
        self.btn_risk = ttk.Button(ctl, text="⚠ Passage à risque suivant", command=lambda: self.goto_risk(any_risk=True))
        self.btn_risk.pack(side="left")
        Tooltip(self.btn_risk, "Va au prochain moment où un visage a pu échapper à la détection. "
                               "« Suivante ▶ » parcourt aussi ces passages avant de valider la vidéo.")
        self.btn_zend = ttk.Button(ctl, text="Fin ici ⏭", command=lambda: self.zone_limit(end=True))
        self.btn_zend.pack(side="right")
        self.btn_zstart = ttk.Button(ctl, text="⏮ Début ici", command=lambda: self.zone_limit(end=False))
        self.btn_zstart.pack(side="right", padx=2)
        self.btn_zmove = ttk.Button(ctl, text="📍 Replacer ici", command=lambda: self.set_draw("replace"))
        self.btn_zmove.pack(side="right", padx=2)
        for bb, tip in ((self.btn_zmove, "Zone manuelle sélectionnée : redessine-la à sa place sur cette image. "
                                         "Entre deux positions, elle se déplace toute seule."),
                        (self.btn_zstart, "Zone manuelle sélectionnée : elle commence à cette image."),
                        (self.btn_zend, "Zone manuelle sélectionnée : elle s'arrête à cette image "
                                        "(par défaut, elle dure jusqu'à la fin de la vidéo).")):
            Tooltip(bb, tip)

        tools = self.tools = ttk.Frame(right, padding=(0, 4))
        tools.pack(fill="x")
        self.btn_add_face = ttk.Button(tools, text="➕ Visage oublié",
                                       command=lambda: self.set_draw("visage"))
        self.btn_add_face.pack(side="left")
        Tooltip(self.btn_add_face, "Clique ici, puis dessine un rectangle autour du visage sur la photo "
                                   "(maintiens le clic gauche et fais glisser).")
        self.btn_add_obj = ttk.Button(tools, text="🏷 Autre chose à cacher",
                                      command=lambda: self.set_draw("objet"))
        self.btn_add_obj.pack(side="left", padx=4)
        Tooltip(self.btn_add_obj, "Pour tout ce qui peut identifier quelqu'un : nom brodé, badge, "
                                  "plaque d'immatriculation, tatouage, écran… Dessine un rectangle dessus.")
        self.btn_del = ttk.Button(tools, text="✕ Supprimer la zone", command=self.delete_selected_box)
        self.btn_del.pack(side="left")
        Tooltip(self.btn_del, "Clique d'abord sur une zone pour la sélectionner (elle devient jaune), "
                              "puis sur ce bouton. Raccourcis : touche Suppr ou clic droit sur la zone.")
        self.btn_doubt = ttk.Button(tools, text="Enlever les « ? »", command=self.remove_doubtful)
        self.btn_doubt.pack(side="left", padx=4)
        Tooltip(self.btn_doubt, "Enlève d'un coup les zones marquées « ? » (probables fausses détections) "
                                "de cette photo. Regarde-les d'abord !")
        bt = ttk.Button(tools, text="Enlever tous les cadres", command=self.clear_boxes)
        bt.pack(side="right")
        Tooltip(bt, "Enlève tous les cadres de CETTE photo (la photo elle-même n'est pas touchée).")
        self.hint_text = ""

        body.add(right, weight=4)

        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right)
        self.canvas.bind("<Button-2>", self.on_right)  # macOS
        self.canvas.bind("<Delete>", lambda e: self.delete_selected_box())
        self.canvas.bind("<BackSpace>", lambda e: self.delete_selected_box())
        self.canvas.bind("<Left>", lambda e: self.step(-1))
        self.canvas.bind("<Right>", lambda e: self.step(1, mark=True))
        self.canvas.bind("<space>", lambda e: self.toggle_play())
        self.bind("<Escape>", lambda e: self.set_draw(None))

        # --- Barre du bas -----------------------------------------------------------
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        self.btn_export = ttk.Button(bottom, text="✔ Anonymiser et enregistrer", style="Big.TButton",
                                     command=self.export)
        self.btn_export.pack(side="right")
        self.progress = ttk.Progressbar(bottom, length=160, mode="determinate")
        self.progress.pack(side="right", padx=10)
        bo = ttk.Button(bottom, text="📂 Destination…", command=self.pick_out)
        bo.pack(side="right")
        self.out_dir = tk.StringVar(value="")
        self.out_lbl = ttk.Label(bottom, text="→ dossier « anonymise »", foreground="#555")
        self.out_lbl.pack(side="right", padx=6)
        Tooltip(bo, "Par défaut, un dossier « anonymise » est créé à côté des photos. "
                    "Les photos d'origine ne sont jamais modifiées. Les vidéos sont enregistrées en MP4, sans le son.")
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left")

    # ======================================================== réglages ==
    def _mode_choices(self) -> list[str]:
        if not self.advanced.get():
            return core.SIMPLE_MODES
        return core.SAFE_MODES if self.safe_only.get() else list(core.MODES)

    def _label(self, key: str) -> str:
        return core.MODES[key] if self.advanced.get() else SIMPLE_LABELS[key]

    def _mode_key(self) -> str:
        for k in core.MODES:
            if self._label(k) == self.mode.get() or core.MODES[k] == self.mode.get() \
                    or SIMPLE_LABELS.get(k) == self.mode.get():
                return k
        return "flou"

    def _refresh_modes(self) -> None:
        key = self._mode_key() if self.mode.get() else "flou"
        keys = self._mode_choices()
        if key not in keys:
            key = "flou"
        self.mode_cb.configure(values=[self._label(k) for k in keys])
        self.mode.set(self._label(key))

    def _toggle_advanced(self) -> None:
        if self.advanced.get():
            self.adv.pack(fill="x", after=self.top)
        else:
            self.adv.pack_forget()
        self._save_cfg()
        self._on_mode()

    def _on_safe_only(self) -> None:
        self._save_cfg()
        self._update_learn_label()
        self._on_mode()

    def _save_cfg(self) -> None:
        self.cfg.update(advanced=self.advanced.get(), safe_only=self.safe_only.get(), learn=self.learn_on.get())
        save_config(self.cfg)

    # ----------------------------------------------------- apprentissage --
    def _learning(self) -> bool:
        return self.learn_on.get() and self.detector.ok

    def _threshold(self) -> float:
        base = self.detector.base_threshold(self.sens.get())
        return self.learner.threshold(self.detector.backend, base) if self._learning() else base

    def _learn(self, kind: str, score: float, w: int, h: int, W: int, H: int) -> None:
        if not self._learning() or not W:
            return
        self.learner.read_only = self._terrain()  # mode terrain : aucune trace écrite
        self.learner.record(kind, self.detector.backend, score, w, h, W, H)
        self.learner.save()
        self._update_learn_label()

    def _update_learn_label(self) -> None:
        c = self.learner.counts(self.detector.backend)
        n = sum(c.values())
        if not self.learn_on.get():
            txt = "désactivé"
        elif n == 0:
            txt = "rien appris pour l'instant"
        else:
            txt = f"{n} correction(s) apprise(s)"
            if self.learner.trained(self.detector.backend):
                txt += " · repère tes fausses détections"
            t0 = self.detector.base_threshold(self.sens.get())
            if self.learner.threshold(self.detector.backend, t0) < t0 - 1e-6:
                txt += " · cherche plus large"
        if self._terrain():
            txt += " (en pause : mode terrain)"
        self.learn_lbl.configure(text=txt)

    def _toggle_learn(self) -> None:
        self._save_cfg()
        self._update_learn_label()
        self.redraw()

    def _reset_learn(self) -> None:
        if messagebox.askyesno(APP_NAME, "Oublier tout ce que la détection a appris de tes corrections ?"):
            self.learner.reset()
            self._update_learn_label()
            self.redraw()

    def _terrain(self) -> bool:
        return self.advanced.get() and self.safe_only.get()

    def _strength(self) -> float:
        return self.strength.get() if self.advanced.get() else core.SIMPLE_STRENGTH

    def _margin(self) -> float:
        return self.margin.get() if self.advanced.get() else 0.30

    def _shape(self) -> str:
        return self.shape.get().lower() if self.advanced.get() else "rectangle"

    def _on_mode(self) -> None:
        self._refresh_modes()
        mode = self._mode_key()
        state = ["disabled"] if mode in ("noir", "emoji") else ["!disabled"]
        self.strength_scale.state(state)
        self._on_strength(redraw=False)
        self.redraw()

    def _has_video(self) -> bool:
        return any(i.is_video for i in self.items)

    def _on_strength(self, redraw: bool = True) -> None:
        if self._terrain():
            if self.strength.get() < core.min_safe_strength(self._has_video()):
                self.strength.set(core.min_safe_strength(self._has_video()))
            if self.margin.get() < core.MIN_SAFE_MARGIN:
                self.margin.set(core.MIN_SAFE_MARGIN)
        self.strength_val.configure(text=f"{round(self.strength.get() * 100)} %")
        level, msg = core.safety(self._mode_key(), self._strength(), self._margin(), self._has_video())
        bg, fg = SAFETY_COLORS[level]
        self.safety_lbl.configure(text=f"{SAFETY_ICONS[level]}  {msg}", bg=bg, fg=fg)
        if redraw and self.preview.get():
            self.redraw()

    def _on_sens(self) -> None:
        if self._sens_job:
            self.after_cancel(self._sens_job)
        self._sens_job = self.after(700, self.redetect_all)

    def set_draw(self, kind: str | None) -> None:
        self.draw_kind = kind
        if kind == "visage":
            self.hint_text = ("✏  Dessine un rectangle autour du visage oublié\n"
                              "(maintiens le clic gauche et fais glisser · Échap pour annuler)")
        elif kind == "objet":
            self.hint_text = "✏  Dessine un rectangle sur ce qu'il faut cacher\n(badge, plaque, tatouage… · Échap pour annuler)"
        elif kind == "replace":
            self.hint_text = "✏  Redessine la zone sélectionnée à sa place sur cette image\n(Échap pour annuler)"
        else:
            self.hint_text = ""
        self.redraw()

    def flash_hint(self, text: str) -> None:
        self.hint_text = text
        self.redraw()
        self.after(2500, lambda: (self.draw_kind is None and self.hint_text == text) and self.set_draw(None))

    # ======================================================== fichiers ==
    def pick_files(self) -> None:
        if self.busy:
            return
        def pat(exts):
            return " ".join("*" + e for e in sorted(exts))
        files = filedialog.askopenfilenames(title="Choisir des photos ou des vidéos",
                                            filetypes=[("Photos et vidéos", pat(core.MEDIA_EXTS)),
                                                       ("Photos", pat(core.IMAGE_EXTS)),
                                                       ("Vidéos", pat(core.VIDEO_EXTS)), ("Tous", "*.*")])
        self.add_paths(list(files))

    def pick_folder(self) -> None:
        if self.busy:
            return
        d = filedialog.askdirectory(title="Choisir un dossier de photos ou de vidéos")
        if d:
            self.add_paths([d])

    def pick_out(self) -> None:
        d = filedialog.askdirectory(title="Dossier où enregistrer les photos anonymisées")
        if d:
            self.out_dir.set(d)
            self.out_lbl.configure(text=f"→ {d if len(d) < 40 else '…' + d[-38:]}")

    def add_paths(self, paths: list[str]) -> None:
        if self.busy:
            return
        known = {os.path.normcase(i.path) for i in self.items}
        excl = [self.out_dir.get()] if self.out_dir.get() else []
        new, skipped = [], 0
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                files, sk = core.scan_images(p, excl)
                skipped += sk
            else:
                files = [p]
            for f in files:
                if os.path.normcase(f) not in known and os.path.splitext(f)[1].lower() in core.MEDIA_EXTS:
                    known.add(os.path.normcase(f))
                    new.append(Item(f))
        if skipped:
            self.status.configure(text=f"{skipped} photo(s) déjà anonymisée(s) ignorée(s) "
                                       "(dossier « anonymise » ou nom « anonyme_0001 »).")
        if not new:
            if paths and not skipped:
                self.status.configure(text="Aucune nouvelle photo trouvée.")
            return
        first = len(self.items)
        self.items.extend(new)
        for it in new:
            self.listbox.insert("end", it.label)
            self._queue(it)
        self._on_strength(redraw=False)  # une vidéo rend le seuil d'anonymat plus strict
        if not skipped:
            self._update_progress()
        if self.current is None:
            self.select_index(first)

    def _queue(self, it: Item) -> None:
        if it.is_video:
            it.gen += 1
            self.vjobs.put((it, it.gen, SPEEDS.get(self.vspeed.get(), "auto")))
        else:
            self.jobs.put(("detect", it, self._threshold()))

    def reanalyse_videos(self) -> None:
        if self.busy:
            return
        for it in self.items:
            if it.is_video and it.state != "erreur":
                it.state, it.progress = "attente", 0.0
                self._queue(it)
                self._refresh_row(it)
        self.redraw()

    def clear(self) -> None:
        if self.busy:
            return
        self._purge_jobs()
        for it in self.items:
            it.cancelled = True
        self._close_reader()
        self.items.clear()
        self.listbox.delete(0, "end")
        self.current = None
        self.proxy = None
        self.redraw()

    def _purge_jobs(self) -> None:
        for q in (self.jobs, self.vjobs):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass

    def remove_selected(self) -> None:
        if self.busy:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if not messagebox.askyesno(APP_NAME, f"Retirer « {os.path.basename(self.items[idx].path)} » de la liste ?\n"
                                             "(le fichier n'est pas supprimé de ton ordinateur)"):
            return
        self.items[idx].cancelled = True
        if self.items[idx] is self.current:
            self._close_reader()
        del self.items[idx]
        self.listbox.delete(idx)
        self.current = None
        self._on_strength(redraw=False)
        if self.items:
            self.select_index(min(idx, len(self.items) - 1))
        else:
            self.proxy = None
            self.redraw()
        self._update_progress()

    # ======================================================= détection ==
    def _worker(self) -> None:
        while True:
            job, it, arg = self.jobs.get()
            try:
                W, H = core.image_size(it.path)
                if job == "probe":  # visage ajouté à la main : le détecteur l'avait-il « presque » vu ?
                    rgb, _ = core.load_image_full(it.path, max_side=DETECT_SIDE)
                    s = rgb.shape[1] / W
                    self.results.put(("probe", it, (arg, self.detector.best_score_near(rgb, arg.scaled(s)), (W, H))))
                    continue
                self.results.put(("start", it, None))
                rgb, _ = core.load_image_full(it.path, max_side=DETECT_SIDE)
                s = W / rgb.shape[1]
                boxes = [b.scaled(s) for b in self.detector.detect(rgb, threshold=arg)]
                self.results.put(("ok", it, (boxes, (W, H))))
            except Exception as exc:  # image corrompue, format inconnu...
                if job != "probe":
                    self.results.put(("err", it, f"{type(exc).__name__} : {exc}"))

    def _vworker(self) -> None:
        """Analyse des vidéos (fil à part : une longue vidéo ne bloque pas les photos)."""
        while True:
            it, gen, speed = self.vjobs.get()
            if it.cancelled or gen != it.gen:
                continue

            def stop(it=it, gen=gen) -> bool:
                return it.cancelled or gen != it.gen

            last = [0.0]

            def prog(fr: float, it=it, gen=gen, last=last) -> None:
                if fr - last[0] >= 0.01:
                    last[0] = fr
                    self.results.put(("vprog", it, (gen, fr)))

            try:
                self.results.put(("start", it, None))
                res = video.analyse(it.path, self.vdetector, speed, prog, stop)
                self.results.put(("vok", it, (gen, res)))
            except video.Cancelled:
                continue
            except Exception as exc:  # vidéo abîmée, format inconnu...
                self.results.put(("err", it, f"{type(exc).__name__} : {exc}"))

    def _rebuild(self, it: Item) -> None:
        """Pistes et passages à risque recalculés (seuil / sensibilité), sans relire la vidéo."""
        def sig(tracks):
            return [(t.keys()[0], t.keys()[-1]) for t in tracks]
        old = sig(it.tracks)
        it.tracks = video.build_tracks(it.cands, self._threshold(), it.vstep, it.vinfo, it.vrejected)
        it.risks = video.find_risks(it.tracks, it.vinfo, it.vstep)
        if it.reviewed and sig(it.tracks) != old:
            it.reviewed = False  # les zones ont changé : à revérifier

    def _poll(self) -> None:
        try:
            while True:
                kind, it, data = self.results.get_nowait()
                if kind == "exp_prog":
                    self.progress.configure(value=it)
                    continue
                if kind == "exp_done":
                    self._export_done(data)
                    continue
                if it not in self.items:
                    continue
                if kind == "probe":
                    box, score, (W, H) = data
                    self._learn("rate", score, box.w, box.h, W, H)
                    continue
                if kind == "vprog":
                    gen, it.progress = data
                    if gen == it.gen:
                        self._refresh_row(it)
                        if it is self.current:
                            self._draw_timeline()
                    continue
                if kind == "start":
                    it.state = "analyse"
                elif kind == "vok":
                    gen, (info, step, cands) = data
                    if gen != it.gen:
                        continue
                    it.vinfo, it.vstep, it.cands, it.size = info, step, cands, (info.width, info.height)
                    it.progress = 1.0
                    self._rebuild(it)
                    it.state = "ok"
                elif kind == "ok":
                    data, it.size = data
                    fresh = [b for b in data if not any(core._overlap(b, r) > 0.5 for r in it.rejected)]
                    old = sorted((b.x, b.y, b.w, b.h) for b in it.boxes if not b.manual)
                    new = sorted((b.x, b.y, b.w, b.h) for b in fresh)
                    if it.reviewed and old != new:
                        it.reviewed = False  # les zones ont changé : à revérifier
                    it.boxes = fresh + [b for b in it.boxes if b.manual]
                    it.state = "ok"
                else:
                    it.state, it.error = "erreur", data
                self._refresh_row(it)
                if it is self.current:
                    self.sel_box = None
                    self.redraw()
                self._update_progress()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def redetect_all(self) -> None:
        self._sens_job = None
        if self.busy:
            return
        for it in self.items:
            if it.is_video:
                if it.cands and it.state in ("ok", "fait"):  # pas besoin de relire la vidéo
                    self._rebuild(it)
                    self._refresh_row(it)
            elif it.state != "erreur":
                it.state = "attente"
                self._refresh_row(it)
                self.jobs.put(("detect", it, self._threshold()))
        self.redraw()

    def clear_boxes(self) -> None:
        it = self.current
        if it and it.is_video and it.all_tracks and messagebox.askyesno(
                APP_NAME, "Supprimer toutes les zones de cette vidéo, sur toute sa durée ?"):
            for t in it.tracks:
                it.vrejected += list(t.samples.items())
            it.tracks, it.mtracks, it.risks = [], [], []
            self.sel_box = None
            self._refresh_row(it)
            self.redraw()
        elif it and not it.is_video and it.boxes and messagebox.askyesno(
                APP_NAME, "Supprimer toutes les zones de cette photo ?"):
            self.current.boxes = []
            self.sel_box = None
            self._refresh_row(self.current)
            self.redraw()

    def _refresh_row(self, it: Item) -> None:
        if it not in self.items:
            return
        idx = self.items.index(it)
        sel = self.listbox.curselection()
        self.listbox.delete(idx)
        self.listbox.insert(idx, it.label)
        if it.state == "erreur":
            self.listbox.itemconfig(idx, foreground="#c00")
        if sel and sel[0] == idx:
            self.listbox.selection_set(idx)
        if it is self.current:
            self._update_review()

    def _update_progress(self) -> None:
        if self.busy:
            return
        total = len(self.items)
        done = sum(i.state in ("ok", "erreur", "fait") for i in self.items)
        self.progress.configure(maximum=max(total, 1), value=done)
        if total:
            rev = sum(i.reviewed for i in self.items)
            self.status.configure(text=f"{total} photo(s) · {rev} vérifiée(s) ✔")
        else:
            self.status.configure(text="")

    def _view(self, it: Item | None = None) -> list[tuple[core.Box, object]]:
        """Zones visibles : (cadre, propriétaire). Propriétaire = le cadre (photo) ou sa piste (vidéo)."""
        it = it or self.current
        if it is None:
            return []
        if it.is_video:
            return [(b, t) for t in it.all_tracks if (b := t.box_at(it.pos)) is not None]
        return [(b, b) for b in it.boxes]

    def _doubtful(self, it: Item) -> list[int]:
        """Numéros des zones auto qui ressemblent à une fausse détection (petites ou peu sûres)."""
        view = self._view(it)
        autos = [b for b, _ in view if not b.manual]
        if not autos:
            return []
        if self._learning() and it.size[0] and self.learner.trained(self.detector.backend):
            W, H = it.size
            return [n for n, (b, _) in enumerate(view, 1) if not b.manual and
                    (self.learner.p_false(self.detector.backend, b.score, b.w, b.h, W, H) or 0) > learning.FALSE_PROBA]
        big = max(b.w for b in autos)
        return [n for n, (b, _) in enumerate(view, 1)
                if not b.manual and (b.score < 0.6 or (len(autos) > 1 and b.w < 0.45 * big))]

    def _update_review(self) -> None:
        it = self.current
        try:
            self.btn_doubt.state(["!disabled"] if it and self._doubtful(it) else ["disabled"])
        except (AttributeError, tk.TclError):
            pass
        if not it:
            self.review_lbl.configure(text="")
            return
        i = self.items.index(it) + 1
        if it.is_video:
            self.review_lbl.configure(text=self._video_review_text(it, i))
            return
        if it.state in ("attente", "analyse"):
            txt = f"Photo {i} / {len(self.items)} · recherche des visages…"
        elif it.state == "erreur":
            txt = f"Photo {i} / {len(self.items)} · illisible"
        else:
            n, o = it.n_faces, len(it.boxes) - it.n_faces
            txt = f"Photo {i} / {len(self.items)} · {n} visage{'s' if n > 1 else ''}"
            if o:
                txt += f" + {o} autre{'s' if o > 1 else ''} zone{'s' if o > 1 else ''}"
            dz = self._doubtful(it)
            if dz:
                txt += f" — ⚠ zone {', '.join(map(str, dz))} : vraiment un visage ? Sinon, sa ✕"
            else:
                txt += " — vérifie qu'il n'en manque aucun"
            txt += "  ✔" if it.reviewed else ""
        self.review_lbl.configure(text=txt)

    def _video_review_text(self, it: Item, i: int) -> str:
        head = f"Vidéo {i} / {len(self.items)}"
        if it.state == "attente":
            return f"{head} · en attente d'analyse"
        if it.state == "analyse":
            return f"{head} · analyse des visages… {round(it.progress * 100)} %"
        if it.state == "erreur":
            return f"{head} · illisible"
        n, o = it.n_faces, len(it.all_tracks) - it.n_faces
        txt = f"{head} · {n} visage{'s' if n > 1 else ''} suivi{'s' if n > 1 else ''}"
        if o:
            txt += f" + {o} autre{'s' if o > 1 else ''} zone{'s' if o > 1 else ''}"
        r = self.cur_risk
        fps = it.vinfo.fps if it.vinfo else 25
        if r in it.risks and abs(it.pos - r.frame) <= 2 * fps:
            txt += f"\n⚠ Passage {it.risks.index(r) + 1} / {len(it.risks)} : " + r.reason.replace("\n", " · ")
        elif it.unseen_risks:
            k = len(it.unseen_risks)
            txt += f" — {k} passage{'s' if k > 1 else ''} à risque à vérifier : « Suivante ▶ » t'y emmène"
        else:
            txt += " — lis la vidéo et vérifie qu'aucun visage n'est oublié"
        return txt + "  · 🔇 son supprimé" + ("  ✔" if it.reviewed else "")

    # ========================================================== aperçu ==
    def select_index(self, idx: int) -> None:
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self.on_select()

    def on_select(self, _e=None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        it = self.items[sel[0]]
        if it is self.current:
            return
        self.current = it
        self.sel_box = None
        self.cur_risk = None
        self._close_reader()
        self.set_draw(None)
        if it.is_video:
            self.vbar.pack(fill="x", before=self.tools)
        else:
            self.vbar.pack_forget()
        try:
            if it.is_video:
                if it.vinfo is None:
                    it.vinfo = video.probe(it.path)
                    it.size = (it.vinfo.width, it.vinfo.height)
                self.reader = video.Reader(it.path, VIDEO_SIDE)
                self.proxy = self.reader.frame(it.pos)
                if self.proxy is None:
                    it.pos = 0
                    self.proxy = self.reader.frame(0)
                if self.proxy is None:
                    raise OSError("aucune image lisible dans cette vidéo")
                self.proxy_scale = self.proxy.shape[1] / it.vinfo.width
            else:
                W, _ = core.image_size(it.path)
                self.proxy, _ = core.load_image_full(it.path, max_side=PROXY_SIDE)
                self.proxy_scale = self.proxy.shape[1] / W
        except Exception as exc:
            self.proxy = None
            if not it.error:
                it.error = f"{type(exc).__name__} : {exc}"
        self._update_review()
        self.redraw()

    def step(self, d: int, mark: bool = False) -> None:
        if not self.items:
            return
        it = self.current
        if mark and it and it.is_video and it.state in ("ok", "fait") and self.goto_risk():
            return  # vidéo : d'abord tous ses passages à risque
        if mark and self.current and self.current.state in ("ok", "fait"):
            self.current.reviewed = True
            self._refresh_row(self.current)
            self._update_progress()
        sel = self.listbox.curselection()
        cur = sel[0] if sel else 0
        if mark and d > 0 and cur == len(self.items) - 1:
            self._end_of_review()
            return
        idx = max(0, min(len(self.items) - 1, cur + d))
        self.select_index(idx)

    def _end_of_review(self) -> None:
        if self.busy:
            return
        todo = [i for i in self.items if i.state in ("ok", "fait") and not i.reviewed]
        if todo:
            messagebox.showinfo(APP_NAME, f"C'était la dernière photo, mais il en reste {len(todo)} "
                                          "à vérifier (sans ✔ dans la liste). Je t'y emmène.")
            self.select_index(self.items.index(todo[0]))
        elif self.items and messagebox.askyesno(
                APP_NAME, "✔ Toutes les photos sont vérifiées !\n\nEnregistrer les photos anonymisées maintenant ?"):
            self.export()

    def _box_canvas(self, b: core.Box) -> tuple[float, float, float, float]:
        k = self.view_scale * self.proxy_scale
        ox, oy = self.offset
        return ox + b.x * k, oy + b.y * k, ox + (b.x + b.w) * k, oy + (b.y + b.h) * k

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self._draw_timeline()
        if self.current is None:
            c.create_text(cw // 2, ch // 2, fill="#aaa", font=("Segoe UI", 14), justify="center",
                          text="Ajoute des photos ou des vidéos avec « ➕ Photos / vidéos… »\n"
                               "(ou glisse-les sur l'icône du programme)")
            return
        if self.proxy is None:
            c.create_text(20, 20, anchor="nw", fill="#ff8a80", font=("Segoe UI", 12), width=cw - 40,
                          text=f"Impossible d'ouvrir cette photo.\n\n{self.current.error}")
            return
        H, W = self.proxy.shape[:2]
        mode = self._mode_key()
        img = self.proxy
        ps = self.proxy_scale
        view = self._view()
        if self.preview.get() and view:
            img = core.anonymize(img, [b.scaled(ps) for b, _ in view], mode, self._shape(),
                                 self._margin(), strength=self._strength())
        self.view_scale = min(cw / W, ch / H, 1.0 / ps if ps < 1 else 1.0)
        dw, dh = max(1, int(W * self.view_scale)), max(1, int(H * self.view_scale))
        self.offset = ((cw - dw) // 2, (ch - dh) // 2)
        self.tkimg = ImageTk.PhotoImage(Image.fromarray(img).resize((dw, dh), Image.BILINEAR))
        c.create_image(*self.offset, anchor="nw", image=self.tkimg)

        k_full = 1 / ps
        self._handles = []
        doubtful = self._doubtful(self.current)
        for n, (b, owner) in enumerate(view, 1):
            if mode == "bandeau" and b.kind == "visage":
                m = 0
            else:
                bm, bs = core.box_mode(b, mode, self._shape())
                m = core.effective_margin(b, self._margin(), bs, bm)
            e = b.expanded(m, int(W * k_full), int(H * k_full))
            x0, y0, x1, y1 = self._box_canvas(e)
            sel = owner is self.sel_box
            col = COL["sel"] if sel else (
                COL["objet"] if b.kind == "objet" else COL["manuel"] if b.manual else COL["auto"])
            width = 3 if sel else 2
            dash = (5, 3) if self.preview.get() and not sel else None
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=width, dash=dash)
            tag = f"{n} ?" if n in doubtful else str(n)
            tw = 14 + 7 * len(tag)
            c.create_rectangle(x0, y0 - 16, x0 + tw, y0, fill=col, outline=col)
            c.create_text(x0 + tw / 2, y0 - 8, text=tag, fill="black", font=("Segoe UI", 8, "bold"))
            # petite croix pour supprimer la zone d'un clic
            c.create_rectangle(x1 - 16, y0 - 16, x1, y0, fill="#222", outline=col)
            c.create_text(x1 - 8, y0 - 8, text="✕", fill="white", font=("Segoe UI", 8, "bold"))
            self._handles.append(((x1 - 16, y0 - 16, x1, y0), owner))
        if self.current.state in ("attente", "analyse"):
            msg = "Recherche des visages…"
            if self.current.is_video:
                msg = f"Analyse de la vidéo… {round(self.current.progress * 100)} %"
            c.create_text(self.offset[0] + 10, self.offset[1] + 10, anchor="nw", fill="yellow",
                          font=("Segoe UI", 12, "bold"), text=msg)
        self._draw_hint()

    def _draw_hint(self) -> None:
        if not self.hint_text:
            return
        c = self.canvas
        cw = max(c.winfo_width(), 50)
        t = c.create_text(cw // 2, 14, anchor="n", text=self.hint_text, fill="white", justify="center",
                          font=("Segoe UI", 12, "bold"), width=cw - 40)
        x0, y0, x1, y1 = c.bbox(t)
        r = c.create_rectangle(x0 - 12, y0 - 8, x1 + 12, y1 + 8, fill="#0a5fb4", outline="")
        c.tag_lower(r, t)

    # ========================================================== souris ==
    def _to_full(self, x: float, y: float) -> tuple[int, int]:
        ox, oy = self.offset
        k = self.view_scale * self.proxy_scale
        H, W = self.proxy.shape[:2]
        fw, fh = W / self.proxy_scale, H / self.proxy_scale
        return int(max(0, min(fw, (x - ox) / k))), int(max(0, min(fh, (y - oy) / k)))

    def _box_at(self, x: float, y: float) -> object | None:
        """Propriétaire (cadre ou piste) de la plus petite zone sous la souris."""
        hits = []
        for b, owner in self._view():
            bm, bs = core.box_mode(b, self._mode_key(), self._shape())
            x0, y0, x1, y1 = self._box_canvas(b.expanded(
                core.effective_margin(b, self._margin(), bs, bm), 10 ** 9, 10 ** 9))
            if x0 <= x <= x1 and y0 <= y <= y1:
                hits.append((b.w * b.h, id(owner), owner))
        return min(hits, key=lambda h: h[:2])[2] if hits else None

    def on_press(self, e) -> None:
        self.canvas.focus_set()  # Suppr agit sur la zone, plus sur la liste
        if self.proxy is None or self.current is None:
            return
        for (hx0, hy0, hx1, hy1), b in self._handles:
            if self.draw_kind is None and hx0 <= e.x <= hx1 and hy0 <= e.y <= hy1:
                self._remove_box(b)
                return
        if self.draw_kind is None:
            hit = self._box_at(e.x, e.y)
            if hit is not None:  # clic sur une zone : sélection
                self.sel_box = hit
                self.redraw()
                return
        if self.draw_kind != "replace":  # « Replacer ici » agit sur la zone sélectionnée
            self.sel_box = None
        self.drag_start = (e.x, e.y)
        col = COL["objet"] if self.draw_kind == "objet" else COL["manuel"]
        self.drag_rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=col, width=2, dash=(3, 2))

    def on_drag(self, e) -> None:
        if self.drag_rect:
            self.canvas.coords(self.drag_rect, *self.drag_start, e.x, e.y)

    def on_release(self, e) -> None:
        if not self.drag_rect:
            return
        x0, y0 = self._to_full(*self.drag_start)
        x1, y1 = self._to_full(e.x, e.y)
        self.drag_rect = None
        if abs(x1 - x0) > 3 and abs(y1 - y0) > 3:
            it = self.current
            kind = self.draw_kind or "visage"
            sel = self.sel_box
            if it.is_video and kind == "replace" and isinstance(sel, video.Track) and sel in it.mtracks:
                sel.set(it.pos, core.Box(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), 1.0, True, sel.kind))
                sel.start, sel.end = min(sel.start, it.pos), max(sel.end, it.pos)
            elif it.is_video:
                b = core.Box(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), manual=True,
                             kind="visage" if kind == "replace" else kind)
                t = video.manual_track(it.pos, b, b.kind, it.vinfo)
                it.mtracks.append(t)
                self.sel_box = t
            else:
                kind = "visage" if kind == "replace" else kind
                b = core.Box(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), manual=True, kind=kind)
                self.current.boxes.append(b)
                self.sel_box = b
                if kind == "visage" and self._learning():
                    self.jobs.put(("probe", self.current, b))
            if self.current.state == "fait":
                self.current.state = "ok"
            self._refresh_row(self.current)
            self.set_draw(None)
        self.redraw()

    def on_right(self, e) -> None:
        if self.proxy is None or self.current is None:
            return
        hit = self._box_at(e.x, e.y)
        if hit is not None:
            self._remove_box(hit)

    def remove_doubtful(self) -> None:
        if not self.current:
            return
        dz = set(self._doubtful(self.current))
        for n, (_, owner) in reversed(list(enumerate(self._view(), 1))):
            if n in dz:
                self._remove_box(owner)

    def delete_selected_box(self) -> None:
        if self.focus_get() is self.listbox:
            return
        if self.sel_box is not None:
            self._remove_box(self.sel_box)
        elif self.current and self.current.n_zones:
            self.flash_hint("Clique d'abord sur un cadre pour le sélectionner (il devient jaune),\n"
                            "ou clique sur sa petite croix ✕.")

    def _remove_box(self, b: object) -> None:
        it = self.current
        if it.is_video:
            if b in it.mtracks:
                it.mtracks.remove(b)
            elif b in it.tracks:  # le visage disparaît sur TOUTE la vidéo, et ne revient pas
                it.tracks.remove(b)
                it.vrejected += list(b.samples.items())
                tb = b.typical()
                self._learn("faux", b.score, tb.w, tb.h, *it.size)
                it.learned = False
                it.risks = video.find_risks(it.tracks, it.vinfo, it.vstep)
            self.sel_box = None
            self._refresh_row(it)
            self.redraw()
            return
        if b in self.current.boxes:
            self.current.boxes.remove(b)
            if not b.manual:
                self.current.rejected.append(b)  # ne reviendra pas à la prochaine détection
                self._learn("faux", b.score, b.w, b.h, *self.current.size)
                self.current.learned = False
        self.sel_box = None
        self._refresh_row(self.current)
        self.redraw()

    # ========================================================== export ==
    def export(self) -> None:
        if self.busy:
            if time.monotonic() - self._export_start > 1.0:  # évite qu'un double-clic annule
                self.cancel = True
                self.btn_export.configure(text="Annulation…")
            return
        if not self.items:
            messagebox.showinfo(APP_NAME, "Aucune photo dans la liste.")
            return
        if any(i.state in ("attente", "analyse") for i in self.items):
            messagebox.showinfo(APP_NAME, "La recherche des visages n'est pas terminée, patiente quelques secondes.")
            return
        if self._terrain() and self.detector.backend != "yunet":
            messagebox.showerror(APP_NAME, "Mode terrain : enregistrement refusé, car la détection IA n'est pas "
                                           "disponible dans ce programme.\nRecompile-le avec le modèle YuNet, "
                                           "ou désactive le mode terrain en connaissance de cause.")
            return
        mode, shape, margin, strength = self._mode_key(), self._shape(), self._margin(), self._strength()
        level, msg = core.safety(mode, strength, margin, self._has_video())
        if level != "ok" and not messagebox.askyesno(
                APP_NAME, "⚠ Ce réglage ne garantit PAS l'anonymat :\n\n" + msg +
                          "\n\nPour un anonymat total : un style 🔒 avec une force suffisante.\n\n"
                          "Enregistrer quand même ?", icon="warning"):
            return
        todo = [i for i in self.items if i.state != "erreur"]
        if not todo:
            messagebox.showwarning(APP_NAME, "Aucune photo lisible dans la liste.")
            return
        not_rev = [i for i in todo if not i.reviewed]
        if not_rev and not messagebox.askyesno(
                APP_NAME, f"{len(not_rev)} photo(s) n'ont pas été vérifiées (pas de ✔ dans la liste).\n"
                          "La détection automatique peut rater des visages.\n\nEnregistrer quand même ?",
                icon="warning"):
            return
        no_face = [i for i in todo if not i.n_zones]
        if no_face and not messagebox.askyesno(
                APP_NAME, f"{len(no_face)} photo(s) ou vidéo(s) sans aucune zone : elles seront copiées sans "
                          "modification (mais nettoyées de leurs informations cachées).\n\nContinuer ?"):
            return
        if any(i.is_video for i in todo) and not self.cfg.get("video_info_vu") and not messagebox.askyesno(
                APP_NAME, "Vidéos : elles seront enregistrées en MP4, SANS LE SON (une voix identifie "
                          "aussi bien qu'un visage).\n\nFormat lisible avec VLC et le lecteur de Windows, "
                          "mais pas toujours par les téléphones ou les sites web.\n\nContinuer ?"):
            return
        delete = self.advanced.get() and self.delete_orig.get()
        if delete and not messagebox.askyesno(
                APP_NAME, f"⚠ Les {len(todo)} photo(s) ou vidéo(s) d'ORIGINE seront écrasées puis supprimées, "
                          "sans passer par la corbeille. C'est définitif.\n\n"
                          "Limites : sur SSD, clé USB, OneDrive/Google Drive ou sauvegardes, des copies "
                          "peuvent subsister.\n\nContinuer ?", icon="warning"):
            return
        reuse = {i.path: i.out_path for i in todo if i.out_path and os.path.exists(i.out_path)
                 and (not self.out_dir.get() or os.path.dirname(i.out_path) == os.path.abspath(self.out_dir.get()))}
        try:
            plan = core.plan_outputs([i.path for i in todo if i.path not in reuse], self.out_dir.get() or None,
                                     self.advanced.get() and self.keep_names.get())
            plan.update(reuse)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Impossible de préparer les noms de fichiers : {exc}")
            return

        self.cfg["video_info_vu"] = self.cfg.get("video_info_vu") or any(i.is_video for i in todo)
        save_config(self.cfg)
        self._stop_play()
        if delete:
            self._close_reader()  # Windows : une vidéo ouverte ne peut pas être effacée
        self._export_start = time.monotonic()
        self.busy, self.cancel = True, False
        self.btn_export.configure(text="✖ Annuler")
        for b in (self.btn_add, self.btn_dir, self.btn_clear):
            b.state(["disabled"])
        self.progress.configure(maximum=len(todo), value=0)
        self.status.configure(text="Enregistrement…")

        jobs = [(it, plan[it.path], list(it.all_tracks) if it.is_video else list(it.boxes)) for it in todo]
        n_reused = len(reuse)

        def run():  # thread : ne touche JAMAIS à Tk, communique par la file self.results
            written, errors, deleted, last, done_items, gone = 0, [], 0, None, [], []
            try:
                for n, (it, dst, boxes) in enumerate(jobs, 1):
                    if self.cancel:
                        break
                    try:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        if it.is_video:
                            video.export(it.path, dst, boxes, mode, shape, margin, strength,
                                         progress=lambda fr, n=n: self.results.put(("exp_prog", n - 1 + fr, None)),
                                         cancel=lambda: self.cancel)
                        else:
                            rgb, alpha = core.load_image_full(it.path)
                            out = core.anonymize(rgb, boxes, mode, shape, margin, strength=strength)
                            core.save_clean(out, dst, alpha)
                        written += 1
                        last = dst
                        done_items.append((it, dst))
                        if delete:
                            core.secure_delete(it.path)
                            deleted += 1
                            gone.append(it)
                    except video.Cancelled:
                        break
                    except Exception as exc:
                        errors.append(f"{os.path.basename(it.path)} : {exc}")
                    self.results.put(("exp_prog", n, None))
            except BaseException as exc:  # filet de sécurité : l'interface ne reste jamais bloquée
                errors.append(f"Erreur inattendue : {exc!r}")
            finally:
                self.results.put(("exp_done", None, dict(written=written, total=len(jobs), errors=errors,
                                                         deleted=deleted, last=last, done=done_items,
                                                         gone=gone, reused=n_reused)))

        self.export_thread = threading.Thread(target=run, daemon=True)
        self.export_thread.start()

    def _export_done(self, r: dict) -> None:
        written, total, errors, deleted, last = r["written"], r["total"], r["errors"], r["deleted"], r["last"]
        for it, dst in r["done"]:
            it.state, it.out_path = "fait", dst
            if it.reviewed and not it.learned:  # zones auto confirmées par l'utilisateur
                if it.is_video:
                    for t in it.tracks:
                        tb = t.typical()
                        self._learn("ok", t.score, tb.w, tb.h, *it.size)
                for b in it.boxes:
                    if not b.manual:
                        self._learn("ok", b.score, b.w, b.h, *it.size)
                it.learned = True
            self._refresh_row(it)
        for it in r["gone"]:  # originaux effacés : on les retire de la liste
            if it in self.items:
                idx = self.items.index(it)
                del self.items[idx]
                self.listbox.delete(idx)
                if it is self.current:
                    self.current, self.proxy = None, None
        self.busy = False
        self.btn_export.configure(text="✔ Anonymiser et enregistrer")
        for b in (self.btn_add, self.btn_dir, self.btn_clear):
            b.state(["!disabled"])
        self._update_progress()
        msg = f"{written} fichier(s) sur {total} enregistré(s), sans informations cachées."
        if self.cancel:
            msg += "\n\nEnregistrement annulé par l'utilisateur."
        if r["reused"]:
            msg += f"\n{r['reused']} photo(s) déjà enregistrée(s) avant : version précédente remplacée."
        if deleted:
            msg += f"\n{deleted} original(aux) effacé(s) et retiré(s) de la liste."
        if errors:
            msg += f"\n\n{len(errors)} erreur(s) :\n" + "\n".join(errors[:10])
        self.status.configure(text=msg.split("\n")[0])
        if self._closing:
            self.destroy()
            return
        if self.current is None and self.items:
            self.select_index(0)
        self.redraw()
        if last and messagebox.askyesno(APP_NAME, msg + "\n\nOuvrir le dossier ?"):
            folder = os.path.dirname(last)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", folder])
        elif not last:
            messagebox.showwarning(APP_NAME, msg)

    # ==================================================== lecteur vidéo ==
    def _close_reader(self) -> None:
        self._stop_play()
        if self.reader is not None:
            self.reader.close()
            self.reader = None

    def _seek(self, f: int) -> None:
        it = self.current
        if not it or not it.is_video or self.reader is None or it.vinfo is None:
            return
        it.pos = max(0, min(it.vinfo.frames - 1, int(f)))
        fr = self.reader.frame(it.pos)
        if fr is not None:
            self.proxy = fr
        self._update_review()
        self.redraw()

    def seek_rel(self, d: int, seconds: bool = False) -> None:
        it = self.current
        if it and it.is_video and it.vinfo:
            self._stop_play()
            self._seek(it.pos + d * (round(it.vinfo.fps) if seconds else 1))

    def _on_timeline(self, e) -> None:
        it = self.current
        if not it or not it.is_video or not it.vinfo:
            return
        self._stop_play()
        w = max(self.timeline.winfo_width(), 100)
        f = round((e.x - 8) / max(1, w - 16) * (it.vinfo.frames - 1))
        it.pos = max(0, min(it.vinfo.frames - 1, f))
        self._draw_timeline()
        if self._seek_job is None:  # glisser : on ne décode pas chaque position
            def go():
                self._seek_job = None
                if self.current is it:
                    self._seek(it.pos)
            self._seek_job = self.after(40, go)

    def toggle_play(self) -> None:
        it = self.current
        if not it or not it.is_video or self.reader is None or it.vinfo is None:
            return
        if self.playing:
            self._stop_play()
            return
        if it.pos >= it.vinfo.frames - 1:
            self._seek(0)
        self.playing = True
        self.btn_play.configure(text="⏸ Pause")
        self._play_tick()

    def _stop_play(self) -> None:
        self.playing = False
        try:
            self.btn_play.configure(text="▶ Lire")
        except (AttributeError, tk.TclError):
            pass  # fenêtre pas encore construite ou déjà détruite

    def _play_tick(self) -> None:
        it = self.current
        if not self.playing or not it or not it.is_video or self.reader is None or it.vinfo is None:
            self._stop_play()
            return
        t0 = time.perf_counter()
        fr = self.reader.frame(it.pos + 1) if it.pos + 1 < it.vinfo.frames else None
        if fr is None:
            self._stop_play()
            return
        it.pos += 1
        self.proxy = fr
        self.redraw()
        if self.cur_risk is not None:
            self._update_review()
        spent = (time.perf_counter() - t0) * 1000
        self.after(max(1, int(1000 / it.vinfo.fps - spent)), self._play_tick)

    def goto_risk(self, any_risk: bool = False) -> bool:
        """Va au prochain passage à risque (non vu, sauf `any_risk`) ; False s'il n'y en a plus."""
        it = self.current
        if not it or not it.is_video or not it.risks:
            return False
        pool = it.risks if any_risk else it.unseen_risks
        if not pool:
            return False
        after = [r for r in pool if r.frame > it.pos] if any_risk else [r for r in pool if r.frame >= it.pos]
        r = (after or pool)[0]
        it.seen.add(r.frame)
        self.cur_risk = r
        self._stop_play()
        self._refresh_row(it)
        self._seek(r.frame)
        return True

    def zone_limit(self, end: bool) -> None:
        it, t = self.current, self.sel_box
        if not it or not it.is_video or not isinstance(t, video.Track) or t not in it.mtracks:
            self.flash_hint("Sélectionne d'abord une zone ajoutée à la main (cadre bleu ou orange).")
            return
        if end:
            t.end = max(it.pos, t.start)
        else:
            t.start = min(it.pos, t.end)
        self.redraw()

    def _draw_timeline(self) -> None:
        c = self.timeline
        c.delete("all")
        it = self.current
        if not it or not it.is_video or not it.vinfo:
            return
        W, H = max(c.winfo_width(), 100), int(c["height"])
        n, pad = max(1, it.vinfo.frames - 1), 8

        def X(f: int) -> float:
            return pad + f / n * (W - 2 * pad)

        c.create_rectangle(pad, 14, W - pad, H - 4, fill="#2b2b2b", outline="#444")
        if it.state in ("attente", "analyse"):
            c.create_rectangle(pad, 14, X(it.progress * n), H - 4, fill="#3a4a5a", outline="")
            c.create_text(W // 2, 30, text=f"Analyse… {round(it.progress * 100)} %", fill="#ccc")
        rows: list[int] = []
        for t in it.all_tracks:
            r = next((i for i, e in enumerate(rows) if e < t.start), None)
            if r is None:
                if len(rows) < 4:
                    rows.append(t.end)
                r = min(len(rows) - 1, 3)
            else:
                rows[r] = t.end
            y = 18 + r * 7
            col = COL["sel"] if t is self.sel_box else (
                COL["objet"] if t.kind == "objet" else COL["manuel"] if t.manual else COL["auto"])
            c.create_rectangle(X(t.start), y, max(X(t.end), X(t.start) + 2), y + 5, fill=col, outline="")
        for r in it.risks:
            x = X(r.frame)
            c.create_polygon(x - 5, 2, x + 5, 2, x, 11, fill="#777" if r.frame in it.seen else "#ff9500", outline="")
        x = X(it.pos)
        c.create_line(x, 0, x, H, fill="white", width=2)
        fps = it.vinfo.fps

        def fmt(f: int) -> str:
            s = f / fps
            return f"{int(s // 60):02d}:{s % 60:04.1f}"
        self.time_lbl.configure(text=f"{fmt(it.pos)} / {fmt(it.vinfo.frames - 1)}")
        manual_sel = isinstance(self.sel_box, video.Track) and self.sel_box in it.mtracks
        for b in (self.btn_zmove, self.btn_zstart, self.btn_zend):
            b.state(["!disabled"] if manual_sel else ["disabled"])
        self.btn_risk.state(["!disabled"] if it.risks else ["disabled"])

    def about(self) -> None:
        engine = {"yunet": "IA (YuNet, empreinte vérifiée)", "haar": "basique (Haar)",
                  "aucun": "aucune"}[self.detector.backend]
        messagebox.showinfo(
            f"À propos – {APP_NAME}",
            f"{APP_NAME} – version {__version__}\n\n"
            f"Détection : {engine}\n"
            f"Photos iPhone (HEIC) : {'oui' if core.HEIF_OK else 'non'}\n"
            "Vidéos : MP4, son supprimé\n\n"
            "Confidentialité : le programme ne se connecte JAMAIS à Internet. Les photos ne quittent "
            "pas ton ordinateur ; seuls quelques réglages et des statistiques numériques "
            "d'apprentissage sont gardés dans %APPDATA%\\AnonymiseurVisages.\n\n"
            "Licence MIT. Composants tiers (OpenCV, YuNet, libheif…) : voir THIRD_PARTY_NOTICES.md "
            "dans le dépôt.\n\n"
            f"Code source, téléchargements et vérification des .exe :\n{REPO_URL}")

    _closing = False
    _handles: list = []
    _export_start = 0.0

    def on_close(self) -> None:
        self._stop_play()
        if self.busy:
            if not messagebox.askyesno(APP_NAME, "Un enregistrement est en cours.\n"
                                                 "Arrêter après la photo en cours et quitter ?"):
                return
            self._closing = True
            self.cancel = True  # _export_done fermera la fenêtre proprement
            return
        self.destroy()


# --------------------------------------------------------------------------- #
def selftest(argv: list[str]) -> int:
    """Vérification sans interface (utilisée par la CI) : détecteur + chaîne complète."""
    import shutil
    import tempfile

    import numpy as np
    i = argv.index("--log") if "--log" in argv else -1
    log_path = argv[i + 1] if 0 <= i < len(argv) - 1 else None
    lines, ok = [], True
    det = core.FaceDetector()
    lines.append(f"version={__version__}")
    lines.append(f"backend={det.backend} heic={core.HEIF_OK}")
    if det.tampered or not core.HEIF_OK:
        ok = False
    lines += [f"probleme={p}" for p in det.problems]
    if not det.ok or ("--require-ai" in argv and det.backend != "yunet"):
        ok = False
    try:
        d = tempfile.mkdtemp(prefix="anonymiseur_é_à ü_")
        src = os.path.join(d, "phöto test.jpg")
        Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 400, 3), dtype=np.uint8)).save(src)
        det.detect(core.load_image(src))
        dst = core.plan_outputs([src])[src]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        core.save_clean(core.anonymize(core.load_image(src), [core.Box(100, 100, 80, 80)]), dst)
        lines.append(f"sortie={dst} existe={os.path.exists(dst)}")
        ok &= os.path.exists(dst)
        # Vidéo : lecture + écriture par le FFmpeg embarqué (DLL d'OpenCV dans le .exe)
        import cv2
        vsrc = os.path.join(d, "vidéo test.mp4")
        wr = cv2.VideoWriter(vsrc, cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), 25, (320, 240))
        for k in range(12):
            wr.write(np.random.default_rng(k).integers(0, 255, (240, 320, 3), dtype=np.uint8))
        wr.release()
        info, step, cands = video.analyse(vsrc, det)
        vdst = core.plan_outputs([vsrc])[vsrc]
        t = video.manual_track(0, core.Box(100, 80, 80, 80), "visage", info)
        n = video.export(vsrc, vdst, video.build_tracks(cands, 0.6, step, info) + [t])
        lines.append(f"video={vdst} images={n}/{info.frames}")
        ok &= n == 12 and info.frames == 12 and os.path.exists(vdst)
        shutil.rmtree(d, ignore_errors=True)
    except Exception as exc:
        lines.append(f"erreur={exc!r}")
        ok = False
    lines.append("RESULTAT=" + ("OK" if ok else "ECHEC"))
    text = "\n".join(lines)
    if log_path:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)
    return 0 if ok else 1


def main() -> None:
    if "--selftest" in sys.argv:
        sys.exit(selftest(sys.argv))
    if sys.platform == "win32":
        try:  # rendu net sur les écrans haute résolution
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App([a for a in sys.argv[1:] if not a.startswith("--")]).mainloop()


if __name__ == "__main__":
    main()
