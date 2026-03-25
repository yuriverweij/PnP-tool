from __future__ import annotations

import io
import math

import fitz  # PyMuPDF
from PIL import Image

PAGE_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (215.9, 279.4),
    "A3": (297.0, 420.0),
}


def compute_grid(
    card_w_mm: float,
    card_h_mm: float,
    page_size: str = "A4",
    margin_mm: float = 5.0,
) -> tuple[int, int]:
    """Return (rows, cols) that fit on the output page."""
    pw, ph = PAGE_SIZES_MM[page_size]
    usable_w = pw - 2 * margin_mm
    usable_h = ph - 2 * margin_mm
    cols = math.floor(usable_w / card_w_mm)
    rows = math.floor(usable_h / card_h_mm)
    if rows < 1 or cols < 1:
        raise ValueError(
            f"Card size {card_w_mm:.1f}×{card_h_mm:.1f}mm is too large to fit on "
            f"{page_size} with {margin_mm}mm margins."
        )
    return rows, cols


def _mirror_back_page(
    backs: list[Image.Image | None],
    rows: int,
    cols: int,
    flip_direction: str,
) -> list[Image.Image | None]:
    """Rearrange backs for duplex alignment.

    horizontal flip (flip along right/left edge):
        reverse each row independently.
    vertical flip (flip along top/bottom edge):
        reverse the order of rows.
    """
    # Pad to full grid
    total = rows * cols
    padded = list(backs) + [None] * (total - len(backs))

    grid = [padded[r * cols : (r + 1) * cols] for r in range(rows)]

    grid = [row[::-1] for row in grid] if flip_direction == "horizontal" else grid[::-1]

    return [cell for row in grid for cell in row]


def _image_to_jpeg_bytes(img: Image.Image, quality: int = 95) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _px_to_pts(px: float, dpi: int) -> float:
    return px * 72.0 / dpi


def _draw_cut_marks(
    page: fitz.Page,
    x0: float,
    y0: float,
    card_w_pts: float,
    card_h_pts: float,
    bleed_pts: float,
    mark_len_pts: float,
    mark_width_pts: float,
) -> None:
    """Draw cut-mark crosses at the 4 inner card corners into the bleed area."""
    length = min(mark_len_pts, bleed_pts)
    if length <= 0:
        return

    corners = [
        (x0 + bleed_pts,               y0 + bleed_pts),               # top-left
        (x0 + card_w_pts - bleed_pts,  y0 + bleed_pts),               # top-right
        (x0 + bleed_pts,               y0 + card_h_pts - bleed_pts),  # bottom-left
        (x0 + card_w_pts - bleed_pts,  y0 + card_h_pts - bleed_pts),  # bottom-right
    ]

    for cx, cy in corners:
        # Horizontal arm — extends through the corner in both directions
        page.draw_line(
            fitz.Point(cx - length, cy),
            fitz.Point(cx + length, cy),
            color=(0, 0, 0),
            width=mark_width_pts,
        )
        # Vertical arm — extends through the corner in both directions
        page.draw_line(
            fitz.Point(cx, cy - length),
            fitz.Point(cx, cy + length),
            color=(0, 0, 0),
            width=mark_width_pts,
        )


