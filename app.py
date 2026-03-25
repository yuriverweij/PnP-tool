from __future__ import annotations

import base64
import io
import json
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from processing.bleed import add_bleed, trim_card
from processing.pdf_writer import PAGE_SIZES_MM, assemble_pdf, compute_grid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="PnP Tool - add bleed")
app.mount("/static", StaticFiles(directory="static"), name="static")

DPI = 300

PRESETS_MM: dict[str, tuple[float, float]] = {
    "poker": (63.0, 88.0),
    "tarot": (70.0, 120.0),
    "mini": (41.0, 63.0),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _detect_dpi(img: Image.Image) -> int | None:
    dpi_info = img.info.get("dpi")
    if dpi_info:
        dpi_val = dpi_info[0] if isinstance(dpi_info, (tuple, list)) else dpi_info
        try:
            val = float(dpi_val)
            if val > 0:
                return round(val)
        except (TypeError, ValueError):
            pass
    return None


def _resize_to_card(img: Image.Image, card_w_px: int, card_h_px: int) -> Image.Image:
    if img.size != (card_w_px, card_h_px):
        logger.warning(
            "Resizing image from %dx%d to %dx%d",
            img.width,
            img.height,
            card_w_px,
            card_h_px,
        )
        img = img.resize((card_w_px, card_h_px), Image.LANCZOS)
    return img


def _ensure_rgb(img: Image.Image) -> Image.Image:
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _normalize_orientation(img: Image.Image, card_w_px: int, card_h_px: int) -> Image.Image:
    """Apply EXIF rotation then auto-rotate if dimensions are transposed vs card size.

    Handles two common cases:
    - JPEG images from cameras that store rotation in EXIF metadata.
    - Card images intentionally saved in landscape orientation when the card is portrait
      (or vice versa) — rotated 90° or 270°.
    """
    # Apply EXIF orientation tag first (common in camera JPEGs)
    img = ImageOps.exif_transpose(img)

    # Auto-rotate if the image aspect ratio is transposed relative to the card.
    # Skip for square cards where rotation has no effect.
    if card_w_px != card_h_px:
        card_portrait = card_h_px > card_w_px
        img_portrait = img.height > img.width
        if card_portrait != img_portrait:
            logger.info(
                "Auto-rotating image from %dx%d to match card orientation %dx%d",
                img.width,
                img.height,
                card_w_px,
                card_h_px,
            )
            img = img.rotate(-90, expand=True)

    return img


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.post("/api/detect-size")
def detect_size(file: UploadFile = File(...)) -> dict:
    """Detect card dimensions from image DPI metadata."""
    data = file.file.read()
    try:
        img = _open_image(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot open image: {exc}") from exc

    src_dpi = _detect_dpi(img)
    if src_dpi is None:
        return {
            "detected_width_mm": None,
            "detected_height_mm": None,
            "suggested_preset": None,
            "has_dpi_metadata": False,
        }

    w_mm = img.width / src_dpi * 25.4
    h_mm = img.height / src_dpi * 25.4

    suggested = None
    for name, (pw, ph) in PRESETS_MM.items():
        if abs(w_mm - pw) <= 2 and abs(h_mm - ph) <= 2:
            suggested = name
            break

    return {
        "detected_width_mm": round(w_mm, 1),
        "detected_height_mm": round(h_mm, 1),
        "suggested_preset": suggested,
        "has_dpi_metadata": True,
    }


@app.post("/api/process")
def process(
    fronts: list[UploadFile] = File(...),
    back_mode: str = Form("single"),
    default_back: UploadFile | None = File(default=None),
    backs: list[UploadFile] = File(default=[]),
    override_backs: str = Form(default="{}"),
    card_width_mm: float = Form(63.0),
    card_height_mm: float = Form(88.0),
    output_page_size: str = Form("A4"),
    output_margin_mm: float = Form(5.0),
    bleed_mm: float = Form(3.0),
    source_mm: float = Form(1.0),
    flip_direction: str = Form("horizontal"),
    jpeg_quality: int = Form(95),
    cut_marks_fronts: bool = Form(default=False),
    cut_marks_backs: bool = Form(default=False),
    cut_mark_length_mm: float = Form(3.0),
    cut_mark_thickness_mm: float = Form(0.2),
    trim_mm: float = Form(0.0),
):
    """Process front (and optional back) card images into a bleed-extended PDF."""
    # --- Validate parameters ---
    if output_page_size not in PAGE_SIZES_MM:
        raise HTTPException(status_code=422, detail=f"Unknown page size: {output_page_size}")
    if flip_direction not in ("horizontal", "vertical"):
        raise HTTPException(
            status_code=422, detail="flip_direction must be 'horizontal' or 'vertical'"
        )
    if not fronts:
        raise HTTPException(status_code=422, detail="No front images provided.")

    try:
        overrides: dict[str, str] = json.loads(override_backs)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422, detail=f"override_backs is not valid JSON: {exc}"
        ) from exc

    card_w_px = round(card_width_mm * DPI / 25.4)
    card_h_px = round(card_height_mm * DPI / 25.4)

    # --- Load front images ---
    front_images: list[Image.Image] = []
    for f in fronts:
        data = f.file.read()
        try:
            img = _ensure_rgb(_open_image(data))
            img = _normalize_orientation(img, card_w_px, card_h_px)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Cannot open front image '{f.filename}': {exc}",
            ) from exc
        img = _resize_to_card(img, card_w_px, card_h_px)
        if trim_mm > 0:
            img = trim_card(img, trim_mm, DPI)
        front_images.append(img)

    # --- Load default back ---
    default_back_img: Image.Image | None = None
    if default_back is not None and default_back.filename:
        data = default_back.file.read()
        try:
            default_back_img = _ensure_rgb(_open_image(data))
            default_back_img = _normalize_orientation(default_back_img, card_w_px, card_h_px)
            default_back_img = _resize_to_card(default_back_img, card_w_px, card_h_px)
            if trim_mm > 0:
                default_back_img = trim_card(default_back_img, trim_mm, DPI)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Cannot open default back image: {exc}"
            ) from exc

    # --- Build back image list ---
    back_images: list[Image.Image | None]

    if back_mode == "single":
        if default_back_img is None:
            raise HTTPException(
                status_code=422, detail="back_mode='single' requires a default_back image."
            )
        back_images = [default_back_img] * len(front_images)

    elif back_mode == "individual":
        if not backs:
            raise HTTPException(
                status_code=422, detail="back_mode='individual' requires back images."
            )
        back_images = []
        for f in backs[: len(front_images)]:
            data = f.file.read()
            try:
                img = _ensure_rgb(_open_image(data))
                img = _normalize_orientation(img, card_w_px, card_h_px)
                img = _resize_to_card(img, card_w_px, card_h_px)
                if trim_mm > 0:
                    img = trim_card(img, trim_mm, DPI)
                back_images.append(img)
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot open back image '{f.filename}': {exc}",
                ) from exc
        # Pad with None if fewer backs than fronts
        back_images += [None] * (len(front_images) - len(back_images))

    elif back_mode == "default_override":
        if default_back_img is None:
            raise HTTPException(
                status_code=422,
                detail="back_mode='default_override' requires a default_back image.",
            )
        back_images = []
        for i in range(len(front_images)):
            key = str(i)
            if key in overrides:
                try:
                    img_bytes = base64.b64decode(overrides[key])
                    img = _ensure_rgb(_open_image(img_bytes))
                    img = _normalize_orientation(img, card_w_px, card_h_px)
                    img = _resize_to_card(img, card_w_px, card_h_px)
                    if trim_mm > 0:
                        img = trim_card(img, trim_mm, DPI)
                    back_images.append(img)
                except Exception as exc:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Cannot decode override back for card {i}: {exc}",
                    ) from exc
            else:
                back_images.append(default_back_img)
    else:
        raise HTTPException(status_code=422, detail=f"Unknown back_mode: {back_mode}")

    # --- Add bleed to all images ---
    try:
        bleed_w_mm = card_width_mm + 2 * bleed_mm
        bleed_h_mm = card_height_mm + 2 * bleed_mm

        bleed_fronts = [add_bleed(img, bleed_mm, source_mm, DPI) for img in front_images]
        bleed_backs: list[Image.Image | None] = [
            add_bleed(img, bleed_mm, source_mm, DPI) if img is not None else None
            for img in back_images
        ]
    except Exception as exc:
        logger.exception("Bleed processing failed")
        raise HTTPException(status_code=500, detail=f"Bleed processing failed: {exc}") from exc

    # --- Validate fit; auto-rotate for better page utilization ---
    try:
        rows_n, cols_n = compute_grid(bleed_w_mm, bleed_h_mm, output_page_size, output_margin_mm)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if bleed_w_mm != bleed_h_mm:
        try:
            rows_r, cols_r = compute_grid(bleed_h_mm, bleed_w_mm, output_page_size, output_margin_mm)
            if rows_r * cols_r > rows_n * cols_n:
                logger.info(
                    "Rotating cards 90° for better fit (%d → %d per page)",
                    rows_n * cols_n,
                    rows_r * cols_r,
                )
                bleed_fronts = [img.rotate(90, expand=True) for img in bleed_fronts]
                # For horizontal flip the sheet is mirrored left↔right, so the
                # card's top (pointing LEFT after 90° CCW) would point RIGHT on
                # the back — the back image must be rotated the opposite way (CW).
                # For vertical flip left/right is unchanged, so backs also get CCW.
                back_rot = -90 if flip_direction == "horizontal" else 90
                bleed_backs = [
                    img.rotate(back_rot, expand=True) if img is not None else None
                    for img in bleed_backs
                ]
                bleed_w_mm, bleed_h_mm = bleed_h_mm, bleed_w_mm
        except ValueError:
            pass  # rotated layout doesn't fit; keep original orientation

    pairs = list(zip(bleed_fronts, bleed_backs, strict=False))

    # --- Assemble PDF ---
    try:
        pdf_bytes = assemble_pdf(
            pairs=pairs,
            card_w_mm=bleed_w_mm,
            card_h_mm=bleed_h_mm,
            bleed_mm=bleed_mm,
            output_page_size=output_page_size,
            margin_mm=output_margin_mm,
            flip_direction=flip_direction,
            dpi=DPI,
            jpeg_quality=jpeg_quality,
            cut_marks_fronts=cut_marks_fronts,
            cut_marks_backs=cut_marks_backs,
            cut_mark_length_mm=cut_mark_length_mm,
            cut_mark_thickness_mm=cut_mark_thickness_mm,
        )
    except Exception as exc:
        logger.exception("PDF assembly failed")
        raise HTTPException(status_code=500, detail=f"PDF assembly failed: {exc}") from exc

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="pnp_output.pdf"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
