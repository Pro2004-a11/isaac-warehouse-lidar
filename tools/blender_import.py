"""Stage 2: import the flattened stage into Blender and rebuild the material
node graphs from materials.json.

Blender's USD importer only reads UsdPreviewSurface, which cannot express the
mask-lerps, desaturate/tint chains and roughness remaps the original MDL
materials use. Stage 1 wrote a faithful-as-possible PreviewSurface *and* a
declarative spec; this script throws away the imported node tree for every
material it has a spec for and builds the real graph with Blender nodes.

Run headless:
    blender --background --factory-startup --python blender_import.py -- \
        --package D:/_EXPORT/isaac_warehouse/out --save D:/_EXPORT/warehouse.blend

Run inside a running Blender: open in the Text Editor and press Run Script
(it will use the defaults below, or set ARGS manually).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import bpy

log = logging.getLogger("isaac_blender")

# Socket names differ across Blender versions; try in order.
_BASE_COLOR = ("Base Color",)
_EMISSION_COLOR = ("Emission Color", "Emission")
_EMISSION_STRENGTH = ("Emission Strength",)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _socket(node: bpy.types.Node, names: tuple[str, ...]):
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def _filtered_kwargs(op: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop kwargs the installed Blender's operator does not define.

    The USD importer gains and loses properties between releases; deriving the
    accepted set from the operator itself beats hard-coding one version's list.
    """
    try:
        props = {p.identifier: p for p in op.get_rna_type().properties}
    except Exception:
        return dict(kwargs)

    accepted: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in kwargs.items():
        prop = props.get(key)
        if prop is None:
            dropped.append(key)
            continue
        # An enum whose value this build does not define would raise TypeError.
        items = getattr(prop, "enum_items", None)
        if items is not None and len(items) and value not in items.keys():
            dropped.append(f"{key}={value!r} (valid: {sorted(items.keys())})")
            continue
        accepted[key] = value
    if dropped:
        log.info("USD importer ignores these options in this Blender: %s",
                 sorted(dropped))
    return accepted


