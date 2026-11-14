"""Blender-side material construction.

Blender's USD importer only understands UsdPreviewSurface, which cannot express
the mask-lerps, desaturate/tint chains and roughness remaps the original MDL
materials use. `stage_convert` writes a faithful-as-possible PreviewSurface so
the import works standalone; this module then discards that node tree and
rebuilds the real graph from the declarative spec.
"""

from __future__ import annotations

import logging
from math import radians
from pathlib import Path
from typing import Any

import bpy

log = logging.getLogger(__name__)

_BASE_COLOR = ("Base Color",)
_EMISSION_COLOR = ("Emission Color", "Emission")
_EMISSION_STRENGTH = ("Emission Strength",)


def _socket(node: bpy.types.Node, names: tuple[str, ...]):
    for name in names:
        if name in node.inputs:
            return node.inputs[name]
    return None


def base_name(mat_name: str) -> str:
    """Strip Blender's `.001` de-duplication suffix."""
    if len(mat_name) > 4 and mat_name[-4] == "." and mat_name[-3:].isdigit():
        return mat_name[:-4]
    return mat_name


class ImageCache:
    """Load each texture file once, however many materials reference it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._images: dict[str, bpy.types.Image | None] = {}
        self.missing: set[str] = set()

    def get(self, rel: str, colorspace: str) -> bpy.types.Image | None:
        key = f"{rel}|{colorspace}"
        if key in self._images:
            return self._images[key]

        # Joining an absolute path onto root yields the absolute path, so this
        # handles both the collected (relative) and in-place (absolute) cases.
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


def set_blended(mat: bpy.types.Material) -> None:
    """Enable alpha blending across Blender's shifting EEVEE material API."""
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
        if hasattr(mat, "shadow_method"):
            mat.shadow_method = "HASHED"


