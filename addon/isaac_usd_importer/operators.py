"""File > Import > Isaac Sim USD (.usd)"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

from .core import lighting, node_builder, stage_convert

log = logging.getLogger(__name__)

_IMPORT_KWARGS_BASE = {
    "import_materials": True,
    "import_meshes": True,
    "import_curves": True,
    "import_shapes": True,
    "import_volumes": False,
    "import_usd_preview": True,
    "set_material_blend": True,
    "read_mesh_uvs": True,
    "read_mesh_colors": True,
    "import_render": True,
    "import_proxy": False,
    "import_guide": False,
    "mtl_name_collision_mode": "MAKE_UNIQUE",
    "relative_path": False,
    "merge_parent_xform": True,
}


def _filtered_kwargs(op: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop options this Blender's USD importer does not define.

    The importer gains and loses properties between releases, so deriving the
    accepted set from the operator beats hard-coding one version's list.
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
        items = getattr(prop, "enum_items", None)
        if items is not None and len(items) and value not in items.keys():
            dropped.append(f"{key}={value!r}")
            continue
        accepted[key] = value
    if dropped:
        log.info("USD importer ignores: %s", sorted(dropped))
    return accepted


class ISAACUSD_OT_import(bpy.types.Operator, ImportHelper):
    """Import an Isaac Sim / Omniverse USD stage, converting MDL materials"""

    bl_idname = "isaac_usd.import_stage"
    bl_label = "Import Isaac Sim USD"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".usd"
    filter_glob: StringProperty(
        default="*.usd;*.usda;*.usdc;*.usdz", options={"HIDDEN"})

    # --- materials --------------------------------------------------------
    convert_materials: BoolProperty(
        name="Convert MDL Materials", default=True,
        description=("Translate Omniverse MDL materials to Blender nodes. "
                     "Without this you get geometry and mostly grey materials"))
    unique_names: BoolProperty(
        name="Make Material Names Unique", default=True,
        description=("Rename duplicate material prims before import. The "
                     "Digital Twin Warehouse has 12 materials called 'decal' "
                     "with different textures; without this they cannot be "
                     "told apart after import"))
    triplanar_blend: FloatProperty(
        name="Triplanar Blend", default=0.3, min=0.0, max=1.0,
        description=("Box-projection blend for materials that used MDL "
                     "world/triplanar projection instead of UVs"))
    overrides_path: StringProperty(
        name="Overrides JSON", default="", subtype="FILE_PATH",
        description=("Optional patch file supplying textures or constants the "
                     "source cannot provide, e.g. cutout alphas that live on a "
                     "Nucleus server"))

    # --- textures ---------------------------------------------------------
    collect_textures: BoolProperty(
        name="Collect Textures", default=False,
        description=("Copy every referenced texture next to a converted stage "
                     "so the result is portable. Off means textures are "
                     "referenced where they already are"))
    package_dir: StringProperty(
        name="Package Folder", default="", subtype="DIR_PATH",
        description="Where to write the collected package (blank = temp folder)")

    # --- scene ------------------------------------------------------------
    import_lights: BoolProperty(
        name="Import Lights", default=False,
        description=("Isaac lights arrive at ~628,000 W. They are normalised "
                     "to the target power below"))
    target_watts: FloatProperty(
        name="Light Power", default=lighting.DEFAULT_TARGET_WATTS,
        min=0.0, soft_max=5000.0, subtype="POWER",
        description=("Brightest non-sun light is pinned to this wattage; the "
                     "rest keep their relative ratios"))
    sun_irradiance: FloatProperty(
        name="Sun Strength", default=lighting.DEFAULT_SUN_IRRADIANCE,
        min=0.0, soft_max=20.0,
        description="Irradiance (W/m^2) applied to imported sun lights")
    import_cameras: BoolProperty(name="Import Cameras", default=False)
    include_hidden: BoolProperty(
        name="Include Hidden Prims", default=False,
        description=("Also import prims marked invisible. The Digital Twin "
                     "Warehouse hides its ceiling and collision proxies"))
    write_report: EnumProperty(
        name="Report",
        items=[("NONE", "None", "No report"),
               ("TEXT", "Text Block", "Write to a Blender text datablock"),
               ("FILE", "File", "Write report.txt beside the package")],
        default="TEXT")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.label(text="Materials", icon="MATERIAL")
        box.prop(self, "convert_materials")
        col = box.column()
        col.enabled = self.convert_materials
        col.prop(self, "unique_names")
        col.prop(self, "triplanar_blend")
        col.prop(self, "overrides_path")

        box = layout.box()
        box.label(text="Textures", icon="TEXTURE")
        box.prop(self, "collect_textures")
        row = box.row()
        row.enabled = self.collect_textures
        row.prop(self, "package_dir")

        box = layout.box()
        box.label(text="Scene", icon="SCENE_DATA")
        box.prop(self, "include_hidden")
        box.prop(self, "import_cameras")
        box.prop(self, "import_lights")
        col = box.column()
        col.enabled = self.import_lights
        col.prop(self, "target_watts")
        col.prop(self, "sun_irradiance")
        layout.prop(self, "write_report")

    # ------------------------------------------------------------------
    def execute(self, context):
        src = Path(self.filepath)
        if not src.is_file():
            self.report({"ERROR"}, f"not a file: {src}")
            return {"CANCELLED"}

        started = time.perf_counter()
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            result = self._run(src, context)
        except Exception as exc:
            log.exception("Isaac USD import failed")
            self.report({"ERROR"}, f"import failed: {exc}")
            return {"CANCELLED"}
        finally:
            wm.progress_end()

        result["seconds"] = time.perf_counter() - started
        self._emit_report(result)
        self.report(
            {"INFO"},
            f"{result['objects']} objects, "
            f"{result['materials_rebuilt']} materials rebuilt "
            f"in {result['seconds']:.1f}s")
        return {"FINISHED"}

    # ------------------------------------------------------------------
    def _run(self, src: Path, context) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        overrides_dir = src.parent
        if self.overrides_path:
            ov = Path(bpy.path.abspath(self.overrides_path))
            try:
                loaded = json.loads(ov.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    overrides = loaded
                    overrides_dir = ov.parent
                else:
                    self.report({"WARNING"}, "overrides file is not a JSON object")
            except (OSError, ValueError) as exc:
                self.report({"WARNING"}, f"could not read overrides: {exc}")

        if self.collect_textures and self.package_dir:
            work_dir = Path(bpy.path.abspath(self.package_dir))
            temp_dir = None
        else:
            temp_dir = tempfile.TemporaryDirectory(prefix="isaac_usd_")
            work_dir = Path(temp_dir.name)

        try:
            if self.convert_materials:
                conv = stage_convert.convert(
                    src, work_dir,
                    overrides=overrides, overrides_dir=overrides_dir,
                    collect_textures=self.collect_textures,
                    unique_names=self.unique_names,
                    progress=lambda m: log.info("[isaac-usd] %s", m),
                )
                stage_path = conv["stage_path"]
            else:
                conv = {"specs": [], "skipped": [], "uv_primvar": "st",
                        "renamed": 0, "collector": None, "rename_warnings": [],
                        "authoring_failures": 0, "up_axis": "?",
                        "meters_per_unit": 1.0}
                stage_path = src

            kwargs = dict(_IMPORT_KWARGS_BASE)
            kwargs["filepath"] = str(stage_path)
            kwargs["import_lights"] = self.import_lights
            kwargs["import_cameras"] = self.import_cameras
            kwargs["import_visible_only"] = not self.include_hidden
            kwargs = _filtered_kwargs(bpy.ops.wm.usd_import, kwargs)

            before = set(bpy.data.objects)
            status = bpy.ops.wm.usd_import(**kwargs)
            if "FINISHED" not in status:
                raise RuntimeError(f"usd_import returned {status}")
            new_objects = [o for o in bpy.data.objects if o not in before]

            mat_stats: dict[str, Any] = {
                "rebuilt": 0, "unmatched": [], "duplicate_spec_names": [],
                "missing_textures": [],
            }
            if self.convert_materials and conv["specs"]:
                collector = conv["collector"]
                rel_for = collector.abs_to_rel if collector else {}
                specs_json = [s.to_json(rel_for) for s in conv["specs"]]
                mat_stats = node_builder.rebuild_materials(
                    specs_json, conv["uv_primvar"], work_dir,
                    self.triplanar_blend,
                    {Path(p).name for p in conv["skipped"]},
                )

            light_report = None
            if self.import_lights:
                lights = [o for o in new_objects if o.type == "LIGHT"]
                if lights:
                    light_report = lighting.normalise_lights(
                        lights, self.target_watts, self.sun_irradiance)

            return {
                "source": src,
                "work_dir": work_dir,
                "objects": len(new_objects),
                "meshes": len([o for o in new_objects if o.type == "MESH"]),
                "materials_rebuilt": mat_stats["rebuilt"],
                "materials_unmatched": mat_stats["unmatched"],
                "duplicate_spec_names": mat_stats["duplicate_spec_names"],
                "missing_textures": mat_stats["missing_textures"],
                "conv": conv,
                "lights": light_report,
            }
        finally:
            # The temp stage is only needed until the importer has read it.
            if temp_dir is not None:
                try:
                    temp_dir.cleanup()
                except OSError:
                    log.warning("could not clean temp dir %s", work_dir)

    # ------------------------------------------------------------------
    def _emit_report(self, result: dict[str, Any]) -> None:
        if self.write_report == "NONE":
            return
        conv = result["conv"]
        specs = conv["specs"]

        lines = ["ISAAC SIM USD IMPORT", "=" * 58,
                 f"source          : {result['source']}",
                 f"up axis         : {conv['up_axis']}  "
                 f"metres/unit: {conv['meters_per_unit']}",
                 f"elapsed         : {result['seconds']:.1f}s",
                 f"objects         : {result['objects']} "
                 f"({result['meshes']} meshes)",
                 ""]

        if specs:
            textured = sum(1 for s in specs if s.textures)
            templates: dict[str, int] = {}
            for s in specs:
                templates[s.template] = templates.get(s.template, 0) + 1
            lines += [
                f"MDL materials converted : {len(specs)}",
                f"  with >=1 texture      : {textured}",
                f"  constants only        : {len(specs) - textured}",
                f"left as authored        : {len(conv['skipped'])}",
                f"rebuilt in Blender      : {result['materials_rebuilt']}",
                f"duplicate names fixed   : {conv['renamed']}",
                "",
                "by graph template:",
            ]
            for name, count in sorted(templates.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {count:5d}  {name}")

        collector = conv.get("collector")
        if collector is not None:
            lines += ["",
                      f"textures collected      : {collector.copied} "
                      f"({collector.failed} failed)",
                      f"assets not on disk      : "
                      f"{len(set(collector.unresolved))}",
                      f"package folder          : {result['work_dir']}"]

        if result["lights"]:
            lr = result["lights"]
            lines += ["",
                      f"lights normalised       : {lr['punctual']} punctual, "
                      f"{lr['suns']} sun",
                      f"  peak {lr['peak_before']:.4g} W -> "
                      f"{lr['target_watts']:.4g} W  (x{lr['factor']:.3g})"]

        for label, key in (("MATERIALS WITH NO SPEC", "materials_unmatched"),
                           ("MISSING TEXTURE FILES", "missing_textures"),
                           ("AMBIGUOUS SPEC NAMES", "duplicate_spec_names")):
            if result[key]:
                lines += ["", f"{label} ({len(result[key])}):"]
                lines += [f"  {v}" for v in result[key][:40]]

        warned = [s for s in specs if s.warnings]
        if warned:
            lines += ["", f"MATERIALS NEEDING ATTENTION ({len(warned)}):"]
            for s in warned:
                lines.append(f"  [{s.mdl_module or '?'}] {s.name}")
                for w in s.warnings:
                    lines.append(f"      - {w}")

        text = "\n".join(lines) + "\n"

        if self.write_report == "FILE":
            try:
                (Path(result["work_dir"]) / "report.txt").write_text(
                    text, encoding="utf-8")
                return
            except OSError:
                log.exception("could not write report file")

        name = "isaac_usd_report.txt"
        block = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        block.clear()
        block.write(text)


def menu_func_import(self, context):
    self.layout.operator(ISAACUSD_OT_import.bl_idname,
                         text="Isaac Sim USD (.usd)")


CLASSES = (ISAACUSD_OT_import,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