def assemble_pdf(
    pairs: list[tuple[Image.Image, Image.Image | None]],
    card_w_mm: float,
    card_h_mm: float,
    bleed_mm: float = 3.0,
    output_page_size: str = "A4",
    margin_mm: float = 5.0,
    flip_direction: str = "horizontal",
    dpi: int = 300,
    jpeg_quality: int = 95,
    cut_marks_fronts: bool = False,
    cut_marks_backs: bool = False,
    cut_mark_length_mm: float = 3.0,
    cut_mark_thickness_mm: float = 0.2,
) -> bytes:
    """Assemble front+back pairs into a duplex-ready PDF.

    Pages alternate: front page, back page, front page, back page, ...
    Back pages are mirrored so that cards align when the sheet is flipped.

    Args:
        pairs: list of (front_image_with_bleed, back_image_with_bleed_or_None)
        card_w_mm / card_h_mm: bleed-extended card size in mm
        bleed_mm: bleed width in mm (used to position cut marks at card boundary)
        output_page_size: "A4", "Letter", or "A3"
        margin_mm: page margin on all sides
        flip_direction: "horizontal" (flip left/right) or "vertical" (flip top/bottom)
        dpi: resolution of the input images
        cut_marks_fronts: draw cut marks on front pages
        cut_marks_backs: draw cut marks on back pages
        jpeg_quality: JPEG quality for embedded images (1–95, default 95)
        cut_mark_length_mm: length of each cut-mark arm in mm (default 3mm)
        cut_mark_thickness_mm: line thickness of cut marks in mm (default 0.2mm)
    """
    rows, cols = compute_grid(card_w_mm, card_h_mm, output_page_size, margin_mm)
    cards_per_page = rows * cols

    pw_mm, ph_mm = PAGE_SIZES_MM[output_page_size]
    pw_pts = pw_mm / 25.4 * 72
    ph_pts = ph_mm / 25.4 * 72
    margin_pts = margin_mm / 25.4 * 72
    card_w_pts = card_w_mm / 25.4 * 72
    card_h_pts = card_h_mm / 25.4 * 72
    bleed_pts = bleed_mm / 25.4 * 72
    mark_len_pts = cut_mark_length_mm / 25.4 * 72
    mark_width_pts = cut_mark_thickness_mm / 25.4 * 72

    # Center the grid on the page (at least margin_mm from each edge)
    grid_w_pts = cols * card_w_pts
    grid_h_pts = rows * card_h_pts
    x_start = max(margin_pts, (pw_pts - grid_w_pts) / 2)
    y_start = max(margin_pts, (ph_pts - grid_h_pts) / 2)

    fronts = [f for f, _ in pairs]
    backs = [b for _, b in pairs]

    doc = fitz.open()

    # Process in page-sized chunks
    for chunk_start in range(0, len(fronts), cards_per_page):
        chunk_fronts = fronts[chunk_start : chunk_start + cards_per_page]
        chunk_backs = backs[chunk_start : chunk_start + cards_per_page]

        # --- Front page ---
        front_page = doc.new_page(width=pw_pts, height=ph_pts)
        for idx, img in enumerate(chunk_fronts):
            if img is None:
                continue
            row, col = divmod(idx, cols)
            x0 = x_start + col * card_w_pts
            y0 = y_start + row * card_h_pts
            rect = fitz.Rect(x0, y0, x0 + card_w_pts, y0 + card_h_pts)
            front_page.insert_image(rect, stream=_image_to_jpeg_bytes(img, jpeg_quality))
            if cut_marks_fronts and bleed_pts > 0:
                _draw_cut_marks(
                    front_page, x0, y0, card_w_pts, card_h_pts,
                    bleed_pts, mark_len_pts, mark_width_pts,
                )

        # --- Back page (skipped when no backs are provided) ---
        if any(b is not None for b in chunk_backs):
            mirrored_backs = _mirror_back_page(chunk_backs, rows, cols, flip_direction)
            back_page = doc.new_page(width=pw_pts, height=ph_pts)
            for idx, img in enumerate(mirrored_backs):
                if img is None:
                    continue
                row, col = divmod(idx, cols)
                x0 = x_start + col * card_w_pts
                y0 = y_start + row * card_h_pts
                rect = fitz.Rect(x0, y0, x0 + card_w_pts, y0 + card_h_pts)
                back_page.insert_image(rect, stream=_image_to_jpeg_bytes(img, jpeg_quality))
                if cut_marks_backs and bleed_pts > 0:
                    _draw_cut_marks(
                        back_page, x0, y0, card_w_pts, card_h_pts,
                        bleed_pts, mark_len_pts, mark_width_pts,
                    )

    return doc.tobytes()
