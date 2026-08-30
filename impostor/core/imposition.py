"""
PDF Imposition Engine
=====================
Handles all page reordering and 2-up layout logic for booklet/magazine printing.

Imposition explained:
  A "signature" is a group of sheets folded together. For a simple booklet
  (1 signature = magazine mode), all sheets are folded as one block.
  For books, signatures are separate gatherings that are then bound together.

  For a signature of N sheets, we have 4*N pages printed on 2*N sides.
  The imposition order for a signature places pages so that when folded,
  they read in sequence.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

import pypdf

logger = logging.getLogger(__name__)

# One printed side (= one output 2-up page): the source page index placed on the
# right half, the one on the left half (None = blank), and the sheet's depth
# inside its signature (0 = outermost sheet).
_Side = tuple[Optional[int], Optional[int], int]


class DuplexMode(Enum):
    """Printer duplex capabilities."""
    MANUAL_NO_COLLATE = auto()   # User flips pages manually, no auto-collate
    MANUAL_COLLATE = auto()      # User flips pages manually, printer collates
    AUTO_DUPLEX = auto()         # Printer handles duplex automatically


@dataclass
class ImpositionSettings:
    """All settings required to perform an imposition run."""
    input_path: Path
    output_path: Path
    sheets_per_signature: int = 0          # 0 = full booklet/magazine
    duplex_mode: DuplexMode = DuplexMode.AUTO_DUPLEX

    zoom: float = 1.0                      # Page zoom factor (0.5–1.0)
    inside_offset: float = 0.0            # Inner margin offset in points
    creep_compensation: float = 0.0       # Margin creep per sheet (pts)
    center_adjustment: float = 0.0        # Printer center offset (pts)

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            self.input_path = Path(self.input_path)
        if not isinstance(self.output_path, Path):
            self.output_path = Path(self.output_path)


@dataclass
class ImpositionResult:
    """Result of an imposition operation."""
    success: bool
    output_path: Optional[Path] = None
    total_pages: int = 0
    padded_pages: int = 0
    num_signatures: int = 0
    sheets_total: int = 0
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _signature_page_order(num_sheets: int) -> list[tuple[int, int, int, int]]:
    """
    Compute the imposition order for a single signature.

    Returns a list of (front_right, front_left, back_left, back_right) page
    indices (0-based) for each physical sheet in the signature.

    Example for 2 sheets (8 pages, indices 0-7):
      Sheet 0 front:  page 7 (right), page 0 (left)
      Sheet 0 back:   page 1 (left),  page 6 (right)
      Sheet 1 front:  page 5 (right), page 2 (left)
      Sheet 1 back:   page 3 (left),  page 4 (right)
    """
    sheets = []
    lo, hi = 0, num_sheets * 4 - 1
    for _ in range(num_sheets):
        # Front side: hi on right, lo on left
        front_right = hi
        front_left = lo
        lo += 1
        hi -= 1
        # Back side: lo on left, hi on right
        back_left = lo
        back_right = hi
        lo += 1
        hi -= 1
        sheets.append((front_right, front_left, back_left, back_right))
    return sheets


def _pad_page_count(num_pages: int, sheets_per_sig: int) -> int:
    """Return the padded page count (multiple of 4*sheets_per_sig)."""
    multiple = 4 * sheets_per_sig
    remainder = num_pages % multiple
    if remainder == 0:
        return num_pages
    return num_pages + (multiple - remainder)


def _iter_sides(
    padded_pages: int,
    sheets_per_sig: int,
    duplex_mode: DuplexMode,
) -> list[_Side]:
    """
    Build the list of printed sides, one entry per output 2-up page.

    Each entry is ``(right_half, left_half, sheet_num_in_sig)``:
      * *right_half* / *left_half* are 0-based source page indices, or None for
        a blank page, for the two halves of the landscape sheet.
      * *sheet_num_in_sig* is the depth of the physical sheet inside its
        signature (0 = outermost). Both sides of a given sheet share the same
        depth, and it restarts at 0 on each new signature — creep compensation
        depends on it.

    Sides are ordered for feeding into the printer:
      For AUTO_DUPLEX:  [sheet0 front, sheet0 back, sheet1 front, sheet1 back, …]
      For MANUAL modes: fronts of all sheets first, then backs (reversed within
                        each signature when the printer does not collate).
    """
    num_sigs = padded_pages // (4 * sheets_per_sig)
    all_fronts: list[_Side] = []
    all_backs: list[_Side] = []

    for sig_idx in range(num_sigs):
        base = sig_idx * 4 * sheets_per_sig
        order = _signature_page_order(sheets_per_sig)

        sig_fronts: list[_Side] = []
        sig_backs: list[_Side] = []

        # NOTE: use default-arg capture to avoid the late-binding closure bug.
        def idx(p: int, _base: int = base) -> Optional[int]:
            real = _base + p
            return real if real < padded_pages else None

        for depth, (front_right, front_left, back_left, back_right) in enumerate(order):
            # Front: right half = front_left (lo page), left half = front_right (hi page).
            # Back:  right half = back_right (hi page), left half = back_left  (lo page).
            sig_fronts.append((idx(front_left), idx(front_right), depth))
            sig_backs.append((idx(back_right), idx(back_left), depth))

        all_fronts.extend(sig_fronts)

        if duplex_mode == DuplexMode.MANUAL_NO_COLLATE:
            # Re-inserting the flipped stack reverses both the sheet order and
            # the two halves of every sheet.
            all_backs.extend(
                (left_half, right_half, depth)
                for right_half, left_half, depth in reversed(sig_backs)
            )
        else:
            all_backs.extend(sig_backs)

    if duplex_mode == DuplexMode.AUTO_DUPLEX:
        # Interleave: each sheet's front is immediately followed by its back
        sides: list[_Side] = []
        for front, back in zip(all_fronts, all_backs, strict=True):
            sides.append(front)
            sides.append(back)
        return sides
    return all_fronts + all_backs


def _build_print_sequence(
    padded_pages: int,
    sheets_per_sig: int,
    duplex_mode: DuplexMode,
) -> list[Optional[int]]:
    """
    Build the flat list of page indices in print order.

    Returns a list where each element is either a 0-based page index or None
    (blank page). Elements come in pairs: ``sequence[i]`` is the page placed on
    the right half of an output sheet and ``sequence[i + 1]`` the one on the
    left half.
    """
    sequence: list[Optional[int]] = []
    for right_half, left_half, _depth in _iter_sides(
        padded_pages, sheets_per_sig, duplex_mode
    ):
        sequence.extend([right_half, left_half])
    return sequence


def _horizontal_translations(
    orig_w: float,
    sheet_num_in_sig: int,
    inside_offset: float,
    creep_compensation: float,
    center_adjustment: float,
) -> tuple[float, float]:
    """
    Horizontal translations for the two halves of a single 2-up sheet.

    Returns ``(right_half_tx, left_half_tx)`` — the x translation applied to the
    page placed on the right half and on the left half of the landscape sheet.
    The spine (fold) is the vertical middle of the sheet, at x = *orig_w*.

    Creep: once folded, sheets nested deeper in a signature protrude further at
    the fore-edge, so they lose more paper when the booklet is trimmed flush.
    To keep the fore-edge margin uniform, the content of inner sheets is shifted
    *toward the spine* by an amount growing with *sheet_num_in_sig* (0 = the
    outermost sheet). Toward the spine means moving left for the right-half page
    and right for the left-half page.

    Inside offset: a binding margin, so it pushes both halves *away* from the
    spine symmetrically — right for the right-half page, left for the left-half
    page. Center adjustment shifts the whole sheet in the same direction, to
    correct a printer that is not perfectly centred.
    """
    creep = creep_compensation * sheet_num_in_sig
    right_half_tx = orig_w + inside_offset - creep + center_adjustment
    left_half_tx = -inside_offset + creep + center_adjustment
    return right_half_tx, left_half_tx


def detect_page_orientations(path: Path) -> tuple[int, int, int]:
    """
    Return (total_pages, portrait_count, landscape_count) for the given PDF.

    Accounts for /Rotate entries so that a portrait mediabox with Rotate=90
    is correctly counted as landscape.
    """
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception:
        return 0, 0, 0

    portrait = landscape = 0
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        rotation = int(page.get("/Rotate", 0)) % 360
        if rotation in (90, 270):
            w, h = h, w
        if w >= h:
            landscape += 1
        else:
            portrait += 1
    total = portrait + landscape
    return total, portrait, landscape


def impose(settings: ImpositionSettings) -> ImpositionResult:
    """
    Main entry point: impose a PDF according to *settings*.

    The function reads the input PDF, pads it to the required page count,
    reorders pages per the imposition algorithm, and writes a 2-up output PDF.
    """
    logger.info("Starting imposition: %s", settings.input_path)

    # 1. Read input 
    try:
        reader = pypdf.PdfReader(str(settings.input_path))
    except Exception as exc:
        logger.exception("Failed to open PDF")
        return ImpositionResult(success=False, error=str(exc))

    num_pages = len(reader.pages)
    if num_pages == 0:
        return ImpositionResult(success=False, error="PDF has no pages.")

    #  2. Resolve sheets_per_signature 
    sps = settings.sheets_per_signature
    if sps <= 0:
        # Magazine/booklet: one signature for the whole document
        sps = math.ceil(num_pages / 4)
        if sps == 0:
            sps = 1

    padded = _pad_page_count(num_pages, sps)
    num_sigs = padded // (4 * sps)
    num_sheets = num_sigs * sps

    warnings: list[str] = []
    blank_pages = padded - num_pages
    if blank_pages:
        warnings.append(
            f"{blank_pages} blank page(s) will be added to complete the last signature."
        )

    logger.debug(
        "pages=%d  padded=%d  sps=%d  signatures=%d  sheets=%d",
        num_pages, padded, sps, num_sigs, num_sheets,
    )

    #  3. Build print sequence
    sides = _iter_sides(padded, sps, settings.duplex_mode)

    #  4. Determine page geometry
    first_page = reader.pages[0]
    orig_w = float(first_page.mediabox.width)
    orig_h = float(first_page.mediabox.height)

    # Output sheet is landscape: width = 2 * orig_w, height = orig_h
    sheet_w = orig_w * 2
    sheet_h = orig_h

    #  5. Build output PDF 
    writer = pypdf.PdfWriter()

    def get_page(idx: Optional[int]) -> Optional[pypdf.PageObject]:
        if idx is None or idx >= num_pages:
            return None  # blank
        return reader.pages[idx]

    def make_blank_page() -> pypdf.PageObject:
        return pypdf.PageObject.create_blank_page(width=orig_w, height=orig_h)

    # Each side becomes one 2-up output page
    for right_half_idx, left_half_idx, sheet_num_in_sig in sides:
        output_page = writer.add_blank_page(width=sheet_w, height=sheet_h)

        right_half_page = get_page(right_half_idx) or make_blank_page()
        left_half_page = get_page(left_half_idx) or make_blank_page()
        zoom = settings.zoom

        right_half_tx, left_half_tx = _horizontal_translations(
            orig_w,
            sheet_num_in_sig,
            settings.inside_offset,
            settings.creep_compensation,
            settings.center_adjustment,
        )
        ty = (orig_h * (1 - zoom)) / 2

        output_page.merge_transformed_page(
            left_half_page,
            pypdf.Transformation().scale(zoom).translate(left_half_tx, ty),
        )
        output_page.merge_transformed_page(
            right_half_page,
            pypdf.Transformation().scale(zoom).translate(right_half_tx, ty),
        )

    #  6. Write output 
    try:
        settings.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings.output_path, "wb") as fh:
            writer.write(fh)
    except Exception as exc:
        logger.exception("Failed to write output PDF")
        return ImpositionResult(success=False, error=str(exc))

    logger.info("Imposition complete → %s", settings.output_path)
    return ImpositionResult(
        success=True,
        output_path=settings.output_path,
        total_pages=num_pages,
        padded_pages=padded,
        num_signatures=num_sigs,
        sheets_total=num_sheets,
        warnings=warnings,
    )
