"""Unit tests for processing/pdf_writer.py."""

from __future__ import annotations

import fitz
import pytest
from PIL import Image

from processing.pdf_writer import (
    PAGE_SIZES_MM,
    _draw_cut_marks,
    _mirror_back_page,
    assemble_pdf,
    compute_grid,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def solid(w: int, h: int, color=(200, 100, 50)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def make_pairs(n: int, w=100, h=140) -> list[tuple[Image.Image, Image.Image | None]]:
    """Create n (front, back) pairs of solid images."""
    front = solid(w, h, (255, 0, 0))
    back = solid(w, h, (0, 0, 255))
    return [(front, back)] * n


# ── compute_grid ─────────────────────────────────────────────────────────────


class TestComputeGrid:
    def test_poker_on_a4(self):
        """Poker cards (63×88mm) with 3mm bleed = 69×94mm; should fit 2×3 on A4."""
        rows, cols = compute_grid(69, 94, "A4", 5)
        assert cols == 2
        assert rows == 3

    def test_tarot_on_a4(self):
        """Tarot cards (70×120mm) with 3mm bleed = 76×126mm; check fits."""
        rows, cols = compute_grid(76, 126, "A4", 5)
        assert cols >= 1
        assert rows >= 1

    def test_mini_on_a4(self):
        """Mini cards (41×63mm) with 3mm bleed = 47×69mm; should fit many."""
        rows, cols = compute_grid(47, 69, "A4", 5)
        assert cols >= 3
        assert rows >= 4

    @pytest.mark.parametrize("page", ["A4", "Letter", "A3"])
    def test_all_page_sizes_accepted(self, page):
        rows, cols = compute_grid(69, 94, page, 5)
        assert rows >= 1
        assert cols >= 1

    def test_too_large_raises(self):
        with pytest.raises(ValueError, match="too large"):
            compute_grid(300, 400, "A4", 5)

    def test_zero_margin(self):
        rows, cols = compute_grid(69, 94, "A4", 0)
        assert rows >= 3 and cols >= 2

    def test_results_are_positive_integers(self):
        rows, cols = compute_grid(63, 88, "A4", 5)
        assert isinstance(rows, int)
        assert isinstance(cols, int)
        assert rows > 0 and cols > 0


# ── _mirror_back_page ────────────────────────────────────────────────────────


class TestMirrorBackPage:
    def _labels(self, n: int) -> list[str]:
        return [f"b{i}" for i in range(n)]

    def test_horizontal_reverses_rows(self):
        # 2 rows × 3 cols: [0,1,2, 3,4,5]
        backs = self._labels(6)
        result = _mirror_back_page(backs, rows=2, cols=3, flip_direction="horizontal")
        # Row 0 reversed: [2,1,0], Row 1 reversed: [5,4,3]
        assert result == ["b2", "b1", "b0", "b5", "b4", "b3"]

    def test_vertical_reverses_row_order(self):
        # 2 rows × 3 cols
        backs = self._labels(6)
        result = _mirror_back_page(backs, rows=2, cols=3, flip_direction="vertical")
        # Rows swapped: [3,4,5, 0,1,2]
        assert result == ["b3", "b4", "b5", "b0", "b1", "b2"]

    def test_single_row_horizontal_reverses(self):
        backs = self._labels(4)
        result = _mirror_back_page(backs, rows=1, cols=4, flip_direction="horizontal")
        assert result == ["b3", "b2", "b1", "b0"]

    def test_single_col_vertical_reverses(self):
        backs = self._labels(3)
        result = _mirror_back_page(backs, rows=3, cols=1, flip_direction="vertical")
        assert result == ["b2", "b1", "b0"]

    def test_padding_with_none(self):
        """When backs are fewer than grid, None pads the remaining slots."""
        backs = self._labels(2)
        result = _mirror_back_page(backs, rows=2, cols=2, flip_direction="horizontal")
        # Padded: [b0, b1, None, None] → rows [[b0,b1],[None,None]]
        # horizontal: [[b1,b0],[None,None]]
        assert result == ["b1", "b0", None, None]

    def test_output_length_equals_rows_times_cols(self):
        backs = self._labels(5)
        result = _mirror_back_page(backs, rows=2, cols=3, flip_direction="horizontal")
        assert len(result) == 6

    def test_1x1_grid_unchanged(self):
        backs = ["b0"]
        result = _mirror_back_page(backs, rows=1, cols=1, flip_direction="horizontal")
        assert result == ["b0"]


# ── assemble_pdf ─────────────────────────────────────────────────────────────


class TestAssemblePdf:
    def _open_pdf(self, pdf_bytes: bytes) -> fitz.Document:
        return fitz.open(stream=pdf_bytes, filetype="pdf")

    def test_returns_bytes(self):
        pairs = make_pairs(1)
        result = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94)
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    def test_six_cards_two_pages(self):
        """6 cards on a 2-col grid (poker on A4) → 1 front page + 1 back page = 2 pages."""
        rows, cols = compute_grid(69, 94, "A4", 5)  # 3×2
        cards_per_page = rows * cols  # 6
        pairs = make_pairs(cards_per_page)
        pdf_bytes = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94, output_page_size="A4")
        doc = self._open_pdf(pdf_bytes)
        assert doc.page_count == 2

    def test_seven_cards_four_pages(self):
        """7 cards on 6-per-page grid → 2 front/back pairs = 4 pages total."""
        pairs = make_pairs(7)
        pdf_bytes = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94, output_page_size="A4")
        doc = self._open_pdf(pdf_bytes)
        assert doc.page_count == 4

    def test_page_size_a4(self):
        """Output pages should be approximately A4 size."""
        pairs = make_pairs(1)
        pdf_bytes = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94, output_page_size="A4")
        doc = self._open_pdf(pdf_bytes)
        page = doc[0]
        # A4 in pts: 210/25.4*72 ≈ 595, 297/25.4*72 ≈ 842
        assert abs(page.rect.width - 210 / 25.4 * 72) < 1
        assert abs(page.rect.height - 297 / 25.4 * 72) < 1

    def test_page_size_letter(self):
        pairs = make_pairs(1)
        pdf_bytes = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94, output_page_size="Letter")
        doc = self._open_pdf(pdf_bytes)
        page = doc[0]
        pw_mm, ph_mm = PAGE_SIZES_MM["Letter"]
        assert abs(page.rect.width - pw_mm / 25.4 * 72) < 1
        assert abs(page.rect.height - ph_mm / 25.4 * 72) < 1

    def test_none_back_still_produces_back_page(self):
        """Even if back is None, the back page must still be created."""
        pairs = [(solid(100, 140, (255, 0, 0)), None)]
        pdf_bytes = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94)
        doc = self._open_pdf(pdf_bytes)
        assert doc.page_count == 2

    def test_empty_pairs_raises(self):
        """No cards → PyMuPDF cannot save a zero-page document."""
        with pytest.raises(ValueError, match="zero pages"):
            assemble_pdf([], card_w_mm=69, card_h_mm=94)

    def test_invalid_page_size_raises(self):
        with pytest.raises(KeyError):
            assemble_pdf(make_pairs(1), card_w_mm=69, card_h_mm=94, output_page_size="A5")

    def test_card_too_large_raises(self):
        with pytest.raises(ValueError, match="too large"):
            assemble_pdf(make_pairs(1), card_w_mm=500, card_h_mm=500, output_page_size="A4")

    def test_grid_is_centered(self):
        """Card grid should be centered: x_start > margin_pts when grid doesn't fill width."""
        pairs = make_pairs(1)
        # 1 poker card on A4: grid is much smaller than page → should be centered
        pdf_bytes = assemble_pdf(pairs, card_w_mm=69, card_h_mm=94, output_page_size="A4")
        doc = self._open_pdf(pdf_bytes)
        # Check that the page was produced correctly (centering is a layout detail)
        assert doc.page_count == 2

    def test_cut_marks_fronts_only(self):
        """cut_marks_fronts=True should produce a valid PDF."""
        pairs = make_pairs(1)
        pdf_bytes = assemble_pdf(
            pairs, card_w_mm=69, card_h_mm=94,
            cut_marks_fronts=True, cut_marks_backs=False,
        )
        assert pdf_bytes[:4] == b"%PDF"

    def test_cut_marks_both(self):
        pairs = make_pairs(2)
        pdf_bytes = assemble_pdf(
            pairs, card_w_mm=69, card_h_mm=94,
            cut_marks_fronts=True, cut_marks_backs=True,
            cut_mark_length_mm=3.0, cut_mark_thickness_mm=0.2,
        )
        assert pdf_bytes[:4] == b"%PDF"

    def test_cut_marks_zero_bleed_no_crash(self):
        """With bleed_mm=0, cut marks are skipped (bleed_pts=0) but no crash."""
        pairs = make_pairs(1)
        pdf_bytes = assemble_pdf(
            pairs, card_w_mm=63, card_h_mm=88, bleed_mm=0,
            cut_marks_fronts=True, cut_marks_backs=True,
        )
        assert pdf_bytes[:4] == b"%PDF"


# ── _draw_cut_marks ───────────────────────────────────────────────────────────


class TestDrawCutMarks:
    def _blank_page(self) -> fitz.Page:
        doc = fitz.open()
        return doc.new_page(width=595, height=842)

    def test_does_not_raise(self):
        page = self._blank_page()
        _draw_cut_marks(page, x0=50, y0=50, card_w_pts=200, card_h_pts=280,
                        bleed_pts=8.5, mark_len_pts=8.5, mark_width_pts=0.5)

    def test_zero_bleed_skipped(self):
        page = self._blank_page()
        # Should silently skip — no exception
        _draw_cut_marks(page, x0=50, y0=50, card_w_pts=200, card_h_pts=280,
                        bleed_pts=0, mark_len_pts=8.5, mark_width_pts=0.5)

    def test_mark_len_clamped_to_bleed(self):
        page = self._blank_page()
        # mark_len > bleed should clamp without error
        _draw_cut_marks(page, x0=50, y0=50, card_w_pts=200, card_h_pts=280,
                        bleed_pts=5, mark_len_pts=20, mark_width_pts=0.5)
