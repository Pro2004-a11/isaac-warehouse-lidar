"""Stage 1: flatten an Isaac Sim / Omniverse USD stage, collect its textures
into a self-contained folder, and convert its MDL materials to
UsdPreviewSurface (which is the only surface Blender's USD importer reads).

The source stage is opened read-only: every edit is made on an anonymous
overlay layer, so nothing under the Isaac Sim install is ever written.

Outputs, all under --out:
    <name>.usdc          flattened, self-contained stage
    textures/*.png|jpg   every texture referenced by the converted materials
    materials/*.mdl      the collected MDL modules, for reference
    materials.json       declarative material specs (input to the Blender step)
    report.txt           human-readable summary of what converted and what did not

Usage:
    python usd_flatten_collect.py <source.usd> --out <dir> [--name scene]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade  # noqa: E402

import mdl_parse  # noqa: E402
from material_spec import MaterialSpec, apply_overrides, build_spec  # noqa: E402

log = logging.getLogger("isaac_export")

_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".tga", ".exr", ".hdr", ".tif", ".tiff", ".bmp",
     ".dds", ".ktx"}
)


# --------------------------------------------------------------------------
# gathering
# --------------------------------------------------------------------------
def _shader_values(
    shader: UsdShade.Shader,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, str]]:
    """Split a shader's authored inputs into scalars, resolved textures, and
    the asset paths that failed to resolve (kept keyed by parameter name --
    which slot was bound-but-missing changes how the constants are read)."""
    scalars: dict[str, Any] = {}
    textures: dict[str, Path] = {}
    missing: dict[str, str] = {}

    for inp in shader.GetInputs():
        name = inp.GetBaseName()
        value = inp.Get()
        if inp.GetTypeName() == Sdf.ValueTypeNames.Asset:
            if value is None or not value.path:
                continue
            if value.resolvedPath:
                textures[name] = Path(value.resolvedPath)
            else:
                missing[name] = value.path
            continue
        if value is None:
            continue
        if isinstance(value, (Gf.Vec2f, Gf.Vec3f, Gf.Vec4f, Gf.Vec2d, Gf.Vec3d)):
            scalars[name] = [float(v) for v in value]
        elif isinstance(value, bool):
            scalars[name] = value
        elif isinstance(value, (int, float)):
            scalars[name] = value
    return scalars, textures, missing


def gather_specs(
    stage: Usd.Stage, mdl_modules: dict[str, mdl_parse.MdlModule],
    overrides: dict[str, Any], overrides_dir: Path,
) -> tuple[list[MaterialSpec], list[str]]:
    """Build a MaterialSpec for every Material prim backed by an MDL shader."""
    specs: list[MaterialSpec] = []
    skipped: list[str] = []

    for prim in stage.TraverseAll():
        if not prim.IsA(UsdShade.Material):
            continue
        mat = UsdShade.Material(prim)

        # Already has a universal (UsdPreviewSurface) surface? Leave it alone.
        universal = mat.GetSurfaceOutput()
        if universal and universal.HasConnectedSource():
            skipped.append(str(prim.GetPath()))
            continue

        source = mat.ComputeSurfaceSource("mdl")
        shader = source[0] if source else None
        if not shader:
            skipped.append(str(prim.GetPath()))
            continue

        asset = shader.GetSourceAsset("mdl")
        module_name = shader.GetSourceAssetSubIdentifier("mdl") or ""
        module_file = Path(str(asset.path)).name if asset else ""
        if not module_name and module_file:
            module_name = Path(module_file).stem

        scalars, textures, missing = _shader_values(shader)
        spec = build_spec(
            name=prim.GetName(),
            prim_path=str(prim.GetPath()),
            mdl_module=module_name,
            usd_inputs=scalars,
            usd_texture_paths=textures,
            usd_missing_textures=missing,
            mdl_mod=mdl_modules.get(module_name),
        )
        for param, raw in sorted(missing.items()):
            spec.warnings.append(f"usd texture unresolved: {param} -> {raw}")
        if overrides:
            apply_overrides(spec, overrides, overrides_dir)
        specs.append(spec)

    return specs, skipped


# --------------------------------------------------------------------------
# UsdPreviewSurface authoring
# --------------------------------------------------------------------------
def _tex_node(
    stage: Usd.Stage,
    parent: Sdf.Path,
    slot: str,
    rel_file: str,
    colorspace: str,
    st_source: UsdShade.Output,
    scale: tuple[float, float, float, float] = (1, 1, 1, 1),
    bias: tuple[float, float, float, float] = (0, 0, 0, 0),
) -> UsdShade.Shader:
    tex = UsdShade.Shader.Define(stage, parent.AppendChild(f"tex_{slot}"))
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(rel_file)
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st_source)
    tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
    tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
    tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(
        "sRGB" if colorspace == "sRGB" else "raw"
    )
    if scale != (1, 1, 1, 1):
        tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*scale))
    if bias != (0, 0, 0, 0):
        tex.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*bias))
    for out_name, out_type in (
        ("rgb", Sdf.ValueTypeNames.Float3),
        ("r", Sdf.ValueTypeNames.Float),
        ("g", Sdf.ValueTypeNames.Float),
        ("b", Sdf.ValueTypeNames.Float),
        ("a", Sdf.ValueTypeNames.Float),
    ):
        tex.CreateOutput(out_name, out_type)
    return tex


def author_preview_surface(
    stage: Usd.Stage, spec: MaterialSpec, uv_primvar: str
) -> None:
    """Write a UsdPreviewSurface network onto an `over` of the material prim.

    Texture paths are authored absolute here; `collect_layer_textures` rewrites
    every asset path in the flattened layer to `textures/<name>` afterwards, so
    that pre-existing UsdPreviewSurface networks (which this function skips) are
    localised by the same code path.

    This is the portable baseline: it carries the textures and the sensible
    scalar values, but not the mask-lerps. Blender gets the exact graph from
    materials.json in stage 2.
    """
    mat_path = Sdf.Path(spec.prim_path)
    over = stage.OverridePrim(mat_path)
    mat = UsdShade.Material(over)
    c = spec.constants

    surf = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewSurface"))
    surf.CreateIdAttr("UsdPreviewSurface")
    surf_out = surf.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(surf_out)

    reader = UsdShade.Shader.Define(stage, mat_path.AppendChild("stReader"))
    reader.CreateIdAttr("UsdPrimvarReader_float2")
    reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(uv_primvar)
    st_out = reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    uv_scale = spec.uv.get("scale")
    uv_trans = spec.uv.get("translate")
    uv_rot = spec.uv.get("rotate")
    tile_u = c.get("uv_tile_u")
    tile_v = c.get("uv_tile_v")
    if tile_u is not None or tile_v is not None:
        uv_scale = [float(tile_u or 1.0), float(tile_v or 1.0)]
    if uv_scale or uv_trans or uv_rot:
        xf = UsdShade.Shader.Define(stage, mat_path.AppendChild("stTransform"))
        xf.CreateIdAttr("UsdTransform2d")
        xf.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(st_out)
        if uv_scale:
            xf.CreateInput("scale", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(float(uv_scale[0]), float(uv_scale[1]))
            )
        if uv_trans:
            xf.CreateInput("translation", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(float(uv_trans[0]), float(uv_trans[1]))
            )
        if uv_rot:
            xf.CreateInput("rotation", Sdf.ValueTypeNames.Float).Set(float(uv_rot))
        st_out = xf.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    surf.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(0)

    def rel(slot: str) -> str | None:
        t = spec.textures.get(slot)
        if t is None:
            return None
        return t.abs_path.as_posix()

    # --- base colour --------------------------------------------------------
    tint = c.get("texture_tint", [1.0, 1.0, 1.0])
    add = float(c.get("albedo_add", 0.0) or 0.0)
    bc_file = rel("basecolor")
    if bc_file:
        tex = _tex_node(
            stage, mat_path, "basecolor", bc_file,
            spec.textures["basecolor"].colorspace, st_out,
            scale=(float(tint[0]), float(tint[1]), float(tint[2]), 1.0),
            bias=(add, add, add, 0.0),
        )
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.GetOutput("rgb")
        )
    else:
        bc = c.get("base_color", [0.5, 0.5, 0.5])
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(v) for v in bc])
        )

    # --- roughness / metallic / occlusion (packed ORM, or separate) ---------
    rmin = c.get("roughness_min")
    rmax = c.get("roughness_max")
    rmul = float(c.get("roughness_multiplier", 1.0) or 1.0)
    if rmin is not None and rmax is not None:
        g_scale, g_bias = float(rmax) - float(rmin), float(rmin)
    else:
        g_scale, g_bias = rmul, 0.0

    orm_file = rel("orm")
    if orm_file:
        orm = _tex_node(
            stage, mat_path, "orm", orm_file, "Non-Color", st_out,
            scale=(1.0, g_scale, 1.0, 1.0), bias=(0.0, g_bias, 0.0, 0.0),
        )
        surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(
            orm.GetOutput("g")
        )
        surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).ConnectToSource(
            orm.GetOutput("b")
        )
        surf.CreateInput("occlusion", Sdf.ValueTypeNames.Float).ConnectToSource(
            orm.GetOutput("r")
        )
    else:
        rough_file = rel("roughness")
        if rough_file:
            rt = _tex_node(
                stage, mat_path, "roughness", rough_file, "Non-Color", st_out,
                scale=(g_scale, 1.0, 1.0, 1.0), bias=(g_bias, 0.0, 0.0, 0.0),
            )
            surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(
                rt.GetOutput("r")
            )
        else:
            surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(
                float(c.get("roughness", 0.5))
            )
        metal_file = rel("metallic")
        if metal_file:
            mt = _tex_node(
                stage, mat_path, "metallic", metal_file, "Non-Color", st_out
            )
            surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).ConnectToSource(
                mt.GetOutput("r")
            )
        else:
            surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(
                float(c.get("metallic", 0.0))
            )
        occ_file = rel("occlusion")
        if occ_file:
            ot = _tex_node(
                stage, mat_path, "occlusion", occ_file, "Non-Color", st_out
            )
            surf.CreateInput("occlusion", Sdf.ValueTypeNames.Float).ConnectToSource(
                ot.GetOutput("r")
            )

    # --- normal -------------------------------------------------------------
    n_file = rel("normal")
    if n_file:
        nt = _tex_node(
            stage, mat_path, "normal", n_file, "Non-Color", st_out,
            scale=(2.0, 2.0, 2.0, 1.0), bias=(-1.0, -1.0, -1.0, 0.0),
        )
        surf.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(
            nt.GetOutput("rgb")
        )

    # --- emission -----------------------------------------------------------
    em_color = c.get("emissive_color")
    em_strength = float(c.get("emissive_strength", 0.0) or 0.0)
    em_file = rel("emissive")
    if em_file and em_color:
        s = [float(v) * em_strength for v in em_color]
        et = _tex_node(
            stage, mat_path, "emissive", em_file,
            spec.textures["emissive"].colorspace, st_out,
            scale=(s[0], s[1], s[2], 1.0),
        )
        surf.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            et.GetOutput("rgb")
        )
    elif em_color and em_strength > 0.0:
        surf.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(v) * em_strength for v in em_color])
        )

    # --- opacity ------------------------------------------------------------
    op_file = rel("opacity")
    if op_file:
        ot = _tex_node(stage, mat_path, "opacity", op_file, "Non-Color", st_out)
        surf.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
            ot.GetOutput("r")
        )
        threshold = float(c.get("opacity_threshold", 0.0) or 0.0)
        if threshold > 0.0:
            surf.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(threshold)
    else:
        opacity = float(c.get("opacity", 1.0))
        if opacity < 1.0:
            surf.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)

    ior = c.get("ior")
    if ior is not None:
        surf.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(float(ior))


# --------------------------------------------------------------------------
# texture collection
# --------------------------------------------------------------------------
class _Collector:
    """Assigns each on-disk asset a unique destination under `out_dir`.

    Collisions between identically-named files from different folders get an
    8-char path hash appended, so nothing is silently overwritten. Keys are
    case-folded because Windows paths reach us with inconsistent casing.
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._by_key: dict[str, str] = {}
        self._taken: dict[str, str] = {}
        self.abs_to_rel: dict[Path, str] = {}
        self.copied = 0
        self.failed = 0
        self.unresolved: list[str] = []

    @staticmethod
    def _key(path: Path) -> str:
        return str(path).replace("\\", "/").casefold()

    def assign(self, src: Path, subdir: str) -> str:
        key = self._key(src)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing

        name = src.name
        if name in self._taken and self._taken[name] != key:
            digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
            name = f"{src.stem}_{digest}{src.suffix}"
        self._taken[name] = key

        rel = f"{subdir}/{name}"
        self._by_key[key] = rel
        self.abs_to_rel[src] = rel

        dst = self.out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not (dst.exists() and dst.stat().st_size == src.stat().st_size):
                shutil.copy2(src, dst)
            self.copied += 1
        except OSError:
            log.exception("failed copying %s", src)
            self.failed += 1
        return rel


