"""
PDF Impostor - Main GUI Application
====================================
A clean, Windows-native Tkinter interface for PDF imposition.

Architecture:
  MainWindow
    ├── FileFrame        – input / output path selection
    ├── SignatureFrame   – sheets-per-signature slider + info
    ├── DuplexFrame      – duplex mode toggle buttons
    ├── AdvancedFrame    – zoom / offset / creep / center adjustment
    ├── ActionFrame      – Run button + progress bar
    └── StatusBar        – status messages
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.ttk as ttk
from pathlib import Path
from typing import Optional

from impostor.core.imposition import (
    DuplexMode, ImpositionSettings, _pad_page_count, detect_page_orientations, impose,
)
from impostor.utils.config import AppConfig, _PROFILE_KEYS, add_recent_file

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────
COLORS = {
    "bg":           "#1C1C1C",   # fond principal – gris charbon
    "surface":      "#252525",   # cartes / panneaux
    "surface2":     "#2E2E2E",   # champs de saisie / sliders
    "accent":       "#2E7D52",   # boutons principaux – vert foncé
    "accent_hover": "#3CA36B",   # survol des boutons
    "accent_dark":  "#1A5C3A",   # état enfoncé / drag-enter
    "text":         "#F0F0F0",   # texte principal – blanc cassé
    "text_dim":     "#888888",   # labels secondaires et placeholders
    "text_bright":  "#FFFFFF",   # texte sur boutons colorés
    "success":      "#52B788",   # confirmations
    "warning":      "#F4A261",   # avertissements
    "error":        "#E76F51",   # erreurs
    "border":       "#3A3A3A",   # séparateurs et contours
}

DUPLEX_OPTIONS = [
    ("Auto recto-verso",      DuplexMode.AUTO_DUPLEX),
    ("Manuel + assemblage",   DuplexMode.MANUAL_COLLATE),
    ("Manuel sans assemblage", DuplexMode.MANUAL_NO_COLLATE),
]

HOWTO_TEXT = """\
📖  GUIDE D'UTILISATION – Imposition PDF
═══════════════════════════════════════════════════════════════

QU'EST-CE QUE L'IMPOSITION ?
─────────────────────────────
L'imposition consiste à réordonner et à disposer les pages d'un document
en 2-up (deux pages côte à côte) sur chaque feuille, de sorte que, une fois
imprimées, pliées et reliées, elles se lisent dans le bon ordre.

QU'EST-CE QU'UNE SIGNATURE ?
──────────────────────────────
Une signature est un groupe de feuilles pliées ensemble pour former un
cahier. Par exemple :

  • 1 feuille par signature → cahier de 4 pages (1 feuille pliée)
  • 4 feuilles par signature → cahier de 16 pages
  • 0 (Magazine)           → toutes les feuilles forment une seule signature

Pour les livres épais, plusieurs signatures sont cousues ou collées ensemble.

MODES RECTO-VERSO
─────────────────
  ✦ Automatique         : votre imprimante gère tout.
  ✦ Manuel + assemblage : imprimez les recto, réinsérez la pile telle quelle.
  ✦ Manuel sans assemblage : imprimez les recto, RETOURNEZ la pile, imprimez
                             les verso.

ÉTAPES RAPIDES
──────────────
  1. Sélectionnez votre PDF source.
  2. Choisissez le fichier de sortie.
  3. Réglez les feuilles par signature (0 = magazine).
  4. Choisissez le mode recto-verso.
  5. Cliquez sur « Imposer le PDF ».
  6. Imprimez le PDF généré, pliez et reliez !

OPTIONS AVANCÉES
────────────────
  ✦ Zoom (%)
      Réduit chaque page avant de la placer sur la feuille.
      Utile pour ajouter une marge blanche autour du contenu.
      Valeur par défaut : 1.0 (pas de réduction).
      Exemple : 0.95 laisse ~5 % de marge de chaque côté.

  ✦ Décalage intérieur
      Décale les pages vers la reliure (en points typographiques,
      1 pt ≈ 0,35 mm). Positif = vers l'intérieur du cahier.
      Utile pour compenser l'espace perdu dans la reliure.

  ✦ Compensation du creep
      Lors du pliage de plusieurs feuilles, les pages intérieures
      « glissent » vers l'extérieur. Ce paramètre applique un décalage
      croissant à chaque feuille pour compenser cet effet.
      Plus votre signature est épaisse, plus cette valeur devra être élevée.

  ✦ Centrage imprimante
      Certaines imprimantes ne centrent pas parfaitement leur impression.
      Ce réglage corrige le décalage horizontal global (en points).
      Mesurez l'écart entre les deux colonnes sur une page imprimée
      et entrez la moitié de cet écart avec le signe approprié.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

