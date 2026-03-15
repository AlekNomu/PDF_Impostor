"""
Imposition Preview Widget
==========================
A Tkinter Canvas widget that renders a schematic of how pages will be
laid out on physical sheets for a given imposition configuration.

Each signature is drawn as a row of sheet pairs (recto / verso).
Each sheet half shows the page number that will be printed there.
Blank padding pages are shown in grey with "—".

Features:
  - Physical sheet number label below each R/V pair      (D)
  - Visual creep offset when creep_compensation > 0      (E)
  - Click a sheet to highlight its signature             (F)
  - Ctrl+scroll to zoom in/out                           (G)
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.ttk as ttk

from impostor.core.imposition import (
    _pad_page_count,
    _signature_page_order,
)

#  Palette
_C = {
    "bg":        "#1C1C1C",
    "surface":   "#252525",
    "accent":    "#2E7D52",
    "text":      "#F0F0F0",
    "text_dim":  "#888888",
    "border":    "#3A3A3A",
    "blank":     "#2E2E2E",
    "sig_a":     "#2A2A2A",
    "sig_b":     "#242424",
    "front":     "#52B788",
    "back":      "#3CA36B",
    "highlight": "#5A3E28",
}

#  Layout constants (base scale = 1.0)
_SHEET_W      = 110
_SHEET_H      = 68
_MARGIN_X     = 16
_MARGIN_Y     = 12
_LABEL_H      = 18
_SIG_GAP      = 14   # vertical gap between signatures
_RV_GAP       = 6    # vertical gap between Recto and Verso within a cell
_COL_GAP      = 20   # horizontal gap between the two columns
_ROW_GAP      = 10   # vertical gap between rows of cells
_SHEET_NUM_H  = 14   # height for "Feuille N" label below each cell
_SHEETS_PER_ROW = 2  # fixed grid width

_ZOOM_MIN  = 0.4
_ZOOM_MAX  = 2.5
_ZOOM_STEP = 0.1


class _AutoScrollbar(ttk.Scrollbar):
    """Scrollbar that shows itself only when content overflows the viewport."""

    def set(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(first, last)


class ImpositionPreview(tk.Frame):
    """
    Embeddable preview widget.

    Public API::

        preview = ImpositionPreview(parent)
        preview.pack(fill="both", expand=True)
        preview.update_preview(num_pages=12, sheets_per_sig=3,
                               creep_compensation=0.5)
    """

    def __init__(self, parent: tk.Widget, **kwargs: object) -> None:
        super().__init__(parent, background=_C["bg"], **kwargs)
        self._last_y: int = _MARGIN_Y

        # State for zoom (G)
        self._canvas_zoom: float = 1.0

        # Stored params for redraw on zoom (G)
        self._last_num_pages: int = 0
        self._last_sps: int = 0
        self._last_creep: float = 0.0

        # Highlight state (F)
        self._highlighted_sig: int | None = None

        # Debounce id for resize events
        self._resize_after_id: str | None = None

        self._build()

    #  Construction 

    def _build(self) -> None:
        header = tk.Frame(self, background=_C["bg"])
        header.pack(fill="x", padx=8, pady=(8, 2))

        tk.Label(
            header, text="Aperçu de l'imposition",
            background=_C["bg"], foreground=_C["text"],
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")

        self._info_lbl = tk.Label(
            header, text="",
            background=_C["bg"], foreground=_C["text_dim"],
            font=("Segoe UI", 9),
        )
        self._info_lbl.pack(side="right")

        wrap = tk.Frame(self, background=_C["bg"])
        wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            wrap, background=_C["bg"],
            highlightthickness=0, relief="flat",
        )
        hbar = _AutoScrollbar(wrap, orient="horizontal",
                               command=self._canvas.xview)
        vbar = _AutoScrollbar(wrap, orient="vertical",
                               command=self._canvas.yview)
        self._canvas.configure(xscrollcommand=hbar.set,
                                yscrollcommand=vbar.set)

        self._canvas.grid(row=0, column=0, sticky="nsew")
        hbar.grid(row=1, column=0, sticky="ew")
        vbar.grid(row=0, column=1, sticky="ns")

        hbar.grid_remove()
        vbar.grid_remove()

        # Scroll: plain wheel = vertical scroll; Ctrl+wheel = zoom (G)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        # Click → highlight signature (F)
        self._canvas.bind("<Button-1>", self._on_canvas_click)
        # Resize → responsive redraw
        self._canvas.bind("<Configure>", self._on_canvas_configure)

        self._render_placeholder()

    #  Public API 

    def update_preview(
        self,
        num_pages: int,
        sheets_per_sig: int,
        creep_compensation: float = 0.0,
    ) -> None:
        """Redraw for the given parameters."""
        self._last_num_pages = num_pages
        self._last_sps = sheets_per_sig
        self._last_creep = creep_compensation
        self._highlighted_sig = None
        self._redraw()

    #  Zoom helpers (G) 

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.state & 0x4:  # Ctrl held
            direction = 1 if event.delta > 0 else -1
            self._canvas_zoom = max(
                _ZOOM_MIN,
                min(_ZOOM_MAX, self._canvas_zoom + direction * _ZOOM_STEP),
            )
            self._redraw()
        else:
            self._canvas.yview_scroll(int(-1 * event.delta / 120), "units")

    def _sc(self, v: float) -> int:
        """Scale a base-grid value by the current zoom."""
        return max(1, round(v * self._canvas_zoom))

    def _on_canvas_configure(self, *_: object) -> None:
        """Debounced redraw on canvas resize."""
        if self._resize_after_id:
            self._canvas.after_cancel(self._resize_after_id)
        self._resize_after_id = self._canvas.after(60, self._redraw)

    #  Click → highlight (F)

    def _on_canvas_click(self, event: tk.Event) -> None:
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        items = self._canvas.find_overlapping(cx - 1, cy - 1, cx + 1, cy + 1)
        for item in items:
            for tag in self._canvas.gettags(item):
                if tag.startswith("sig_"):
                    sig_idx = int(tag[4:])
                    if self._highlighted_sig == sig_idx:
                        self._highlighted_sig = None
                    else:
                        self._highlighted_sig = sig_idx
                    self._apply_highlight()
                    return

    def _apply_highlight(self) -> None:
        """Recolour canvas items to reflect the current highlight state."""
        num_sigs = self._canvas.getvar("num_sigs") if False else self._last_num_sigs
        for s in range(num_sigs):
            tag = f"sig_{s}"
            fill = _C["highlight"] if s == self._highlighted_sig else (
                _C["sig_a"] if s % 2 == 0 else _C["sig_b"]
            )
            # Only recolour rectangle items (not text/lines)
            for item in self._canvas.find_withtag(tag):
                if self._canvas.type(item) == "rectangle":
                    self._canvas.itemconfig(item, fill=fill)

    def _redraw(self) -> None:
        self._canvas.delete("all")
        num_pages = self._last_num_pages
        if num_pages <= 0:
            self._render_placeholder()
            return

        sps    = self._last_sps if self._last_sps > 0 else (math.ceil(num_pages / 4) or 1)
        padded = _pad_page_count(num_pages, sps)
        num_sigs = padded // (4 * sps)
        self._last_num_sigs = num_sigs

        self._info_lbl.config(
            text=(f"{num_pages} page(s)  ·  {num_sigs} signature(s)"
                  f"  ·  {num_sigs * sps} feuille(s)")
        )

        canvas_w, canvas_h = self._render_grid(num_pages, sps, padded, num_sigs)
        canvas_h = self._render_legend(num_pages, padded, canvas_w)
        self._canvas.configure(scrollregion=(0, 0, canvas_w, canvas_h))

        # Reapply highlight if one was set before redraw
        if self._highlighted_sig is not None:
            self._apply_highlight()

    def _render_placeholder(self) -> None:
        self._canvas.create_text(
            200, 70,
            text="Sélectionnez un PDF pour voir l'aperçu",
            fill=_C["text_dim"], font=("Segoe UI", 10), anchor="center",
        )
        self._canvas.configure(scrollregion=(0, 0, 400, 140))
        self._info_lbl.config(text="")
        self._last_num_sigs = 0

    def _render_grid(
        self,
        num_pages: int,
        sps: int,
        padded: int,
        num_sigs: int,
    ) -> tuple[int, int]:
        """
        Draw all sheets in a 2-column grid (vertical scroll).

        Each cell = one physical sheet:  Recto (top) + Verso (below).
        Sheets fill left→right, 2 per row, then wrap to next row.
        Signatures are separated by a labelled divider line.
        Returns (canvas_width, canvas_height_so_far).
        """
        z   = self._canvas_zoom
        mx  = self._sc(_MARGIN_X)
        my  = self._sc(_MARGIN_Y)
        lh  = self._sc(_LABEL_H)
        sg  = self._sc(_SIG_GAP)
        rv  = self._sc(_RV_GAP)
        cg  = self._sc(_COL_GAP)
        rg  = self._sc(_ROW_GAP)
        snh = self._sc(_SHEET_NUM_H)

        # Responsive width: fill the actual canvas width
        canvas_w = max(1, self._canvas.winfo_width())
        if canvas_w <= 1:
            # Canvas not yet laid out – use a sane default
            canvas_w = mx + _SHEETS_PER_ROW * self._sc(_SHEET_W) + (_SHEETS_PER_ROW - 1) * cg + mx

        inner_w = canvas_w - 2 * mx - (_SHEETS_PER_ROW - 1) * cg
        sw = max(self._sc(40), inner_w // _SHEETS_PER_ROW)
        sh = max(self._sc(24), round(sw * _SHEET_H / _SHEET_W))
        cell_h = sh + rv + sh + snh
        col_x = [mx + c * (sw + cg) for c in range(_SHEETS_PER_ROW)]
        creep_px = min(self._last_creep * z, 20.0 * z)
        y = my
        global_sheet = 0

        for sig_idx in range(num_sigs):
            base      = sig_idx * 4 * sps
            order     = _signature_page_order(sps)
            sig_color = _C["sig_a"] if sig_idx % 2 == 0 else _C["sig_b"]
            sig_tag   = f"sig_{sig_idx}"

            # Signature label spanning full width
            self._canvas.create_text(
                mx, y,
                text=f"Signature {sig_idx + 1}  ·  {sps} feuille{'s' if sps > 1 else ''}",
                fill=_C["accent"],
                font=("Segoe UI", self._sc(8), "bold"),
                anchor="nw",
                tags=(sig_tag,),
            )
            self._canvas.create_line(
                mx, y + lh - self._sc(3),
                canvas_w - mx, y + lh - self._sc(3),
                fill=_C["border"],
                tags=(sig_tag,),
            )
            y += lh

            col = 0
            row_top = y

            for sheet_in_sig, (fr, fl, bl, br) in enumerate(order):
                cx        = col_x[col]
                creep_off = round(creep_px * sheet_in_sig)

                # Recto
                self._draw_sheet(
                    cx, row_top + creep_off,
                    left_pg=base + fr, right_pg=base + fl,
                    num_pages=num_pages,
                    side_label="R", fill=sig_color, side_color=_C["front"],
                    sig_tag=sig_tag, sw=sw, sh=sh,
                )
                # Verso
                self._draw_sheet(
                    cx, row_top + sh + rv + creep_off,
                    left_pg=base + bl, right_pg=base + br,
                    num_pages=num_pages,
                    side_label="V", fill=sig_color, side_color=_C["back"],
                    sig_tag=sig_tag, sw=sw, sh=sh,
                )
                # "Feuille N" label (D)
                global_sheet += 1
                self._canvas.create_text(
                    cx + sw // 2, row_top + sh + rv + sh + self._sc(3),
                    text=f"Feuille {global_sheet}",
                    fill=_C["text_dim"],
                    font=("Segoe UI", self._sc(7)),
                    anchor="n",
                    tags=(sig_tag,),
                )

                col += 1
                if col >= _SHEETS_PER_ROW:
                    col = 0
                    row_top += cell_h + rg

            # Advance y past the last (possibly partial) row
            if col > 0:
                row_top += cell_h + rg
            y = row_top + sg

        self._last_y = y
        return canvas_w, y

    def _render_legend(self, num_pages: int, padded: int, canvas_w: int) -> int:
        """Draw the R/V legend in the top-right corner, and a blank-page warning
        below the grid if needed.  Returns total canvas height."""
        mx  = self._sc(_MARGIN_X)
        my  = self._sc(_MARGIN_Y)
        box = self._sc(10)

        #  Top-right legend 
        items = [
            (_C["front"], "R = Recto"),
            (_C["back"],  "V = Verso"),
            (_C["blank"], "— = Vierge"),
        ]
        item_h = self._sc(16)
        ly = my
        for color, label in items:
            lx = canvas_w - mx - box - self._sc(72)
            self._canvas.create_rectangle(lx, ly + 2, lx + box, ly + box + 2,
                                           fill=color, outline="")
            self._canvas.create_text(
                lx + box + self._sc(4), ly + box // 2 + 2,
                text=label,
                fill=_C["text_dim"],
                font=("Segoe UI", self._sc(8)),
                anchor="w",
            )
            ly += item_h

        #  Blank-page warning below grid
        y = self._last_y
        blanks = padded - num_pages
        if blanks > 0:
            self._canvas.create_text(
                mx, y,
                text=f"⚠  {blanks} page(s) vierge(s) ajoutée(s) pour compléter la dernière signature.",
                fill="#F9E2AF",
                font=("Segoe UI", self._sc(8), "italic"),
                anchor="nw",
            )
            y += self._sc(18)

        return y + my

    def _draw_sheet(
        self,
        x: int, y: int,
        left_pg: int, right_pg: int,
        num_pages: int,
        side_label: str,
        fill: str,
        side_color: str,
        sig_tag: str,
        sw: int = 0,
        sh: int = 0,
    ) -> None:
        """Draw one 2-up sheet at (x, y)."""
        if not sw:
            sw = self._sc(_SHEET_W)
        if not sh:
            sh = self._sc(_SHEET_H)
        mid = x + sw // 2

        def label(pg: int) -> str:
            return "—" if pg >= num_pages else str(pg + 1)

        def bg(pg: int) -> str:
            return _C["blank"] if pg >= num_pages else fill

        def fg(pg: int) -> str:
            return _C["text_dim"] if pg >= num_pages else _C["text"]

        tags = (sig_tag,)
        font_main = ("Segoe UI", self._sc(10), "bold")
        font_badge = ("Segoe UI", self._sc(7), "bold")

        # Outer rect
        self._canvas.create_rectangle(
            x, y, x + sw, y + sh,
            fill=fill, outline=_C["border"], width=1, tags=tags,
        )
        # Fold line
        self._canvas.create_line(
            mid, y + self._sc(4), mid, y + sh - self._sc(4),
            fill=_C["border"], dash=(3, 3), tags=tags,
        )
        # Left half
        self._canvas.create_rectangle(
            x + 2, y + 2, mid - 2, y + sh - 2,
            fill=bg(left_pg), outline="", tags=tags,
        )
        self._canvas.create_text(
            x + sw // 4, y + sh // 2,
            text=label(left_pg), fill=fg(left_pg), font=font_main, tags=tags,
        )
        # Right half
        self._canvas.create_rectangle(
            mid + 2, y + 2, x + sw - 2, y + sh - 2,
            fill=bg(right_pg), outline="", tags=tags,
        )
        self._canvas.create_text(
            x + 3 * sw // 4, y + sh // 2,
            text=label(right_pg), fill=fg(right_pg), font=font_main, tags=tags,
        )
        # R/V badge
        bs = self._sc(12)
        bx, by = x + sw - bs - self._sc(2), y + self._sc(4)
        self._canvas.create_rectangle(
            bx, by, bx + bs, by + bs,
            fill=side_color, outline="", tags=tags,
        )
        self._canvas.create_text(
            bx + bs // 2, by + bs // 2,
            text=side_label, fill=_C["bg"], font=font_badge, tags=tags,
        )