class ImageCache:
    """Load each texture file once, no matter how many materials use it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._images: dict[str, bpy.types.Image | None] = {}
        self.missing: set[str] = set()

    def get(self, rel: str, colorspace: str) -> bpy.types.Image | None:
        key = f"{rel}|{colorspace}"
        if key in self._images:
            return self._images[key]

        path = (self.root / rel).resolve()
        img: bpy.types.Image | None = None
        if path.is_file():
            try:
                img = bpy.data.images.load(str(path), check_existing=True)
                try:
                    img.colorspace_settings.name = colorspace
                except (TypeError, AttributeError):
                    log.warning("colorspace %r rejected for %s", colorspace, rel)
            except RuntimeError:
                log.exception("could not load image %s", path)
        else:
            self.missing.add(rel)
        self._images[key] = img
        return img


# --------------------------------------------------------------------------
# material graph construction
# --------------------------------------------------------------------------
class MaterialBuilder:
    """Builds one Blender node graph from one material spec."""

    def __init__(self, mat: bpy.types.Material, spec: dict[str, Any],
                 images: ImageCache) -> None:
        self.mat = mat
        self.spec = spec
        self.images = images
        self.c: dict[str, Any] = spec.get("constants", {})
        self.tex: dict[str, Any] = spec.get("textures", {})
        self.flags: dict[str, Any] = spec.get("flags", {})
        self.uv_cfg: dict[str, Any] = spec.get("uv", {})
        self.triplanar = bool(self.flags.get("project_uvw"))

        mat.use_nodes = True
        self.nt = mat.node_tree
        self.nt.nodes.clear()
        self.out = self.nt.nodes.new("ShaderNodeOutputMaterial")
        self.out.location = (600, 0)
        self.bsdf = self.nt.nodes.new("ShaderNodeBsdfPrincipled")
        self.bsdf.location = (260, 0)
        self.nt.links.new(self.bsdf.outputs["BSDF"], self.out.inputs["Surface"])
        self._vector_out = None
        self._row = 0

    # --- plumbing ---------------------------------------------------------
    def _next_row(self) -> int:
        self._row += 1
        return -300 * (self._row - 1)

    def _vector(self, uv_name: str):
        """UV (or object-space box) coordinates, built once and reused."""
        if self._vector_out is not None:
            return self._vector_out

        if self.triplanar:
            src = self.nt.nodes.new("ShaderNodeTexCoord")
            src.location = (-1200, 300)
            out = src.outputs["Object"]
        else:
            src = self.nt.nodes.new("ShaderNodeUVMap")
            src.location = (-1200, 300)
            src.uv_map = uv_name
            out = src.outputs["UV"]

        scale = self.uv_cfg.get("scale")
        translate = self.uv_cfg.get("translate")
        rotate = self.uv_cfg.get("rotate")
        tile_u = self.c.get("uv_tile_u")
        tile_v = self.c.get("uv_tile_v")
        if tile_u is not None or tile_v is not None:
            scale = [float(tile_u or 1.0), float(tile_v or 1.0)]

        if scale or translate or rotate:
            mapping = self.nt.nodes.new("ShaderNodeMapping")
            mapping.location = (-1000, 300)
            self.nt.links.new(out, mapping.inputs["Vector"])
            if scale:
                mapping.inputs["Scale"].default_value = (
                    float(scale[0]), float(scale[1]), 1.0)
            if translate:
                mapping.inputs["Location"].default_value = (
                    float(translate[0]), float(translate[1]), 0.0)
            if rotate:
                from math import radians
                mapping.inputs["Rotation"].default_value = (
                    0.0, 0.0, radians(float(rotate)))
            out = mapping.outputs["Vector"]

        self._vector_out = out
        return out

    def _image_node(self, slot: str, uv_name: str):
        entry = self.tex.get(slot)
        if not entry:
            return None
        img = self.images.get(entry["file"], entry.get("colorspace", "sRGB"))
        if img is None:
            return None
        node = self.nt.nodes.new("ShaderNodeTexImage")
        node.location = (-800, self._next_row())
        node.image = img
        node.label = slot
        node.extension = "REPEAT"
        if self.triplanar:
            node.projection = "BOX"
            node.projection_blend = 0.3
        self.nt.links.new(self._vector(uv_name), node.inputs["Vector"])
        return node

    def _mix_rgb(self, fac, a, b, loc):
        """A Mix node in RGBA mode: result = lerp(a, b, fac)."""
        node = self.nt.nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = "MIX"
        node.location = loc
        # RGBA sockets are the second A/B pair; address them by index.
        a_in, b_in = node.inputs[6], node.inputs[7]
        if hasattr(fac, "default_value") or fac is None:
            pass
        if isinstance(a, (tuple, list)):
            a_in.default_value = (*[float(v) for v in a[:3]], 1.0)
        else:
            self.nt.links.new(a, a_in)
        if isinstance(b, (tuple, list)):
            b_in.default_value = (*[float(v) for v in b[:3]], 1.0)
        else:
            self.nt.links.new(b, b_in)
        if fac is not None and not isinstance(fac, float):
            self.nt.links.new(fac, node.inputs[0])
        elif isinstance(fac, float):
            node.inputs[0].default_value = fac
        return node.outputs[2]

    def _multiply_rgb(self, source, color, loc):
        node = self.nt.nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = "MULTIPLY"
        node.location = loc
        node.inputs[0].default_value = 1.0
        self.nt.links.new(source, node.inputs[6])
        node.inputs[7].default_value = (*[float(v) for v in color[:3]], 1.0)
        return node.outputs[2]

    # --- channels ---------------------------------------------------------
    def _build_base_color(self, uv_name: str) -> None:
        template = self.spec.get("template", "omni_pbr")
        base_tex = self._image_node("basecolor", uv_name)
        socket = _socket(self.bsdf, _BASE_COLOR)
        if socket is None:
            return

        if base_tex is None:
            bc = self.c.get("base_color", [0.5, 0.5, 0.5])
            socket.default_value = (*[float(v) for v in bc[:3]], 1.0)
            return

        out = base_tex.outputs["Color"]

        if template == "ue4_desat_tint":
            desat = float(self.c.get("desaturation", 0.0) or 0.0)
            if desat > 1e-4:
                hsv = self.nt.nodes.new("ShaderNodeHueSaturation")
                hsv.location = (-520, base_tex.location[1])
                hsv.inputs["Saturation"].default_value = max(0.0, 1.0 - desat)
                self.nt.links.new(out, hsv.inputs["Color"])
                out = hsv.outputs["Color"]

        elif template == "ue4_mask_lerp":
            mask = self._image_node("mask", uv_name)
            if mask is not None:
                flat = self.c.get("base_color", [0.145, 0.145, 0.145])
                # MDL: lerp(ColorAlbedo, AlbedoTexture, MaskSelection)
                out = self._mix_rgb(
                    mask.outputs["Color"], flat, out, (-500, base_tex.location[1])
                )

        elif template == "ue4_color_mask4":
            mask = self._image_node("mask", uv_name)
            cols = [self.c.get(f"mask_color_{i}") for i in (4, 1, 2, 3)]
            if mask is not None and any(cols):
                sep = self.nt.nodes.new("ShaderNodeSeparateColor")
                sep.location = (-560, mask.location[1])
                self.nt.links.new(mask.outputs["Color"], sep.inputs["Color"])
                # MDL chains: lerp(lerp(lerp(tex*C4, tex*C1, m.r), tex*C2, m.g), tex*C3, m.b)
                acc = self._tinted(out, cols[0], (-460, base_tex.location[1]))
                for idx, channel in enumerate(("Red", "Green", "Blue"), start=1):
                    branch = self._tinted(
                        out, cols[idx], (-460, base_tex.location[1] - 120 * idx)
                    )
                    acc = self._mix_rgb(
                        sep.outputs[channel], acc, branch,
                        (-300 + 60 * idx, base_tex.location[1] - 60 * idx),
                    )
                out = acc

        tint = self.c.get("texture_tint")
        if tint and any(abs(float(v) - 1.0) > 1e-4 for v in tint):
            out = self._multiply_rgb(out, tint, (-200, base_tex.location[1]))

        add = float(self.c.get("albedo_add", 0.0) or 0.0)
        if add > 1e-4:
            node = self.nt.nodes.new("ShaderNodeMix")
            node.data_type = "RGBA"
            node.blend_type = "ADD"
            node.location = (-120, base_tex.location[1])
            node.inputs[0].default_value = 1.0
            self.nt.links.new(out, node.inputs[6])
            node.inputs[7].default_value = (add, add, add, 1.0)
            out = node.outputs[2]

        self.nt.links.new(out, socket)

    def _tinted(self, source, color, loc):
        if not color or all(abs(float(v) - 1.0) < 1e-4 for v in color[:3]):
            return source
        return self._multiply_rgb(source, color, loc)

    def _build_orm(self, uv_name: str) -> None:
        orm = self._image_node("orm", uv_name)
        rough_socket = self.bsdf.inputs["Roughness"]
        metal_socket = self.bsdf.inputs["Metallic"]

        rmin = self.c.get("roughness_min")
        rmax = self.c.get("roughness_max")
        rmul = float(self.c.get("roughness_multiplier", 1.0) or 1.0)

        if orm is not None:
            sep = self.nt.nodes.new("ShaderNodeSeparateColor")
            sep.location = (-520, orm.location[1])
            self.nt.links.new(orm.outputs["Color"], sep.inputs["Color"])
            rough_out = sep.outputs["Green"]
            # AO (Red) is deliberately unwired: OmniUe4Base ignores it too.
            self.nt.links.new(sep.outputs["Blue"], metal_socket)
        else:
            rough_tex = self._image_node("roughness", uv_name)
            rough_out = rough_tex.outputs["Color"] if rough_tex else None
            metal_tex = self._image_node("metallic", uv_name)
            if metal_tex is not None:
                self.nt.links.new(metal_tex.outputs["Color"], metal_socket)
            else:
                metal_socket.default_value = float(self.c.get("metallic", 0.0))

        if rough_out is None:
            rough_socket.default_value = float(self.c.get("roughness", 0.5))
            return

        if rmin is not None and rmax is not None:
            remap = self.nt.nodes.new("ShaderNodeMapRange")
            remap.location = (-340, self.bsdf.location[1] - 200)
            remap.inputs["To Min"].default_value = float(rmin)
            remap.inputs["To Max"].default_value = float(rmax)
            self.nt.links.new(rough_out, remap.inputs["Value"])
            rough_out = remap.outputs["Result"]
        elif abs(rmul - 1.0) > 1e-4:
            mul = self.nt.nodes.new("ShaderNodeMath")
            mul.operation = "MULTIPLY"
            mul.location = (-340, self.bsdf.location[1] - 200)
            mul.inputs[1].default_value = rmul
            self.nt.links.new(rough_out, mul.inputs[0])
            rough_out = mul.outputs["Value"]

        self.nt.links.new(rough_out, rough_socket)

    def _build_normal(self, uv_name: str) -> None:
        tex = self._image_node("normal", uv_name)
        if tex is None:
            return
        nmap = self.nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (-460, tex.location[1])
        if not self.triplanar:
            nmap.uv_map = uv_name
        strength = float(self.c.get("bump_factor", 1.0) or 1.0)
        nmap.inputs["Strength"].default_value = max(0.0, min(10.0, strength))
        self.nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
        self.nt.links.new(nmap.outputs["Normal"], self.bsdf.inputs["Normal"])

    def _build_emission(self, uv_name: str) -> None:
        color_socket = _socket(self.bsdf, _EMISSION_COLOR)
        strength_socket = _socket(self.bsdf, _EMISSION_STRENGTH)
        if color_socket is None or strength_socket is None:
            return

        em_color = self.c.get("emissive_color")
        strength = float(self.c.get("emissive_strength", 0.0) or 0.0)
        if strength <= 0.0:
            strength_socket.default_value = 0.0
            return

        template = self.spec.get("template", "")
        if template == "ue4_emissive_from_orm_alpha":
            # MDL drives emission from the ORM map's alpha channel.
            orm = self._image_node("orm", uv_name)
            if orm is not None:
                self.nt.links.new(orm.outputs["Alpha"], strength_socket)
                if em_color:
                    color_socket.default_value = (
                        *[float(v) for v in em_color[:3]], 1.0)
                return

        tex = self._image_node("emissive", uv_name)
        if tex is not None:
            out = tex.outputs["Color"]
            if em_color and any(abs(float(v) - 1.0) > 1e-4 for v in em_color[:3]):
                out = self._multiply_rgb(out, em_color, (-300, tex.location[1]))
            self.nt.links.new(out, color_socket)
        elif em_color:
            color_socket.default_value = (*[float(v) for v in em_color[:3]], 1.0)
        strength_socket.default_value = strength

    def _build_opacity(self, uv_name: str) -> None:
        alpha_socket = self.bsdf.inputs.get("Alpha")
        if alpha_socket is None:
            return
        tex = self._image_node("opacity", uv_name)
        opacity = float(self.c.get("opacity", 1.0))
        transparent = False

        if tex is not None:
            self.nt.links.new(tex.outputs["Color"], alpha_socket)
            transparent = True
        elif opacity < 1.0:
            alpha_socket.default_value = opacity
            transparent = True

        ior = self.c.get("ior")
        if ior is not None and "IOR" in self.bsdf.inputs:
            self.bsdf.inputs["IOR"].default_value = float(ior)

        if transparent:
            _set_blended(self.mat)

    def build(self, uv_name: str) -> None:
        self._build_base_color(uv_name)
        self._build_orm(uv_name)
        self._build_normal(uv_name)
        self._build_emission(uv_name)
        self._build_opacity(uv_name)
        spec_val = self.c.get("specular")
        if spec_val is not None and "Specular IOR Level" in self.bsdf.inputs:
            self.bsdf.inputs["Specular IOR Level"].default_value = float(spec_val)


def _set_blended(mat: bpy.types.Material) -> None:
    """Enable alpha blending across Blender's shifting EEVEE material API."""
    if hasattr(mat, "surface_render_method"):          # 4.2+ / EEVEE Next
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):                 # legacy EEVEE
        mat.blend_method = "BLEND"
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = "HASHED"


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def _base_name(mat_name: str) -> str:
    """Strip Blender's `.001` de-duplication suffix."""
    if len(mat_name) > 4 and mat_name[-4] == "." and mat_name[-3:].isdigit():
        return mat_name[:-4]
    return mat_name