class TooltipMixin:
    """Simple tooltip support for any widget."""

    def add_tooltip(self, widget: tk.Widget, text: str) -> None:
        tip: Optional[tk.Toplevel] = None

        def show(event: tk.Event) -> None:
            nonlocal tip
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            lbl = tk.Label(
                tip, text=text, background=COLORS["surface"], foreground=COLORS["text"],
                relief="solid", borderwidth=1, font=("Segoe UI", 9),
                padx=8, pady=4, wraplength=300,
            )
            lbl.pack()

        def hide(_event: tk.Event) -> None:
            nonlocal tip
            if tip:
                tip.destroy()
                tip = None

        widget.bind("<Enter>", show, add=True)
        widget.bind("<Leave>", hide, add=True)


class StyledButton(tk.Button):
    """A button with hover effects using COLORS palette."""

    def __init__(
        self, parent: tk.Widget, *,
        text: str,
        command: Optional[callable] = None,
        style: str = "primary",
        **kwargs: object,
    ) -> None:
        bg = COLORS["accent"] if style == "primary" else COLORS["surface2"]
        fg = COLORS["text_bright"]
        super().__init__(
            parent,
            text=text,
            command=command,
            background=bg,
            foreground=fg,
            activebackground=COLORS["accent_hover"],
            activeforeground=COLORS["text_bright"],
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
            padx=16,
            pady=8,
            **kwargs,
        )
        self._bg = bg
        self.bind("<Enter>", lambda _: self.config(background=COLORS["accent_hover"]))
        self.bind("<Leave>", lambda _: self.config(background=self._bg))


class SectionLabel(tk.Label):
    def __init__(self, parent: tk.Widget, text: str, **kwargs: object) -> None:
        bg = parent.cget("background") if hasattr(parent, "cget") else COLORS["surface"]
        super().__init__(
            parent, text=text,
            background=bg,
            foreground=COLORS["text_dim"],
            font=("Segoe UI", 8, "bold"),
            **kwargs,
        )


class Card(tk.Frame):
    """A card frame with surface background."""

    def __init__(self, parent: tk.Widget, **kwargs: object) -> None:
        super().__init__(parent, background=COLORS["surface"], relief="flat", **kwargs)


def _make_scale(
    parent: tk.Widget,
    variable: tk.Variable,
    from_: float, to: float, resolution: float,
) -> tk.Scale:
    """Unified slider factory — ensures all sliders look identical.

    With bd=0 and highlightthickness=0 the only visible surfaces are
    the trough and the thumb. Setting background=accent makes the thumb
    the same green as the main action button.
    """
    return tk.Scale(
        parent,
        variable=variable,
        from_=from_, to=to, resolution=resolution,
        orient="horizontal",
        background=COLORS["accent"],
        foreground=COLORS["text_bright"],
        troughcolor=COLORS["surface2"],
        activebackground=COLORS["accent_hover"],
        highlightthickness=0, bd=0,
        showvalue=False,
        sliderlength=20,
        sliderrelief="flat",
    )


# ── Sub-frames ────────────────────────────────────────────────────────────────

