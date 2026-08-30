"""
build.py – Génère le .exe Windows avec PyInstaller.

Usage:
    pip install pyinstaller pypdf
    python build.py

Le .exe se retrouve dans dist/PDFImpostor/PDFImpostor.exe

Options:
    --onefile    Crée un unique .exe (plus lent au démarrage, plus facile à distribuer)
    --debug      Inclut une console pour le débogage
"""

from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path

try:
    import tkinterdnd2
except ImportError:
    tkinterdnd2 = None  # type: ignore[assignment]


APP_NAME   = "PDF Impostor"
ENTRY_POINT = "main.py"
ICON_PATH   = Path("impostor/assets/icon.ico")


# ── Icône ICO générée programmatiquement ──────────────────────────────────────

def _make_ico(path: Path) -> None:
    """
    Generate a multi-size ICO file (16, 32, 48 px) without any external dependencies.
    Uses classic BMP-in-ICO format (not PNG-in-ICO) for maximum PyInstaller/Windows
    compatibility.  Draws a stylised document-with-fold-corner icon in purple/white.
    """

    def _draw(size: int) -> bytearray:
        """Return RGBA pixel array (size×size) for the icon."""
        pixels = bytearray(size * size * 4)
        s = size / 32.0  # scale factor relative to the 32-px reference grid

        body   = (44,  44,  62, 255)
        fold   = (124, 106, 247, 255)
        line   = (205, 214, 244, 200)
        border = (124, 106, 247, 255)
        inner  = (30,  30,  46, 255)   # fold reveal colour

        def px(x: int, y: int, rgba: tuple[int, int, int, int]) -> None:
            if 0 <= x < size and 0 <= y < size:
                i = (y * size + x) * 4
                pixels[i:i + 4] = rgba

        def rect(x0: int, y0: int, x1: int, y1: int,
                 fill: tuple[int, int, int, int]) -> None:
            for yy in range(y0, y1 + 1):
                for xx in range(x0, x1 + 1):
                    px(xx, yy, fill)

        def sc(v: float) -> int:
            return max(1, round(v * s))

        m   = sc(4)           # left/top margin
        r   = size - sc(5)    # right edge of document
        bot = size - sc(4)    # bottom edge of document
        fs  = sc(7)           # fold triangle size

        rect(m, m, r, bot, body)

        # Fold corner (top-right) — filled purple triangle, then reveal
        for dy in range(fs):
            for dx in range(fs - dy):
                px(r - dx, m + dy, fold)
            for dx in range(dy + 1):
                px(r - dx, m + dy, inner)

        # Border
        for x in range(m, r + 1):
            px(x, m, border)
            px(x, bot, border)
        for y in range(m, bot + 1):
            px(m, y, border)
            px(r, y, border)

        # Text lines (only when there's enough room)
        if size >= 24:
            for ly, lw in [(sc(12), sc(18)), (sc(16), sc(14)),
                           (sc(20), sc(16)), (sc(24), sc(10))]:
                if ly + 1 < size:
                    rect(m + sc(4), ly, m + sc(4) + lw, ly + 1, line)

        return pixels

    def _to_bmp(pixels: bytearray, size: int) -> bytes:
        """
        Convert RGBA pixels to the BMP-in-ICO image format:
        BITMAPINFOHEADER (40 bytes) + XOR mask (BGRA, bottom-up) + AND mask (1bpp).
        biHeight is doubled because the ICO BMP stores XOR and AND masks stacked.
        """
        header = struct.pack(
            "<IiiHHIIiiII",
            40,          # biSize
            size,        # biWidth
            size * 2,    # biHeight (XOR + AND stacked)
            1,           # biPlanes
            32,          # biBitCount
            0,           # biCompression (BI_RGB)
            0,           # biSizeImage
            0, 0, 0, 0,  # pels/m, clrUsed, clrImportant
        )

        # XOR mask: BGRA, rows bottom-to-top
        xor = bytearray()
        for y in range(size - 1, -1, -1):
            for x in range(size):
                i = (y * size + x) * 4
                rr, g, b, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
                xor += bytes([b, g, rr, a])

        # AND mask: 1 bpp, 0 = opaque, 1 = transparent; rows DWORD-padded, bottom-to-top
        row_dwords = (size + 31) // 32
        and_mask = bytearray(row_dwords * 4 * size)
        for y in range(size):
            for x in range(size):
                a = pixels[(y * size + x) * 4 + 3]
                if a == 0:
                    row = size - 1 - y  # bottom-to-top
                    and_mask[row * row_dwords * 4 + x // 8] |= 0x80 >> (x % 8)

        return header + bytes(xor) + bytes(and_mask)

    sizes  = [16, 32, 48]
    images = [_to_bmp(_draw(s), s) for s in sizes]

    # ICO container
    n         = len(sizes)
    ico_hdr   = struct.pack("<HHH", 0, 1, n)
    offset    = 6 + 16 * n  # header + directory
    directory = b""
    for s, img in zip(sizes, images):
        directory += struct.pack("<BBBBHHII",
                                  s, s,        # width, height (use 0 for 256 px)
                                  0,           # color count
                                  0,           # reserved
                                  1,           # planes
                                  32,          # bit count
                                  len(img), offset)
        offset += len(img)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico_hdr + directory + b"".join(images))
    total = sum(len(i) for i in images)
    print(f" Icône générée : {path}  ({total} bytes BMP-in-ICO, tailles {sizes}px)")


# ── Build ──────────────────────────────────────────────────────────────────────

def build(onefile: bool = False, debug: bool = False) -> None:
    # Always regenerate the icon to avoid stale ICO files
    print("  Génération de l'icône…")
    _make_ico(ICON_PATH)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onefile" if onefile else "--onedir",
        "--icon", str(ICON_PATH),
        "--clean",
        "--noconfirm",
    ]

    if not debug:
        args.append("--windowed")  # no console on Windows

    # Include assets directory
    args += ["--add-data", f"impostor/assets{';' if sys.platform == 'win32' else ':'}impostor/assets"]

    # tkinterdnd2 ships its own Tcl extension DLL — PyInstaller needs a hint
    args += ["--hidden-import", "tkinterdnd2"]
    if tkinterdnd2 is not None:
        _dnd_dir = os.path.dirname(tkinterdnd2.__file__)
        args += ["--add-data", f"{_dnd_dir}{';' if sys.platform == 'win32' else ':'}."]
    else:
        print("  ⚠  tkinterdnd2 non trouvé – drag-and-drop désactivé dans le .exe")

    args.append(ENTRY_POINT)

    print("=" * 60)
    print(f"  Building {APP_NAME}  ({'onefile' if onefile else 'onedir'})…")
    print("=" * 60)

    result = subprocess.run(args, check=False)

    if result.returncode == 0:
        if onefile:
            exe = Path("dist") / f"{APP_NAME}.exe"
        else:
            exe = Path("dist") / APP_NAME / f"{APP_NAME}.exe"
        print(f"\n Build réussi !")
        print(f"   Exécutable : {exe.resolve()}")
        print(f"   Taille     : {exe.stat().st_size / 1024 / 1024:.1f} MB"
              if exe.exists() else "   (fichier non trouvé sur cette plateforme)")
    else:
        print("\n Build échoué. Vérifiez les logs ci-dessus.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build PDFImpostor .exe")
    parser.add_argument("--onefile", action="store_true",
                        help="Créer un seul .exe (plus lent au lancement)")
    parser.add_argument("--debug", action="store_true",
                        help="Garder la console pour le débogage")
    args = parser.parse_args()
    build(onefile=args.onefile, debug=args.debug)
