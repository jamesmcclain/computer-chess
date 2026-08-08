#!/usr/bin/env python3
"""Chroma-key near-green backgrounds to alpha for chess piece sprites.

Diffusion greens are not pure #00FF00. We key by HSV hue band + green
dominance, feather the alpha at edges, and despill residual green fringing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def rgb_to_hsv_vectorized(rgb: np.ndarray) -> np.ndarray:
    """rgb float32 [0,1] -> hsv float32, H in [0,1), S,V in [0,1]."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    v = maxc
    deltac = maxc - minc
    s = np.where(maxc > 0, deltac / np.maximum(maxc, 1e-8), 0.0)

    h = np.zeros_like(maxc)
    # avoid div by zero
    d = np.maximum(deltac, 1e-8)
    rc = (maxc - r) / d
    gc = (maxc - g) / d
    bc = (maxc - b) / d

    mask_r = (maxc == r) & (deltac > 0)
    mask_g = (maxc == g) & (deltac > 0)
    mask_b = (maxc == b) & (deltac > 0)
    h[mask_r] = (bc - gc)[mask_r]
    h[mask_g] = (2.0 + rc - bc)[mask_g]
    h[mask_b] = (4.0 + gc - rc)[mask_b]
    h = (h / 6.0) % 1.0
    h[deltac == 0] = 0.0
    return np.stack([h, s, v], axis=-1)


def green_key_alpha(
    rgb_u8: np.ndarray,
    *,
    hue_center: float = 0.333,  # pure green in [0,1) hue
    hue_width: float = 0.12,
    min_sat: float = 0.25,
    min_val: float = 0.15,
    green_dom: float = 1.15,
    min_g: float = 40.0,
    soft: float = 0.08,
) -> np.ndarray:
    """Return alpha float32 in [0,1], 0 = fully keyed (transparent)."""
    rgb = rgb_u8.astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hsv = rgb_to_hsv_vectorized(rgb / 255.0)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    # Circular hue distance to green
    dh = np.abs(h - hue_center)
    dh = np.minimum(dh, 1.0 - dh)

    # Hard-ish membership scores in [0,1], 1 = more green/background
    hue_score = np.clip(1.0 - dh / hue_width, 0.0, 1.0)
    sat_score = np.clip((s - min_sat) / max(soft, 1e-6), 0.0, 1.0)
    val_score = np.clip((v - min_val) / max(soft, 1e-6), 0.0, 1.0)

    # Green channel dominates red and blue (catches desaturated screen greens)
    # Use max(r,b) so cream/white (r~g~b) stays solid.
    rb = np.maximum(r, b)
    dom_ratio = g / np.maximum(rb, 1.0)
    dom_score = np.clip((dom_ratio - 1.0) / max(green_dom - 1.0, 1e-6), 0.0, 1.0)
    g_score = np.clip((g - min_g) / 40.0, 0.0, 1.0)

    # Combine: need green-ish hue OR strong green dominance, plus some sat/val/g
    chroma_score = np.maximum(hue_score * sat_score, dom_score * 0.85)
    bg_score = chroma_score * np.maximum(sat_score, dom_score) * val_score * g_score

    # Also catch near-pure screen greens with low R/B even if sat math is odd
    pure_green = (g > r + 25) & (g > b + 25) & (g > min_g)
    bg_score = np.where(pure_green, np.maximum(bg_score, dom_score * g_score), bg_score)
    bg_score = np.clip(bg_score, 0.0, 1.0)

    alpha = 1.0 - bg_score
    return alpha.astype(np.float32)