class FileFrame(Card, TooltipMixin):
    def __init__(self, parent: tk.Widget, config: AppConfig) -> None:
        super().__init__(parent)
        self._config = config
        self._input_var = tk.StringVar()
        self._output_var = tk.StringVar()
        self._build()
        self._setup_drag_drop()

    def _build(self) -> None:
        header = tk.Frame(self, background=COLORS["surface"])
        header.grid(row=0, column=0, columnspan=4, sticky="ew", padx=16, pady=(10, 4))

        SectionLabel(header, text="FICHIERS").pack(side="left")

        # B — recent files menu button
        self._recent_btn = tk.Menubutton(
            header, text="Récents ▾",
            background=COLORS["surface2"], foreground=COLORS["text_dim"],
            activebackground=COLORS["surface2"], activeforeground=COLORS["text"],
            relief="flat", font=("Segoe UI", 8), cursor="hand2",
            padx=8, pady=3,
        )
        self._recent_menu = tk.Menu(
            self._recent_btn, tearoff=False,
            background=COLORS["surface2"], foreground=COLORS["text"],
            activebackground=COLORS["accent"], activeforeground=COLORS["text_bright"],
            font=("Segoe UI", 9),
        )
        self._recent_btn.configure(menu=self._recent_menu)
        self._recent_btn.pack(side="right")
        self._populate_recent_menu()

        for row, (lbl_text, var, browse_cmd) in enumerate([
            ("PDF source :", self._input_var,  self._browse_input),
            ("PDF sortie :", self._output_var, self._browse_output),
        ], start=1):
            tk.Label(
                self, text=lbl_text,
                background=COLORS["surface"], foreground=COLORS["text"],
                font=("Segoe UI", 10),
            ).grid(row=row, column=0, sticky="w", padx=16, pady=4)

            entry = tk.Entry(
                self, textvariable=var, width=44,
                background=COLORS["surface2"], foreground=COLORS["text"],
                insertbackground=COLORS["text"], relief="flat",
                font=("Segoe UI", 10), bd=4,
            )
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=4)

            if row == 1:
                self._input_entry = entry

            StyledButton(
                self, text="Parcourir…", style="secondary", command=browse_cmd,
            ).grid(row=row, column=2, padx=(0, 16), pady=4)

        self.columnconfigure(1, weight=1)

        tk.Frame(self, height=6, background=COLORS["surface"]).grid(
            row=3, column=0)

    def _populate_recent_menu(self) -> None:
        self._recent_menu.delete(0, "end")
        recent: list[str] = self._config.get("recent_files", [])
        valid = [p for p in recent if Path(p).exists()]
        if not valid:
            self._recent_menu.add_command(label="(aucun fichier récent)", state="disabled")
        else:
            for p in valid:
                self._recent_menu.add_command(
                    label=Path(p).name,
                    command=lambda path=p: self._set_input(path),
                )

    def _setup_drag_drop(self) -> None:
        """Wire drag-and-drop on the input entry using tkinterdnd2 if available."""
        try:
            self._input_entry.drop_target_register("DND_Files")
            self._input_entry.dnd_bind("<<Drop>>", self._on_drop)
            self._input_entry.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self._input_entry.dnd_bind("<<DragLeave>>", self._on_drag_leave)
        except (AttributeError, tk.TclError):
            pass

    def _on_drag_enter(self, _event: object) -> None:
        self._input_entry.config(background=COLORS["accent_dark"])

    def _on_drag_leave(self, _event: object) -> None:
        self._input_entry.config(background=COLORS["surface2"])

    def _on_drop(self, event: object) -> None:
        self._on_drag_leave(event)
        raw: str = getattr(event, "data", "")
        paths = raw.strip("{}").split("} {") if "{" in raw else raw.split()
        pdf_paths = [p.strip("{}") for p in paths if p.lower().endswith(".pdf")]
        if pdf_paths:
            self._set_input(pdf_paths[0])

    def _set_input(self, path: str) -> None:
        self._input_var.set(path)
        self._config.set("last_input_dir", str(Path(path).parent))
        p = Path(path)
        self._output_var.set(str(p.parent / f"{p.stem}_imposed.pdf"))
        add_recent_file(self._config, path)
        self._config.save()
        self._populate_recent_menu()

    def _browse_input(self) -> None:
        path = fd.askopenfilename(
            title="Sélectionner le PDF source",
            initialdir=self._config.get("last_input_dir", str(Path.home())),
            filetypes=[("Fichiers PDF", "*.pdf"), ("Tous les fichiers", "*.*")],
        )
        if path:
            self._set_input(path)

    def _browse_output(self) -> None:
        path = fd.asksaveasfilename(
            title="Enregistrer le PDF imposé",
            initialdir=self._config.get("last_output_dir", str(Path.home())),
            defaultextension=".pdf",
            filetypes=[("Fichiers PDF", "*.pdf")],
        )
        if path:
            self._output_var.set(path)
            self._config.set("last_output_dir", str(Path(path).parent))

    @property
    def input_path(self) -> str:
        return self._input_var.get().strip()

    @property
    def output_path(self) -> str:
        return self._output_var.get().strip()


