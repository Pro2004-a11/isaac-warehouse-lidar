"""Turn an Omniverse/Isaac MDL material into a backend-neutral declarative spec.

Three sources are merged, weakest first:

  1. `base_materials.BASE_MATERIAL_LUT` -- for NVIDIA base-library modules that
     are not on disk (Nucleus-hosted); constants only.
  2. Defaults parsed out of the *collected* .mdl modules that DO sit next to the
     stage -- this is where UE4-converted assets keep their texture bindings.
  3. Inputs actually authored on the USD Shader prim -- these always win.

The spec records a `template` naming the MDL graph topology, because
UsdPreviewSurface cannot express the mask-lerps and roughness remaps these
materials use. The USD writer emits a faithful-as-possible PreviewSurface; the
Blender writer consumes the same spec and rebuilds the graph exactly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import base_materials
from .mdl_parse import MdlModule

log = logging.getLogger(__name__)

# --- slot vocabulary -------------------------------------------------------
# Every texture ends up in one of these slots regardless of which MDL family
# named it. `orm` is the packed AO(R)/Roughness(G)/Metallic(B) map.
SLOTS = (
    "basecolor", "normal", "orm", "roughness", "metallic", "occlusion",
    "emissive", "opacity", "mask", "detail_normal",
)

_LINEAR_SLOTS = frozenset(
    {"normal", "orm", "roughness", "metallic", "occlusion", "opacity", "mask",
     "detail_normal"}
)

# MDL parameter name -> slot. Covers both the OmniPBR family and the
# OmniUe4Base family used by the UE4-converted warehouse assets.
PARAM_TO_SLOT: dict[str, str] = {
    # OmniPBR / vMaterials
    "diffuse_texture": "basecolor",
    "normalmap_texture": "normal",
    "normal_texture": "normal",
    "detail_normalmap_texture": "detail_normal",
    "ORM_texture": "orm",
    "reflectionroughness_texture": "roughness",
    "metallic_texture": "metallic",
    "ao_texture": "occlusion",
    "opacity_texture": "opacity",
    "emissive_color_texture": "emissive",
    "emissive_mask_texture": "emissive",
    # OmniUe4Base (UE4 import)
    "BaseColor": "basecolor",
    "AlbedoTexture": "basecolor",
    "Normal": "normal",
    "MainNormalInput": "normal",
    "AO_R_Rough_G_Metallic_B_": "orm",
    "MergeMapInput": "orm",
    "Emission": "emissive",
    "ColorMask": "mask",
    "MaskSelection": "mask",
}

# Filename-suffix fallback, used only for inline texture literals that are not
# exposed as named parameters (e.g. M_TrafficCone). Longest/most specific first
# -- `_ao_r_m` must beat `_m`.
_SUFFIX_SLOTS: tuple[tuple[str, str], ...] = (
    ("_ao_r_m_e", "orm"), ("_ao_r_m", "orm"), ("_orm", "orm"),
    ("_normal", "normal"), ("_n", "normal"),
    ("_albedo", "basecolor"), ("_basecolor", "basecolor"),
    ("_diffuse", "basecolor"), ("_bc", "basecolor"), ("_d", "basecolor"),
    ("_opacity", "opacity"), ("_alpha", "opacity"),
    ("_emissive", "emissive"), ("_e", "emissive"),
    ("_mask", "mask"), ("_m", "mask"),
    ("_roughness", "roughness"),
)

# MDL module -> graph template. Anything unlisted falls back to `omni_pbr`
# when it carries OmniPBR-style params, else `ue4_basic`, else `constant`.
MODULE_TEMPLATES: dict[str, str] = {
    "MI_CeilingA_06b": "ue4_mask_lerp",
    "MI_DoorA": "ue4_color_mask4",
    "MI_LampCeilingA": "ue4_desat_tint",
    "MI_Clock": "ue4_emissive_from_orm_alpha",
    "MI_PC_ON_02": "ue4_basic",
    "MI_Props": "ue4_basic",
    "M_TrafficCone": "ue4_basic",
    "OmniPBR": "omni_pbr",
}

# M_TrafficCone drives emissive from albedo*stripes*Param*(1-fresnel) -- a rim
# hack that reads as a blown-out self-lit cone if taken literally. Drop it.
_DROP_EMISSIVE_MODULES = frozenset({"M_TrafficCone"})


@dataclass
class TextureRef:
    slot: str
    abs_path: Path
    colorspace: str

    def to_json(self, rel_path: str) -> dict[str, str]:
        return {"file": rel_path, "colorspace": self.colorspace}


@dataclass
class MaterialSpec:
    name: str
    prim_path: str
    mdl_module: str = ""
    template: str = "constant"
    sources: list[str] = field(default_factory=list)
    textures: dict[str, TextureRef] = field(default_factory=dict)
    constants: dict[str, Any] = field(default_factory=dict)
    uv: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unresolved_slots: set[str] = field(default_factory=set)
    """Slots whose texture WAS authored but could not be found on disk. The
    MDL constants behind such a slot are dead data in the source and must not
    be promoted -- see `_finalise`."""
    lut: dict[str, Any] = field(default_factory=dict)
    """Base-library approximation, kept apart from `constants` so it can still
    serve as a fallback after a dead constant is discarded."""

    def to_json(self, rel_for: dict[Path, str]) -> dict[str, Any]:
        return {
            "name": self.name,
            "prim_path": self.prim_path,
            "mdl_module": self.mdl_module,
            "template": self.template,
            "sources": self.sources,
            "textures": {
                slot: t.to_json(rel_for.get(t.abs_path, t.abs_path.as_posix()))
                for slot, t in sorted(self.textures.items())
            },
            "constants": self.constants,
            "uv": self.uv,
            "flags": self.flags,
            "warnings": self.warnings,
        }


def apply_overrides(
    spec: MaterialSpec, overrides: dict[str, Any], base_dir: Path
) -> None:
    """Merge a user-authored patch into a spec, matched by material name or
    prim path (prim path wins).

    This is the escape hatch for everything the source cannot supply: the
    ~25 NVIDIA base materials that live on a Nucleus server, and cutouts like
    Chain_Link_Fence whose alpha map is an MDL default we can never see. Point
    a slot at your own file and it is collected like any other texture.

    Schema:
        {"Chain_Link_Fence": {
            "textures":  {"opacity": "D:/tex/chainlink_alpha.png"},
            "constants": {"base_color": [0.56, 0.57, 0.58], "roughness": 0.4},
            "flags":     {"project_uvw": false}}}
    """
    patch = overrides.get(spec.prim_path) or overrides.get(spec.name)
    if not patch:
        return

    for slot, raw in (patch.get("textures") or {}).items():
        if slot not in SLOTS:
            spec.warnings.append(f"override: unknown texture slot {slot!r}")
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = (base_dir / raw)
        path = path.resolve()
        if not path.is_file():
            spec.warnings.append(f"override texture not found: {path}")
            continue
        spec.textures[slot] = TextureRef(slot, path, _colorspace(slot))
        spec.unresolved_slots.discard(slot)
        spec.warnings.append(f"override: {slot} <- {path.name}")

    for key, value in (patch.get("constants") or {}).items():
        spec.constants[key] = value
    for key, value in (patch.get("flags") or {}).items():
        spec.flags[key] = value
    if "template" in patch:
        spec.template = patch["template"]

    # A supplied alpha map means the cutout is recoverable after all.
    if "opacity" in spec.textures:
        spec.flags["enable_opacity"] = True
        spec.flags["enable_opacity_texture"] = True
        spec.warnings = [
            w for w in spec.warnings if not w.startswith("CUTOUT LOST")
        ]
    spec.sources.append("override")


def slot_for(param_name: str, asset_path: str) -> str | None:
    """Classify a texture by parameter name first, filename suffix second."""
    if param_name in PARAM_TO_SLOT:
        return PARAM_TO_SLOT[param_name]
    stem = Path(asset_path).stem.lower()
    # strip a UDIM/sequence tag like `.1001`
    if "." in stem:
        stem = stem.split(".")[0]
    for suffix, slot in _SUFFIX_SLOTS:
        if stem.endswith(suffix):
            return slot
    return None


def _colorspace(slot: str, gamma: str | None = None) -> str:
    if gamma == "srgb":
        return "sRGB"
    if gamma == "linear":
        return "Non-Color"
    return "Non-Color" if slot in _LINEAR_SLOTS else "sRGB"


def _vec3(value: Any) -> list[float] | None:
    """Coerce Gf.Vec3f / Gf.Vec4f / list to a 3-float list."""
    if value is None:
        return None
    try:
        vals = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    if len(vals) < 3:
        return None
    return vals[:3]


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_spec(
    *,
    name: str,
    prim_path: str,
    mdl_module: str,
    usd_inputs: dict[str, Any],
    usd_texture_paths: dict[str, Path],
    usd_missing_textures: dict[str, str] | None,
    mdl_mod: MdlModule | None,
) -> MaterialSpec:
    """Merge LUT < MDL defaults < USD-authored inputs into one spec.

    `usd_inputs` holds every non-asset Shader input value keyed by base name.
    `usd_texture_paths` holds resolved-on-disk asset inputs keyed by base name.
    `usd_missing_textures` holds the ones that were authored but did not
    resolve, which changes how the scalar constants are interpreted.
    """
    spec = MaterialSpec(name=name, prim_path=prim_path, mdl_module=mdl_module)
    c = spec.constants

    for param, raw in (usd_missing_textures or {}).items():
        slot = slot_for(param, raw)
        if slot is not None:
            spec.unresolved_slots.add(slot)

    # --- 1. base-library LUT ------------------------------------------------
    lut = base_materials.lookup(mdl_module)
    if lut:
        spec.sources.append("base_lut")
        spec.lut = lut
        c.update(lut)

    # --- 2. collected MDL module defaults -----------------------------------
    if mdl_mod is not None:
        spec.sources.append("mdl_module")
        for param, (asset, gamma) in mdl_mod.textures.items():
            slot = slot_for(param, asset)
            if slot is None:
                continue
            resolved = mdl_mod.resolve(asset)
            if resolved is None:
                spec.warnings.append(f"mdl default texture missing: {asset}")
                continue
            spec.textures[slot] = TextureRef(slot, resolved, _colorspace(slot, gamma))
        for asset, gamma in mdl_mod.inline_textures:
            slot = slot_for("", asset)
            if slot is None or slot in spec.textures:
                continue
            resolved = mdl_mod.resolve(asset)
            if resolved is None:
                spec.warnings.append(f"mdl inline texture missing: {asset}")
                continue
            spec.textures[slot] = TextureRef(slot, resolved, _colorspace(slot, gamma))
        _absorb_scalars(c, spec, mdl_mod.scalars, mdl_mod.vectors)

    # --- 3. USD-authored overrides (strongest) ------------------------------
    if usd_inputs or usd_texture_paths:
        spec.sources.append("usd_inputs")
    for param, path in usd_texture_paths.items():
        slot = slot_for(param, path.name)
        if slot is None:
            continue
        spec.textures[slot] = TextureRef(slot, path, _colorspace(slot))
    _absorb_scalars(c, spec, usd_inputs, usd_inputs)

    # --- template & post-conditions ----------------------------------------
    spec.template = _pick_template(mdl_module, spec)
    _apply_template_rules(spec)
    _finalise(spec)
    return spec


def _absorb_scalars(
    c: dict[str, Any], spec: MaterialSpec, scalars: dict[str, Any],
    vectors: dict[str, Any],
) -> None:
    """Map MDL parameter names onto the neutral constant vocabulary."""

    def setf(key: str, src: str) -> None:
        v = _num(scalars.get(src))
        if v is not None:
            c[key] = v

    def setv(key: str, *srcs: str) -> None:
        for src in srcs:
            v = _vec3(vectors.get(src))
            if v is not None:
                c[key] = v
                return

    # Held separately from `base_color`: in OmniPBR the constant only applies
    # when no diffuse texture is bound. `_finalise` decides whether it is live.
    setv("diffuse_constant", "diffuse_color_constant", "metal_color")
    setv("tint", "diffuse_tint")
    setv("emissive_color_const", "emissive_color")
    # UE4 float4 tints
    setv("ue4_color", "Color", "ColorAlbedo", "BaseColor_Tint")
    setv("ue4_emission_color", "EmissionColor")
    for i in range(1, 5):
        setv(f"mask_color_{i}", f"Color0{i}")

    # scalars
    setf("roughness", "reflection_roughness_constant")
    setf("roughness", "roughness")
    setf("metallic", "metallic_constant")
    setf("albedo_add", "albedo_add")
    setf("albedo_brightness", "albedo_brightness")
    setf("albedo_desaturation", "albedo_desaturation")
    setf("desaturation", "Desaturation")
    setf("opacity", "opacity_constant")
    setf("opacity_threshold", "opacity_threshold")
    setf("emissive_intensity", "emissive_intensity")
    setf("emissive_intensity", "EmissionIntensity")
    setf("roughness_min", "RoughnessMin")
    setf("roughness_max", "RoughnessMax")
    setf("roughness_multiplier", "RoughnessMultiplier")
    setf("roughness_texture_influence", "reflection_roughness_texture_influence")
    setf("bump_factor", "bump_factor")
    setf("specular", "specular_level")
    setf("uv_tile_u", "U_Tiling")
    setf("uv_tile_v", "V_Tiling")

    # booleans / flags
    for key, src in (
        ("enable_emission", "enable_emission"),
        ("enable_opacity", "enable_opacity"),
        ("enable_opacity_texture", "enable_opacity_texture"),
        ("enable_orm_texture", "enable_ORM_texture"),
        ("project_uvw", "project_uvw"),
        ("world_or_object", "world_or_object"),
        ("flip_tangent_v", "flip_tangent_v"),
    ):
        v = scalars.get(src)
        if isinstance(v, bool):
            spec.flags[key] = v

    # uv transform
    scale = vectors.get("texture_scale")
    if scale is not None:
        try:
            spec.uv["scale"] = [float(scale[0]), float(scale[1])]
        except (TypeError, ValueError, IndexError):
            pass
    trans = vectors.get("texture_translate")
    if trans is not None:
        try:
            spec.uv["translate"] = [float(trans[0]), float(trans[1])]
        except (TypeError, ValueError, IndexError):
            pass
    rot = _num(scalars.get("texture_rotate"))
    if rot is not None:
        spec.uv["rotate"] = rot
    tiling = vectors.get("MainTiling")
    if tiling is not None:
        try:
            spec.uv["scale"] = [float(tiling[0]), float(tiling[1])]
        except (TypeError, ValueError, IndexError):
            pass


def _pick_template(mdl_module: str, spec: MaterialSpec) -> str:
    if mdl_module in MODULE_TEMPLATES:
        return MODULE_TEMPLATES[mdl_module]
    c = spec.constants
    omni_markers = {"albedo_add", "albedo_brightness", "albedo_desaturation"}
    if omni_markers & c.keys() or "enable_orm_texture" in spec.flags:
        return "omni_pbr"
    if spec.textures:
        return "ue4_basic"
    return "constant"


def _apply_template_rules(spec: MaterialSpec) -> None:
    c = spec.constants

    if spec.mdl_module in _DROP_EMISSIVE_MODULES:
        spec.textures.pop("emissive", None)
        c.pop("emissive_intensity", None)
        spec.warnings.append(
            "emissive dropped: MDL drives it from a fresnel rim hack that does "
            "not survive translation"
        )

    # A 4-way colour mask whose four colours are all white collapses to the
    # plain albedo texture -- do not build a dead node graph for it.
    if spec.template == "ue4_color_mask4":
        cols = [c.get(f"mask_color_{i}") for i in range(1, 5)]
        if all(col is None or all(abs(x - 1.0) < 1e-4 for x in col) for col in cols):
            spec.template = "ue4_basic"
            spec.textures.pop("mask", None)
            for i in range(1, 5):
                c.pop(f"mask_color_{i}", None)
            spec.warnings.append(
                "colour-mask collapsed: all four tint slots are white"
            )

    # A mask-lerp with no mask texture cannot lerp.
    if spec.template == "ue4_mask_lerp" and "mask" not in spec.textures:
        spec.template = "ue4_basic"
        spec.warnings.append("mask-lerp downgraded: no mask texture resolved")

    # Desaturate/tint template with neither effect authored is just basic.
    if spec.template == "ue4_desat_tint":
        desat = float(c.get("desaturation", 0.0) or 0.0)
        tint = c.get("ue4_color") or [1.0, 1.0, 1.0]
        if desat < 1e-4 and all(abs(x - 1.0) < 1e-4 for x in tint):
            spec.template = "ue4_basic"

    # OmniPBR honours enable_ORM_texture; an ORM map with the switch off is dead.
    if spec.template == "omni_pbr" and spec.flags.get("enable_orm_texture") is False:
        spec.textures.pop("orm", None)


def _finalise(spec: MaterialSpec) -> None:
    """Fill defaults, fold tint/brightness, and flag anything lossy."""
    c = spec.constants

    # OmniPBR uses `diffuse_color_constant` ONLY when no diffuse texture is
    # bound. Several warehouse materials leave a stale constant behind a bound
    # texture -- Satin_Paint_Gray carries (1, 0, 0). Promoting that when the
    # texture happens to be missing turns grey walls blood red, so only take
    # the constant when the source genuinely had no texture for the slot.
    diffuse_constant = c.pop("diffuse_constant", None)
    basecolor_bound = "basecolor" in spec.textures or "basecolor" in spec.unresolved_slots
    if diffuse_constant is not None and not basecolor_bound:
        c["base_color"] = diffuse_constant
    elif diffuse_constant is not None and "basecolor" in spec.unresolved_slots:
        c["base_color"] = spec.lut.get("base_color", [0.5, 0.5, 0.5])
        spec.warnings.append(
            f"diffuse_color_constant {[round(v, 3) for v in diffuse_constant]} "
            "ignored: it sits behind a texture that is not on disk, so it is "
            "dead data in the source"
        )

    # Same rule for roughness: influence 1.0 means the map fully drives it,
    # leaving reflection_roughness_constant dead.
    if spec.unresolved_slots & {"roughness", "orm"}:
        influence = float(c.get("roughness_texture_influence", 0.0) or 0.0)
        if influence >= 1.0 and "roughness" in spec.lut:
            c["roughness"] = spec.lut["roughness"]
    c.pop("roughness_texture_influence", None)

    c.setdefault("base_color", [0.5, 0.5, 0.5])
    c.setdefault("roughness", 0.5)
    c.setdefault("metallic", 0.0)
    c.setdefault("opacity", 1.0)

    # OmniPBR albedo chain: constant * tint * brightness + add
    tint = c.pop("tint", None)
    brightness = float(c.pop("albedo_brightness", 1.0) or 1.0)
    add = float(c.get("albedo_add", 0.0) or 0.0)
    if tint is not None:
        c["base_color"] = [b * t for b, t in zip(c["base_color"], tint)]
        c["texture_tint"] = [t * brightness for t in tint]
    elif abs(brightness - 1.0) > 1e-4:
        c["texture_tint"] = [brightness] * 3
    if abs(brightness - 1.0) > 1e-4:
        c["base_color"] = [v * brightness for v in c["base_color"]]
    if add:
        # albedo_add is signed -- Chain_Link_Fence uses -1.0 to crush the
        # albedo to black behind a cutout. Clamp both ends: a negative base
        # colour is not a valid Principled input.
        c["base_color"] = [min(1.0, max(0.0, v + add)) for v in c["base_color"]]

    # UE4 float4 tint multiplies the albedo texture.
    ue4_color = c.pop("ue4_color", None)
    if ue4_color is not None and any(abs(v - 1.0) > 1e-4 for v in ue4_color):
        prev = c.get("texture_tint", [1.0, 1.0, 1.0])
        c["texture_tint"] = [p * u for p, u in zip(prev, ue4_color)]
        if "basecolor" not in spec.textures:
            c["base_color"] = ue4_color

    # Emission: colour * intensity, only when something actually enables it.
    intensity = float(c.pop("emissive_intensity", 0.0) or 0.0)
    em_const = c.pop("emissive_color_const", None)
    em_ue4 = c.pop("ue4_emission_color", None)
    em_color = em_ue4 or em_const
    enabled = (
        spec.flags.get("enable_emission", False)
        or "emissive" in spec.textures
        or spec.template == "ue4_emissive_from_orm_alpha"
    )
    if enabled and intensity > 0.0 and (em_color is None or any(em_color)):
        c["emissive_color"] = em_color or [1.0, 1.0, 1.0]
        c["emissive_strength"] = intensity
    else:
        spec.textures.pop("emissive", None)

    # Opacity: only treat as transparent when MDL says so.
    wants_opacity = spec.flags.get("enable_opacity", False)
    wants_opacity_tex = spec.flags.get("enable_opacity_texture", False)
    if not wants_opacity and not wants_opacity_tex:
        spec.textures.pop("opacity", None)
        if c.get("opacity", 1.0) >= 1.0:
            c["opacity"] = 1.0
    elif wants_opacity_tex and "opacity" not in spec.textures:
        # The material is a cutout, but the alpha map is an MDL default inside
        # a module that is not on disk -- so there is nothing to export and the
        # surface will render solid. Only an override can fix this.
        spec.warnings.append(
            "CUTOUT LOST: enable_opacity_texture is on but no alpha map "
            "resolved (it is a default inside a Nucleus-hosted .mdl). This "
            "surface will render SOLID -- supply one via the overrides file."
        )

    # Roughness remap only means something with an ORM/roughness map behind it.
    if "orm" not in spec.textures and "roughness" not in spec.textures:
        rmin = c.pop("roughness_min", None)
        rmax = c.pop("roughness_max", None)
        if rmin is not None and rmax is not None:
            c["roughness"] = (float(rmin) + float(rmax)) / 2.0

    if spec.flags.get("project_uvw"):
        spec.warnings.append(
            "project_uvw: MDL used triplanar/world projection; Blender build "
            "uses BOX projection instead of the mesh UVs"
        )
    if not spec.textures and spec.template != "constant":
        spec.warnings.append("no texture resolved; constants only")
