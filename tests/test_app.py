"""Integration tests for FastAPI routes in app.py.

Uses httpx's ASGI transport to test the app in-process without a running server.
Note: static files (GET /) are not tested here because the test runner doesn't
have the static directory on the ASGI path; the route logic is trivially a FileResponse.
"""

from __future__ import annotations

import io

import fitz
from fastapi.testclient import TestClient
from PIL import Image

from app import _normalize_orientation, app

client = TestClient(app, raise_server_exceptions=True)


# ── Helpers ──────────────────────────────────────────────────────────────────


def make_png_bytes(
    w: int = 200, h: int = 280, color=(180, 100, 60), dpi: int | None = 300
) -> bytes:
    """Return in-memory PNG bytes for a solid-colour image."""
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    if dpi:
        img.save(buf, format="PNG", dpi=(dpi, dpi))
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


def png_file(name: str = "card.png", **kwargs) -> tuple[str, bytes, str]:
    """Return a (name, bytes, mime) triple for multipart upload."""
    return (name, make_png_bytes(**kwargs), "image/png")


# ── _normalize_orientation ───────────────────────────────────────────────────


class TestNormalizeOrientation:
    def test_portrait_image_for_portrait_card_unchanged(self):
        img = Image.new("RGB", (200, 280))  # portrait
        result = _normalize_orientation(img, card_w_px=200, card_h_px=280)
        assert result.size == (200, 280)

    def test_landscape_image_for_portrait_card_rotated(self):
        img = Image.new("RGB", (280, 200))  # landscape
        result = _normalize_orientation(img, card_w_px=200, card_h_px=280)
        # After -90° rotation with expand=True: (200, 280)
        assert result.width < result.height

    def test_portrait_image_for_landscape_card_rotated(self):
        img = Image.new("RGB", (200, 280))  # portrait
        result = _normalize_orientation(img, card_w_px=280, card_h_px=200)
        assert result.width > result.height

    def test_square_card_never_rotated(self):
        img = Image.new("RGB", (300, 200))  # landscape image
        result = _normalize_orientation(img, card_w_px=100, card_h_px=100)
        assert result.size == (300, 200)  # unchanged

    def test_already_correct_landscape(self):
        img = Image.new("RGB", (400, 200))  # landscape
        result = _normalize_orientation(img, card_w_px=400, card_h_px=200)
        assert result.size == (400, 200)  # unchanged

    def test_landscape_top_long_edge_maps_to_portrait_right_column(self):
        """Verify CW rotation direction matches the upload convention.

        Convention:
          - Landscape (horizontal) card: top of image = long edge (W side at y=0)
          - Portrait (vertical) card: top of image = short edge (W side at y=0)

        After rotating landscape (W>H) to portrait via -90° (CW):
          - Landscape top row (long edge)  → portrait right column
          - Landscape left column          → portrait top row (short edge)
        """
        # 8×4 landscape image; mark top row RED (long edge) and left col BLUE
        img = Image.new("RGB", (8, 4), (0, 200, 0))
        for x in range(8):
            img.putpixel((x, 0), (255, 0, 0))   # top row RED  (long edge of landscape card)
        for y in range(4):
            img.putpixel((0, y), (0, 0, 255))   # left col BLUE (overrides corner at (0,0))

        result = _normalize_orientation(img, card_w_px=4, card_h_px=8)
        assert result.size == (4, 8)  # portrait

        # CW: left col → portrait top row  (short edge convention satisfied)
        assert result.getpixel((1, 0))[:3] == (0, 0, 255)   # BLUE in top row
        assert result.getpixel((2, 0))[:3] == (0, 0, 255)   # BLUE in top row

        # CW: top row → portrait right column
        assert result.getpixel((3, 2))[:3] == (255, 0, 0)   # RED in right col
        assert result.getpixel((3, 5))[:3] == (255, 0, 0)   # RED in right col


# ── POST /api/detect-size ────────────────────────────────────────────────────