class SignatureFrame(Card, TooltipMixin):
    def __init__(self, parent: tk.Widget, config: AppConfig) -> None:
        super().__init__(parent)
        self._config = config
        self._sps_var = tk.IntVar(value=config.get("sheets_per_signature", 0))
        self._build()

    def _build(self) -> None:
        SectionLabel(self, text="SIGNATURE").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 4))

        lbl = tk.Label(
            self, text="Feuilles par signature :",
            background=COLORS["surface"], foreground=COLORS["text"],
            font=("Segoe UI", 10),
        )
        lbl.grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.add_tooltip(lbl, "0 = Magazine (toutes les feuilles forment un seul cahier)")

        _make_scale(self, self._sps_var, 0, 20, 1).grid(
            row=1, column=1, sticky="ew", padx=8, pady=4)

        self._value_lbl = tk.Label(
            self, width=14,
            background=COLORS["surface"], foreground=COLORS["accent"],
            font=("Segoe UI Semibold", 10),
        )
        self._value_lbl.grid(row=1, column=2, padx=(0, 16))

        self._desc_lbl = tk.Label(
            self, wraplength=440, justify="left",
            background=COLORS["surface"], foreground=COLORS["text_dim"],
            font=("Segoe UI", 9),
        )
        self._desc_lbl.grid(row=2, column=0, columnspan=3, sticky="w",
                             padx=16, pady=(0, 10))

        self.columnconfigure(1, weight=1)
        self._sps_var.trace_add("write", lambda *_: self._on_change())
        self._on_change()

    def _on_change(self) -> None:
        self._sps_var.set(int(self._sps_var.get()))
        v = self._sps_var.get()
        if v == 0:
            label = "Magazine / Livret"
            desc = "Toutes les feuilles forment une seule grande signature – idéal pour les magazines et petits livrets."
        elif v == 1:
            label = "1 feuille"
            desc = "Chaque signature = 1 feuille pliée = 4 pages. Bon pour tester, mais peu pratique pour les agendas."
        else:
            label = f"{v} feuilles"
            desc = f"Chaque signature = {v} feuilles = {v * 4} pages. Pliez {v} feuilles ensemble, puis reliez les carnets."
        self._value_lbl.config(text=label)
        self._desc_lbl.config(text=desc)

    @property
    def sheets_per_signature(self) -> int:
        return self._sps_var.get()


class DuplexFrame(Card):
    """Mode recto-verso — barre de boutons toggle mutuellement exclusifs."""

    def __init__(self, parent: tk.Widget, config: AppConfig) -> None:
        super().__init__(parent)
        self._config = config
        stored = config.get("duplex_mode", "AUTO_DUPLEX")
        self._mode_var = tk.StringVar(value=stored)
        self._buttons: list[tuple[tk.Button, str]] = []
        self._build()

    def _build(self) -> None:
        SectionLabel(self, text="MODE RECTO-VERSO").grid(
            row=0, column=0, sticky="w", padx=16, pady=(10, 6))

        bar = tk.Frame(self, background=COLORS["surface"])
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        for label, mode in DUPLEX_OPTIONS:
            name = mode.name
            btn = tk.Button(
                bar,
                text=label,
                font=("Segoe UI Semibold", 9),
                relief="flat", borderwidth=0,
                cursor="hand2",
                padx=14, pady=7,
                command=lambda n=name: self._select(n),
            )
            btn.pack(side="left", padx=(0, 4))
            self._buttons.append((btn, name))

        self._refresh()

    def _select(self, name: str) -> None:
        self._mode_var.set(name)
        self._refresh()

    def _refresh(self) -> None:
        current = self._mode_var.get()
        for btn, name in self._buttons:
            if name == current:
                btn.config(
                    background=COLORS["accent"],
                    foreground=COLORS["text_bright"],
                    activebackground=COLORS["accent_hover"],
                    activeforeground=COLORS["text_bright"],
                )
            else:
                btn.config(
                    background=COLORS["surface2"],
                    foreground=COLORS["text_dim"],
                    activebackground=COLORS["surface2"],
                    activeforeground=COLORS["text"],
                )

    @property
    def duplex_mode(self) -> DuplexMode:
        return DuplexMode[self._mode_var.get()]


