"""Anonymiseur de visages - interface graphique (Tkinter).

Utilisation :
  * Ajouter des images (ou un dossier, ou glisser des fichiers sur le .exe).
  * Les visages sont détectés automatiquement (cadres rouges).
  * Vérifier chaque image : glisser à la souris pour AJOUTER une zone,
    clic droit sur une zone pour la SUPPRIMER.
  * « Anonymiser et enregistrer » : les copies sont créées dans un sous-dossier
    « anonymise » (les originaux ne sont jamais modifiés).
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
COL_AUTO = "#ff3b30"
COL_MANUAL = "#0a84ff"


class Item:
    def __init__(self, path: str) -> None:
        self.path = path
        self.boxes: list[core.Box] = []
        self.state = "attente"  # attente | analyse | ok | erreur | fait
        self.error = ""

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
        mark = "✅" if self.state == "fait" else ("👤" if n else "❔")
        return f"{mark}  {name}  —  {n} zone{'s' if n > 1 else ''}"


class App(tk.Tk):
    def __init__(self, initial: list[str]) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x780")
        self.minsize(900, 600)
        try:
            self.iconbitmap(core.resource_path("icon.ico"))
        except Exception:
            pass

        self.items: list[Item] = []
        self.current: Item | None = None
        self.current_rgb = None
        self.scale = 1.0
        self.offset = (0, 0)
        self.drag_start = None
        self.drag_rect = None
        self.tkimg = None

        self.detector = core.FaceDetector()
        self.jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

        self._build_ui()
        self._on_mode()
        self.after(100, self._poll)
        if initial:
            self.after(200, lambda: self.add_paths(initial))

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)

        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Button(top, text="➕ Images…", command=self.pick_files).pack(side="left")
        ttk.Button(top, text="📁 Dossier…", command=self.pick_folder).pack(side="left", padx=4)
        ttk.Button(top, text="🗑 Vider la liste", command=self.clear).pack(side="left")

        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(top, text="Méthode :").pack(side="left")
        self.mode = tk.StringVar(value=core.MODES["caviardage"])
        cb = ttk.Combobox(top, textvariable=self.mode, values=list(core.MODES.values()),
                          state="readonly", width=34)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_mode())

        ttk.Label(top, text="Forme :").pack(side="left", padx=(8, 0))
        self.shape = tk.StringVar(value="Rectangle")
        cb2 = ttk.Combobox(top, textvariable=self.shape, values=["Rectangle", "Ellipse"],
                           state="readonly", width=10)
        cb2.pack(side="left", padx=4)
        cb2.bind("<<ComboboxSelected>>", lambda e: self.redraw())

        self.preview = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Aperçu du résultat", variable=self.preview,
                        command=self.redraw).pack(side="left", padx=10)


        # Ligne 2 : réglages
        opts = ttk.Frame(self, padding=(8, 0, 8, 6))
        opts.pack(fill="x")
        ttk.Label(opts, text="Sensibilité de détection :").pack(side="left")
        self.sens = tk.DoubleVar(value=0.6)
        ttk.Scale(opts, from_=0.0, to=1.0, variable=self.sens, length=160).pack(side="left", padx=4)
        ttk.Button(opts, text="🔄 Relancer la détection", command=self.redetect_all).pack(side="left", padx=4)
        ttk.Label(opts, text="   Marge autour du visage :").pack(side="left")
        self.margin = tk.DoubleVar(value=0.30)
        ttk.Scale(opts, from_=0.0, to=0.8, variable=self.margin, length=140,
                  command=lambda v: self.redraw()).pack(side="left", padx=4)
        self.strength_lbl = ttk.Label(opts, text="   Force de l'effet :")
        self.strength_lbl.pack(side="left")
        self.strength = tk.DoubleVar(value=0.6)
        self.strength_scale = ttk.Scale(opts, from_=0.0, to=1.0, variable=self.strength, length=140,
                                        command=lambda v: self._on_strength())
        self.strength_scale.pack(side="left", padx=4)
        self.strength_val = ttk.Label(opts, text="60 %", width=5)
        self.strength_val.pack(side="left")
        self.out_dir = tk.StringVar(value="")

        # Corps : liste + canvas
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8)

        left = ttk.Frame(body)
        self.listbox = tk.Listbox(left, activestyle="none", font=("Segoe UI", 10), width=38,
                                  selectbackground="#0a84ff", borderwidth=0, highlightthickness=1)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self.listbox.bind("<Delete>", lambda e: self.remove_selected())
        body.add(left, weight=1)

        right = ttk.Frame(body)
        self.canvas = tk.Canvas(right, background="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        tools = ttk.Frame(right, padding=(0, 4))
        tools.pack(fill="x")
        ttk.Label(tools, text="Glisser : ajouter une zone  ·  Clic droit : supprimer  ·  ◀ ▶ : naviguer", foreground="#444").pack(side="left")
        ttk.Button(tools, text="Effacer les zones", command=self.clear_boxes).pack(side="right")
        ttk.Button(tools, text="Re-détecter", command=self.redetect_current).pack(side="right", padx=4)
        body.add(right, weight=4)

        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right)
        self.canvas.bind("<Button-2>", self.on_right)  # macOS
        self.bind("<Left>", lambda e: self.step(-1))
        self.bind("<Right>", lambda e: self.step(1))

        # Barre d'état
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="✔ Anonymiser et enregistrer", style="Big.TButton",
                   command=self.export).pack(side="right")
        self.progress = ttk.Progressbar(bottom, length=160, mode="determinate")
        self.progress.pack(side="right", padx=10)
        ttk.Button(bottom, text="📂 Sortie…", command=self.pick_out).pack(side="right")
        self.out_lbl = ttk.Label(bottom, text="→ sous-dossier « anonymise »", foreground="#555")
        self.out_lbl.pack(side="right", padx=6)
        engine = "IA (YuNet)" if self.detector.backend == "yunet" else "Haar (basique)"
        self.status = ttk.Label(bottom, text=f"Détection : {engine}.  ⚠ Vérifie chaque image.")
        self.status.pack(side="left")

    # ------------------------------------------------------------- fichiers --
    def pick_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="Choisir des images",
            filetypes=[("Images", " ".join("*" + e for e in sorted(core.IMAGE_EXTS))), ("Tous", "*.*")])
        self.add_paths(list(files))

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="Choisir un dossier d'images")
        if d:
            self.add_paths([d])

    def pick_out(self) -> None:
        d = filedialog.askdirectory(title="Dossier où enregistrer les images anonymisées")
        if d:
            self.out_dir.set(d)
            self.out_lbl.configure(text=f"→ {d if len(d) < 40 else '…' + d[-38:]}")

    def add_paths(self, paths: list[str]) -> None:
        known = {i.path for i in self.items}
        new = []
        for p in paths:
            p = os.path.abspath(p)
            files = core.list_images(p) if os.path.isdir(p) else [p]
            for f in files:
                if f not in known and os.path.splitext(f)[1].lower() in core.IMAGE_EXTS:
                    known.add(f)
                    new.append(Item(f))
        if not new:
            return
        self.items.extend(new)
        for it in new:
            self.listbox.insert("end", it.label)
            self.jobs.put((it, self.sens.get()))
        self._update_progress()
        if self.current is None:
            self.listbox.selection_set(len(self.items) - len(new))
            self.on_select()

    def clear(self) -> None:
        self.items.clear()
        self.listbox.delete(0, "end")
        self.current = None
        self.current_rgb = None
        self.canvas.delete("all")

    def remove_selected(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.items[idx]
        self.listbox.delete(idx)
        self.current = None
        if self.items:
            self.listbox.selection_set(min(idx, len(self.items) - 1))
            self.on_select()
        else:
            self.canvas.delete("all")

    # ------------------------------------------------------------ détection --
    def _worker(self) -> None:
        while True:
            it, sens = self.jobs.get()
            try:
                self.results.put(("start", it, None))
                rgb = core.load_image(it.path)
                boxes = self.detector.detect(rgb, sens)
                self.results.put(("ok", it, boxes))
            except Exception as exc:  # image corrompue, format inconnu...
                self.results.put(("err", it, str(exc)))

    def _poll(self) -> None:
        try:
            while True:
                kind, it, data = self.results.get_nowait()
                if it not in self.items:
                    continue
                if kind == "start":
                    it.state = "analyse"
                elif kind == "ok":
                    manual = [b for b in it.boxes if b.manual]
                    it.boxes = core.merge_boxes(data) + manual
                    it.state = "ok"
                else:
                    it.state, it.error = "erreur", data
                self._refresh_row(it)
                if it is self.current:
                    self.redraw()
                self._update_progress()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def redetect_all(self) -> None:
        for it in self.items:
            it.state = "attente"
            self._refresh_row(it)
            self.jobs.put((it, self.sens.get()))

    def redetect_current(self) -> None:
        if self.current:
            self.current.state = "attente"
            self._refresh_row(self.current)
            self.jobs.put((self.current, self.sens.get()))

    def clear_boxes(self) -> None:
        if self.current:
            self.current.boxes = []
            self._refresh_row(self.current)
            self.redraw()

    def _refresh_row(self, it: Item) -> None:
        idx = self.items.index(it)
        sel = self.listbox.curselection()
        self.listbox.delete(idx)
        self.listbox.insert(idx, it.label)
        if it.state == "erreur":
            self.listbox.itemconfig(idx, foreground="#c00")
        if sel and sel[0] == idx:
            self.listbox.selection_set(idx)

    def _update_progress(self) -> None:
        total = len(self.items)
        done = sum(i.state in ("ok", "erreur", "fait") for i in self.items)
        self.progress.configure(maximum=max(total, 1), value=done)

    # -------------------------------------------------------------- aperçu --
    def on_select(self, _e=None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        it = self.items[sel[0]]
        if it is self.current:
            return
        self.current = it
        try:
            self.current_rgb = core.load_image(it.path)
        except Exception as exc:
            self.current_rgb = None
            self.canvas.delete("all")
            self.canvas.create_text(20, 20, anchor="nw", fill="white", text=f"Image illisible : {exc}")
            return
        self.redraw()

    def step(self, d: int) -> None:
        if not self.items:
            return
        sel = self.listbox.curselection()
        idx = (sel[0] if sel else 0) + d
        idx = max(0, min(len(self.items) - 1, idx))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self.on_select()

    def _on_mode(self) -> None:
        # Le caviardage est déjà « 100 % » : la force n'a pas de sens, on grise le curseur.
        state = ["disabled"] if self._mode_key() == "caviardage" else ["!disabled"]
        self.strength_scale.state(state)
        self.strength_lbl.state(state)
        self.strength_val.state(state)
        if self._mode_key() != "caviardage" and not self.preview.get():
            self.preview.set(True)  # on montre directement l'effet du curseur
        self.redraw()

    def _on_strength(self) -> None:
        self.strength_val.configure(text=f"{round(self.strength.get() * 100)} %")
        if self.preview.get():
            self.redraw()

    def _mode_key(self) -> str:
        return next(k for k, v in core.MODES.items() if v == self.mode.get())

    def redraw(self) -> None:
        self.canvas.delete("all")
        if self.current is None or self.current_rgb is None:
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    fill="#aaa", font=("Segoe UI", 14),
                                    text="Ajoute des images avec « ➕ Images… » ou « 📁 Dossier… »\n"
                                         "(ou glisse-les directement sur le .exe)")
            return
        rgb = self.current_rgb
        boxes = self.current.boxes
        if self.preview.get():
            rgb = core.anonymize(rgb, boxes, self._mode_key(), self.shape.get().lower(), self.margin.get(),
                                 strength=self.strength.get())
        H, W = rgb.shape[:2]
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        self.scale = min(cw / W, ch / H, 1.0)
        dw, dh = max(1, int(W * self.scale)), max(1, int(H * self.scale))
        self.offset = ((cw - dw) // 2, (ch - dh) // 2)
        img = Image.fromarray(rgb).resize((dw, dh), Image.LANCZOS)
        self.tkimg = ImageTk.PhotoImage(img)
        ox, oy = self.offset
        self.canvas.create_image(ox, oy, anchor="nw", image=self.tkimg)

        if not self.preview.get():
            for b in boxes:
                e = b.expanded(0 if b.manual else self.margin.get(), W, H)
                col = COL_MANUAL if b.manual else COL_AUTO
                x0, y0 = ox + e.x * self.scale, oy + e.y * self.scale
                x1, y1 = x0 + e.w * self.scale, y0 + e.h * self.scale
                if self.shape.get() == "Ellipse":
                    self.canvas.create_oval(x0, y0, x1, y1, outline=col, width=2, dash=(4, 2))
                self.canvas.create_rectangle(x0, y0, x1, y1, outline=col, width=2)
        if self.current.state in ("attente", "analyse"):
            self.canvas.create_text(ox + 10, oy + 10, anchor="nw", fill="yellow",
                                    font=("Segoe UI", 12, "bold"), text="Analyse en cours…")

    # -------------------------------------------------------------- souris --
    def _to_img(self, x: float, y: float) -> tuple[int, int]:
        ox, oy = self.offset
        H, W = self.current_rgb.shape[:2]
        ix = int((x - ox) / self.scale)
        iy = int((y - oy) / self.scale)
        return max(0, min(W, ix)), max(0, min(H, iy))

    def on_press(self, e) -> None:
        if self.current_rgb is None:
            return
        self.drag_start = (e.x, e.y)
        self.drag_rect = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=COL_MANUAL, width=2, dash=(3, 2))

    def on_drag(self, e) -> None:
        if self.drag_rect:
            x0, y0 = self.drag_start
            self.canvas.coords(self.drag_rect, x0, y0, e.x, e.y)

    def on_release(self, e) -> None:
        if not self.drag_rect:
            return
        x0, y0 = self._to_img(*self.drag_start)
        x1, y1 = self._to_img(e.x, e.y)
        self.drag_rect = None
        if abs(x1 - x0) > 4 and abs(y1 - y0) > 4:
            self.current.boxes.append(core.Box(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0), manual=True))
            if self.current.state == "fait":
                self.current.state = "ok"
            self._refresh_row(self.current)
        self.redraw()

    def on_right(self, e) -> None:
        if self.current_rgb is None:
            return
        ix, iy = self._to_img(e.x, e.y)
        H, W = self.current_rgb.shape[:2]
        # supprime la plus petite zone sous le curseur
        hits = [b for b in self.current.boxes
                if (lambda z: z.x <= ix <= z.x + z.w and z.y <= iy <= z.y + z.h)(
                    b.expanded(0 if b.manual else self.margin.get(), W, H))]
        if hits:
            self.current.boxes.remove(min(hits, key=lambda b: b.w * b.h))
            self._refresh_row(self.current)
            self.redraw()

    # -------------------------------------------------------------- export --
    def export(self) -> None:
        if not self.items:
            messagebox.showinfo(APP_NAME, "Aucune image dans la liste.")
            return
        pending = [i for i in self.items if i.state in ("attente", "analyse")]
        if pending:
            messagebox.showinfo(APP_NAME, "L'analyse n'est pas terminée, patiente quelques secondes.")
            return
        no_face = [i for i in self.items if i.state != "erreur" and not i.boxes]
        if no_face and not messagebox.askyesno(
                APP_NAME, f"{len(no_face)} image(s) sans aucune zone détectée.\n"
                          "Elles seront enregistrées sans modification (mais nettoyées de leurs métadonnées).\n\n"
                          "Continuer ?"):
            return

        mode, shape, margin = self._mode_key(), self.shape.get().lower(), self.margin.get()
        strength = self.strength.get()
        out_dir = self.out_dir.get() or None
        todo = [i for i in self.items if i.state != "erreur"]
        self.progress.configure(maximum=len(todo), value=0)
        self.status.configure(text="Enregistrement…")

        def run():
            errors, last = [], None
            for n, it in enumerate(todo, 1):
                try:
                    rgb = core.load_image(it.path)
                    out = core.anonymize(rgb, it.boxes, mode, shape, margin, strength=strength)
                    last = core.output_path(it.path, out_dir)
                    core.save_clean(out, last)
                    it.state = "fait"
                except Exception as exc:
                    errors.append(f"{os.path.basename(it.path)} : {exc}")
                self.after(0, lambda n=n, it=it: (self.progress.configure(value=n), self._refresh_row(it)))
            self.after(0, lambda: self._export_done(len(todo) - len(errors), errors, last))

        threading.Thread(target=run, daemon=True).start()

    def _export_done(self, ok: int, errors: list[str], last: str | None) -> None:
        self.status.configure(text=f"{ok} image(s) anonymisée(s) et enregistrée(s).")
        msg = f"{ok} image(s) enregistrée(s) sans métadonnées."
        if errors:
            msg += "\n\nErreurs :\n" + "\n".join(errors[:10])
        if last and messagebox.askyesno(APP_NAME, msg + "\n\nOuvrir le dossier de sortie ?"):
            folder = os.path.dirname(last)
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", folder])
        elif not last:
            messagebox.showwarning(APP_NAME, msg)


def main() -> None:
    if sys.platform == "win32":
        try:  # rendu net sur les écrans haute résolution
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App(sys.argv[1:]).mainloop()


if __name__ == "__main__":
    main()
