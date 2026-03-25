from __future__ import annotations

from PIL import Image


def add_bleed(
    card: Image.Image,
    bleed_mm: float = 3.0,
    source_mm: float = 1.0,
    dpi: int = 300,
) -> Image.Image:
    """Add mirrored bleed to a card image.

    Takes the outer `source_mm` strip of each edge, mirrors it, and tiles it
    to fill `bleed_mm` on all four sides.
    """
    if bleed_mm <= 0:
        return card.copy()
    bleed_px = _mm_to_px(bleed_mm, dpi)
    source_px = _mm_to_px(source_mm, dpi)
    source_px = max(1, min(source_px, card.width, card.height))
    return _add_bleed_px(card, bleed_px, source_px)


def trim_card(card: Image.Image, trim_mm: float, dpi: int = 300) -> Image.Image:
    """Crop trim_mm from each edge, then resize back to the original dimensions.

    Removes colored border artifacts before bleed is sourced from the edge.
    """
    if trim_mm <= 0:
        return card.copy()
    trim_px = _mm_to_px(trim_mm, dpi)
    trim_px = min(trim_px, card.width // 2 - 1, card.height // 2 - 1)
    w, h = card.size
    cropped = card.crop((trim_px, trim_px, w - trim_px, h - trim_px))
    return cropped.resize((w, h), Image.LANCZOS)


def _mm_to_px(mm: float, dpi: int) -> int:
    return max(1, round(mm * dpi / 25.4))


def _add_bleed_px(card: Image.Image, bleed_px: int, source_px: int) -> Image.Image:
    w, h = card.size
    mode = card.mode

    # --- Edge strips ---
    # Top: crop top source_px rows, flip vertically, tile to bleed_px rows
    top_src = card.crop((0, 0, w, source_px)).transpose(Image.FLIP_TOP_BOTTOM)
    top_strip = _tile_vertical(top_src, bleed_px)

    # Bottom: crop bottom source_px rows, flip vertically, tile to bleed_px rows
    bot_src = card.crop((0, h - source_px, w, h)).transpose(Image.FLIP_TOP_BOTTOM)
    bot_strip = _tile_vertical(bot_src, bleed_px)

    # Left: crop left source_px cols, flip horizontally, tile to bleed_px cols
    left_src = card.crop((0, 0, source_px, h)).transpose(Image.FLIP_LEFT_RIGHT)
    left_strip = _tile_horizontal(left_src, bleed_px)

    # Right: crop right source_px cols, flip horizontally, tile to bleed_px cols
    right_src = card.crop((w - source_px, 0, w, h)).transpose(Image.FLIP_LEFT_RIGHT)
    right_strip = _tile_horizontal(right_src, bleed_px)

    # --- Corner blocks (cross-mirror: flip both axes) ---
    tl = _tile_corner(
        card.crop((0, 0, source_px, source_px))
        .transpose(Image.FLIP_LEFT_RIGHT)
        .transpose(Image.FLIP_TOP_BOTTOM),
        bleed_px,
    )
    tr = _tile_corner(
        card.crop((w - source_px, 0, w, source_px))
        .transpose(Image.FLIP_LEFT_RIGHT)
        .transpose(Image.FLIP_TOP_BOTTOM),
        bleed_px,
    )
    bl = _tile_corner(
        card.crop((0, h - source_px, source_px, h))
        .transpose(Image.FLIP_LEFT_RIGHT)
        .transpose(Image.FLIP_TOP_BOTTOM),
        bleed_px,
    )
    br = _tile_corner(
        card.crop((w - source_px, h - source_px, w, h))
        .transpose(Image.FLIP_LEFT_RIGHT)
        .transpose(Image.FLIP_TOP_BOTTOM),
        bleed_px,
    )

    # --- Compose ---
    out_w = w + 2 * bleed_px
    out_h = h + 2 * bleed_px
    out = Image.new(mode, (out_w, out_h))

    out.paste(tl, (0, 0))
    out.paste(top_strip, (bleed_px, 0))
    out.paste(tr, (bleed_px + w, 0))

    out.paste(left_strip, (0, bleed_px))
    out.paste(card, (bleed_px, bleed_px))
    out.paste(right_strip, (bleed_px + w, bleed_px))

    out.paste(bl, (0, bleed_px + h))
    out.paste(bot_strip, (bleed_px, bleed_px + h))
    out.paste(br, (bleed_px + w, bleed_px + h))

    return out


def _tile_vertical(strip: Image.Image, target_height: int) -> Image.Image:
    """Tile a strip downward until it reaches target_height."""
    sw, sh = strip.size
    result = Image.new(strip.mode, (sw, target_height))
    y = 0
    while y < target_height:
        chunk_h = min(sh, target_height - y)
        result.paste(strip.crop((0, 0, sw, chunk_h)), (0, y))
        y += chunk_h
    return result


def _tile_horizontal(strip: Image.Image, target_width: int) -> Image.Image:
    """Tile a strip rightward until it reaches target_width."""
    sw, sh = strip.size
    result = Image.new(strip.mode, (target_width, sh))
    x = 0
    while x < target_width:
        chunk_w = min(sw, target_width - x)
        result.paste(strip.crop((0, 0, chunk_w, sh)), (x, 0))
        x += chunk_w
    return result


def _tile_corner(block: Image.Image, size: int) -> Image.Image:
    """Tile a small block to fill a size×size square."""
    bw, bh = block.size
    result = Image.new(block.mode, (size, size))
    y = 0
    while y < size:
        chunk_h = min(bh, size - y)
        x = 0
        while x < size:
            chunk_w = min(bw, size - x)
            result.paste(block.crop((0, 0, chunk_w, chunk_h)), (x, y))
            x += chunk_w
        y += chunk_h
    return result
