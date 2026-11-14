"""USD-side conversion, run in-process against Blender's bundled OpenUSD.

Opens an Isaac Sim / Omniverse stage read-only, converts its MDL materials to
UsdPreviewSurface on an anonymous overlay layer, gives every material a unique
prim name, optionally localises the textures, and writes a temporary flattened
stage for Blender's USD importer to consume.

Nothing under the source tree is ever written.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from . import mdl_parse
from .material_spec import MaterialSpec, apply_overrides, build_spec

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".tga", ".exr", ".hdr", ".tif", ".tiff", ".bmp",
     ".dds", ".ktx"}
)

Progress = Callable[[str], None]


# --------------------------------------------------------------------------
# gathering
# --------------------------------------------------------------------------
def _shader_values(
    shader: UsdShade.Shader,
) -> tuple[dict[str, Any], dict[str, Path], dict[str, str]]:
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


def find_mdl_dir(src: Path) -> Path:
    """Locate the collected .mdl modules that sit beside a Collect-ed stage."""
    for candidate in (src.parent / "SubUSDs" / "materials",
                      src.parent / "materials",
                      src.parent.parent / "SubUSDs" / "materials"):
        if candidate.is_dir():
            return candidate
    return src.parent / "materials"


def dominant_uv_primvar(stage: Usd.Stage) -> str:
    counts: dict[str, int] = {}
    for prim in stage.TraverseAll():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        for pv in UsdGeom.PrimvarsAPI(prim).GetPrimvars():
            if str(pv.GetTypeName()).startswith(("texCoord2f", "float2")):
                name = pv.GetPrimvarName()
                counts[name] = counts.get(name, 0) + 1
    return max(counts.items(), key=lambda kv: kv[1])[0] if counts else "st"


def gather_specs(
    stage: Usd.Stage, mdl_modules: dict[str, mdl_parse.MdlModule],
    overrides: dict[str, Any], overrides_dir: Path,
) -> tuple[list[MaterialSpec], list[str]]:
    specs: list[MaterialSpec] = []
    skipped: list[str] = []

    for prim in stage.TraverseAll():
        if not prim.IsA(UsdShade.Material):
            continue
        mat = UsdShade.Material(prim)

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
        if not module_name and asset:
            module_name = Path(str(asset.path)).stem

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
def _tex_node(stage, parent, slot, file_path, colorspace, st_source,
              scale=(1, 1, 1, 1), bias=(0, 0, 0, 0)):
    tex = UsdShade.Shader.Define(stage, parent.AppendChild(f"tex_{slot}"))
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(file_path)
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st_source)
    tex.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("repeat")
    tex.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("repeat")
    tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set(
        "sRGB" if colorspace == "sRGB" else "raw")
    if scale != (1, 1, 1, 1):
        tex.CreateInput("scale", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*scale))
    if bias != (0, 0, 0, 0):
        tex.CreateInput("bias", Sdf.ValueTypeNames.Float4).Set(Gf.Vec4f(*bias))
    for out_name, out_type in (("rgb", Sdf.ValueTypeNames.Float3),
                               ("r", Sdf.ValueTypeNames.Float),
                               ("g", Sdf.ValueTypeNames.Float),
                               ("b", Sdf.ValueTypeNames.Float),
                               ("a", Sdf.ValueTypeNames.Float)):
        tex.CreateOutput(out_name, out_type)
    return tex


def author_preview_surface(stage: Usd.Stage, spec: MaterialSpec,
                           uv_primvar: str) -> None:
    """Baseline UsdPreviewSurface so Blender's importer has something to read.

    Texture paths are authored absolute; `collect_layer_assets` localises them
    afterwards if the user asked for a portable package.
    """
    mat_path = Sdf.Path(spec.prim_path)
    over = stage.OverridePrim(mat_path)
    mat = UsdShade.Material(over)
    c = spec.constants

    surf = UsdShade.Shader.Define(stage, mat_path.AppendChild("PreviewSurface"))
    surf.CreateIdAttr("UsdPreviewSurface")
    mat.CreateSurfaceOutput().ConnectToSource(
        surf.CreateOutput("surface", Sdf.ValueTypeNames.Token))

    reader = UsdShade.Shader.Define(stage, mat_path.AppendChild("stReader"))
    reader.CreateIdAttr("UsdPrimvarReader_float2")
    reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set(uv_primvar)
    st_out = reader.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    uv_scale = spec.uv.get("scale")
    tile_u, tile_v = c.get("uv_tile_u"), c.get("uv_tile_v")
    if tile_u is not None or tile_v is not None:
        uv_scale = [float(tile_u or 1.0), float(tile_v or 1.0)]
    uv_trans = spec.uv.get("translate")
    uv_rot = spec.uv.get("rotate")
    if uv_scale or uv_trans or uv_rot:
        xf = UsdShade.Shader.Define(stage, mat_path.AppendChild("stTransform"))
        xf.CreateIdAttr("UsdTransform2d")
        xf.CreateInput("in", Sdf.ValueTypeNames.Float2).ConnectToSource(st_out)
        if uv_scale:
            xf.CreateInput("scale", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(float(uv_scale[0]), float(uv_scale[1])))
        if uv_trans:
            xf.CreateInput("translation", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(float(uv_trans[0]), float(uv_trans[1])))
        if uv_rot:
            xf.CreateInput("rotation", Sdf.ValueTypeNames.Float).Set(float(uv_rot))
        st_out = xf.CreateOutput("result", Sdf.ValueTypeNames.Float2)

    surf.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(0)

    def rel(slot: str) -> str | None:
        t = spec.textures.get(slot)
        return t.abs_path.as_posix() if t else None

    tint = c.get("texture_tint", [1.0, 1.0, 1.0])
    add = float(c.get("albedo_add", 0.0) or 0.0)
    bc_file = rel("basecolor")
    if bc_file:
        tex = _tex_node(stage, mat_path, "basecolor", bc_file,
                        spec.textures["basecolor"].colorspace, st_out,
                        scale=(float(tint[0]), float(tint[1]), float(tint[2]), 1.0),
                        bias=(add, add, add, 0.0))
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex.GetOutput("rgb"))
    else:
        bc = c.get("base_color", [0.5, 0.5, 0.5])
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(v) for v in bc]))

    rmin, rmax = c.get("roughness_min"), c.get("roughness_max")
    rmul = float(c.get("roughness_multiplier", 1.0) or 1.0)
    if rmin is not None and rmax is not None:
        g_scale, g_bias = float(rmax) - float(rmin), float(rmin)
    else:
        g_scale, g_bias = rmul, 0.0

    orm_file = rel("orm")
    if orm_file:
        orm = _tex_node(stage, mat_path, "orm", orm_file, "Non-Color", st_out,
                        scale=(1.0, g_scale, 1.0, 1.0),
                        bias=(0.0, g_bias, 0.0, 0.0))
        surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(
            orm.GetOutput("g"))
        surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).ConnectToSource(
            orm.GetOutput("b"))
        surf.CreateInput("occlusion", Sdf.ValueTypeNames.Float).ConnectToSource(
            orm.GetOutput("r"))
    else:
        rough_file = rel("roughness")
        if rough_file:
            rt = _tex_node(stage, mat_path, "roughness", rough_file, "Non-Color",
                           st_out, scale=(g_scale, 1.0, 1.0, 1.0),
                           bias=(g_bias, 0.0, 0.0, 0.0))
            surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(
                rt.GetOutput("r"))
        else:
            surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(
                float(c.get("roughness", 0.5)))
        metal_file = rel("metallic")
        if metal_file:
            mt = _tex_node(stage, mat_path, "metallic", metal_file, "Non-Color",
                           st_out)
            surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).ConnectToSource(
                mt.GetOutput("r"))
        else:
            surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(
                float(c.get("metallic", 0.0)))

    n_file = rel("normal")
    if n_file:
        nt = _tex_node(stage, mat_path, "normal", n_file, "Non-Color", st_out,
                       scale=(2.0, 2.0, 2.0, 1.0), bias=(-1.0, -1.0, -1.0, 0.0))
        surf.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(
            nt.GetOutput("rgb"))

    em_color = c.get("emissive_color")
    em_strength = float(c.get("emissive_strength", 0.0) or 0.0)
    em_file = rel("emissive")
    if em_file and em_color:
        s = [float(v) * em_strength for v in em_color]
        et = _tex_node(stage, mat_path, "emissive", em_file,
                       spec.textures["emissive"].colorspace, st_out,
                       scale=(s[0], s[1], s[2], 1.0))
        surf.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            et.GetOutput("rgb"))
    elif em_color and em_strength > 0.0:
        surf.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*[float(v) * em_strength for v in em_color]))

    op_file = rel("opacity")
    if op_file:
        ot = _tex_node(stage, mat_path, "opacity", op_file, "Non-Color", st_out)
        surf.CreateInput("opacity", Sdf.ValueTypeNames.Float).ConnectToSource(
            ot.GetOutput("r"))
        threshold = float(c.get("opacity_threshold", 0.0) or 0.0)
        if threshold > 0.0:
            surf.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(
                threshold)
    else:
        opacity = float(c.get("opacity", 1.0))
        if opacity < 1.0:
            surf.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)

    ior = c.get("ior")
    if ior is not None:
        surf.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(float(ior))


# --------------------------------------------------------------------------
# unique material names
# --------------------------------------------------------------------------
def uniquify_material_names(
    layer: Sdf.Layer, specs: list[MaterialSpec],
    skipped_paths: list[str] | None = None,
) -> tuple[int, list[str], list[str]]:
    """Rename Material prims so no two share a leaf name.

    Blender names materials after the USD prim, so 12 prims called `decal`
    become `decal`, `decal.001`, ... and there is no reliable way to map them
    back to the material they came from. Making the names unique up front
    removes the ambiguity instead of guessing at it afterwards.

    Materials left as authored (they already had a UsdPreviewSurface) take part
    too: if one of them shares a name with a converted material, Blender could
    hand the unsuffixed name to the wrong one and the rebuild would land on the
    wrong material.

    Relationship targets (`material:binding`) are repointed explicitly rather
    than trusting Apply() to fix them up.
    """
    skipped_paths = list(skipped_paths or [])
    # (path, name, spec_or_None) in a stable order; the first of each name keeps it.
    entries: list[tuple[str, str, MaterialSpec | None]] = [
        (s.prim_path, s.name, s) for s in specs
    ]
    entries += [(p, Sdf.Path(p).name, None) for p in skipped_paths]

    by_name: dict[str, list[tuple[str, str, MaterialSpec | None]]] = {}
    for entry in entries:
        by_name.setdefault(entry[1], []).append(entry)

    renames: dict[str, str] = {}          # old prim path -> new leaf name
    taken = {name for _, name, _ in entries}
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        for index, (path, _, _) in enumerate(group[1:], start=1):
            candidate = f"{name}_{index:03d}"
            while candidate in taken:
                index += 1
                candidate = f"{name}_{index:03d}"
            taken.add(candidate)
            renames[path] = candidate

    if not renames:
        return 0, [], list(skipped_paths)

    edit = Sdf.BatchNamespaceEdit()
    for old_path, new_name in renames.items():
        edit.Add(Sdf.NamespaceEdit.Rename(Sdf.Path(old_path), new_name))
    if not layer.Apply(edit):
        return 0, ["material rename failed; names left as authored"], list(skipped_paths)

    path_map = {
        old: Sdf.Path(old).GetParentPath().AppendChild(new).pathString
        for old, new in renames.items()
    }

    # Repoint every relationship that targeted a renamed material.
    def visit(path: Sdf.Path) -> None:
        spec_obj = layer.GetObjectAtPath(path)
        if not isinstance(spec_obj, Sdf.RelationshipSpec):
            return
        targets = list(spec_obj.targetPathList.explicitItems)
        if not targets:
            return
        changed = False
        updated = []
        for target in targets:
            new = path_map.get(target.pathString)
            if new:
                updated.append(Sdf.Path(new))
                changed = True
            else:
                updated.append(target)
        if changed:
            spec_obj.targetPathList.explicitItems = updated

    layer.Traverse(Sdf.Path("/"), visit)

    for spec in specs:
        new_name = renames.get(spec.prim_path)
        if new_name:
            spec.prim_path = path_map[spec.prim_path]
            spec.name = new_name

    updated_skipped = [path_map.get(p, p) for p in skipped_paths]
    return len(renames), [], updated_skipped


# --------------------------------------------------------------------------
# texture collection
# --------------------------------------------------------------------------
class AssetCollector:
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
        if key in self._by_key:
            return self._by_key[key]

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


def collect_layer_assets(layer: Sdf.Layer, out_dir: Path,
                         anchors: list[Path]) -> AssetCollector:
    """Copy every referenced asset into `out_dir` and rewrite paths relative."""
    collector = AssetCollector(out_dir)

    def visit(path: Sdf.Path) -> None:
        spec_obj = layer.GetObjectAtPath(path)
        if not isinstance(spec_obj, Sdf.AttributeSpec):
            return
        if spec_obj.typeName != Sdf.ValueTypeNames.Asset:
            return
        value = spec_obj.default
        if value is None or not getattr(value, "path", ""):
            return
        raw = value.path
        suffix = Path(raw).suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            subdir = "textures"
        elif suffix == ".mdl":
            subdir = "materials"
        else:
            return
        resolved = _resolve_asset(raw, anchors)
        if resolved is None:
            collector.unresolved.append(raw)
            spec_obj.default = Sdf.AssetPath(f"{subdir}/{Path(raw).name}")
            return
        spec_obj.default = Sdf.AssetPath(collector.assign(resolved, subdir))

    layer.Traverse(Sdf.Path("/"), visit)
    return collector


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def convert(
    src: Path,
    work_dir: Path,
    *,
    overrides: dict[str, Any] | None = None,
    overrides_dir: Path | None = None,
    collect_textures: bool = False,
    unique_names: bool = True,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Convert a stage and write a flattened .usdc into `work_dir`.

    Returns a result dict with the written stage path, the specs, and counters
    for the report.
    """
    def step(msg: str) -> None:
        log.info(msg)
        if progress:
            progress(msg)

    work_dir.mkdir(parents=True, exist_ok=True)

    step(f"opening {src.name}")
    src_stage = Usd.Stage.Open(str(src), load=Usd.Stage.LoadAll)
    if src_stage is None:
        raise RuntimeError(f"could not open {src}")

    uv_primvar = dominant_uv_primvar(src_stage)
    mdl_dir = find_mdl_dir(src)
    mdl_modules = mdl_parse.load_mdl_dir(mdl_dir)
    step(f"parsed {len(mdl_modules)} MDL module(s); UV primvar '{uv_primvar}'")

    specs, skipped = gather_specs(
        src_stage, mdl_modules, overrides or {},
        overrides_dir or src.parent,
    )
    step(f"converted {len(specs)} MDL material(s), {len(skipped)} left as authored")

    root = Sdf.Layer.CreateAnonymous("isaac_import_overlay.usda")
    root.subLayerPaths.append(src.resolve().as_posix())
    stage = Usd.Stage.Open(root, load=Usd.Stage.LoadAll)
    stage.SetEditTarget(Usd.EditTarget(root))

    failures = 0
    for spec in specs:
        try:
            author_preview_surface(stage, spec, uv_primvar)
        except Exception:
            log.exception("preview surface failed for %s", spec.prim_path)
            spec.warnings.append("preview-surface authoring failed")
            failures += 1

    UsdGeom.SetStageUpAxis(stage, UsdGeom.GetStageUpAxis(src_stage))
    UsdGeom.SetStageMetersPerUnit(stage, UsdGeom.GetStageMetersPerUnit(src_stage))
    default_prim = src_stage.GetDefaultPrim()
    if default_prim:
        stage.SetDefaultPrim(stage.GetPrimAtPath(default_prim.GetPath()))

    step("flattening stage")
    flat = stage.Flatten()

    renamed = 0
    rename_warnings: list[str] = []
    if unique_names:
        renamed, rename_warnings, skipped = uniquify_material_names(
            flat, specs, skipped)
        if renamed:
            step(f"made {renamed} duplicate material name(s) unique")

    collector = None
    if collect_textures:
        step("collecting textures")
        anchors = [src.parent, src.parent / "SubUSDs", mdl_dir]
        collector = collect_layer_assets(flat, work_dir, anchors)
        step(f"collected {collector.copied} asset(s)")

    out_usd = work_dir / f"{src.stem}_converted.usdc"
    step(f"writing {out_usd.name}")
    flat.Export(str(out_usd))

    return {
        "stage_path": out_usd,
        "specs": specs,
        "skipped": skipped,
        "uv_primvar": uv_primvar,
        "renamed": renamed,
        "authoring_failures": failures,
        "collector": collector,
        "rename_warnings": rename_warnings,
        "up_axis": str(UsdGeom.GetStageUpAxis(src_stage)),
        "meters_per_unit": UsdGeom.GetStageMetersPerUnit(src_stage),
    }
