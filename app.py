"""Anonymiseur de visages - interface graphique (Tkinter).

  * Mode simple (par défaut) : un style, toujours réglé pour un anonymat total.
  * Options avancées : force, marge, sensibilité, méthodes classiques (moins sûres),
    noms de fichiers, suppression des originaux.
  * Revue image par image : « Suivante ▶ » marque l'image comme vérifiée.

Lancement en ligne de commande : AnonymiseurVisages.exe [images ou dossiers…]
Auto-test (CI) : AnonymiseurVisages.exe --selftest [--require-ai] [--log fichier]
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import core

APP_NAME = "Anonymiseur de visages"
PROXY_SIDE = 1600       # taille max de l'image d'aperçu (fluidité sur les photos 48 Mpx)
DETECT_SIDE = 2560      # taille max utilisée pour la détection

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

    @property
    def n_faces(self) -> int:
        return sum(b.kind == "visage" for b in self.boxes)

    @property
    def label(self) -> str:
        name = os.path.basename(self.path)
        if self.state == "attente":
            return f"⏳  {name}"
        if self.state == "analyse":
            return f"🔍  {name}"
        if self.state == "erreur":
            return f"⚠  {name}  (illisible)"
        n = len(self.boxes)
        mark = "💾" if self.state == "fait" else ("✔" if self.reviewed else "•")
        return f"{mark}  {name}  —  {n} zone{'s' if n > 1 else ''}"


# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self, initial: list[str]) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1240x820")
        self.minsize(980, 640)
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

        self.detector = core.FaceDetector()
        self.jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        self._build_ui()
        self._on_mode()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll)
        if not self.detector.ok:
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
        self.btn_add = ttk.Button(top, text="➕ Ajouter des photos…", command=self.pick_files)
        self.btn_add.pack(side="left")
        self.btn_dir = ttk.Button(top, text="📁 Un dossier…", command=self.pick_folder)
        self.btn_dir.pack(side="left", padx=4)
        self.btn_clear = ttk.Button(top, text="🗑 Vider la liste", command=self.clear)
        self.btn_clear.pack(side="left")
        Tooltip(self.btn_add, "Choisir une ou plusieurs photos. Tu peux aussi glisser des photos sur l'icône du programme.")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(top, text="Style :").pack(side="left")
        self.mode = tk.StringVar()
        self.mode_cb = ttk.Combobox(top, textvariable=self.mode, state="readonly", width=34)
        self.mode_cb.pack(side="left", padx=4)
        self.mode_cb.bind("<<ComboboxSelected>>", lambda e: self._on_mode())
        Tooltip(self.mode_cb, "Comment cacher les visages.\n🔒 = anonymat total\n⚠ / ⛔ = méthodes classiques, "
                              "la personne peut rester reconnaissable (options avancées).")

        self.preview = tk.BooleanVar(value=True)
        cbp = ttk.Checkbutton(top, text="Voir le résultat", variable=self.preview, command=self.redraw)
        cbp.pack(side="left", padx=10)
        Tooltip(cbp, "Affiche la photo telle qu'elle sera enregistrée. Les cadres restent visibles par-dessus.")

        self.advanced = tk.BooleanVar(value=False)
        self.btn_adv = ttk.Checkbutton(top, text="⚙ Options avancées", variable=self.advanced,
                                       command=self._toggle_advanced)
        self.btn_adv.pack(side="right")

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

        ttk.Label(r1, text="   Sensibilité :").pack(side="left")
        self.sens = tk.DoubleVar(value=0.6)
        ss = ttk.Scale(r1, from_=0.0, to=1.0, variable=self.sens, length=110, command=lambda v: self._on_sens())
        ss.pack(side="left", padx=4)
        Tooltip(ss, "À droite : trouve plus de visages, mais aussi plus de fausses détections.\n"
                    "La détection est relancée automatiquement (tes zones manuelles sont gardées).")

        r2 = ttk.Frame(self.adv)
        r2.pack(fill="x", pady=(4, 0))
        self.safe_only = tk.BooleanVar(value=False)
        c1 = ttk.Checkbutton(r2, text="🔒 Méthodes sûres uniquement (mode terrain)", variable=self.safe_only,
                             command=self._on_safe_only)
        c1.pack(side="left")
        Tooltip(c1, "Masque les méthodes classiques et empêche de descendre sous le seuil d'anonymat total.")
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
        b = ttk.Button(r2, text="🔄 Relancer la détection", command=self.redetect_all)
        b.pack(side="right")

        # --- Bandeaux d'information --------------------------------------------
        self.engine_lbl = tk.Label(self, anchor="w", padx=10, pady=3, font=("Segoe UI", 9, "bold"))
        if self.detector.backend == "haar":
            self.engine_lbl.configure(text="⚠ Détection basique (le modèle IA n'est pas dans ce programme) : "
                                           "des visages risquent d'être ratés. Vérifie chaque photo très attentivement.",
                                      bg="#fde8e7", fg="#b3261e")
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
        body.add(left, weight=1)

        right = ttk.Frame(body)
        review = ttk.Frame(right, padding=(0, 0, 0, 4))
        review.pack(fill="x")
        self.review_lbl = ttk.Label(review, text="", font=("Segoe UI", 10, "bold"))
        self.review_lbl.pack(side="left")
        self.btn_next = ttk.Button(review, text="Suivante ▶", command=lambda: self.step(1, mark=True))
        self.btn_next.pack(side="right")
        ttk.Button(review, text="◀ Précédente", command=lambda: self.step(-1)).pack(side="right", padx=4)
        Tooltip(self.btn_next, "Passe à la photo suivante et marque celle-ci comme vérifiée (✔).")

        self.canvas = tk.Canvas(right, background="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        tools = ttk.Frame(right, padding=(0, 4))
        tools.pack(fill="x")
        self.btn_add_face = ttk.Button(tools, text="➕ Ajouter un visage oublié",
                                       command=lambda: self.set_draw("visage"))
        self.btn_add_face.pack(side="left")
        Tooltip(self.btn_add_face, "Clique ici, puis dessine un rectangle autour du visage sur la photo "
                                   "(maintiens le clic gauche et fais glisser).")
        self.btn_add_obj = ttk.Button(tools, text="🏷 Cacher autre chose (badge, plaque…)",
                                      command=lambda: self.set_draw("objet"))
        self.btn_add_obj.pack(side="left", padx=4)
        Tooltip(self.btn_add_obj, "Pour tout ce qui peut identifier quelqu'un : nom brodé, badge, "
                                  "plaque d'immatriculation, tatouage, écran… Dessine un rectangle dessus.")
        self.btn_del = ttk.Button(tools, text="✕ Supprimer la zone", command=self.delete_selected_box)
        self.btn_del.pack(side="left")
        Tooltip(self.btn_del, "Clique d'abord sur une zone pour la sélectionner (elle devient jaune), "
                              "puis sur ce bouton. Raccourcis : touche Suppr ou clic droit sur la zone.")
        ttk.Button(tools, text="Tout effacer", command=self.clear_boxes).pack(side="right")
        self.hint = ttk.Label(tools, text="", foreground="#0a5fb4")
        self.hint.pack(side="left", padx=8)

        body.add(right, weight=4)

        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right)
        self.canvas.bind("<Button-2>", self.on_right)  # macOS
        self.bind("<Delete>", lambda e: self.delete_selected_box())
        self.bind("<Escape>", lambda e: self.set_draw(None))
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1, mark=True))

        # --- Barre du bas -----------------------------------------------------------
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        self.btn_export = ttk.Button(bottom, text="✔ Anonymiser et enregistrer", style="Big.TButton",
                                     command=self.export)
        self.btn_export.pack(side="right")
        self.progress = ttk.Progressbar(bottom, length=160, mode="determinate")
        self.progress.pack(side="right", padx=10)
        bo = ttk.Button(bottom, text="📂 Où enregistrer…", command=self.pick_out)
        bo.pack(side="right")
        self.out_dir = tk.StringVar(value="")
        self.out_lbl = ttk.Label(bottom, text="→ dossier « anonymise » à côté des photos", foreground="#555")
        self.out_lbl.pack(side="right", padx=6)
        Tooltip(bo, "Par défaut, un dossier « anonymise » est créé à côté des photos. "
                    "Les photos d'origine ne sont jamais modifiées.")
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
        self._on_mode()

    def _on_safe_only(self) -> None:
        self._on_mode()

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
        if self.safe_only.get() and self.strength.get() < core.min_safe_strength():
            self.strength.set(core.min_safe_strength())
        self._on_strength(redraw=False)
        self.redraw()

    def _on_strength(self, redraw: bool = True) -> None:
        if self.advanced.get() and self.safe_only.get() and self.strength.get() < core.min_safe_strength():
            self.strength.set(core.min_safe_strength())
        self.strength_val.configure(text=f"{round(self.strength.get() * 100)} %")
        level, msg = core.safety(self._mode_key(), self._strength(), self._margin())
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
            self.hint.configure(text="✏ Dessine un rectangle autour du visage (Échap pour annuler)")
        elif kind == "objet":
            self.hint.configure(text="✏ Dessine un rectangle sur l'élément à cacher (Échap pour annuler)")
        else:
            self.hint.configure(text="")

    # ======================================================== fichiers ==
    def pick_files(self) -> None:
        if self.busy:
            return
        exts = " ".join("*" + e for e in sorted(core.IMAGE_EXTS))
        files = filedialog.askopenfilenames(title="Choisir des photos",
                                            filetypes=[("Images", exts), ("Tous", "*.*")])
        self.add_paths(list(files))

    def pick_folder(self) -> None:
        if self.busy:
            return
        d = filedialog.askdirectory(title="Choisir un dossier de photos")
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
        new = []
        for p in paths:
            p = os.path.abspath(p)
            files = core.list_images(p, excl) if os.path.isdir(p) else [p]
            for f in files:
                if os.path.normcase(f) not in known and os.path.splitext(f)[1].lower() in core.IMAGE_EXTS:
                    known.add(os.path.normcase(f))
                    new.append(Item(f))
        if not new:
            if paths:
                self.status.configure(text="Aucune nouvelle photo trouvée.")
            return
        first = len(self.items)
        self.items.extend(new)
        for it in new:
            self.listbox.insert("end", it.label)
            self.jobs.put((it, self.sens.get()))
        self._update_progress()
        if self.current is None:
            self.select_index(first)

    def clear(self) -> None:
        if self.busy:
            return
        self.items.clear()
        self.listbox.delete(0, "end")
        self.current = None
        self.proxy = None
        self.redraw()

    def remove_selected(self) -> None:
        if self.busy:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.items[idx]
        self.listbox.delete(idx)
        self.current = None
        if self.items:
            self.select_index(min(idx, len(self.items) - 1))
        else:
            self.proxy = None
            self.redraw()
        self._update_progress()

    # ======================================================= détection ==
    def _worker(self) -> None:
        while True:
            it, sens = self.jobs.get()
            try:
                self.results.put(("start", it, None))
                W, H = core.image_size(it.path)
                rgb, _ = core.load_image_full(it.path, max_side=DETECT_SIDE)
                s = W / rgb.shape[1]
                boxes = [b.scaled(s) for b in self.detector.detect(rgb, sens)]
                self.results.put(("ok", it, boxes))
            except Exception as exc:  # image corrompue, format inconnu...
                self.results.put(("err", it, f"{type(exc).__name__} : {exc}"))

    def _poll(self) -> None:
        try:
            while True:
                kind, it, data = self.results.get_nowait()
                if it not in self.items:
                    continue
                if kind == "start":
                    it.state = "analyse"
                elif kind == "ok":
                    it.boxes = data + [b for b in it.boxes if b.manual]
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
            if it.state != "erreur":
                it.state = "attente"
                self._refresh_row(it)
                self.jobs.put((it, self.sens.get()))
        self.redraw()

    def clear_boxes(self) -> None:
        if self.current and self.current.boxes and messagebox.askyesno(
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
        engine = {"yunet": "IA", "haar": "basique", "aucun": "aucune"}[self.detector.backend]
        if total:
            rev = sum(i.reviewed for i in self.items)
            self.status.configure(text=f"{total} photo(s) · {rev} vérifiée(s) · détection : {engine}")
        else:
            self.status.configure(text=f"Détection : {engine}")

    def _update_review(self) -> None:
        it = self.current
        if not it:
            self.review_lbl.configure(text="")
            return
        i = self.items.index(it) + 1
        if it.state in ("attente", "analyse"):
            txt = f"Photo {i} / {len(self.items)} · recherche des visages…"
        elif it.state == "erreur":
            txt = f"Photo {i} / {len(self.items)} · illisible"
        else:
            n, o = it.n_faces, len(it.boxes) - it.n_faces
            txt = f"Photo {i} / {len(self.items)} · {n} visage{'s' if n > 1 else ''}"
            if o:
                txt += f" + {o} autre{'s' if o > 1 else ''} zone{'s' if o > 1 else ''}"
            txt += " — vérifie qu'il n'en manque aucun" + ("  ✔" if it.reviewed else "")
        self.review_lbl.configure(text=txt)

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
        self.set_draw(None)
        try:
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
        if mark and self.current and self.current.state in ("ok", "fait"):
            self.current.reviewed = True
            self._refresh_row(self.current)
            self._update_progress()
        sel = self.listbox.curselection()
        idx = max(0, min(len(self.items) - 1, (sel[0] if sel else 0) + d))
        self.select_index(idx)

    def _box_canvas(self, b: core.Box) -> tuple[float, float, float, float]:
        k = self.view_scale * self.proxy_scale
        ox, oy = self.offset
        return ox + b.x * k, oy + b.y * k, ox + (b.x + b.w) * k, oy + (b.y + b.h) * k

    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        if self.current is None:
            c.create_text(cw // 2, ch // 2, fill="#aaa", font=("Segoe UI", 14), justify="center",
                          text="Ajoute des photos avec « ➕ Ajouter des photos… »\n"
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
        if self.preview.get() and self.current.boxes:
            img = core.anonymize(img, [b.scaled(ps) for b in self.current.boxes], mode, self._shape(),
                                 self._margin(), strength=self._strength())
        self.view_scale = min(cw / W, ch / H, 1.0 / ps if ps < 1 else 1.0)
        dw, dh = max(1, int(W * self.view_scale)), max(1, int(H * self.view_scale))
        self.offset = ((cw - dw) // 2, (ch - dh) // 2)
        self.tkimg = ImageTk.PhotoImage(Image.fromarray(img).resize((dw, dh), Image.BILINEAR))
        c.create_image(*self.offset, anchor="nw", image=self.tkimg)

        k_full = 1 / ps
        for n, b in enumerate(self.current.boxes, 1):
            if mode == "bandeau" and b.kind == "visage":
                m = 0
            else:
                bm, bs = core.box_mode(b, mode, self._shape())
                m = core.effective_margin(b, self._margin(), bs, bm)
            e = b.expanded(m, int(W * k_full), int(H * k_full))
            x0, y0, x1, y1 = self._box_canvas(e)
            col = COL["sel"] if b is self.sel_box else (
                COL["objet"] if b.kind == "objet" else COL["manuel"] if b.manual else COL["auto"])
            width = 3 if b is self.sel_box else 2
            dash = (5, 3) if self.preview.get() and b is not self.sel_box else None
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=width, dash=dash)
            c.create_rectangle(x0, y0 - 16, x0 + 20, y0, fill=col, outline=col)
            c.create_text(x0 + 10, y0 - 8, text=str(n), fill="black", font=("Segoe UI", 8, "bold"))
        if self.current.state in ("attente", "analyse"):
            c.create_text(self.offset[0] + 10, self.offset[1] + 10, anchor="nw", fill="yellow",
                          font=("Segoe UI", 12, "bold"), text="Recherche des visages…")

    # ========================================================== souris ==
    def _to_full(self, x: float, y: float) -> tuple[int, int]:
        ox, oy = self.offset
        k = self.view_scale * self.proxy_scale
        H, W = self.proxy.shape[:2]
        fw, fh = W / self.proxy_scale, H / self.proxy_scale
        return int(max(0, min(fw, (x - ox) / k))), int(max(0, min(fh, (y - oy) / k)))

    def _box_at(self, x: float, y: float) -> core.Box | None:
        hits = []
        for b in self.current.boxes:
            bm, bs = core.box_mode(b, self._mode_key(), self._shape())
            x0, y0, x1, y1 = self._box_canvas(b.expanded(
                core.effective_margin(b, self._margin(), bs, bm), 10 ** 9, 10 ** 9))
            if x0 <= x <= x1 and y0 <= y <= y1:
                hits.append(b)
        return min(hits, key=lambda b: b.w * b.h) if hits else None

    def on_press(self, e) -> None:
        if self.proxy is None or self.current is None:
            return
        if self.draw_kind is None:
            hit = self._box_at(e.x, e.y)
            if hit is not None:  # clic sur une zone : sélection
                self.sel_box = hit
                self.redraw()
                return
        self.sel_box = None
        self.drag_start = (e.x, e.y)
        col = COL["objet"] if self.draw_kind == "objet" else COL["manuel"]
        self.drag_rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=col, width=2, dash=(3, 2))

    def on_drag(self, e) -> None:
        if self.drag_rect:
            self.canvas.coords(self.drag_rect, *self.drag_start, e.x, e.y)

    def on_release(self, e) -> None:
        if not self.drag_rect:
            self.redraw()
            return
        x0, y0 = self._to_full(*self.drag_start)
        x1, y1 = self._to_full(e.x, e.y)
        self.drag_rect = None
        if abs(x1 - x0) > 3 and abs(y1 - y0) > 3:
            kind = self.draw_kind or "visage"
            b = core.Box(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), manual=True, kind=kind)
            self.current.boxes.append(b)
            self.sel_box = b
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

    def delete_selected_box(self) -> None:
        if self.focus_get() is self.listbox:
            return
        if self.sel_box is not None:
            self._remove_box(self.sel_box)
        elif self.current and self.current.boxes:
            self.hint.configure(text="Clique d'abord sur une zone pour la sélectionner.")

    def _remove_box(self, b: core.Box) -> None:
        if b in self.current.boxes:
            self.current.boxes.remove(b)
        self.sel_box = None
        self._refresh_row(self.current)
        self.redraw()

    # ========================================================== export ==
    def export(self) -> None:
        if self.busy:
            import time
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
        mode, shape, margin, strength = self._mode_key(), self._shape(), self._margin(), self._strength()
        level, msg = core.safety(mode, strength, margin)
        if level != "ok" and not messagebox.askyesno(
                APP_NAME, "⚠ Ce réglage ne garantit PAS l'anonymat :\n\n" + msg +
                          "\n\nPour un anonymat total : un style 🔒 avec une force suffisante.\n\n"
                          "Enregistrer quand même ?", icon="warning"):
            return
        todo = [i for i in self.items if i.state != "erreur"]
        if not todo:
            messagebox.showwarning(APP_NAME, "Aucune photo lisible dans la liste.")
            return
        not_rev = [i for i in todo if not i.reviewed and i is not self.current]
        if not_rev and not messagebox.askyesno(
                APP_NAME, f"{len(not_rev)} photo(s) n'ont pas été vérifiées (pas de ✔ dans la liste).\n"
                          "La détection automatique peut rater des visages.\n\nEnregistrer quand même ?",
                icon="warning"):
            return
        no_face = [i for i in todo if not i.boxes]
        if no_face and not messagebox.askyesno(
                APP_NAME, f"{len(no_face)} photo(s) sans aucune zone : elles seront copiées sans modification "
                          "(mais nettoyées de leurs informations cachées).\n\nContinuer ?"):
            return
        delete = self.advanced.get() and self.delete_orig.get()
        if delete and not messagebox.askyesno(
                APP_NAME, f"⚠ Les {len(todo)} photo(s) d'ORIGINE seront écrasées puis supprimées, "
                          "sans passer par la corbeille. C'est définitif.\n\n"
                          "Limites : sur SSD, clé USB, OneDrive/Google Drive ou sauvegardes, des copies "
                          "peuvent subsister.\n\nContinuer ?", icon="warning"):
            return
        try:
            plan = core.plan_outputs([i.path for i in todo], self.out_dir.get() or None,
                                     self.advanced.get() and self.keep_names.get())
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Impossible de préparer les noms de fichiers : {exc}")
            return

        import time
        self._export_start = time.monotonic()
        self.busy, self.cancel = True, False
        self.btn_export.configure(text="✖ Annuler")
        for b in (self.btn_add, self.btn_dir, self.btn_clear):
            b.state(["disabled"])
        self.progress.configure(maximum=len(todo), value=0)
        self.status.configure(text="Enregistrement…")

        jobs = [(it, plan[it.path], list(it.boxes)) for it in todo]

        def run():
            written, errors, deleted, last = 0, [], 0, None
            for n, (it, dst, boxes) in enumerate(jobs, 1):
                if self.cancel:
                    break
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    rgb, alpha = core.load_image_full(it.path)
                    out = core.anonymize(rgb, boxes, mode, shape, margin, strength=strength)
                    core.save_clean(out, dst, alpha)
                    written += 1
                    last = dst
                    it.state = "fait"
                    if delete:
                        core.secure_delete(it.path)
                        deleted += 1
                except Exception as exc:
                    errors.append(f"{os.path.basename(it.path)} : {exc}")
                self.after(0, lambda n=n, it=it: (self.progress.configure(value=n), self._refresh_row(it)))
            self.after(0, lambda: self._export_done(written, len(jobs), errors, deleted, last))

        self.export_thread = threading.Thread(target=run, daemon=True)
        self.export_thread.start()

    def _export_done(self, written: int, total: int, errors: list[str], deleted: int, last: str | None) -> None:
        self.busy = False
        self.btn_export.configure(text="✔ Anonymiser et enregistrer")
        for b in (self.btn_add, self.btn_dir, self.btn_clear):
            b.state(["!disabled"])
        self._update_progress()
        msg = f"{written} photo(s) sur {total} enregistrée(s), sans informations cachées."
        if self.cancel:
            msg += "\n\nEnregistrement annulé par l'utilisateur."
        if deleted:
            msg += f"\n{deleted} original(aux) effacé(s)."
        if errors:
            msg += f"\n\n{len(errors)} erreur(s) :\n" + "\n".join(errors[:10])
        self.status.configure(text=msg.split("\n")[0])
        if self._closing:
            self.destroy()
            return
        if last and messagebox.askyesno(APP_NAME, msg + "\n\nOuvrir le dossier ?"):
            folder = os.path.dirname(last)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", folder])
        elif not last:
            messagebox.showwarning(APP_NAME, msg)

    _closing = False
    _export_start = 0.0

    def on_close(self) -> None:
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
    import tempfile

    import numpy as np
    log_path = argv[argv.index("--log") + 1] if "--log" in argv else None
    lines, ok = [], True
    det = core.FaceDetector()
    lines.append(f"backend={det.backend}")
    lines += [f"probleme={p}" for p in det.problems]
    if not det.ok or ("--require-ai" in argv and det.backend != "yunet"):
        ok = False
    try:
        d = os.path.join(tempfile.gettempdir(), "test_é_à ü")
        os.makedirs(d, exist_ok=True)
        src = os.path.join(d, "phöto test.jpg")
        Image.fromarray(np.random.default_rng(0).integers(0, 255, (300, 400, 3), dtype=np.uint8)).save(src)
        det.detect(core.load_image(src))
        dst = core.plan_outputs([src])[src]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        core.save_clean(core.anonymize(core.load_image(src), [core.Box(100, 100, 80, 80)]), dst)
        lines.append(f"sortie={dst} existe={os.path.exists(dst)}")
        ok &= os.path.exists(dst)
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
