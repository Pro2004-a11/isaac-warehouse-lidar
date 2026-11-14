"""Blender script: render the stills and clip frames used by the page.

blender -b warehouse2_Lidar.blend --python tools/render_media.py -- <out_dir> [step] [samples]
Writes <out_dir>/stills/*.png and <out_dir>/frames_*/NNNN.png (Blender writes PNG whatever the name).
Encode with tools/encode_media.py.
"""
from __future__ import annotations

import logging
import sys
import time

import bpy
from mathutils import Vector

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("render_media")


def _gpu() -> None:
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "OPTIX"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type != "CPU"
    bpy.context.scene.cycles.device = "GPU"


def _camera(scene, name, loc, target, lens=20):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = loc
    obj.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return obj


def main(out: str, step: int, samples: int) -> None:
    scene = bpy.context.scene
    _gpu()
    scene.cycles.samples, scene.cycles.use_denoising = samples, True
    for vl in scene.view_layers:  # the shipped file has the beauty layer disabled
        vl.use = True
    robot = bpy.data.objects["robot"]
    _camera(scene, "Aisle_Cam", (-3.9, -6.0, 1.6), (-2.5, -1.5, 0.3))
    top = _camera(scene, "Top_Cam", (-3.0, -1.5, 3.0), (-2.5, -1.5, 0.0))

    def shot(cam, layer, path, w, h, frame):
        scene.camera = bpy.data.objects[cam] if isinstance(cam, str) else cam
        scene.frame_set(frame)
        scene.render.resolution_x, scene.render.resolution_y = w, h
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True, layer=layer)

    t0 = time.time()
    for fr in range(120, 801, step):
        n = f"{(fr - 120) // step:04d}"
        shot("Aisle_Cam", "ViewLayer", f"{out}/frames_room_beauty/{n}", 1280, 720, fr)
        p = robot.matrix_world.translation
        top.location, top.rotation_euler = (p.x + 0.2, p.y, 3.0), (0, 0, 0)
        shot(top, "ViewLayer", f"{out}/frames_top_beauty/{n}", 1280, 720, fr)
        shot("Room_Camera", "LiDAR", f"{out}/frames_room_lidar/{n}", 1280, 720, fr)
        shot("Scan_Camera", "LiDAR", f"{out}/frames_scan_lidar/{n}", 720, 720, fr)
        shot("Scan_Camera", "ViewLayer", f"{out}/frames_scan_beauty/{n}", 720, 720, fr)
        log.info("frame %d (%.0fs)", fr, time.time() - t0)


if __name__ == "__main__":
    try:
        a = sys.argv[sys.argv.index("--") + 1:]
        main(a[0], int(a[1]) if len(a) > 1 else 8, int(a[2]) if len(a) > 2 else 12)
    except Exception:
        log.exception("render failed")
        raise