class AdvancedFrame(Card, TooltipMixin):
    """Options avancées : zoom, décalage, creep, centrage."""

    _PARAMS = [
        ("Zoom (%)",            "zoom",              0.5,  1.0, 0.01,
         "Réduire les pages pour ajouter une marge d'impression."),
        ("Décalage intérieur",  "inside_offset",    -50,   50,  1.0,
         "Décalage supplémentaire vers la reliure (en points)."),
        ("Compensation creep",  "creep_compensation", 0,   10,  0.5,
         "Compense le glissement des pages intérieures lors du pliage."),
        ("Centrage imprimante", "center_adjustment", -20,  20,  0.5,
         "Corrige le décalage horizontal de votre imprimante."),
    ]

    def __init__(self, parent: tk.Widget, config: AppConfig) -> None:
        super().__init__(parent)
        self._config = config
        self._vars: dict[str, tk.DoubleVar] = {
            key: tk.DoubleVar(value=config.get(key, 0.0))
            for _, key, *_ in self._PARAMS
        }
        # zoom default is 1.0, not 0.0
        self._vars["zoom"].set(config.get("zoom", 1.0))
        self._build()

    def _build(self) -> None:
        SectionLabel(self, text="OPTIONS AVANCÉES").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 4))

        for row, (label, key, mn, mx, res, tooltip) in enumerate(self._PARAMS, start=1):
            lbl = tk.Label(
                self, text=label + " :",
                background=COLORS["surface"], foreground=COLORS["text"],
                font=("Segoe UI", 10),
            )
            lbl.grid(row=row, column=0, sticky="w", padx=16, pady=3)
            self.add_tooltip(lbl, tooltip)

            _make_scale(self, self._vars[key], mn, mx, res).grid(
                row=row, column=1, sticky="ew", padx=8, pady=3)

            tk.Label(
                self, textvariable=self._vars[key], width=6,
                background=COLORS["surface"], foreground=COLORS["accent"],
                font=("Segoe UI", 9),
            ).grid(row=row, column=2, padx=(0, 16))

        tk.Frame(self, height=8, background=COLORS["surface"]).grid(
            row=len(self._PARAMS) + 1, column=0)

        self.columnconfigure(1, weight=1)

    @property
    def zoom(self) -> float:
        return round(self._vars["zoom"].get(), 3)

    @property
    def inside_offset(self) -> float:
        return round(self._vars["inside_offset"].get(), 1)

    @property
    def creep_compensation(self) -> float:
        return round(self._vars["creep_compensation"].get(), 2)

    @property
    def center_adjustment(self) -> float:
        return round(self._vars["center_adjustment"].get(), 1)


# ── Profiles frame (C) ────────────────────────────────────────────────────────