def despill(rgb_u8: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Pull green toward max(r,b) on semi-transparent / fringing pixels."""
    out = rgb_u8.astype(np.float32).copy()
    r, g, b = out[..., 0], out[..., 1], out[..., 2]
    max_rb = np.maximum(r, b)
    # Only despill where green exceeds max(r,b) and pixel is not fully opaque bg-free
    excess = g - max_rb
    fringe = (excess > 0) & (alpha < 0.98)
    # Strength grows as alpha drops (closer to background)
    strength = fringe.astype(np.float32) * (1.0 - alpha) ** 0.5
    g2 = g - excess * np.clip(strength * 1.5, 0.0, 1.0)
    # Where almost transparent, also nudge r/b toward neutral so green halo dies
    almost_bg = alpha < 0.35
    # keep
    out[..., 1] = np.where(fringe, g2, g)
    # For near-bg pixels still partially visible, mute green cast
    for c in (0, 1, 2):
        out[..., c] = np.where(
            almost_bg & (alpha > 0.01),
            out[..., c] * alpha[..., None].squeeze() * 0 + out[..., c],  # no-op placeholder
            out[..., c],
        )
    # Simpler second pass: clamp g to max_rb on fringe
    r, g, b = out[..., 0], out[..., 1], out[..., 2]
    max_rb = np.maximum(r, b)
    g = np.where(fringe, np.minimum(g, max_rb + 2.0), g)
    out[..., 1] = g
    return np.clip(out, 0, 255).astype(np.uint8)


def process_image(
    src: Path,
    dst: Path,
    **key_kwargs,
) -> dict:
    im = Image.open(src).convert("RGB")
    rgb = np.array(im)
    alpha = green_key_alpha(rgb, **key_kwargs)

    # Mild 3x3 box blur on alpha for softer edges (no scipy)
    k = np.array([1, 2, 1], dtype=np.float32)
    k = k / k.sum()
    # separable blur
    pad = np.pad(alpha, ((1, 1), (1, 1)), mode="edge")
    tmp = k[0] * pad[0:-2, 1:-1] + k[1] * pad[1:-1, 1:-1] + k[2] * pad[2:, 1:-1]
    pad2 = np.pad(tmp, ((0, 0), (1, 1)), mode="edge")
    alpha_s = k[0] * pad2[:, 0:-2] + k[1] * pad2[:, 1:-1] + k[2] * pad2[:, 2:]
    # Only soften near edges; keep hard interior
    edge = (alpha > 0.05) & (alpha < 0.95)
    alpha = np.where(edge, alpha_s, alpha)
    alpha = np.clip(alpha, 0.0, 1.0)

    rgb_d = despill(rgb, alpha)
    a_u8 = (alpha * 255.0 + 0.5).astype(np.uint8)

    # Fully transparent where alpha very low — zero RGB to avoid dirty halos
    kill = a_u8 < 8
    rgb_d = rgb_d.copy()
    rgb_d[kill] = 0
    a_u8[kill] = 0

    rgba = np.dstack([rgb_d, a_u8])
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(dst)

    return {
        "src": str(src),
        "dst": str(dst),
        "transparent_frac": float((a_u8 == 0).mean()),
        "opaque_frac": float((a_u8 > 250).mean()),
        "mean_alpha": float(a_u8.mean() / 255.0),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Input PNG files (default: ~/chess/black/*.png and ~/chess/white/*.png)",
    )
    p.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=None,
        help="If set, write all outputs here (flat). Default: <parent>_rgba/ or sibling * _rgba",
    )
    p.add_argument(
        "--in-place-suffix",
        default="_rgba",
        help="Suffix before .png when out-dir not set (default: _rgba)",
    )
    p.add_argument("--hue-width", type=float, default=0.12)
    p.add_argument("--min-sat", type=float, default=0.22)
    p.add_argument("--green-dom", type=float, default=1.12)
    args = p.parse_args(argv)

    if args.inputs:
        inputs = args.inputs
    else:
        root = Path.home() / "chess"
        inputs = sorted((root / "black").glob("*.png")) + sorted(
            (root / "white").glob("*.png")
        )

    if not inputs:
        print("No input PNGs found.", file=sys.stderr)
        return 1

    key_kwargs = dict(
        hue_width=args.hue_width,
        min_sat=args.min_sat,
        green_dom=args.green_dom,
    )

    for src in inputs:
        src = src.expanduser().resolve()
        if args.out_dir:
            dst = args.out_dir.expanduser().resolve() / (src.stem + ".png")
        else:
            # write next to source: foo.png -> foo_rgba.png
            dst = src.with_name(src.stem + args.in_place_suffix + src.suffix)
        stats = process_image(src, dst, **key_kwargs)
        print(
            f"{src.name} -> {dst.name}  "
            f"transparent={stats['transparent_frac']:.1%}  "
            f"opaque={stats['opaque_frac']:.1%}  "
            f"mean_a={stats['mean_alpha']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