def _observe(mat: bpy.types.Material) -> dict[str, Any]:
    """Read back what Blender's USD importer actually built for this material.

    Stage 1 authored the UsdPreviewSurface *from* the spec, so the imported
    graph is a faithful readout of it. That makes it a reliable fingerprint for
    telling apart the many materials that share a name (12 prims called
    `decal`, 6 called `tag`, ...) -- far safer than assuming Blender's `.001`
    suffixes follow stage traversal order.
    """
    images: set[str] = set()
    base_color: tuple[float, ...] | None = None
    roughness: float | None = None
    metallic: float | None = None

    nt = mat.node_tree
    if nt is not None:
        for node in nt.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                name = Path(node.image.filepath or node.image.name).name
                if name:
                    images.add(name.casefold())
            elif node.type == "BSDF_PRINCIPLED":
                sock = _socket(node, _BASE_COLOR)
                if sock is not None and not sock.is_linked:
                    base_color = tuple(round(float(v), 3)
                                       for v in sock.default_value[:3])
                for key, target in (("Roughness", "roughness"),
                                    ("Metallic", "metallic")):
                    s = node.inputs.get(key)
                    if s is not None and not s.is_linked:
                        val = round(float(s.default_value), 3)
                        if target == "roughness":
                            roughness = val
                        else:
                            metallic = val
    return {"images": images, "base_color": base_color,
            "roughness": roughness, "metallic": metallic}