class ProfilesFrame(Card, TooltipMixin):
    """Save / load named setting presets."""

    def __init__(
        self,
        parent: tk.Widget,
        config: AppConfig,
        on_load: object,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_load = on_load
        self._name_var = tk.StringVar()
        self._build()

    def _build(self) -> None:
        SectionLabel(self, text="PROFILS").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(10, 4))

        tk.Label(
            self, text="Nom :",
            background=COLORS["surface"], foreground=COLORS["text"],
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", padx=16, pady=4)

        self._combo = ttk.Combobox(
            self, textvariable=self._name_var, width=22,
            font=("Segoe UI", 10),
        )
        self._combo.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=4)
        self._refresh_list()

        StyledButton(self, text="Sauvegarder", style="secondary",
                     command=self._save).grid(row=1, column=2, padx=(0, 4), pady=4)
        StyledButton(self, text="Charger", style="secondary",
                     command=self._load).grid(row=1, column=3, padx=(0, 4), pady=4)
        StyledButton(self, text="Supprimer", style="secondary",
                     command=self._delete).grid(row=1, column=4, padx=(0, 16), pady=4)

        tk.Frame(self, height=6, background=COLORS["surface"]).grid(row=2, column=0)
        self.columnconfigure(1, weight=1)

    def _refresh_list(self) -> None:
        profiles: dict = self._config.get("profiles", {})
        self._combo.configure(values=sorted(profiles.keys()))

    def _save(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            return
        profiles: dict = dict(self._config.get("profiles", {}))
        profiles[name] = self._on_load("collect")  # type: ignore[operator]
        self._config.set("profiles", profiles)
        self._config.save()
        self._refresh_list()

    def _load(self) -> None:
        name = self._name_var.get().strip()
        profiles: dict = self._config.get("profiles", {})
        if name not in profiles:
            return
        self._on_load("apply", profiles[name])  # type: ignore[operator]

    def _delete(self) -> None:
        name = self._name_var.get().strip()
        profiles: dict = dict(self._config.get("profiles", {}))
        if name in profiles:
            del profiles[name]
            self._config.set("profiles", profiles)
            self._config.save()
            self._name_var.set("")
            self._refresh_list()


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(TooltipMixin):
    """Root application window."""

    APP_TITLE = "PDF Impostor"
    VERSION = "1.0.0"

    def __init__(self) -> None:
        self._config = AppConfig()
        self._running = False

        try:
            from tkinterdnd2 import TkinterDnD  # type: ignore[import-untyped]
            self._root = TkinterDnD.Tk()
            self._dnd_available = True
        except Exception:
            self._root = tk.Tk()
            self._dnd_available = False
        self._root.title(f"{self.APP_TITLE}  v{self.VERSION}")
        self._root.resizable(True, True)
        self._root.minsize(640, 580)
        self._root.configure(background=COLORS["bg"])

        geo = self._config.get("window_geometry", "")
        if geo:
            try:
                self._root.geometry(geo)
            except Exception:
                pass
        else:
            self._root.geometry("740x660")

        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_styles()
        self._build_ui()

    def _apply_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Horizontal.TProgressbar",
                        troughcolor=COLORS["surface2"],
                        background=COLORS["accent"])
        style.configure("TNotebook",
                        background=COLORS["bg"],
                        borderwidth=0)
        style.configure("TNotebook.Tab",
                        background=COLORS["surface2"],
                        foreground=COLORS["text_dim"],
                        padding=[12, 6],
                        font=("Segoe UI", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", COLORS["surface"])],
                  foreground=[("selected", COLORS["text"])])

    def _build_ui(self) -> None:
        root = self._root

        # ── Title bar ──────────────────────────────────────────────────────
        title_frame = tk.Frame(root, background=COLORS["bg"])
        title_frame.pack(fill="x", padx=20, pady=(12, 6))

        tk.Label(
            title_frame, text="📄  PDF Impostor",
            background=COLORS["bg"], foreground=COLORS["text_bright"],
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")

        StyledButton(title_frame, text="Guide", style="secondary",
                     command=self._show_howto).pack(side="right", padx=4)

        # ── Notebook ───────────────────────────────────────────────────────
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=8, pady=2)

        settings_tab = tk.Frame(notebook, background=COLORS["bg"])
        preview_tab = tk.Frame(notebook, background=COLORS["bg"])
        notebook.add(settings_tab, text="  ⚙ Paramètres  ")
        notebook.add(preview_tab, text="  👁 Aperçu  ")

        # ── Settings tab – direct layout, no scroll ────────────────────────
        CARD_PAD = {"padx": 12, "pady": 4, "fill": "x"}

        self._file_frame = FileFrame(settings_tab, self._config)
        self._file_frame.pack(**CARD_PAD)

        self._sig_frame = SignatureFrame(settings_tab, self._config)
        self._sig_frame.pack(**CARD_PAD)

        self._duplex_frame = DuplexFrame(settings_tab, self._config)
        self._duplex_frame.pack(**CARD_PAD)

        self._adv_frame = AdvancedFrame(settings_tab, self._config)
        self._adv_frame.pack(**CARD_PAD)

        self._profiles_frame = ProfilesFrame(
            settings_tab, self._config, self._profiles_dispatch,
        )
        self._profiles_frame.pack(**CARD_PAD)

        # ── Preview tab ────────────────────────────────────────────────────
        from impostor.gui.preview import ImpositionPreview
        self._preview = ImpositionPreview(preview_tab)
        self._preview.pack(fill="both", expand=True)

        self._sig_frame._sps_var.trace_add("write", self._refresh_preview)
        self._duplex_frame._mode_var.trace_add("write", self._refresh_preview)
        for var in self._adv_frame._vars.values():
            var.trace_add("write", self._refresh_preview)
        self._file_frame._input_var.trace_add("write", self._on_input_changed)
        notebook.bind("<<NotebookTabChanged>>", lambda _: self._refresh_preview())

        self._bind_shortcuts()
        self._setup_window_dnd()

        # ── Action area ────────────────────────────────────────────────────
        action_card = Card(root)
        action_card.pack(fill="x", padx=12, pady=(4, 0))

        self._progress = ttk.Progressbar(action_card, mode="indeterminate")
        self._progress.pack(fill="x", padx=16, pady=(10, 4))
        self._progress.stop()

        self._run_btn = StyledButton(
            action_card, text="▶  Imposer le PDF", command=self._run,
        )
        self._run_btn.config(font=("Segoe UI Semibold", 12), pady=10)
        self._run_btn.pack(fill="x", padx=16, pady=(0, 10))

        # ── Status bar ─────────────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Prêt.")
        tk.Label(
            root, textvariable=self._status_var,
            background=COLORS["surface"], foreground=COLORS["text_dim"],
            font=("Segoe UI", 9), anchor="w", padx=12, pady=4,
        ).pack(fill="x", side="bottom")

    # ── Profiles helpers (C) ───────────────────────────────────────────────

    def _profiles_dispatch(self, action: str, data: dict | None = None) -> dict | None:
        """Single callback used by ProfilesFrame for both collect and apply."""
        if action == "collect":
            return {k: self._config.get(k) for k in _PROFILE_KEYS}
        if action == "apply" and data:
            self._apply_settings(data)
        return None

    def _apply_settings(self, d: dict) -> None:
        if "sheets_per_signature" in d:
            self._sig_frame._sps_var.set(int(d["sheets_per_signature"]))
        if "duplex_mode" in d:
            self._duplex_frame._select(str(d["duplex_mode"]))
        for key in ("zoom", "inside_offset", "creep_compensation", "center_adjustment"):
            if key in d and key in self._adv_frame._vars:
                self._adv_frame._vars[key].set(float(d[key]))

    # ── Keyboard shortcuts (A) ─────────────────────────────────────────────

    def _bind_shortcuts(self) -> None:
        self._root.bind("<Return>", self._shortcut_run)
        self._root.bind("<Escape>", self._shortcut_cancel)
        self._root.bind("<Control-o>", self._shortcut_open)

    def _shortcut_run(self, _event: tk.Event) -> None:
        if isinstance(self._root.focus_get(), tk.Entry):
            return
        self._run()

    def _shortcut_cancel(self, _event: tk.Event) -> None:
        if self._running:
            self._running = False
            self._progress.stop()
            self._run_btn.config(state="normal", text="▶  Imposer le PDF")
            self._status("Annulé.")

    def _shortcut_open(self, _event: tk.Event) -> None:
        self._file_frame._browse_input()

    def _setup_window_dnd(self) -> None:
        """Register the root window as a drop target for PDF files.

        Requires tkinterdnd2.  When a PDF is dropped anywhere on the window
        (outside of widgets that already handle DnD themselves), it is loaded
        as the input file.
        """
        if not self._dnd_available:
            return
        try:
            self._root.drop_target_register("DND_Files")
            self._root.dnd_bind("<<Drop>>", self._on_window_drop)
            self._root.dnd_bind("<<DragEnter>>", self._on_window_drag_enter)
            self._root.dnd_bind("<<DragLeave>>", self._on_window_drag_leave)
        except Exception:
            pass

    def _on_window_drop(self, event: object) -> None:
        self._on_window_drag_leave(event)
        raw: str = getattr(event, "data", "")
        paths = raw.strip("{}").split("} {") if "{" in raw else raw.split()
        pdf_paths = [p.strip("{}") for p in paths if p.lower().endswith(".pdf")]
        if pdf_paths:
            self._file_frame._set_input(pdf_paths[0])

    def _on_window_drag_enter(self, *_: object) -> None:
        self._root.configure(background=COLORS["accent_dark"])

    def _on_window_drag_leave(self, *_: object) -> None:
        self._root.configure(background=COLORS["bg"])

    # ── Actions ────────────────────────────────────────────────────────────

    def _status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _on_input_changed(self, *_: object) -> None:
        path = self._file_frame.input_path
        if path and Path(path).exists():
            try:
                import pypdf
                self._preview_page_count = len(pypdf.PdfReader(path).pages)
            except Exception:
                self._preview_page_count = 0
            total, portrait, landscape = detect_page_orientations(Path(path))
            if total > 0 and portrait > 0 and landscape > 0:
                self._status(
                    f"⚠ Mix portrait/paysage détecté "
                    f"({portrait} portrait, {landscape} paysage) — vérifiez le résultat."
                )
            else:
                self._status("Prêt.")
        else:
            self._preview_page_count = 0
        self._refresh_preview()

    def _refresh_preview(self, *_: object) -> None:
        self._preview.update_preview(
            num_pages=getattr(self, "_preview_page_count", 0),
            sheets_per_sig=self._sig_frame.sheets_per_signature,
            creep_compensation=self._adv_frame.creep_compensation,
        )

    def _run(self) -> None:
        if self._running:
            return

        inp = self._file_frame.input_path
        out = self._file_frame.output_path

        if not inp:
            mb.showerror("Erreur", "Veuillez sélectionner un fichier PDF source.")
            return
        if not Path(inp).exists():
            mb.showerror("Erreur", f"Fichier introuvable :\n{inp}")
            return
        if not out:
            mb.showerror("Erreur", "Veuillez spécifier un fichier de sortie.")
            return

        num_pages = getattr(self, "_preview_page_count", 0)
        sps = self._sig_frame.sheets_per_signature
        if num_pages > 0:
            effective_sps = sps if sps > 0 else (-(num_pages // -4))  # ceil div
            padded = _pad_page_count(num_pages, effective_sps)
            blanks = padded - num_pages
            if blanks > 0:
                if not mb.askyesno(
                    "Pages vierges ajoutées",
                    f"{blanks} page(s) vierge(s) seront ajoutées pour compléter\n"
                    f"la dernière signature ({num_pages} → {padded} pages).\n\n"
                    "Continuer quand même ?",
                    icon="warning",
                ):
                    return

        settings = ImpositionSettings(
            input_path=Path(inp),
            output_path=Path(out),
            sheets_per_signature=self._sig_frame.sheets_per_signature,
            duplex_mode=self._duplex_frame.duplex_mode,
            zoom=self._adv_frame.zoom,
            inside_offset=self._adv_frame.inside_offset,
            creep_compensation=self._adv_frame.creep_compensation,
            center_adjustment=self._adv_frame.center_adjustment,
        )

        self._config.set("sheets_per_signature", settings.sheets_per_signature)
        self._config.set("duplex_mode", settings.duplex_mode.name)
        self._config.set("zoom", settings.zoom)
        self._config.set("inside_offset", settings.inside_offset)
        self._config.set("creep_compensation", settings.creep_compensation)
        self._config.set("center_adjustment", settings.center_adjustment)
        self._config.save()

        self._running = True
        self._run_btn.config(state="disabled", text="⏳  Traitement en cours…")
        self._progress.start(10)
        self._status("Imposition en cours…")

        def _worker() -> None:
            result = impose(settings)
            self._root.after(0, lambda: self._on_done(result))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_done(self, result) -> None:
        self._running = False
        self._progress.stop()
        self._run_btn.config(state="normal", text="▶  Imposer le PDF")

        if result.success:
            lines = [
                "PDF imposé avec succès !",
                f"Pages : {result.total_pages}  →  Feuilles : {result.sheets_total}"
                f"  |  Signatures : {result.num_signatures}",
            ]
            lines.append(f"\nFichier : {result.output_path}")
            self._status(f"Terminé – {result.sheets_total} feuille(s) à imprimer.")

            if mb.askyesno("Succès", "\n".join(lines) + "\n\nOuvrir le fichier maintenant ?"):
                self._open_file(result.output_path)
        else:
            self._status(f"Erreur : {result.error}")
            mb.showerror("Erreur lors de l'imposition", result.error or "Erreur inconnue.")

    @staticmethod
    def _open_file(path) -> None:
        import os, sys
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
        except Exception as exc:
            logger.warning("Could not open file: %s", exc)

    def _show_howto(self) -> None:
        win = tk.Toplevel(self._root)
        win.title("Guide d'utilisation – PDF Impostor")
        win.geometry("600x520")
        win.configure(background=COLORS["bg"])
        win.resizable(True, True)

        text = tk.Text(
            win, wrap="word",
            background=COLORS["surface"], foreground=COLORS["text"],
            font=("Consolas", 10), relief="flat", padx=20, pady=16,
            insertbackground=COLORS["text"], state="normal",
        )
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("1.0", HOWTO_TEXT)
        text.config(state="disabled")

        StyledButton(win, text="Fermer", command=win.destroy).pack(pady=8)

    def _on_close(self) -> None:
        self._config.set("window_geometry", self._root.geometry())
        self._config.save()
        self._root.destroy()

    def run(self) -> None:
        self._root.mainloop()
