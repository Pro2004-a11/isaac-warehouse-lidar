"""Blender script: dump per-frame robot/sensor transforms and LiDAR shader params.

blender -b warehouse2_Lidar.blend --python tools/extract_run_data.py -- data/_raw.json
Then: python tools/compute_metrics.py
"""
from __future__ import annotations

import json
import logging
import math
import sys

import bpy

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("extract_run_data")

F0, F1 = 120, 800


def main(out_path: str) -> None:
    scene = bpy.context.scene
    robot = bpy.data.objects["robot"]
    sensor = bpy.data.objects["LiDAR_Sensor"]
    cam = bpy.data.objects["Scan_Camera"]

    rows = []
    for f in range(F0, F1 + 1):
        scene.frame_set(f)
        m = robot.matrix_world
        p, e, q = m.translation, m.to_euler(), sensor.matrix_world.translation
        rows.append([f, round(p.x, 4), round(p.y, 4), round(p.z, 4), round(math.degrees(e.z), 2),
                     round(q.x, 4), round(q.y, 4), round(q.z, 4)])

    mat = bpy.data.materials["LiDAR_Pass"]
    group = next(n for n in mat.node_tree.nodes if n.type == "GROUP")
    params = {n.name: round(n.outputs[0].default_value, 4)
              for n in group.node_tree.nodes if n.type == "VALUE"}

    out = dict(
        rows=rows,
        params=params,
        cam=dict(lens=cam.data.lens, type=cam.data.type,
                 fisheye=getattr(cam.data, "panorama_type", None)),
        render=dict(res=[scene.render.resolution_x, scene.render.resolution_y],
                    fps=scene.render.fps, samples=scene.cycles.samples),
        scene=dict(objects=len(bpy.data.objects), meshes=len(bpy.data.meshes),
                   materials=len(bpy.data.materials), images=len(bpy.data.images),
                   lights=sum(1 for o in bpy.data.objects if o.type == "LIGHT"),
                   tris=sum(len(o.data.polygons) for o in bpy.data.objects
                            if o.type == "MESH" and o.data),
                   apriltags=sorted(o.name for o in bpy.data.objects
                                    if o.name.startswith("apriltag_") and o.name != "apriltag_board")),
    )
    with open(out_path, "w") as fh:
        json.dump(out, fh)
    log.info("wrote %s (%d frames)", out_path, len(rows))


if __name__ == "__main__":
    try:
        main(sys.argv[sys.argv.index("--") + 1])
    except Exception:
        log.exception("extract failed")
        raise