def _expected(spec: dict[str, Any]) -> dict[str, Any]:
    """The same fingerprint, predicted from a spec."""
    c = spec.get("constants", {})
    images = {
        Path(t["file"]).name.casefold()
        for t in spec.get("textures", {}).values() if t.get("file")
    }
    tex = spec.get("textures", {})
    base_color = None
    if "basecolor" not in tex:
        bc = c.get("base_color")
        if bc:
            base_color = tuple(round(float(v), 3) for v in bc[:3])
    roughness = None
    if "orm" not in tex and "roughness" not in tex:
        roughness = round(float(c.get("roughness", 0.5)), 3)
    metallic = None
    if "orm" not in tex and "metallic" not in tex:
        metallic = round(float(c.get("metallic", 0.0)), 3)
    return {"images": images, "base_color": base_color,
            "roughness": roughness, "metallic": metallic}


def _score(observed: dict[str, Any], expected: dict[str, Any]) -> float:
    """How well a spec explains an imported material. Higher is better."""
    score = 0.0
    obs_img, exp_img = observed["images"], expected["images"]
    if obs_img or exp_img:
        overlap = len(obs_img & exp_img)
        score += 10.0 * overlap
        score -= 4.0 * len(obs_img ^ exp_img)
    for key, weight in (("base_color", 3.0), ("roughness", 2.0), ("metallic", 2.0)):
        o, e = observed[key], expected[key]
        if o is None or e is None:
            continue
        if isinstance(o, tuple) and isinstance(e, tuple):
            delta = sum(abs(a - b) for a, b in zip(o, e)) / 3.0
        else:
            delta = abs(float(o) - float(e))
        score += weight * max(0.0, 1.0 - delta * 4.0)
    return score