def _resolve_asset(raw: str, anchors: list[Path]) -> Path | None:
    """Resolve an authored asset path against the layer/source directories."""
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for anchor in anchors:
        probe = (anchor / raw).resolve()
        if probe.is_file():
            return probe
    return None


def collect_layer_textures(
    layer: Sdf.Layer, out_dir: Path, anchors: list[Path]
) -> _Collector:
    """Walk a flattened layer, copy every referenced asset into `out_dir`, and
    rewrite the authored path to a relative one.

    This runs after Flatten() so it catches textures behind material networks
    this tool did not author -- notably the 15 materials that already shipped a
    UsdPreviewSurface, whose relative paths Flatten() turns into absolute paths
    back into the Isaac Sim install.
    """
    collector = _Collector(out_dir)

    def visit(path: Sdf.Path) -> None:
        spec = layer.GetObjectAtPath(path)
        if not isinstance(spec, Sdf.AttributeSpec):
            return
        if spec.typeName != Sdf.ValueTypeNames.Asset:
            return
        value = spec.default
        if value is None or not getattr(value, "path", ""):
            return
        raw = value.path
        suffix = Path(raw).suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            subdir = "textures"
        elif suffix == ".mdl":
            subdir = "materials"
        else:
            return
        resolved = _resolve_asset(raw, anchors)
        if resolved is None:
            # Not on disk anywhere (Nucleus-hosted, never localised). Still
            # rewrite it to the package-relative slot it *would* occupy, so the
            # export stays portable and dropping the file in later just works.
            collector.unresolved.append(raw)
            spec.default = Sdf.AssetPath(f"{subdir}/{Path(raw).name}")
            return
        spec.default = Sdf.AssetPath(collector.assign(resolved, subdir))

    layer.Traverse(Sdf.Path("/"), visit)
    return collector


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def dominant_uv_primvar(stage: Usd.Stage) -> str:
    counts: dict[str, int] = {}
    for prim in stage.TraverseAll():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        for pv in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
            if str(pv.GetTypeName()).startswith(("texCoord2f", "float2")):
                counts[pv.GetPrimvarName()] = counts.get(pv.GetPrimvarName(), 0) + 1
    if not counts:
        return "st"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def load_overrides(path: Path | None) -> tuple[dict[str, Any], Path]:
    if path is None:
        return {}, Path.cwd()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.exception("could not read overrides file %s", path)
        return {}, path.parent
    if not isinstance(data, dict):
        log.error("overrides file must be a JSON object, got %s", type(data).__name__)
        return {}, path.parent
    log.info("loaded %d material override(s) from %s", len(data), path)
    return data, path.parent


