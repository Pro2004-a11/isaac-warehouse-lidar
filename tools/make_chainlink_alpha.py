"""Synthesise a tileable chain-link alpha map.

The warehouse fence's real alpha lives inside a Nucleus-hosted
`Chain_Link_Fence.mdl` that Isaac Sim never localised, so it cannot be
recovered. This generates a stand-in: a diamond lattice of wire, opaque on the
wire and transparent in the aperture.

    blender --background --factory-startup --python make_chainlink_alpha.py -- \
        --out D:/_EXPORT/isaac_warehouse/custom_textures/chainlink_alpha.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy
import numpy as np


def chainlink_alpha(size: int, wire_px: float, softness: float) -> np.ndarray:
    """Diamond lattice: |u+v| or |u-v| near a multiple of the cell pitch."""
    y, x = np.meshgrid(
        np.arange(size, dtype=np.float32),
        np.arange(size, dtype=np.float32),
        indexing="ij",
    )
    pitch = size / 2.0

    def wire(coord: np.ndarray) -> np.ndarray:
        # distance to the nearest lattice line running along this diagonal
        d = np.abs(((coord % pitch) / pitch) - 0.5) * pitch
        return np.clip((wire_px - d) / max(softness, 1e-3), 0.0, 1.0)

    lattice = np.maximum(wire(x + y), wire(x - y))
    return lattice.astype(np.float32)


def save_png(alpha: np.ndarray, out: Path) -> None:
    size = alpha.shape[0]
    img = bpy.data.images.new(out.stem, width=size, height=size, alpha=True)
    rgba = np.empty((size, size, 4), dtype=np.float32)
    # The mask lives in RGB -- that is the channel the converter wires to
    # Alpha. The file's own alpha stays fully opaque so the PNG previews as a
    # readable black/white mask instead of compositing to a blank white field.
    rgba[..., 0] = alpha
    rgba[..., 1] = alpha
    rgba[..., 2] = alpha
    rgba[..., 3] = 1.0
    img.pixels.foreach_set(rgba.ravel())
    img.file_format = "PNG"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.filepath_raw = str(out)
    img.save()
    coverage = float(alpha.mean())
    print(f"wrote {out}  {size}x{size}  wire coverage {coverage * 100:.1f}%")
    if not 0.05 < coverage < 0.6:
        print("WARNING: coverage looks wrong for a chain-link fence "
              "(expect roughly 10-35% wire)")


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--wire-px", type=float, default=9.0,
                    help="half-thickness of the wire in pixels")
    ap.add_argument("--softness", type=float, default=1.5)
    args = ap.parse_args(argv)

    alpha = chainlink_alpha(args.size, args.wire_px, args.softness)
    save_png(alpha, args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