class SpecMatcher:
    """Assigns each imported Blender material the spec that actually made it."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.by_name: dict[str, list[dict]] = {}
        for spec in payload.get("materials", []):
            self.by_name.setdefault(spec["name"], []).append(spec)
        self._expected = {
            id(s): _expected(s)
            for specs in self.by_name.values() for s in specs
        }
        self._digest = {
            id(s): json.dumps(
                {k: s.get(k) for k in ("template", "textures", "constants",
                                       "uv", "flags")},
                sort_keys=True,
            )
            for specs in self.by_name.values() for s in specs
        }
        self._claimed: set[str] = set()
        self.low_confidence: list[str] = []

    def match(self, mat: bpy.types.Material) -> dict[str, Any] | None:
        candidates = self.by_name.get(_base_name(mat.name))
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        observed = _observe(mat)
        ranked = sorted(
            candidates,
            key=lambda s: (
                s["prim_path"] in self._claimed,          # prefer unclaimed
                -_score(observed, self._expected[id(s)]),
            ),
        )
        best = ranked[0]
        best_score = _score(observed, self._expected[id(best)])
        rival = next(
            (s for s in ranked[1:] if s["prim_path"] not in self._claimed), None
        )
        if rival is not None:
            rival_score = _score(observed, self._expected[id(rival)])
            # A tie between specs that render identically is not a real
            # ambiguity -- either choice produces the same material.
            same_render = self._digest[id(best)] == self._digest[id(rival)]
            if best_score - rival_score < 1.0 and not same_render:
                self.low_confidence.append(
                    f"{mat.name}: {best_score:.1f} vs {rival_score:.1f} "
                    f"-> {best['prim_path']}"
                )
        self._claimed.add(best["prim_path"])
        return best


def import_package(package: Path, light_scale: float, import_lights: bool,
                   import_cameras: bool, include_hidden: bool) -> dict[str, Any]:
    manifest = package / "materials.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    stage = package / payload["stage"]
    if not stage.is_file():
        raise FileNotFoundError(stage)

    kwargs = {
        "filepath": str(stage),
        "import_materials": True,
        "import_meshes": True,
        "import_lights": import_lights,
        "import_cameras": import_cameras,
        "import_curves": True,
        "import_volumes": False,
        "import_shapes": True,
        "import_usd_preview": True,
        "set_material_blend": True,
        "read_mesh_uvs": True,
        "read_mesh_colors": True,
        "import_visible_only": not include_hidden,
        "import_render": True,
        "import_proxy": False,
        "import_guide": False,
        "mtl_name_collision_mode": "MAKE_UNIQUE",
        "import_textures_mode": "IMPORT_NONE",
        "light_intensity_scale": light_scale,
        "create_collection": True,
        "relative_path": False,
        "merge_parent_xform": True,
    }
    kwargs = _filtered_kwargs(bpy.ops.wm.usd_import, kwargs)
    log.info("importing %s", stage)
    result = bpy.ops.wm.usd_import(**kwargs)
    if "FINISHED" not in result:
        raise RuntimeError(f"usd_import returned {result}")

    matcher = SpecMatcher(payload)
    uv_name = payload.get("uv_primvar", "st")
    images = ImageCache(package)
    left_as_authored = {
        Path(p).name for p in payload.get("skipped_already_preview", [])
    }

    rebuilt = 0
    unmatched: list[str] = []
    for mat in bpy.data.materials:
        spec = matcher.match(mat)
        if spec is None:
            if mat.users and _base_name(mat.name) not in left_as_authored:
                unmatched.append(mat.name)
            continue
        try:
            MaterialBuilder(mat, spec, images).build(uv_name)
            rebuilt += 1
        except Exception:
            log.exception("failed rebuilding material %s", mat.name)

    return {
        "materials_total": len(bpy.data.materials),
        "materials_rebuilt": rebuilt,
        "materials_unmatched": unmatched,
        "left_as_authored": sorted(left_as_authored),
        "low_confidence": matcher.low_confidence,
        "missing_textures": sorted(images.missing),
        "objects": len(bpy.context.scene.objects),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--package", type=Path, required=True,
                    help="folder produced by usd_flatten_collect.py")
    ap.add_argument("--save", type=Path, default=None, help="output .blend")
    ap.add_argument("--light-scale", type=float, default=1.0)
    # Off by default: Isaac ships area lights at ~6.3e5 W and a sun at 1.2e4 W,
    # which are unusable in Blender without a conversion nobody has derived.
    # Lighting the scene in Blender is the saner workflow.
    ap.add_argument("--lights", action="store_true",
                    help="import USD lights (very high intensities, see --light-scale)")
    ap.add_argument("--cameras", action="store_true", help="import USD cameras")
    ap.add_argument(
        "--include-hidden", action="store_true",
        help="also import prims marked invisible in the USD. The Digital Twin "
             "Warehouse hides its ceiling and its collision proxies; pass this "
             "if you want the ceiling for a closed interior render.",
    )
    ap.add_argument("--keep-scene", action="store_true",
                    help="do not wipe the current scene before importing")
    args = ap.parse_args(argv)

    if not args.keep_scene:
        bpy.ops.wm.read_factory_settings(use_empty=True)

    try:
        stats = import_package(
            args.package.resolve(), args.light_scale,
            args.lights, args.cameras, args.include_hidden,
        )
    except Exception:
        log.exception("import failed")
        return 1

    print("\nBLENDER IMPORT SUMMARY")
    print("=" * 50)
    print(f"objects in scene     : {stats['objects']}")
    print(f"materials total      : {stats['materials_total']}")
    print(f"materials rebuilt    : {stats['materials_rebuilt']}")
    print(f"already UsdPreview    : {len(stats['left_as_authored'])} "
          "(kept as imported, by design)")
    if stats["materials_unmatched"]:
        print(f"UNMATCHED            : {len(stats['materials_unmatched'])}")
        for n in stats["materials_unmatched"][:15]:
            print(f"    {n}")
    if stats["low_confidence"]:
        print(f"LOW-CONFIDENCE match : {len(stats['low_confidence'])} "
              "(shared name, near-identical specs)")
        for n in stats["low_confidence"]:
            print(f"    {n}")
    if stats["missing_textures"]:
        print(f"missing textures     : {len(stats['missing_textures'])}")
        for n in stats["missing_textures"]:
            print(f"    {n}")

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save.resolve()))
        print(f"\nsaved: {args.save.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