def run(src: Path, out_dir: Path, name: str,
        overrides_path: Path | None = None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    overrides, overrides_dir = load_overrides(overrides_path)

    log.info("opening %s (read-only)", src)
    src_stage = Usd.Stage.Open(str(src), load=Usd.Stage.LoadAll)
    if src_stage is None:
        log.error("could not open %s", src)
        return 1

    uv_primvar = dominant_uv_primvar(src_stage)
    log.info("dominant UV primvar: %s", uv_primvar)

    mdl_dir = src.parent / "SubUSDs" / "materials"
    if not mdl_dir.is_dir():
        mdl_dir = src.parent / "materials"
    mdl_modules = mdl_parse.load_mdl_dir(mdl_dir)
    log.info("parsed %d MDL module(s) from %s", len(mdl_modules), mdl_dir)

    specs, skipped = gather_specs(src_stage, mdl_modules, overrides, overrides_dir)
    log.info("built %d material spec(s); %d already had a preview surface",
             len(specs), len(skipped))

    # Edit on an anonymous overlay so the Isaac Sim install is never touched.
    root = Sdf.Layer.CreateAnonymous("isaac_export_overlay.usda")
    root.subLayerPaths.append(Path(src).resolve().as_posix())
    stage = Usd.Stage.Open(root, load=Usd.Stage.LoadAll)
    stage.SetEditTarget(Usd.EditTarget(root))

    for spec in specs:
        try:
            author_preview_surface(stage, spec, uv_primvar)
        except Exception:
            log.exception("failed authoring preview surface for %s", spec.prim_path)
            spec.warnings.append("preview-surface authoring failed")

    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(src_stage))
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(src_stage))
    default_prim = src_stage.GetDefaultPrim()
    if default_prim:
        stage.SetDefaultPrim(stage.GetPrimAtPath(default_prim.GetPath()))

    log.info("flattening ...")
    flat = stage.Flatten()

    anchors = [src.parent, src.parent / "SubUSDs", mdl_dir]
    collector = collect_layer_textures(flat, out_dir, anchors)
    log.info(
        "collected %d asset(s): %d copied, %d failed, %d unresolved",
        len(collector.abs_to_rel), collector.copied, collector.failed,
        len(set(collector.unresolved)),
    )

    usd_out = out_dir / f"{name}.usdc"
    log.info("exporting %s", usd_out)
    flat.Export(str(usd_out))

    rel_for = collector.abs_to_rel
    payload = {
        "source": str(src),
        "stage": usd_out.name,
        "up_axis": str(UsdGeom.GetStageUpAxis(src_stage)),
        "meters_per_unit": UsdGeom.GetStageMetersPerUnit(src_stage),
        "uv_primvar": uv_primvar,
        "skipped_already_preview": skipped,
        "unresolved_assets": sorted(
            {f"{'materials' if a.lower().endswith('.mdl') else 'textures'}"
             f"/{Path(a).name}" for a in collector.unresolved}
        ),
        "materials": [s.to_json(rel_for) for s in specs],
    }
    (out_dir / "materials.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    _write_report(out_dir, specs, skipped, collector)
    log.info("done -> %s", out_dir)
    return 0


def _write_report(
    out_dir: Path, specs: list[MaterialSpec], skipped: list[str],
    collector: _Collector,
) -> None:
    lines: list[str] = []
    by_template: dict[str, int] = {}
    textured = 0
    warned: list[MaterialSpec] = []
    names: dict[str, int] = {}

    for s in specs:
        by_template[s.template] = by_template.get(s.template, 0) + 1
        if s.textures:
            textured += 1
        if s.warnings:
            warned.append(s)
        names[s.name] = names.get(s.name, 0) + 1

    lines.append("ISAAC SIM -> BLENDER EXPORT REPORT")
    lines.append("=" * 60)
    lines.append(f"materials converted     : {len(specs)}")
    lines.append(f"  with >=1 texture      : {textured}")
    lines.append(f"  constants only        : {len(specs) - textured}")
    lines.append(f"left as authored        : {len(skipped)}")
    lines.append(
        f"assets collected        : {collector.copied} "
        f"({collector.failed} copy failures)"
    )
    unresolved = sorted(set(collector.unresolved))
    lines.append(f"assets unresolvable     : {len(unresolved)}")
    lines.append("")
    lines.append("by graph template:")
    for k, v in sorted(by_template.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {v:5d}  {k}")

    dupes = {n: c for n, c in names.items() if c > 1}
    if dupes:
        lines.append("")
        lines.append("DUPLICATE material names (Blender will suffix .001, .002 ...):")
        for n, c in sorted(dupes.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {c:5d}  {n}")

    if unresolved:
        lines.append("")
        lines.append(
            f"ASSETS NOT ON DISK ({len(unresolved)}) -- these live on an Omniverse"
        )
        lines.append(
            "Nucleus server and were never localised by the original Collect:"
        )
        for u in unresolved:
            lines.append(f"  {u}")

    if warned:
        lines.append("")
        lines.append(f"MATERIALS NEEDING ATTENTION ({len(warned)}):")
        for s in warned:
            lines.append(f"  [{s.mdl_module or '?'}] {s.name}")
            for w in s.warnings:
                lines.append(f"      - {w}")

    (out_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default="scene")
    ap.add_argument(
        "--overrides", type=Path, default=None,
        help="JSON patch file supplying textures/constants the source cannot "
             "provide (Nucleus-hosted materials, lost cutout alphas)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    try:
        return run(args.source.resolve(), args.out.resolve(), args.name,
                   args.overrides.resolve() if args.overrides else None)
    except Exception:
        log.exception("export failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
