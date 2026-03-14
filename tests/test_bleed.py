"""Unit tests for processing/bleed.py."""

from __future__ import annotations

import pytest
from PIL import Image

from processing.bleed import _mm_to_px, _tile_corner, _tile_horizontal, _tile_vertical, add_bleed

# ── Helpers ──────────────────────────────────────────────────────────────────


def solid(w: int, h: int, color=(200, 100, 50), mode="RGB") -> Image.Image:
    return Image.new(mode, (w, h), color)


def gradient_h(w: int, h: int) -> Image.Image:
    """Horizontal gradient so left/right edges have different colours."""
    img = Image.new("RGB", (w, h))
    for x in range(w):
        v = int(x / (w - 1) * 255)
        for y in range(h):
            img.putpixel((x, y), (v, 128, 255 - v))
    return img


# ── _mm_to_px ────────────────────────────────────────────────────────────────


class TestMmToPx:
    def test_standard(self):
        # 25.4mm = exactly 1 inch → 300 px at 300 dpi
        assert _mm_to_px(25.4, 300) == 300

    def test_small_value(self):
        # 1mm at 300dpi ≈ 11.8 → rounds to 12
        assert _mm_to_px(1.0, 300) == 12

    def test_minimum_one(self):
        # Very small values clamp to 1
        assert _mm_to_px(0.01, 72) == 1

    def test_zero(self):
        assert _mm_to_px(0.0, 300) == 1  # clamped

    def test_bleed_3mm_300dpi(self):
        # 3mm at 300dpi ≈ 35.4 → 35
        assert _mm_to_px(3.0, 300) == 35


# ── _tile_vertical ───────────────────────────────────────────────────────────


class TestTileVertical:
    def test_exact_multiple(self):
        strip = solid(10, 5, (1, 2, 3))
        result = _tile_vertical(strip, 10)
        assert result.size == (10, 10)

    def test_non_multiple(self):
        strip = solid(8, 3, (1, 2, 3))
        result = _tile_vertical(strip, 7)
        assert result.size == (8, 7)

    def test_smaller_than_strip(self):
        strip = solid(4, 10, (1, 2, 3))
        result = _tile_vertical(strip, 4)
        assert result.size == (4, 4)

    def test_content_is_tiled(self):
        # Strip is red on top half, blue on bottom half
        strip = Image.new("RGB", (4, 2))
        strip.putpixel((0, 0), (255, 0, 0))
        strip.putpixel((0, 1), (0, 0, 255))
        result = _tile_vertical(strip, 4)
        assert result.getpixel((0, 0)) == (255, 0, 0)
        assert result.getpixel((0, 1)) == (0, 0, 255)
        assert result.getpixel((0, 2)) == (255, 0, 0)  # tiled
        assert result.getpixel((0, 3)) == (0, 0, 255)


# ── _tile_horizontal ─────────────────────────────────────────────────────────


class TestTileHorizontal:
    def test_exact_multiple(self):
        strip = solid(5, 10, (10, 20, 30))
        result = _tile_horizontal(strip, 10)
        assert result.size == (10, 10)

    def test_non_multiple(self):
        strip = solid(3, 8, (10, 20, 30))
        result = _tile_horizontal(strip, 7)
        assert result.size == (7, 8)


# ── _tile_corner ─────────────────────────────────────────────────────────────


class TestTileCorner:
    def test_output_size(self):
        block = solid(3, 3, (5, 10, 15))
        result = _tile_corner(block, 10)
        assert result.size == (10, 10)

    def test_larger_block_clipped(self):
        block = solid(5, 5, (1, 2, 3))
        result = _tile_corner(block, 3)
        assert result.size == (3, 3)


# ── add_bleed: output size ───────────────────────────────────────────────────


class TestAddBleedSize:
    @pytest.mark.parametrize(
        "bleed_mm,source_mm,dpi",
        [
            (3.0, 1.0, 300),
            (3.0, 1.0, 150),
            (1.0, 0.5, 300),
            (5.0, 2.0, 300),
        ],
    )
    def test_output_larger_by_twice_bleed(self, bleed_mm, source_mm, dpi):
        card = solid(200, 280)  # ~16.9 × 23.6mm at 300dpi
        result = add_bleed(card, bleed_mm=bleed_mm, source_mm=source_mm, dpi=dpi)
        expected_bleed_px = round(bleed_mm * dpi / 25.4)
        assert result.width == card.width + 2 * expected_bleed_px
        assert result.height == card.height + 2 * expected_bleed_px

    def test_zero_bleed_returns_same_size(self):
        card = solid(100, 140)
        result = add_bleed(card, bleed_mm=0.0)
        assert result.size == card.size

    def test_zero_bleed_returns_copy(self):
        card = solid(100, 140, color=(10, 20, 30))
        result = add_bleed(card, bleed_mm=0.0)
        assert result is not card  # returns a copy
        assert result.getpixel((0, 0)) == (10, 20, 30)

    def test_original_card_not_modified(self):
        card = solid(100, 140, color=(10, 20, 30))
        original_size = card.size
        add_bleed(card, bleed_mm=3.0)
        assert card.size == original_size