class MaterialBuilder:
    """Builds one Blender node graph from one material spec."""

    def __init__(self, mat: bpy.types.Material, spec: dict[str, Any],
                 images: ImageCache, triplanar_blend: float = 0.3) -> None:
        self.mat = mat
        self.spec = spec
        self.images = images
        self.triplanar_blend = triplanar_blend
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
        tile_u, tile_v = self.c.get("uv_tile_u"), self.c.get("uv_tile_v")
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
            node.projection_blend = self.triplanar_blend
        self.nt.links.new(self._vector(uv_name), node.inputs["Vector"])
        return node

    def _mix_rgb(self, fac, a, b, loc):
        """Mix node in RGBA mode: result = lerp(a, b, fac)."""
        node = self.nt.nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = "MIX"
        node.location = loc
        a_in, b_in = node.inputs[6], node.inputs[7]
        if isinstance(a, (tuple, list)):
            a_in.default_value = (*[float(v) for v in a[:3]], 1.0)
        else:
            self.nt.links.new(a, a_in)
        if isinstance(b, (tuple, list)):
            b_in.default_value = (*[float(v) for v in b[:3]], 1.0)
        else:
            self.nt.links.new(b, b_in)
        if isinstance(fac, float):
            node.inputs[0].default_value = fac
        elif fac is not None:
            self.nt.links.new(fac, node.inputs[0])
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

    def _tinted(self, source, color, loc):
        if not color or all(abs(float(v) - 1.0) < 1e-4 for v in color[:3]):
            return source
        return self._multiply_rgb(source, color, loc)

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
        row = base_tex.location[1]

        if template == "ue4_desat_tint":
            desat = float(self.c.get("desaturation", 0.0) or 0.0)
            if desat > 1e-4:
                hsv = self.nt.nodes.new("ShaderNodeHueSaturation")
                hsv.location = (-520, row)
                hsv.inputs["Saturation"].default_value = max(0.0, 1.0 - desat)
                self.nt.links.new(out, hsv.inputs["Color"])
                out = hsv.outputs["Color"]

        elif template == "ue4_mask_lerp":
            mask = self._image_node("mask", uv_name)
            if mask is not None:
                flat = self.c.get("base_color", [0.145, 0.145, 0.145])
                # MDL: lerp(ColorAlbedo, AlbedoTexture, MaskSelection)
                out = self._mix_rgb(mask.outputs["Color"], flat, out, (-500, row))

        elif template == "ue4_color_mask4":
            mask = self._image_node("mask", uv_name)
            cols = [self.c.get(f"mask_color_{i}") for i in (4, 1, 2, 3)]
            if mask is not None and any(cols):
                sep = self.nt.nodes.new("ShaderNodeSeparateColor")
                sep.location = (-560, mask.location[1])
                self.nt.links.new(mask.outputs["Color"], sep.inputs["Color"])
                acc = self._tinted(out, cols[0], (-460, row))
                for idx, channel in enumerate(("Red", "Green", "Blue"), start=1):
                    branch = self._tinted(out, cols[idx], (-460, row - 120 * idx))
                    acc = self._mix_rgb(sep.outputs[channel], acc, branch,
                                        (-300 + 60 * idx, row - 60 * idx))
                out = acc

        tint = self.c.get("texture_tint")
        if tint and any(abs(float(v) - 1.0) > 1e-4 for v in tint):
            out = self._multiply_rgb(out, tint, (-200, row))

        add = float(self.c.get("albedo_add", 0.0) or 0.0)
        if add > 1e-4:
            node = self.nt.nodes.new("ShaderNodeMix")
            node.data_type = "RGBA"
            node.blend_type = "ADD"
            node.location = (-120, row)
            node.inputs[0].default_value = 1.0
            self.nt.links.new(out, node.inputs[6])
            node.inputs[7].default_value = (add, add, add, 1.0)
            out = node.outputs[2]

        self.nt.links.new(out, socket)

    def _build_orm(self, uv_name: str) -> None:
        orm = self._image_node("orm", uv_name)
        rough_socket = self.bsdf.inputs["Roughness"]
        metal_socket = self.bsdf.inputs["Metallic"]

        rmin, rmax = self.c.get("roughness_min"), self.c.get("roughness_max")
        rmul = float(self.c.get("roughness_multiplier", 1.0) or 1.0)

        if orm is not None:
            sep = self.nt.nodes.new("ShaderNodeSeparateColor")
            sep.location = (-520, orm.location[1])
            self.nt.links.new(orm.outputs["Color"], sep.inputs["Color"])
            rough_out = sep.outputs["Green"]
            # AO (Red) stays unwired -- OmniUe4Base ignores it too.
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

        if self.spec.get("template") == "ue4_emissive_from_orm_alpha":
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
            set_blended(self.mat)

    def build(self, uv_name: str) -> None:
        self._build_base_color(uv_name)
        self._build_orm(uv_name)
        self._build_normal(uv_name)
        self._build_emission(uv_name)
        self._build_opacity(uv_name)
        spec_val = self.c.get("specular")
        if spec_val is not None and "Specular IOR Level" in self.bsdf.inputs:
            self.bsdf.inputs["Specular IOR Level"].default_value = float(spec_val)


def rebuild_materials(specs_json: list[dict[str, Any]], uv_name: str,
                      root: Path, triplanar_blend: float = 0.3,
                      left_as_authored: set[str] | None = None,
                      ) -> dict[str, Any]:
    """Rebuild every Blender material that has a spec.

    Names are unique by construction (see `uniquify_material_names`), so this
    matches directly by name rather than guessing between same-named specs.
    """
    by_name: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for spec in specs_json:
        if spec["name"] in by_name:
            duplicates.append(spec["name"])
        by_name[spec["name"]] = spec

    images = ImageCache(root)
    rebuilt = 0
    unmatched: list[str] = []
    left_as_authored = left_as_authored or set()

    for mat in bpy.data.materials:
        spec = by_name.get(base_name(mat.name))
        if spec is None:
            if mat.users and base_name(mat.name) not in left_as_authored:
                unmatched.append(mat.name)
            continue
        try:
            MaterialBuilder(mat, spec, images, triplanar_blend).build(uv_name)
            rebuilt += 1
        except Exception:
            log.exception("failed rebuilding material %s", mat.name)
            unmatched.append(f"{mat.name} (build error)")

    return {
        "rebuilt": rebuilt,
        "unmatched": unmatched,
        "duplicate_spec_names": duplicates,
        "missing_textures": sorted(images.missing),
    }
