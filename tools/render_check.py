"""Render a few views of an imported warehouse .blend so the result can be
eyeballed instead of inferred from counters.

    blender --background warehouse.blend --python render_check.py -- \
        --out D:/_EXPORT/isaac_warehouse/preview --samples 32
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def scene_bounds(reject_factor: float = 3.0) -> tuple[Vector, Vector]:
    """Robust world-space bounds of the mesh geometry.

    The absolute min/max is useless for framing here: the warehouse walls come
    from a CAD trace whose polyfaces carry stray vertices ~240 m outside the
    building, which would push the camera back until the building is a speck.

    Trimming a percentile off each *axis* is the wrong fix -- it clips real
    geometry wherever few objects reach (the +Y end of this scene). Instead
    reject outlier *objects* by distance from the median object centre, then
    take the exact bounds of what is left, so real extents survive intact.
    """
    boxes: list[tuple[Vector, Vector, Vector]] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = Vector((min(c[i] for c in corners) for i in range(3)))
        hi = Vector((max(c[i] for c in corners) for i in range(3)))
        boxes.append((lo, hi, (lo + hi) / 2.0))

    if not boxes:
        return Vector((0, 0, 0)), Vector((1, 1, 1))

    median = Vector((
        sorted(b[2][i] for b in boxes)[len(boxes) // 2] for i in range(3)
    ))
    distances = sorted((b[2] - median).length for b in boxes)
    typical = distances[int(len(distances) * 0.75)] or 1.0
    cutoff = max(typical * reject_factor, 1.0)

    kept = [b for b in boxes if (b[2] - median).length <= cutoff]
    rejected = len(boxes) - len(kept)
    if rejected:
        print(f"framing: ignored {rejected} outlier object(s) beyond "
              f"{cutoff:.1f} m of the median centre")
    if not kept:
        kept = boxes

    lo = Vector((min(b[0][i] for b in kept) for i in range(3)))
    hi = Vector((max(b[1][i] for b in kept) for i in range(3)))
    return lo, hi


def make_camera(name: str, location: Vector, target: Vector) -> bpy.types.Object:
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = 24.0
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = location
    direction = target - location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return cam


def setup_render(samples: int, resolution: tuple[int, int]) -> None:
    scene = bpy.context.scene
    # The EEVEE enum identifier moved between 4.x and 5.x; take what exists.
    available = {
        e.identifier
        for e in scene.render.bl_rna.properties["engine"].enum_items
    }
    for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        if candidate in available:
            scene.render.engine = candidate
            break
    print(f"render engine: {scene.render.engine}")
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    try:
        scene.eevee.taa_render_samples = samples
    except AttributeError:
        pass
    scene.view_settings.view_transform = "AgX"


def ensure_world_light(strength: float) -> None:
    """A neutral grey world so material albedo is visible even if the USD
    lights import at an unusable intensity."""
    world = bpy.data.worlds.get("PreviewWorld") or bpy.data.worlds.new("PreviewWorld")
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.55, 0.58, 0.62, 1.0)
    bg.inputs["Strength"].default_value = strength
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bpy.context.scene.world = world


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--samples", type=int, default=32)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--world-strength", type=float, default=1.0)
    ap.add_argument(
        "--no-scene-lights", action="store_true",
        help="disable the lights that came from the USD and light with the "
             "world only -- isolates material response from light intensity",
    )
    ap.add_argument("--light-scale", type=float, default=None,
                    help="multiply every imported light's energy by this")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    setup_render(args.samples, (args.width, args.height))
    ensure_world_light(args.world_strength)

    lights = [o for o in bpy.context.scene.objects if o.type == "LIGHT"]
    print(f"scene lights: {len(lights)}")
    for lt in lights[:12]:
        print(f"   {lt.name}: type={lt.data.type} energy={lt.data.energy:.4g}")
    if args.no_scene_lights:
        for lt in lights:
            lt.hide_render = True
        print("scene lights disabled for this render")
    elif args.light_scale is not None:
        for lt in lights:
            lt.data.energy *= args.light_scale
        print(f"scaled {len(lights)} light(s) by {args.light_scale}")

    lo, hi = scene_bounds()
    centre = (lo + hi) / 2.0
    size = (hi - lo)
    radius = max(size.x, size.y) * 0.75
    print(f"scene bounds: {tuple(round(v, 2) for v in lo)} .. "
          f"{tuple(round(v, 2) for v in hi)}  size={tuple(round(v, 2) for v in size)}")

    views: list[tuple[str, Vector, Vector]] = [
        ("overview", centre + Vector((radius, -radius, radius * 0.9)), centre),
        ("interior", Vector((centre.x, lo.y + size.y * 0.15, lo.z + 1.7)),
         Vector((centre.x, hi.y, lo.z + 1.6))),
        ("racks", centre + Vector((-radius * 0.45, -radius * 0.45, size.z * 0.55)),
         centre - Vector((0, 0, size.z * 0.2))),
    ]

    scene = bpy.context.scene
    for name, loc, target in views:
        cam = make_camera(f"preview_{name}", loc, target)
        scene.camera = cam
        scene.render.filepath = str(args.out / f"{name}.png")
        print(f"rendering {name} -> {scene.render.filepath}")
        bpy.ops.render.render(write_still=True)

    # Also render from each camera that came in with the USD.
    usd_cams = [o for o in scene.objects
                if o.type == "CAMERA" and not o.name.startswith("preview_")]
    for cam in usd_cams[:2]:
        scene.camera = cam
        scene.render.filepath = str(args.out / f"usdcam_{cam.name}.png")
        print(f"rendering {cam.name} -> {scene.render.filepath}")
        bpy.ops.render.render(write_still=True)

    print(f"\nwrote {len(list(args.out.glob('*.png')))} image(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