# ── add_bleed: original card preserved in centre ─────────────────────────────


class TestAddBleedContent:
    def test_card_pixels_at_centre(self):
        """The original card image should appear unchanged at (bleed_px, bleed_px)."""
        card = gradient_h(50, 70)
        bleed_mm = 2.0
        dpi = 300
        bleed_px = round(bleed_mm * dpi / 25.4)
        result = add_bleed(card, bleed_mm=bleed_mm, source_mm=1.0, dpi=dpi)

        for x in range(card.width):
            for y in range(card.height):
                assert result.getpixel((x + bleed_px, y + bleed_px)) == card.getpixel((x, y))

    def test_solid_card_entire_output_same_color(self):
        """A solid-colour card should produce an entirely uniform output."""
        color = (77, 133, 200)
        card = solid(40, 56, color)
        result = add_bleed(card, bleed_mm=3.0, source_mm=1.0, dpi=300)
        pixels = set(
            result.getpixel((x, y)) for x in range(result.width) for y in range(result.height)
        )
        assert pixels == {color}


# ── add_bleed: mirroring ─────────────────────────────────────────────────────


class TestAddBleedMirror:
    def test_left_bleed_is_mirror_of_left_edge(self):
        """Pixels just left of the card should be a mirror of the card's left column."""
        card = Image.new("RGB", (20, 20), (0, 0, 0))
        # Set distinct left-column values
        for y in range(20):
            card.putpixel((0, y), (y * 5, 100, 50))

        bleed_mm = 1.0
        dpi = 300
        bleed_px = round(bleed_mm * dpi / 25.4)
        result = add_bleed(card, bleed_mm=bleed_mm, source_mm=1.0, dpi=dpi)

        # The pixel immediately to the left of the card (position bleed_px - 1)
        # should equal card pixel (0, y) — mirrored strip tiled
        for y in range(20):
            expected = card.getpixel((0, y))
            got = result.getpixel((bleed_px - 1, y + bleed_px))
            assert got == expected, f"Row {y}: expected {expected}, got {got}"

    def test_top_bleed_is_mirror_of_top_edge(self):
        """Pixels just above the card should mirror the card's top row."""
        card = Image.new("RGB", (20, 20), (0, 0, 0))
        for x in range(20):
            card.putpixel((x, 0), (x * 5, 50, 200))

        bleed_mm = 1.0
        dpi = 300
        bleed_px = round(bleed_mm * dpi / 25.4)
        result = add_bleed(card, bleed_mm=bleed_mm, source_mm=1.0, dpi=dpi)

        for x in range(20):
            expected = card.getpixel((x, 0))
            got = result.getpixel((x + bleed_px, bleed_px - 1))
            assert got == expected, f"Col {x}: expected {expected}, got {got}"


# ── add_bleed: mode preservation ─────────────────────────────────────────────


class TestAddBleedMode:
    def test_rgba_preserved(self):
        card = Image.new("RGBA", (50, 70), (100, 150, 200, 128))
        result = add_bleed(card, bleed_mm=2.0)
        assert result.mode == "RGBA"

    def test_grayscale_preserved(self):
        card = Image.new("L", (50, 70), 128)
        result = add_bleed(card, bleed_mm=2.0)
        assert result.mode == "L"

    def test_rgb_preserved(self):
        card = solid(50, 70)
        result = add_bleed(card)
        assert result.mode == "RGB"


# ── add_bleed: source_px clamping ───────────────────────────────────────────


class TestAddBleedClamping:
    def test_source_larger_than_card_does_not_raise(self):
        """When source_mm translates to more pixels than the card, it should clamp."""
        card = solid(5, 5)  # tiny card
        # source_mm=5 at 300dpi = ~59px, far larger than card
        result = add_bleed(card, bleed_mm=1.0, source_mm=5.0, dpi=300)
        bleed_px = round(1.0 * 300 / 25.4)
        assert result.width == card.width + 2 * bleed_px
        assert result.height == card.height + 2 * bleed_px