class TestDetectSize:
    def test_with_dpi_metadata(self):
        data = make_png_bytes(w=744, h=1039, dpi=300)  # ~63×88mm at 300dpi
        res = client.post("/api/detect-size", files={"file": ("card.png", data, "image/png")})
        assert res.status_code == 200
        body = res.json()
        assert body["has_dpi_metadata"] is True
        assert body["detected_width_mm"] is not None
        assert body["detected_height_mm"] is not None

    def test_poker_size_suggests_preset(self):
        # 744×1039 px at 300dpi ≈ 63.0×88.1mm → within ±2mm of poker preset
        data = make_png_bytes(w=744, h=1039, dpi=300)
        res = client.post("/api/detect-size", files={"file": ("card.png", data, "image/png")})
        assert res.status_code == 200
        body = res.json()
        assert body["suggested_preset"] == "poker"

    def test_no_dpi_metadata(self):
        data = make_png_bytes(dpi=None)
        res = client.post("/api/detect-size", files={"file": ("card.png", data, "image/png")})
        assert res.status_code == 200
        body = res.json()
        assert body["has_dpi_metadata"] is False
        assert body["detected_width_mm"] is None
        assert body["detected_height_mm"] is None
        assert body["suggested_preset"] is None

    def test_unrecognised_size_no_preset(self):
        # 600×800px at 300dpi ≈ 50.8×67.7mm — no close preset
        data = make_png_bytes(w=600, h=800, dpi=300)
        res = client.post("/api/detect-size", files={"file": ("card.png", data, "image/png")})
        assert res.status_code == 200
        body = res.json()
        assert body["suggested_preset"] is None

    def test_invalid_file_returns_422(self):
        res = client.post(
            "/api/detect-size",
            files={"file": ("card.png", b"not an image", "image/png")},
        )
        assert res.status_code == 422


# ── POST /api/process ────────────────────────────────────────────────────────


