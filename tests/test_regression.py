"""
Regression tests — each test here documents a bug that was found and fixed.
"""

from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path

import pypdf
import pytest

from build import _make_ico
from impostor.core.imposition import (
    DuplexMode,
    ImpositionSettings,
    _build_print_sequence,
    _horizontal_translations,
    _iter_sides,
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


class TestCreepDirectionRegression:
    """
    Regression: creep compensation was applied in the wrong direction. Inner
    sheets were shifted *away* from the spine (+creep on the right half, -creep
    on the left half), which adds to the physical push-out instead of cancelling
    it — on a 25-sheet booklet the innermost pages drifted further into the
    fore-edge trim area the higher the setting was.

    Correct behaviour: once folded, inner sheets protrude at the fore-edge and
    lose more paper when trimmed flush, so their content must move *toward the
    spine* — left for the right-half page, right for the left-half page.

    Fixed by: _horizontal_translations() subtracting creep on the right half and
    adding it on the left half.
    """

    ORIG_W = 595.0  # A4 portrait width in points

    def test_outermost_sheet_has_no_creep(self) -> None:
        """Sheet 0 is the outermost: creep must not move it at all."""
        right_tx, left_tx = _horizontal_translations(
            self.ORIG_W, 0, inside_offset=0.0,
            creep_compensation=2.0, center_adjustment=0.0,
        )
        assert right_tx == self.ORIG_W
        assert left_tx == 0.0

    def test_inner_sheets_move_toward_the_spine(self) -> None:
        """
        The spine sits at x = ORIG_W. The right-half page must move left
        (decreasing x) and the left-half page right (increasing x) as sheets
        get deeper into the signature.
        """
        creep = 2.0
        previous_right = None
        previous_left = None
        for sheet_num in range(5):
            right_tx, left_tx = _horizontal_translations(
                self.ORIG_W, sheet_num, inside_offset=0.0,
                creep_compensation=creep, center_adjustment=0.0,
            )
            # Distance from the spine shrinks on both halves
            assert right_tx == self.ORIG_W - creep * sheet_num
            assert left_tx == creep * sheet_num
            if previous_right is not None:
                assert right_tx < previous_right, "right half must move toward the spine"
                assert left_tx > previous_left, "left half must move toward the spine"
            previous_right, previous_left = right_tx, left_tx

    def test_creep_is_symmetric_around_the_spine(self) -> None:
        """Both halves must close in on the spine by the same amount."""
        for sheet_num in range(1, 6):
            right_tx, left_tx = _horizontal_translations(
                self.ORIG_W, sheet_num, inside_offset=0.0,
                creep_compensation=1.5, center_adjustment=0.0,
            )
            right_gap = self.ORIG_W - right_tx   # right half shifted left by this
            left_gap = left_tx                   # left half shifted right by this
            assert right_gap == left_gap

    def test_zero_creep_is_a_noop(self) -> None:
        """With creep disabled, sheet depth must not change anything."""
        for sheet_num in range(5):
            right_tx, left_tx = _horizontal_translations(
                self.ORIG_W, sheet_num, inside_offset=10.0,
                creep_compensation=0.0, center_adjustment=3.0,
            )
            assert right_tx == self.ORIG_W + 10.0 + 3.0
            assert left_tx == -10.0 + 3.0


class TestCreepSheetDepthRegression:
    """
    Regression: impose() derived the sheet depth used for creep with
    `sheet_num_in_sig = (i // 2) % sps`, where `i` walks the flat page sequence.
    That index counts *printed sides*, not sheets, so it was wrong twice over:

      * recto and verso of the same physical sheet got different creep values,
        even though they are the two faces of one piece of paper;
      * `% sps` wrapped after `sps` sides — i.e. halfway through the booklet —
        resetting creep to 0. On a 25-sheet booklet printed in AUTO_DUPLEX the
        compensation visibly restarted from zero around the middle.

    Correct behaviour: depth is a property of the physical sheet (0 = outermost),
    shared by both of its faces, restarting only on a new signature.

    Fixed by: _iter_sides() emitting the depth alongside each side, so impose()
    no longer recomputes it from the flat sequence index.
    """

    def test_both_faces_of_a_sheet_share_one_depth(self) -> None:
        """AUTO_DUPLEX emits front then back of each sheet: depths pair up."""
        sides = _iter_sides(100, 25, DuplexMode.AUTO_DUPLEX)
        depths = [depth for _r, _l, depth in sides]
        assert len(depths) == 50  # 25 sheets x 2 faces
        for sheet in range(25):
            front, back = depths[2 * sheet], depths[2 * sheet + 1]
            assert front == back == sheet, (
                f"sheet {sheet}: faces got depths {front}/{back}"
            )

    def test_depth_never_resets_mid_booklet(self) -> None:
        """The 25-sheet booklet that exposed the bug: depth rises 0→24 once."""
        sides = _iter_sides(100, 25, DuplexMode.AUTO_DUPLEX)
        depths = [depth for _r, _l, depth in sides]
        # Deduplicate consecutive repeats (each sheet appears twice in a row)
        per_sheet = depths[::2]
        assert per_sheet == list(range(25))
        assert max(depths) == 24, "innermost sheet must reach full creep"

    @pytest.mark.parametrize("mode", list(DuplexMode))
    def test_depth_stays_within_signature_bounds(self, mode: DuplexMode) -> None:
        """Whatever the duplex mode, depth is always in [0, sps-1]."""
        sps = 6
        sides = _iter_sides(4 * sps * 3, sps, mode)  # 3 signatures
        depths = [depth for _r, _l, depth in sides]
        assert min(depths) == 0
        assert max(depths) == sps - 1
        # Each depth occurs exactly twice per signature (recto + verso)
        for depth in range(sps):
            assert depths.count(depth) == 2 * 3

    @pytest.mark.parametrize("mode", list(DuplexMode))
    def test_every_sheet_is_emitted_once_per_face(self, mode: DuplexMode) -> None:
        """Sanity: side count always equals 2 x total sheets."""
        for sps, num_sigs in [(1, 1), (2, 3), (25, 1), (6, 4)]:
            sides = _iter_sides(4 * sps * num_sigs, sps, mode)
            assert len(sides) == 2 * sps * num_sigs


class TestInsideOffsetSymmetryRegression:
    """
    Regression: inside_offset was only applied to the page on the right half of
    the sheet, so asking for a binding margin shifted one page of the spread and
    left the other in place — the two pages of a spread ended up misaligned
    instead of both gaining gutter.

    Correct behaviour: inside_offset is a binding margin, so it must push both
    halves *away* from the spine by the same amount.

    Fixed by: _horizontal_translations() subtracting inside_offset on the left
    half, mirroring the addition on the right half.
    """

    ORIG_W = 595.0

    def test_both_halves_move_away_from_the_spine(self) -> None:
        offset = 12.0
        right_tx, left_tx = _horizontal_translations(
            self.ORIG_W, 0, inside_offset=offset,
            creep_compensation=0.0, center_adjustment=0.0,
        )
        # Spine is at x = ORIG_W: right half moves right, left half moves left.
        assert right_tx == self.ORIG_W + offset
        assert left_tx == -offset

    def test_gutter_is_symmetric(self) -> None:
        """Both halves must gain the same gutter, whatever the offset."""
        for offset in (0.0, 5.0, 20.0, 50.0):
            right_tx, left_tx = _horizontal_translations(
                self.ORIG_W, 0, inside_offset=offset,
                creep_compensation=0.0, center_adjustment=0.0,
            )
            assert (right_tx - self.ORIG_W) == offset
            assert -left_tx == offset

    def test_center_adjustment_shifts_both_halves_the_same_way(self) -> None:
        """Printer centring is a whole-sheet shift, not a symmetric one."""
        right_tx, left_tx = _horizontal_translations(
            self.ORIG_W, 0, inside_offset=0.0,
            creep_compensation=0.0, center_adjustment=7.0,
        )
        assert right_tx == self.ORIG_W + 7.0
        assert left_tx == 7.0


class TestIconGeneration:
    """
    Regression: build.py must generate a valid multi-size BMP-in-ICO without
    external deps.  Previously used PNG-in-ICO (single size), which was not
    recognised by some PyInstaller versions and fell back to a default icon.
    Fixed by: BMP-in-ICO with three sizes (16, 32, 48 px).
    """

    def test_ico_header_magic(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".ico")
        os.close(fd)
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
        fd, path = tempfile.mkstemp(suffix=".ico")
        os.close(fd)
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

