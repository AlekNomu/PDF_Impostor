"""
Regression tests — each test here documents a bug that was found and fixed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pypdf

from impostor.core.imposition import (
    DuplexMode,
    ImpositionSettings,
    _build_print_sequence,
    impose,
)


class TestClosureBugRegression:
    """
    Regression: _build_print_sequence used a closure `idx()` that captured
    `base` by reference (late-binding). For multi-signature documents, all
    signatures would use the *last* value of `base`, producing wrong page order.

    Fixed by: `def idx(p, _base=base): ...`
    """

    def test_multi_signature_pages_are_unique(self) -> None:
        """Each page index must appear exactly once across all signatures."""
        for num_sigs in range(1, 6):
            sps = 2
            padded = num_sigs * 4 * sps
            for mode in DuplexMode:
                seq = _build_print_sequence(padded, sps, mode)
                assert sorted(p for p in seq if p is not None) == list(range(padded)), (
                    f"Duplicate or missing pages for {num_sigs} sigs, mode={mode.name}"
                )

    def test_two_signature_page_order_is_correct(self) -> None:
        """
        For 2 signatures of 2 sheets (16 pages total), verify that
        pages 8-15 (second signature) appear in the second half of
        the AUTO_DUPLEX sequence, not duplicated with the first.
        """
        seq = _build_print_sequence(16, 2, DuplexMode.AUTO_DUPLEX)
        # All 16 indices must be present
        assert sorted(seq) == list(range(16))
        # First signature covers pages 0-7, second covers 8-15
        # They must not overlap
        first_sig_pages = set(seq[:8])
        second_sig_pages = set(seq[8:])
        assert first_sig_pages == set(range(8))
        assert second_sig_pages == set(range(8, 16))

    def test_three_signatures_integration(self) -> None:
        """Integration: 24-page PDF with sps=2 (3 signatures) produces valid output."""
        writer = pypdf.PdfWriter()
        for _ in range(24):
            writer.add_blank_page(width=595, height=842)
        src = Path(tempfile.mktemp(suffix=".pdf"))
        out = Path(tempfile.mktemp(suffix="_imposed.pdf"))
        with open(src, "wb") as f:
            writer.write(f)

        result = impose(ImpositionSettings(src, out, sheets_per_signature=2))
        assert result.success, result.error
        assert result.num_signatures == 3
        assert result.sheets_total == 6

        reader = pypdf.PdfReader(str(out))
        assert len(reader.pages) == 12  # 24 pages → 12 two-up sheets


class TestFrontBackPagePlacementRegression:
    """
    Regression: _build_print_sequence emitted pairs in the wrong order, causing
    impose() to place pages on the wrong half of each 2-up sheet.

    impose() maps sequence[i] → right half, sequence[i+1] → left half.
    For a 6-sheet signature (24 pages), sheet 1 must be:
      Front: LEFT=page 24 (index 23), RIGHT=page 1 (index 0)
      Back:  LEFT=page  2 (index  1), RIGHT=page 23 (index 22)

    Fixed by: swapping pair order in _build_print_sequence for both fronts
    (front_left first, front_right second) and backs (back_right first,
    back_left second).
    """

    def test_first_sheet_front_placement(self) -> None:
        """Sheet 1 front: sequence pair must be [front_left, front_right] = [0, 23]."""
        seq = _build_print_sequence(24, 6, DuplexMode.AUTO_DUPLEX)
        # AUTO_DUPLEX interleaves: front pair, back pair, front pair, back pair ...
        # Sheet 1 front is the first pair (indices 0-1 of seq)
        front_left_slot, front_right_slot = seq[0], seq[1]
        # impose() places seq[0] on the RIGHT half, seq[1] on the LEFT half.
        # RIGHT half = page 1 (index 0 = front_left)
        # LEFT half  = page 24 (index 23 = front_right)
        assert front_left_slot == 0,  f"RIGHT half should be page 1 (idx 0), got idx {front_left_slot}"
        assert front_right_slot == 23, f"LEFT half should be page 24 (idx 23), got idx {front_right_slot}"

    def test_first_sheet_back_placement(self) -> None:
        """Sheet 1 back: pair must be [back_right, back_left] = [22, 1]."""
        seq = _build_print_sequence(24, 6, DuplexMode.AUTO_DUPLEX)
        # AUTO_DUPLEX: front pair at [0:2], back pair at [2:4]
        back_right_slot, back_left_slot = seq[2], seq[3]
        assert back_right_slot == 22, f"RIGHT half should be page 23 (idx 22), got idx {back_right_slot}"
        assert back_left_slot == 1,  f"LEFT half should be page 2 (idx 1), got idx {back_left_slot}"

    def test_48_pages_6_sps_all_sheets(self) -> None:
        """48 pages / 6 SPS: full coverage and correct outer-sheet placement."""
        seq = _build_print_sequence(48, 6, DuplexMode.AUTO_DUPLEX)
        # All 48 indices present
        assert sorted(seq) == list(range(48))
        # Signature 1, sheet 1 front: RIGHT=idx 0 (page 1), LEFT=idx 23 (page 24)
        assert seq[0] == 0  and seq[1] == 23
        # Signature 1, sheet 1 back: RIGHT=idx 22 (page 23), LEFT=idx 1 (page 2)
        assert seq[2] == 22 and seq[3] == 1


class TestIconGeneration:
    """
    Regression: build.py must generate a valid multi-size BMP-in-ICO without
    external deps.  Previously used PNG-in-ICO (single size), which was not
    recognised by some PyInstaller versions and fell back to a default icon.
    Fixed by: BMP-in-ICO with three sizes (16, 32, 48 px).
    """

    def test_ico_header_magic(self) -> None:
        from build import _make_ico
        fd, path = tempfile.mkstemp(suffix=".ico")
        import os; os.close(fd)
        out = Path(path)
        _make_ico(out)
        data = out.read_bytes()
        # ICO magic: reserved=0, type=1 (icon)
        assert data[0:2] == b"\x00\x00"
        assert data[2:4] == b"\x01\x00"
        # Three image sizes (16, 32, 48 px)
        assert data[4:6] == b"\x03\x00"
        out.unlink(missing_ok=True)

    def test_ico_images_are_bmp(self) -> None:
        """Each embedded image must be a BMP (BITMAPINFOHEADER, biSize=40)."""
        import struct
        from build import _make_ico
        fd, path = tempfile.mkstemp(suffix=".ico")
        import os; os.close(fd)
        out = Path(path)
        _make_ico(out)
        data = out.read_bytes()
        # Directory entry layout: B B B B H H I I (16 bytes each, starting at offset 6)
        # Image data offset is the last field (I) at entry_start + 12
        for i in range(3):
            entry_start = 6 + i * 16
            img_offset = struct.unpack_from("<I", data, entry_start + 12)[0]
            bi_size = struct.unpack_from("<I", data, img_offset)[0]
            assert bi_size == 40, (
                f"Image {i} does not start with a valid BITMAPINFOHEADER (biSize={bi_size})"
            )
        out.unlink(missing_ok=True)