class TestProcess:
    # -- Happy paths --

    def test_single_back_mode_returns_pdf(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "output_page_size": "A4",
                "bleed_mm": 3,
                "source_mm": 1,
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.content[:4] == b"%PDF"

    def test_individual_back_mode(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "individual",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "output_page_size": "A4",
                "bleed_mm": 3,
                "source_mm": 1,
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("backs", ("b1.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_multiple_fronts_single_back(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "bleed_mm": 3,
                "source_mm": 1,
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("fronts", ("f2.png", front, "image/png")),
                ("fronts", ("f3.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200

    def test_vertical_flip(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "flip_direction": "vertical",
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200

    def test_zero_bleed(self):
        """Zero bleed should still produce a valid PDF."""
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "bleed_mm": 0,
                "source_mm": 1,
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    # -- Validation errors --

    def test_no_fronts_returns_422(self):
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={"back_mode": "single"},
            files=[("default_back", ("back.png", back, "image/png"))],
        )
        assert res.status_code == 422

    def test_single_mode_without_back_returns_422(self):
        front = make_png_bytes()
        res = client.post(
            "/api/process",
            data={"back_mode": "single", "card_width_mm": 63, "card_height_mm": 88},
            files=[("fronts", ("f1.png", front, "image/png"))],
        )
        assert res.status_code == 422

    def test_individual_mode_without_backs_returns_422(self):
        front = make_png_bytes()
        res = client.post(
            "/api/process",
            data={"back_mode": "individual", "card_width_mm": 63, "card_height_mm": 88},
            files=[("fronts", ("f1.png", front, "image/png"))],
        )
        assert res.status_code == 422

    def test_unknown_back_mode_returns_422(self):
        front = make_png_bytes()
        res = client.post(
            "/api/process",
            data={"back_mode": "bogus", "card_width_mm": 63, "card_height_mm": 88},
            files=[("fronts", ("f1.png", front, "image/png"))],
        )
        assert res.status_code == 422

    def test_invalid_page_size_returns_422(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "output_page_size": "A99",
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 422

    def test_invalid_flip_direction_returns_422(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "flip_direction": "diagonal",
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 422

    def test_card_too_large_returns_422(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 500,
                "card_height_mm": 500,
                "output_page_size": "A4",
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 422

    def test_invalid_override_backs_json_returns_422(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "default_override",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "override_backs": "not-json",
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 422

    def test_corrupt_front_image_returns_422(self):
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={"back_mode": "single", "card_width_mm": 63, "card_height_mm": 88},
            files=[
                ("fronts", ("bad.png", b"not-an-image", "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 422

    # -- Rotation handling --

    def test_landscape_front_auto_rotated_for_portrait_card(self):
        """A front image supplied in landscape (W>H) is accepted for a portrait card."""
        # Portrait card: 63×88mm. Supply a landscape image (280×200) → should rotate.
        front = make_png_bytes(w=280, h=200)  # landscape
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={"back_mode": "single", "card_width_mm": 63, "card_height_mm": 88},
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_portrait_front_auto_rotated_for_landscape_card(self):
        """A portrait image is accepted for a landscape card (W>H)."""
        # Landscape card: 88×63mm. Supply a portrait image (200×280) → should rotate.
        front = make_png_bytes(w=200, h=280)  # portrait
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={"back_mode": "single", "card_width_mm": 88, "card_height_mm": 63},
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    # -- Cut marks --

    def test_cut_marks_on_fronts_and_backs(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "cut_marks_fronts": "true",
                "cut_marks_backs": "true",
                "cut_mark_length_mm": 3,
                "cut_mark_thickness_mm": 0.2,
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_cut_marks_off(self):
        front = make_png_bytes()
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 63,
                "card_height_mm": 88,
                "cut_marks_fronts": "false",
                "cut_marks_backs": "false",
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_square_card_no_rotation_applied(self):
        """Square cards are never rotated regardless of image orientation."""
        front = make_png_bytes(w=300, h=200)  # landscape image
        back = make_png_bytes(color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={"back_mode": "single", "card_width_mm": 70, "card_height_mm": 70},
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200


# ── Mixed orientations ────────────────────────────────────────────────────────


class TestMixedOrientations:
    """Tests where some front images are landscape and some are portrait.

    Upload convention:
      - Horizontal (landscape) card image: image top = long edge
      - Vertical (portrait) card image:    image top = short edge
    All images are normalized to the card preset orientation before processing.
    """

    def _process(self, files, *, card_w=63, card_h=88, back_mode="single", **extra_data):
        data = {"back_mode": back_mode, "card_width_mm": card_w, "card_height_mm": card_h,
                "bleed_mm": 3, "source_mm": 1, **extra_data}
        return client.post("/api/process", data=data, files=files)

    def test_mixed_fronts_single_back_portrait_card(self):
        """3 landscape + 2 portrait fronts all normalise to a portrait card preset."""
        landscape = make_png_bytes(w=280, h=200)  # horizontal → top is long edge
        portrait  = make_png_bytes(w=200, h=280)  # vertical   → top is short edge
        back = make_png_bytes(color=(0, 100, 200))
        res = self._process(
            files=[
                ("fronts", ("l1.png", landscape, "image/png")),
                ("fronts", ("p1.png", portrait,  "image/png")),
                ("fronts", ("l2.png", landscape, "image/png")),
                ("fronts", ("p2.png", portrait,  "image/png")),
                ("fronts", ("l3.png", landscape, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_mixed_fronts_single_back_landscape_card(self):
        """Mixed fronts for a landscape card preset (W>H)."""
        landscape = make_png_bytes(w=280, h=200)  # matches landscape card → no rotation
        portrait  = make_png_bytes(w=200, h=280)  # mismatch → rotated to landscape
        back = make_png_bytes(color=(0, 100, 200))
        res = self._process(
            files=[
                ("fronts", ("l1.png", landscape, "image/png")),
                ("fronts", ("p1.png", portrait,  "image/png")),
                ("fronts", ("l2.png", landscape, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
            card_w=88, card_h=63,  # landscape preset
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_mixed_fronts_individual_backs_portrait_card(self):
        """Individual backs follow the same orientation normalisation as their fronts."""
        landscape = make_png_bytes(w=280, h=200)
        portrait  = make_png_bytes(w=200, h=280)
        back_l = make_png_bytes(w=280, h=200, color=(0, 100, 200))  # landscape back
        back_p = make_png_bytes(w=200, h=280, color=(0, 100, 200))  # portrait back
        res = self._process(
            files=[
                ("fronts", ("l1.png", landscape, "image/png")),
                ("fronts", ("p1.png", portrait,  "image/png")),
                ("fronts", ("l2.png", landscape, "image/png")),
                ("backs",  ("bl1.png", back_l,   "image/png")),
                ("backs",  ("bp1.png", back_p,   "image/png")),
                ("backs",  ("bl2.png", back_l,   "image/png")),
            ],
            back_mode="individual",
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"

    def test_all_landscape_fronts_for_portrait_card(self):
        """All landscape images supplied for a portrait preset are all rotated uniformly."""
        landscape = make_png_bytes(w=280, h=200)
        back = make_png_bytes(color=(0, 100, 200))
        res = self._process(
            files=[
                ("fronts", (f"l{i}.png", landscape, "image/png")) for i in range(4)
            ] + [("default_back", ("back.png", back, "image/png"))],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"


# ── Layout rotation for better page fit ──────────────────────────────────────


class TestLayoutRotationBackOrientation:
    """Verify the rotation *direction* applied to back images during layout optimization.

    When layout rotation triggers (cards rotated 90° CCW so more fit per page):
      - Front images: 90° CCW  → card top moves to the LEFT side of the placed card.
      - Backs, horizontal flip: 90° CW → card top moves to the RIGHT side (so after
        the sheet is flipped left↔right, both fronts and backs look the same way up).
      - Backs, vertical flip: 90° CCW (same as fronts), because vertical flip doesn't
        change left/right orientation.

    Test card: portrait 200×280 px, TOP HALF RED, bottom half WHITE.
    After CCW rotation: left half of card = RED.
    After CW  rotation: right half of card = RED.

    Poker preset: 63×88mm + 3mm bleed = 69×94mm.
    Layout rotation chosen: 2 cols × 4 rows = 8/page (vs portrait 2 cols × 3 rows = 6/page).
    After rotation card dimensions in PDF: w=94mm, h=69mm.

    Geometry (A4 210×297mm, margin 5mm, 72/25.4 pt/mm):
      card_w_pt ≈ 266.5,  card_h_pt ≈ 195.6
      x_start   ≈  31.2,  y_start   ≈  29.8
    """

    MM = 72 / 25.4
    CARD_W_MM, CARD_H_MM = 63.0, 88.0
    BLEED_MM = 3.0
    # After layout rotation: width ↔ height of bleed card
    BW = (CARD_H_MM + 2 * BLEED_MM) * MM  # 94mm → pts
    BH = (CARD_W_MM + 2 * BLEED_MM) * MM  # 69mm → pts
    PW = 210 * MM
    PH = 297 * MM
    MARGIN = 5 * MM
    COLS = 2
    ROWS = 4
    X_START = max(MARGIN, (PW - COLS * BW) / 2)
    Y_START = max(MARGIN, (PH - ROWS * BH) / 2)

    BASE = {
        "back_mode": "single",
        "card_width_mm": CARD_W_MM,
        "card_height_mm": CARD_H_MM,
        "bleed_mm": BLEED_MM,
        "output_page_size": "A4",
        "output_margin_mm": 5,
    }

    @staticmethod
    def _make_top_half_red_card():
        """Portrait 200×280: top 140 rows RED, bottom 140 rows WHITE."""
        img = Image.new("RGB", (200, 280), (255, 255, 255))
        pixels = img.load()
        for y in range(140):
            for x in range(200):
                pixels[x, y] = (220, 0, 0)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _is_red(pixel) -> bool:
        return pixel[0] > 150 and pixel[1] < 80 and pixel[2] < 80

    @staticmethod
    def _is_not_red(pixel) -> bool:
        return pixel[0] < 120 or pixel[1] > 120

    def _sample(self, pix, x: float, y: float):
        return pix.pixel(int(x), int(y))

    def test_horizontal_flip_front_top_goes_left(self):
        """Front card: layout rotation is 90° CCW → card top (RED) on LEFT side."""
        card = self._make_top_half_red_card()
        res = client.post(
            "/api/process",
            data={**self.BASE, "flip_direction": "horizontal"},
            files=[
                ("fronts", ("f.png", card, "image/png")),
                ("default_back", ("b.png", card, "image/png")),
            ],
        )
        assert res.status_code == 200
        front_pix = fitz.open(stream=res.content, filetype="pdf")[0].get_pixmap(
            matrix=fitz.Matrix(1, 1)
        )
        # Front card is at (X_START, Y_START), size BW×BH
        cx_left  = self.X_START + self.BW * 0.25
        cx_right = self.X_START + self.BW * 0.75
        cy       = self.Y_START + self.BH * 0.50

        assert self._is_red(self._sample(front_pix, cx_left, cy)), \
            "Front card left side should be RED (card top after 90° CCW)"
        assert self._is_not_red(self._sample(front_pix, cx_right, cy)), \
            "Front card right side should NOT be red (card bottom after 90° CCW)"

    def test_horizontal_flip_back_top_goes_right(self):
        """Back card (horizontal flip): 90° CW → card top (RED) on RIGHT side.

        With 1 card in a 2-column grid, _mirror_back_page moves it to column 1.
        """
        card = self._make_top_half_red_card()
        res = client.post(
            "/api/process",
            data={**self.BASE, "flip_direction": "horizontal"},
            files=[
                ("fronts", ("f.png", card, "image/png")),
                ("default_back", ("b.png", card, "image/png")),
            ],
        )
        assert res.status_code == 200
        back_pix = fitz.open(stream=res.content, filetype="pdf")[1].get_pixmap(
            matrix=fitz.Matrix(1, 1)
        )
        # _mirror_back_page: [card, None] → [None, card] → card at column 1
        x0       = self.X_START + self.BW          # column 1
        cx_left  = x0 + self.BW * 0.25
        cx_right = x0 + self.BW * 0.75
        cy       = self.Y_START + self.BH * 0.50

        assert self._is_red(self._sample(back_pix, cx_right, cy)), \
            "Back card right side should be RED (card top after 90° CW for h-flip)"
        assert self._is_not_red(self._sample(back_pix, cx_left, cy)), \
            "Back card left side should NOT be red (card bottom after 90° CW)"

    def test_vertical_flip_back_top_also_goes_left(self):
        """Back card (vertical flip): same 90° CCW as front → card top on LEFT side.

        With 1 card in a 2×4 grid, vertical flip moves it to row 3 (row order reversed).
        """
        card = self._make_top_half_red_card()
        res = client.post(
            "/api/process",
            data={**self.BASE, "flip_direction": "vertical"},
            files=[
                ("fronts", ("f.png", card, "image/png")),
                ("default_back", ("b.png", card, "image/png")),
            ],
        )
        assert res.status_code == 200
        back_pix = fitz.open(stream=res.content, filetype="pdf")[1].get_pixmap(
            matrix=fitz.Matrix(1, 1)
        )
        # _mirror_back_page vertical: rows reversed → card (was row 0) goes to row 3
        x0       = self.X_START                    # column 0 unchanged
        y0       = self.Y_START + 3 * self.BH      # row 3
        cx_left  = x0 + self.BW * 0.25
        cx_right = x0 + self.BW * 0.75
        cy       = y0 + self.BH * 0.50

        assert self._is_red(self._sample(back_pix, cx_left, cy)), \
            "Back card left side should be RED (90° CCW same as front for v-flip)"
        assert self._is_not_red(self._sample(back_pix, cx_right, cy)), \
            "Back card right side should NOT be red"


class TestLayoutRotation:
    """Tests for the auto-rotate-for-better-fit optimization in /api/process.

    For poker cards (63×88mm + 3mm bleed = 69×94mm) on A4 with 5mm margin:
      - Portrait layout: cols=floor(200/69)=2, rows=floor(287/94)=3 → 6 cards/page
      - Landscape layout: cols=floor(200/94)=2, rows=floor(287/69)=4 → 8 cards/page
    The landscape layout wins, so all cards are rotated 90° before assembly.
    """

    BASE = {
        "back_mode": "single",
        "card_width_mm": 63,
        "card_height_mm": 88,
        "bleed_mm": 3,
        "output_page_size": "A4",
        "output_margin_mm": 5,
    }

    def test_seven_cards_fit_on_one_page_after_rotation(self):
        """Without rotation 7 cards need 2 front+back page pairs (4 pages total).
        With layout rotation (8/page) they fit on 1 pair → 2 PDF pages."""
        front = make_png_bytes(w=200, h=280)  # portrait front
        back  = make_png_bytes(w=200, h=280, color=(0, 100, 200))
        files = [("fronts", (f"f{i}.png", front, "image/png")) for i in range(7)]
        files.append(("default_back", ("back.png", back, "image/png")))
        res = client.post("/api/process", data=self.BASE, files=files)
        assert res.status_code == 200
        doc = fitz.open(stream=res.content, filetype="pdf")
        assert doc.page_count == 2  # 1 front page + 1 back page

    def test_nine_cards_need_two_page_pairs_after_rotation(self):
        """9 cards exceed 8/page → needs 2 front+back pairs = 4 PDF pages."""
        front = make_png_bytes(w=200, h=280)
        back  = make_png_bytes(w=200, h=280, color=(0, 100, 200))
        files = [("fronts", (f"f{i}.png", front, "image/png")) for i in range(9)]
        files.append(("default_back", ("back.png", back, "image/png")))
        res = client.post("/api/process", data=self.BASE, files=files)
        assert res.status_code == 200
        doc = fitz.open(stream=res.content, filetype="pdf")
        assert doc.page_count == 4  # 2 front pages + 2 back pages

    def test_layout_rotation_with_individual_backs(self):
        """Layout rotation applied to fronts (CCW) and backs (CW for horizontal flip)."""
        front = make_png_bytes(w=200, h=280, color=(255, 0, 0))
        back  = make_png_bytes(w=200, h=280, color=(0, 0, 255))
        files = [("fronts", (f"f{i}.png", front, "image/png")) for i in range(3)]
        files += [("backs", (f"b{i}.png", back, "image/png")) for i in range(3)]
        res = client.post(
            "/api/process",
            data={**self.BASE, "back_mode": "individual"},
            files=files,
        )
        assert res.status_code == 200
        doc = fitz.open(stream=res.content, filetype="pdf")
        assert doc.page_count == 2

    def test_layout_rotation_with_mixed_orientation_fronts(self):
        """Landscape and portrait fronts, both normalized then layout-rotated."""
        landscape = make_png_bytes(w=280, h=200)
        portrait  = make_png_bytes(w=200, h=280)
        back = make_png_bytes(color=(0, 100, 200))
        # 3 landscape + 4 portrait = 7 fronts → 1 page with rotation
        files = (
            [("fronts", (f"l{i}.png", landscape, "image/png")) for i in range(3)]
            + [("fronts", (f"p{i}.png", portrait,  "image/png")) for i in range(4)]
            + [("default_back", ("back.png", back, "image/png"))]
        )
        res = client.post("/api/process", data=self.BASE, files=files)
        assert res.status_code == 200
        doc = fitz.open(stream=res.content, filetype="pdf")
        assert doc.page_count == 2

    def test_square_card_no_layout_rotation(self):
        """Square cards: both orientations yield identical grids, no rotation occurs."""
        front = make_png_bytes(w=200, h=200)
        back  = make_png_bytes(w=200, h=200, color=(0, 100, 200))
        res = client.post(
            "/api/process",
            data={
                "back_mode": "single",
                "card_width_mm": 70,
                "card_height_mm": 70,
                "bleed_mm": 3,
                "output_page_size": "A4",
                "output_margin_mm": 5,
            },
            files=[
                ("fronts", ("f1.png", front, "image/png")),
                ("default_back", ("back.png", back, "image/png")),
            ],
        )
        assert res.status_code == 200
        assert res.content[:4] == b"%PDF"
