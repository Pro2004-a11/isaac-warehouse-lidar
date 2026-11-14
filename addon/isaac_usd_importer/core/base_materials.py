"""Fallback constants for NVIDIA base-library MDL modules that are *not* shipped
with Isaac Sim.

The Digital Twin Warehouse stage references ~25 modules under
`.../NVIDIA/Materials/Base/**` and `.../NVIDIA/Assets/DigitalTwin/Materials/**`.
Those live on a Nucleus server, not on disk, so their parameter defaults (and
their textures) cannot be read. A shader prim that overrides nothing therefore
carries *zero* usable information.

These values are hand-authored PBR approximations keyed on module name. They are
approximations, not ground truth -- `converted_from` in the report marks every
material that landed here so they are easy to find and hand-tune in Blender.

Fields (all optional): base_color, roughness, metallic, opacity, ior, emissive.
"""

from __future__ import annotations

BASE_MATERIAL_LUT: dict[str, dict[str, object]] = {
    # --- metals -----------------------------------------------------------
    "Aluminum_Polished": {
        "base_color": [0.912, 0.914, 0.920], "metallic": 1.0, "roughness": 0.08},
    "Aluminum_Anodized_Black": {
        "base_color": [0.045, 0.045, 0.048], "metallic": 1.0, "roughness": 0.38},
    "Copper_D": {
        "base_color": [0.955, 0.638, 0.538], "metallic": 1.0, "roughness": 0.35},
    "Gold_A": {
        "base_color": [1.000, 0.766, 0.336], "metallic": 1.0, "roughness": 0.20},
    "Gold_D": {
        "base_color": [1.000, 0.766, 0.336], "metallic": 1.0, "roughness": 0.42},
    "Metal_Glossy_A": {
        "base_color": [0.560, 0.570, 0.580], "metallic": 1.0, "roughness": 0.15},
    "DGX_Gold_Matte_A": {
        "base_color": [0.586, 0.520, 0.393], "metallic": 1.0, "roughness": 0.55},
    "DGX_Gold_Shiny_A": {
        "base_color": [0.586, 0.520, 0.393], "metallic": 1.0, "roughness": 0.18},
    "DGX_Gold_Mesh_A": {
        "base_color": [0.586, 0.520, 0.393], "metallic": 1.0, "roughness": 0.45},
    "Chain_Link_Fence": {
        "base_color": [0.560, 0.570, 0.580], "metallic": 1.0, "roughness": 0.40},

    # --- plastics ---------------------------------------------------------
    "Plastic": {
        "base_color": [0.800, 0.800, 0.800], "metallic": 0.0, "roughness": 0.40},
    "Plastic_White_A": {
        "base_color": [0.880, 0.880, 0.870], "metallic": 0.0, "roughness": 0.35},
    "Plastic_Black_C": {
        "base_color": [0.030, 0.030, 0.032], "metallic": 0.0, "roughness": 0.30},
    "Plastic_Rough_Black_A": {
        "base_color": [0.028, 0.028, 0.030], "metallic": 0.0, "roughness": 0.70},
    "Plastic_Acrylic": {
        "base_color": [0.900, 0.900, 0.900], "metallic": 0.0, "roughness": 0.05,
        "opacity": 0.15, "ior": 1.49},

    # --- glass ------------------------------------------------------------
    "Clear_Glass": {
        "base_color": [1.000, 1.000, 1.000], "metallic": 0.0, "roughness": 0.02,
        "opacity": 0.08, "ior": 1.52},

    # --- paint / masonry --------------------------------------------------
    "Paint_Gloss": {
        "base_color": [0.700, 0.700, 0.700], "metallic": 0.0, "roughness": 0.08},
    "Paint_Satin": {
        "base_color": [0.700, 0.700, 0.700], "metallic": 0.0, "roughness": 0.35},
    "Concrete_Polished": {
        "base_color": [0.420, 0.412, 0.400], "metallic": 0.0, "roughness": 0.35},

    # --- wood -------------------------------------------------------------
    "Oak_Planks": {
        "base_color": [0.450, 0.310, 0.190], "metallic": 0.0, "roughness": 0.60},
    "Plywood": {
        "base_color": [0.660, 0.520, 0.350], "metallic": 0.0, "roughness": 0.70},
    "Wood_Recycled_A": {
        "base_color": [0.380, 0.300, 0.240], "metallic": 0.0, "roughness": 0.75},

    # --- cardboard (textures live on Nucleus; constants only) -------------
    "M_FlatBox_Cardboard_01": {
        "base_color": [0.520, 0.380, 0.245], "metallic": 0.0, "roughness": 0.85},
    "M_MultiDepthBox_Cardboard_01": {
        "base_color": [0.520, 0.380, 0.245], "metallic": 0.0, "roughness": 0.85},
    "M_MultiDepthBox_Decal_01": {
        "base_color": [0.620, 0.470, 0.320], "metallic": 0.0, "roughness": 0.85},
}


def lookup(module_name: str) -> dict[str, object]:
    """Return a shallow copy of the LUT entry for an MDL module, or {}."""
    return dict(BASE_MATERIAL_LUT.get(module_name, {}))
