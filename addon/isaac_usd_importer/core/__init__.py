"""Backend for the Isaac Sim USD importer.

`mdl_parse`, `base_materials` and `material_spec` are pure Python -- they
import neither `bpy` nor `pxr`, so they are testable outside Blender.
`stage_convert` needs `pxr` (Blender bundles it); `node_builder` and `lighting`
need `bpy`.
"""
