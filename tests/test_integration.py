"""
Integration tests for the full imposition pipeline.
Creates real (minimal) PDFs in memory and verifies output.

Run with: python -m pytest tests/ -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pypdf

from impostor.core.imposition import (
    DuplexMode,
    ImpositionSettings,
    impose,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_pdf(num_pages: int) -> Path:
    """Create a minimal multi-page PDF in a temp file and return its path."""
    writer = pypdf.PdfWriter()
    for i in range(num_pages):
        writer.add_blank_page(width=595, height=842)  # A4 portrait
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()
    return Path(tmp.name)


def _output_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix="_imposed.pdf", delete=False)
    tmp.close()
    return Path(tmp.name)


def _page_count(path: Path) -> int:
    return len(pypdf.PdfReader(str(path)).pages)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestImposeBasic:
    def test_returns_success_for_valid_pdf(self) -> None:
        src = _make_pdf(8)
        out = _output_path()
        result = impose(ImpositionSettings(src, out, sheets_per_signature=2))
        assert result.success
        assert result.output_path == out
        assert out.exists()

    def test_output_is_valid_pdf(self) -> None:
        src = _make_pdf(8)
        out = _output_path()
        impose(ImpositionSettings(src, out, sheets_per_signature=2))
        # pypdf should be able to open the result without raising
        reader = pypdf.PdfReader(str(out))
        assert len(reader.pages) > 0

    def test_output_page_count_2up(self) -> None:
        """8 input pages → 4 two-up output pages (auto-duplex)."""
        src = _make_pdf(8)
        out = _output_path()
        result = impose(ImpositionSettings(
            src, out,
            sheets_per_signature=2,
            duplex_mode=DuplexMode.AUTO_DUPLEX,
        ))
        assert result.success
        # 8 pages / 2 slots per sheet = 4 output pages
        assert _page_count(out) == 4

    def test_output_landscape_dimensions(self) -> None:
        """Output pages should be landscape (width = 2 × original width)."""
        src = _make_pdf(4)
        out = _output_path()
        impose(ImpositionSettings(src, out, sheets_per_signature=1))
        reader = pypdf.PdfReader(str(out))
        first = reader.pages[0]
        assert float(first.mediabox.width) == pytest.approx(595 * 2, abs=1)
        assert float(first.mediabox.height) == pytest.approx(842, abs=1)


class TestImposeEdgeCases:
    def test_odd_page_count_padded(self) -> None:
        """A 6-page PDF (sps=2) gets padded to 8, generating a warning."""
        src = _make_pdf(6)
        out = _output_path()
        result = impose(ImpositionSettings(src, out, sheets_per_signature=2))
        assert result.success
        assert result.padded_pages == 8
        assert any("vierge" in w.lower() or "blank" in w.lower() for w in result.warnings)

    def test_single_page_pdf(self) -> None:
        """Single page input should succeed (padded to 4)."""
        src = _make_pdf(1)
        out = _output_path()
        result = impose(ImpositionSettings(src, out, sheets_per_signature=1))
        assert result.success
        assert result.padded_pages == 4

    def test_magazine_mode_sps_zero(self) -> None:
        """sps=0 = magazine: one big signature for all pages."""
        src = _make_pdf(12)
        out = _output_path()
        result = impose(ImpositionSettings(src, out, sheets_per_signature=0))
        assert result.success
        assert result.num_signatures == 1

    def test_missing_input_returns_error(self) -> None:
        result = impose(ImpositionSettings(
            input_path=Path("/nonexistent/file.pdf"),
            output_path=_output_path(),
        ))
        assert not result.success
        assert result.error is not None

    def test_empty_pdf_returns_error(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.write(b"%PDF-1.4\n%%EOF\n")
        tmp.close()
        result = impose(ImpositionSettings(
            input_path=Path(tmp.name),
            output_path=_output_path(),
        ))
        assert not result.success


class TestImposeSignatureCounts:
    @pytest.mark.parametrize("pages,sps,expected_sigs", [
        (8,  2, 1),
        (16, 2, 2),
        (16, 4, 1),
        (32, 4, 2),
        (4,  1, 1),
    ])
    def test_signature_count(self, pages: int, sps: int, expected_sigs: int) -> None:
        src = _make_pdf(pages)
        out = _output_path()
        result = impose(ImpositionSettings(src, out, sheets_per_signature=sps))
        assert result.success
        assert result.num_signatures == expected_sigs


class TestDuplexModes:
    @pytest.mark.parametrize("mode", list(DuplexMode))
    def test_all_duplex_modes_produce_valid_pdf(self, mode: DuplexMode) -> None:
        src = _make_pdf(8)
        out = _output_path()
        result = impose(ImpositionSettings(
            src, out, sheets_per_signature=2, duplex_mode=mode,
        ))
        assert result.success
        assert _page_count(out) > 0
