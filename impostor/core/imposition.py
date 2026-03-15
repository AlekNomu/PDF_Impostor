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


def _build_print_sequence(
    padded_pages: int,
    sheets_per_sig: int,
    duplex_mode: DuplexMode,
) -> list[Optional[int]]:
    """
    Build the flat list of page indices in print order.

    Returns a list where each element is either a 0-based page index or None
    (blank page). The list is ordered for feeding into the printer.

    For AUTO_DUPLEX:  [front_page, back_page, front_page, back_page, ...]
    For MANUAL modes: fronts of all sheets first, then backs (possibly reversed
                      depending on collation).
    """
    num_sigs = padded_pages // (4 * sheets_per_sig)
    all_fronts: list[Optional[int]] = []
    all_backs: list[Optional[int]] = []

    for sig_idx in range(num_sigs):
        base = sig_idx * 4 * sheets_per_sig
        order = _signature_page_order(sheets_per_sig)

        sig_fronts: list[Optional[int]] = []
        sig_backs: list[Optional[int]] = []

        # NOTE: use default-arg capture to avoid the late-binding closure bug.
        def idx(p: int, _base: int = base) -> Optional[int]:
            real = _base + p
            return real if real < padded_pages else None

        for front_right, front_left, back_left, back_right in order:
            # impose() maps sequence[i] → right half, sequence[i+1] → left half.
            # Front: left half = front_right (hi page), right half = front_left (lo page).
            # Back:  left half = back_left   (lo page), right half = back_right  (hi page).
            sig_fronts.extend([idx(front_left), idx(front_right)])
            sig_backs.extend([idx(back_right), idx(back_left)])

        all_fronts.extend(sig_fronts)

        if duplex_mode == DuplexMode.MANUAL_NO_COLLATE:
            # Backs must be in reverse order so re-inserting the stack works
            all_backs.extend(reversed(sig_backs))
        else:
            all_backs.extend(sig_backs)

    if duplex_mode == DuplexMode.AUTO_DUPLEX:
        # Interleave front/back pairs
        sequence: list[Optional[int]] = []
        for i in range(0, len(all_fronts), 2):
            sequence.extend(all_fronts[i : i + 2])
            sequence.extend(all_backs[i : i + 2])
        return sequence
    else:
        return all_fronts + all_backs


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
    sequence = _build_print_sequence(padded, sps, settings.duplex_mode)

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
        page = pypdf.PageObject.create_blank_page(width=orig_w, height=orig_h)
        return page

    # sequence is pairs: [left, right, left, right, ...]
    # Each pair becomes one 2-up output page
    for i in range(0, len(sequence), 2):
        left_idx = sequence[i]
        right_idx = sequence[i + 1] if i + 1 < len(sequence) else None

        output_page = writer.add_blank_page(width=sheet_w, height=sheet_h)

        left_page = get_page(left_idx) or make_blank_page()
        right_page = get_page(right_idx) or make_blank_page()
        zoom = settings.zoom

        # Creep compensation: inner pages get pushed outward slightly
        sheet_num_in_sig = (i // 2) % sps
        creep = settings.creep_compensation * sheet_num_in_sig

        # Left page → placed on right half of sheet (it's the inner side when folded)
        left_tx = orig_w + settings.inside_offset + creep + settings.center_adjustment
        left_ty = (orig_h * (1 - zoom)) / 2

        # Right page → placed on left half
        right_tx = -creep + settings.center_adjustment
        right_ty = (orig_h * (1 - zoom)) / 2

        output_page.merge_transformed_page(
            right_page,
            pypdf.Transformation().scale(zoom).translate(right_tx, right_ty),
        )
        output_page.merge_transformed_page(
            left_page,
            pypdf.Transformation().scale(zoom).translate(left_tx, left_ty),
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
