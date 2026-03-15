"""
Tests for the imposition core engine.
Run with: python -m pytest tests/ -v
"""

import pytest
from impostor.core.imposition import (
    DuplexMode,
    _build_print_sequence,
    _pad_page_count,
    _signature_page_order,
)


# ── _pad_page_count ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pages,sps,expected", [
    (8,  2, 8),   # already a multiple of 4*2=8
    (7,  2, 8),   # round up to 8
    (9,  2, 16),  # round up to next multiple of 8
    (4,  1, 4),   # multiple of 4
    (3,  1, 4),
    (1,  1, 4),
    (16, 4, 16),
    (17, 4, 32),
])
def test_pad_page_count(pages: int, sps: int, expected: int) -> None:
    assert _pad_page_count(pages, sps) == expected


# ── _signature_page_order ─────────────────────────────────────────────────────

def test_signature_order_1_sheet() -> None:
    """1 sheet = 4 pages: pages 3,0 on front; 1,2 on back."""
    order = _signature_page_order(1)
    assert len(order) == 1
    front_right, front_left, back_left, back_right = order[0]
    assert (front_right, front_left) == (3, 0)
    assert (back_left, back_right) == (1, 2)


def test_signature_order_2_sheets() -> None:
    """2 sheets = 8 pages. Verify all 8 page indices appear exactly once."""
    order = _signature_page_order(2)
    all_pages = [p for quad in order for p in quad]
    assert sorted(all_pages) == list(range(8))


def test_signature_order_covers_all_pages(sheets: int = 5) -> None:
    """Every page index must appear exactly once."""
    order = _signature_page_order(sheets)
    all_pages = [p for quad in order for p in quad]
    assert sorted(all_pages) == list(range(sheets * 4))


# ── _build_print_sequence ──────────────────────────────────────────────────────

def test_sequence_length_auto_duplex() -> None:
    """Output sequence length = padded_pages (each pair is one output 2-up page)."""
    seq = _build_print_sequence(8, 2, DuplexMode.AUTO_DUPLEX)
    # 8 page indices → 4 physical sheets → 8 slots (pairs of 2 per 2-up sheet)
    assert len(seq) == 8


def test_sequence_all_pages_present_auto() -> None:
    """All padded page indices (0..7) must appear in the sequence."""
    seq = _build_print_sequence(8, 2, DuplexMode.AUTO_DUPLEX)
    real = [p for p in seq if p is not None]
    assert sorted(real) == list(range(8))


def test_sequence_manual_no_collate_length() -> None:
    seq = _build_print_sequence(8, 2, DuplexMode.MANUAL_NO_COLLATE)
    assert len(seq) == 8


def test_sequence_magazine_16_pages() -> None:
    """16-page magazine (sps=4): verify coverage."""
    seq = _build_print_sequence(16, 4, DuplexMode.AUTO_DUPLEX)
    real = sorted(p for p in seq if p is not None)
    assert real == list(range(16))


def test_sequence_covers_all_padded_indices() -> None:
    """
    The sequence contains all padded page indices.
    Blank pages (beyond num_pages) are rendered as blank at the PDF layer,
    not as None in the index sequence.
    """
    padded = _pad_page_count(5, 2)  # = 8
    seq = _build_print_sequence(padded, 2, DuplexMode.AUTO_DUPLEX)
    real = [p for p in seq if p is not None]
    assert sorted(real) == list(range(padded))
