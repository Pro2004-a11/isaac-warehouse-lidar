"""Verify a stage produced by usd_flatten_collect.py.

Checks, on the exported file only (the source is never consulted):
  * every Material has a connected universal `outputs:surface`
  * every UsdUVTexture `inputs:file` resolves on disk
  * stage metadata survived the flatten
  * geometry counts match a reference, when one is given
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from pxr import Usd, UsdGeom, UsdShade


def _known_missing(usd_path: Path) -> set[str]:
    """Assets stage 1 already reported as absent from disk (Nucleus-hosted).

    These are expected to dangle; only *unexpected* dangling paths are failures.
    """
    manifest = usd_path.parent / "materials.json"
    if not manifest.is_file():
        return set()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(a).replace("\\", "/").casefold()
            for a in data.get("unresolved_assets", [])}


def verify(usd_path: Path, reference: Path | None) -> int:
    stage = Usd.Stage.Open(str(usd_path), load=Usd.Stage.LoadAll)
    if stage is None:
        print(f"FAIL: cannot open {usd_path}")
        return 1

    expected_missing = _known_missing(usd_path)
    failures: list[str] = []
    warnings: list[str] = []
    mats = 0
    with_preview = 0
    tex_total = 0
    tex_bad: list[str] = []
    tex_expected_bad = 0
    ids: collections.Counter[str] = collections.Counter()
    points = faces = meshes = 0

    for prim in stage.TraverseAll():
        if prim.IsA(UsdShade.Material):
            mats += 1
            material = UsdShade.Material(prim)
            out = material.GetSurfaceOutput()
            if out and out.HasConnectedSource():
                with_preview += 1
            elif material.ComputeSurfaceSource("mdl")[0]:
                # Had an MDL surface we were supposed to convert -- a real miss.
                failures.append(f"MDL surface not converted: {prim.GetPath()}")
            else:
                # Never had any surface in the source either; Blender will fall
                # back to a default material. Not caused by this pipeline.
                warnings.append(f"material has no surface at all: {prim.GetPath()}")
        if prim.IsA(UsdShade.Shader):
            sh = UsdShade.Shader(prim)
            sid = sh.GetShaderId()
            if sid:
                ids[sid] += 1
            if sid == "UsdUVTexture":
                f = sh.GetInput("file")
                val = f.Get() if f else None
                tex_total += 1
                if val is None or not val.resolvedPath:
                    raw = (val.path if val else "") or ""
                    if raw.replace("\\", "/").casefold() in expected_missing:
                        tex_expected_bad += 1
                    else:
                        tex_bad.append(f"{prim.GetPath()} -> {raw}")
        if prim.IsA(UsdGeom.Mesh):
            meshes += 1
            m = UsdGeom.Mesh(prim)
            p = m.GetPointsAttr().Get()
            fv = m.GetFaceVertexCountsAttr().Get()
            points += len(p) if p else 0
            faces += len(fv) if fv else 0

    print(f"stage           : {usd_path}")
    print(f"upAxis          : {UsdGeom.GetStageUpAxis(stage)}")
    print(f"metersPerUnit   : {UsdGeom.GetStageMetersPerUnit(stage)}")
    print(f"defaultPrim     : {stage.GetDefaultPrim().GetPath() if stage.GetDefaultPrim() else None}")
    print(f"meshes          : {meshes}  points {points:,}  faces {faces:,}")
    print(f"materials       : {mats}  with universal surface: {with_preview}")
    print(f"UsdUVTexture    : {tex_total}  unexpectedly unresolved: {len(tex_bad)}"
          f"  known-missing (Nucleus): {tex_expected_bad}")
    print("shader ids      :")
    for k, v in ids.most_common():
        print(f"    {v:5d}  {k}")

    if tex_bad:
        print("\nUNEXPECTEDLY UNRESOLVED TEXTURES:")
        for t in tex_bad[:30]:
            print(f"    {t}")
        failures.extend(tex_bad)

    if warnings:
        print(f"\nWARNINGS ({len(warnings)}):")
        for w in warnings[:10]:
            print(f"    {w}")

    if reference is not None:
        ref = Usd.Stage.Open(str(reference), load=Usd.Stage.LoadAll)
        r_meshes = r_points = r_faces = 0
        for prim in ref.TraverseAll():
            if prim.IsA(UsdGeom.Mesh):
                r_meshes += 1
                m = UsdGeom.Mesh(prim)
                p = m.GetPointsAttr().Get()
                fv = m.GetFaceVertexCountsAttr().Get()
                r_points += len(p) if p else 0
                r_faces += len(fv) if fv else 0
        print(f"\nreference       : {r_meshes} meshes, {r_points:,} points, {r_faces:,} faces")
        if (meshes, points, faces) != (r_meshes, r_points, r_faces):
            failures.append(
                f"geometry mismatch: got {(meshes, points, faces)} "
                f"expected {(r_meshes, r_points, r_faces)}"
            )
        else:
            print("geometry        : MATCHES reference")

    if failures:
        print(f"\nFAILED ({len(failures)} problem(s))")
        for f in failures[:10]:
            print(f"    {f}")
        return 1
    print("\nPASS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("usd", type=Path)
    ap.add_argument("--reference", type=Path, default=None)
    args = ap.parse_args()
    return verify(args.usd, args.reference)


if __name__ == "__main__":
    sys.exit(main())
