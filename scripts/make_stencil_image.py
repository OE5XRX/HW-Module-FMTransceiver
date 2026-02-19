#!/usr/bin/env python3
"""
make_stencil_image.py – Convert a KiBot SVG paste-layer export into a
stencil-like PNG suitable for use as an InvenTree part image.

Works with the pcb_print SVG output (filled shapes, white background).

Colour mapping applied:
  • Background (white/near-white)  → steel grey   (#B8C0CC)
  • F.Paste apertures (any colour) → black        (#000000)  = holes
  • Edge.Cuts outline (yellow)     → gold         (#D4A017)

Usage:
  python3 scripts/make_stencil_image.py <input.svg> <output.png> [--dpi DPI]
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
import numpy as np


# ── colour targets ────────────────────────────────────────────────────────────
STEEL = np.array([184, 192, 204], dtype=np.uint8)   # background (stainless)
BLACK = np.array([  0,   0,   0], dtype=np.uint8)   # paste apertures (holes)
WHITE = np.array([255, 255, 255], dtype=np.uint8)   # silkscreen text/outlines
GOLD  = np.array([212, 160,  23], dtype=np.uint8)   # PCB outline

# KiBot layer colours as defined in production.kibot.yaml
# F.Paste      → #000000  (black)
# F.Silkscreen → #0055AA  (blue)
# Edge.Cuts    → #FFB300  (amber)
_PASTE_COLOR = (  0,   0,   0)
_SILK_COLOR  = (  0,  85, 170)
_EDGE_COLOR  = (255, 179,   0)

# Tolerance for colour matching (per-channel)
_TOL = 40

# A pixel is "background" when all channels are >= this value
WHITE_THRESHOLD = 220


def _svg_to_png(svg_path: Path, png_path: Path, dpi: int) -> None:
    """Rasterise *svg_path* to *png_path* using rsvg-convert or Inkscape."""
    for cmd in [
        ["rsvg-convert", "-d", str(dpi), "-p", str(dpi), "-o", str(png_path), str(svg_path)],
        ["inkscape", "--export-dpi", str(dpi), f"--export-filename={png_path}", str(svg_path)],
    ]:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    sys.exit(f"ERROR: neither rsvg-convert nor inkscape found; cannot convert {svg_path}")


def _composite_on_white(img: Image.Image) -> Image.Image:
    """Flatten an RGBA image onto a white background → RGB."""
    bg = Image.new("RGB", img.size, (255, 255, 255))
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img.convert("RGB"))
    return bg


def _near(arr: np.ndarray, color: tuple, tol: int = _TOL) -> np.ndarray:
    """Return boolean mask where pixels are within *tol* of *color* (RGB)."""
    r, g, b = color
    a = arr
    return (
        (np.abs(a[:, :, 0].astype(int) - r) <= tol) &
        (np.abs(a[:, :, 1].astype(int) - g) <= tol) &
        (np.abs(a[:, :, 2].astype(int) - b) <= tol)
    )


def _recolor(img: Image.Image) -> Image.Image:
    """Apply the stencil colour mapping to a white-background RGB image."""
    arr = np.array(img, dtype=np.uint8)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    # Background: all channels bright
    bg_mask   = (r >= WHITE_THRESHOLD) & (g >= WHITE_THRESHOLD) & (b >= WHITE_THRESHOLD)
    edge_mask = _near(arr, _EDGE_COLOR) & ~bg_mask
    silk_mask = _near(arr, _SILK_COLOR) & ~bg_mask & ~edge_mask
    # Paste: everything else (including pure black from the layer colour)
    paste_mask = ~bg_mask & ~edge_mask & ~silk_mask

    out = np.empty_like(arr)
    out[bg_mask]    = STEEL
    out[edge_mask]  = GOLD
    out[silk_mask]  = WHITE
    out[paste_mask] = BLACK

    # Diagnostics
    total = arr.shape[0] * arr.shape[1]
    print(f"  bg={bg_mask.sum()} ({100*bg_mask.sum()//total}%)  "
          f"edge={edge_mask.sum()} ({100*edge_mask.sum()//total}%)  "
          f"silk={silk_mask.sum()} ({100*silk_mask.sum()//total}%)  "
          f"paste={paste_mask.sum()} ({100*paste_mask.sum()//total}%)")

    return Image.fromarray(out, "RGB")


def _autocrop(img: Image.Image, padding: int = 30) -> Image.Image:
    """Crop to the bounding box of non-STEEL pixels + *padding*."""
    arr = np.array(img)
    steel = STEEL.tolist()
    non_bg = ~(
        (arr[:, :, 0] == steel[0]) &
        (arr[:, :, 1] == steel[1]) &
        (arr[:, :, 2] == steel[2])
    )
    rows = np.any(non_bg, axis=1)
    cols = np.any(non_bg, axis=0)
    if not rows.any():
        return img
    rmin = int(np.argmax(rows))
    rmax = int(len(rows) - 1 - np.argmax(rows[::-1]))
    cmin = int(np.argmax(cols))
    cmax = int(len(cols) - 1 - np.argmax(cols[::-1]))
    h, w = arr.shape[:2]
    return img.crop((
        max(0, cmin - padding),
        max(0, rmin - padding),
        min(w, cmax + padding),
        min(h, rmax + padding),
    ))


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert KiBot SVG to stencil PNG")
    parser.add_argument("input",  help="Input SVG file (from KiBot pcb_print output)")
    parser.add_argument("output", help="Output PNG file")
    parser.add_argument("--dpi",  type=int, default=300, help="Rasterisation DPI (default: 300)")
    args = parser.parse_args()

    svg_path = Path(args.input)
    out_path = Path(args.output)

    if not svg_path.exists():
        sys.exit(f"ERROR: input file not found: {svg_path}")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_png = Path(tmp.name)

    try:
        print(f"Rasterising {svg_path} at {args.dpi} DPI …")
        _svg_to_png(svg_path, tmp_png, args.dpi)

        print("Applying stencil colour mapping …")
        img = _composite_on_white(Image.open(tmp_png))
        img = _recolor(img)
        img = _autocrop(img)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "PNG", optimize=True)
        print(f"Saved stencil image → {out_path}  ({img.size[0]}×{img.size[1]} px)")
    finally:
        tmp_png.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
