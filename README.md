# PnP Tool - add bleed

A local web app that adds **bleed** to PnP (Print and Play) card images so that manual duplex printing is easier to align and cuts don't show white edges.

## How it works

Bleed is a small margin of artwork that extends beyond the cut line. Without it, even a tiny misalignment when flipping paper for duplex printing is visible as a white edge. This tool adds ~3 mm of bleed to each card by mirroring and tiling the outer 1 mm of the card's edge — so the printed bleed area looks like a natural continuation of the card art.

The output is a PDF where:
- **Front pages** have cards arranged in a grid (auto-calculated to fit your output page)
- **Back pages** have the same cards in mirrored order, so they align when you flip the sheet for duplex printing

---

## Setup

Requires **Python 3.11+**.

```bash
git clone <repo>
cd PnP-tool

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Running

```bash
python app.py
```

Then open **http://localhost:8000** in your browser.

---

## Usage

### Step 1 — Upload front card images

Drop or browse for your front card images (PNG or JPG). All cards must be the same size.

The app tries to detect the card size from the image DPI metadata and suggests a preset:

| Preset | Size |
|---|---|
| Poker | 63 × 88 mm |
| Tarot | 70 × 120 mm |
| Mini American | 41 × 63 mm |

If no DPI metadata is found, or the size doesn't match a preset, select the correct size from the dropdown or enter custom dimensions in mm.

You can **drag thumbnails** to reorder cards — the order affects how fronts and backs are paired.

Cards may be uploaded in either landscape or portrait orientation regardless of the card preset — the app normalises orientation automatically.

### Step 2 — Assign backs

Choose one of three modes:

**Same back for all cards**
Upload one back image. All cards share this back.

**Individual back per card**
Upload one back image for each front, in the same order. If you upload fewer backs than fronts, the remaining cards are printed single-sided.

**Default back + overrides**
Upload one default back image. Then use the card list to override specific cards with a different back image. Useful when most cards share a back but a few have unique backs.

### Step 3 — Output settings

| Setting | Default | Description |
|---|---|---|
| Output page | A4 | Page size for the output PDF (A4, Letter, A3) |
| Duplex flip | Horizontal | How you flip the sheet to print the back. **Horizontal** = flip along the left/right edge (most common). **Vertical** = flip along the top/bottom edge. |

**Advanced settings** (expand with the arrow):

| Setting | Default | Description |
|---|---|---|
| Bleed | 3 mm | How much bleed to add on each side of the card |
| Source strip | 1 mm | How wide a strip of the card edge is used as the bleed source |
| Pre-bleed trim | 0 mm | Crops this much from each edge of the card *before* bleed is added. Use this to remove a thin coloured border or outline on the card image so the bleed mirrors the actual card background instead of the border colour. |
| Page margin | 5 mm | Margin around the card grid on each page |
| Image quality | 95 | JPEG quality of embedded card images (1–95). Lower = smaller PDF, higher = sharper. |
| Cut marks — Fronts / Backs | on | Draw cross-shaped cut marks at each card corner on front and/or back pages |
| Mark length | 3 mm | Length of each cut-mark arm |
| Mark thickness | 0.2 mm | Line width of cut marks |

### Process & Download

Click **Process & Download PDF**. The app will:

1. Normalise card orientation (handles landscape or portrait uploads)
2. Resize images to the specified card dimensions (if needed)
3. Trim the outer edge (if pre-bleed trim > 0), then add bleed to every card (front and back)
4. Calculate how many cards fit on each output page, automatically rotating the grid 90° if it fits more cards
5. Arrange cards in a grid on front pages
6. Mirror the arrangement on back pages for duplex alignment
7. Return a PDF download

Use the **New session** button in the header to clear all uploaded images and start fresh.

---

## Printing tips

1. Print the PDF on your printer using **manual duplex** (print odd pages first, flip, print even pages).
2. Choose the **flip direction** that matches how your printer / your manual process flips the paper:
   - Most desktop printers flip along the **long edge** (left/right) → use **Horizontal**
   - Some printers or binding workflows flip along the **short edge** (top/bottom) → use **Vertical**
3. Cut along the card borders. Because of the bleed, small misalignments won't show a white edge.
4. Use a paper cutter or guillotine for straight cuts.

---

## Development

```bash
# Lint
.venv/Scripts/ruff check .

# Format
.venv/Scripts/ruff format .

# Tests
.venv/Scripts/pytest
```

### Project structure

```
PnP-tool/
├── app.py                  # FastAPI routes
├── processing/
│   ├── bleed.py            # Mirror-tile bleed algorithm
│   └── pdf_writer.py       # Grid packing, duplex mirror, PDF assembly
├── static/
│   ├── index.html          # UI
│   ├── style.css
│   └── app.js
├── tests/
│   ├── test_bleed.py
│   ├── test_pdf_writer.py
│   └── test_app.py
└── requirements.txt
```
