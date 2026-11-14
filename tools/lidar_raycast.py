"""Blender script: cast the LiDAR's own 480 x 40 rays against the scene along the robot path.

blender -b warehouse2_Lidar.blend --python tools/lidar_raycast.py -- out/lidar_raw.npz [frame_step]

Ray model (from data/lidar_params.json): 480 azimuth steps over 360 deg, 40 elevation steps from
-38 deg to +14 deg about the sensor's local horizontal plane, max range 14 m. One full ring per sampled
frame (a snapshot scan, not a rotating sweep). The robot's own meshes are hidden so beams do not hit it.

Output npz: pts (N,3) world xyz, frame (N,), rng (N,), inten (N,) = |cos(incidence)|, plus per-scan stats.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time

import bpy
import numpy as np
from mathutils import Vector

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("lidar_raycast")

F0, F1 = 120, 800
AZ, EL = 480, 40
FOV_UP, FOV_DOWN, MAX_RANGE = math.radians(14.0), math.radians(38.0), 14.0


def _local_dirs() -> np.ndarray:
    az = np.linspace(0, 2 * math.pi, AZ, endpoint=False)
    el = np.linspace(-FOV_DOWN, FOV_UP, EL)
    a, e = np.meshgrid(az, el)
    return np.stack([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)], -1).reshape(-1, 3)


def _hide_robot() -> int:
    robot = bpy.data.objects["robot"]
    n = 0
    for o in robot.children_recursive:
        if o.type == "MESH":
            o.hide_viewport = True
            n += 1
    return n


def main(out_path: str, step: int) -> None:
    scene = bpy.context.scene
    sensor = bpy.data.objects["LiDAR_Sensor"]
    dirs_local = _local_dirs()
    log.info("hidden robot meshes: %d, rays/scan: %d", _hide_robot(), len(dirs_local))

    pts, frames, rngs, inten, stats = [], [], [], [], []
    t0 = time.time()
    for f in range(F0, F1 + 1, step):
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        m = sensor.matrix_world
        rot = np.array(m.to_3x3())
        origin = m.translation.copy()
        dirs = dirs_local @ rot.T
        if f == F0:
            log.info("sensor up vector at start: %s", tuple(round(v, 3) for v in rot[:, 2]))
        hit_p, hit_r, hit_i = [], [], []
        for d in dirs:
            ok, loc, nrm, _idx, _obj, _mat = scene.ray_cast(dg, origin, Vector(d), distance=MAX_RANGE)
            if ok:
                hit_p.append(tuple(loc))
                hit_r.append((loc - origin).length)
                hit_i.append(abs(float(nrm.dot(Vector(-d)))))
        n = len(hit_p)
        if n:
            pts.append(np.array(hit_p, np.float32))
            frames.append(np.full(n, f, np.uint16))
            rngs.append(np.array(hit_r, np.float32))
            inten.append(np.array(hit_i, np.float32))
        stats.append(dict(frame=f, rays=len(dirs), hits=n, hit_rate=round(n / len(dirs), 4),
                          mean_range_m=round(float(np.mean(hit_r)), 3) if n else 0.0,
                          max_range_m=round(float(np.max(hit_r)), 3) if n else 0.0))
        log.info("frame %d: %d/%d hits (%.0fs)", f, n, len(dirs), time.time() - t0)

    np.savez_compressed(out_path, pts=np.concatenate(pts), frame=np.concatenate(frames),
                        rng=np.concatenate(rngs), inten=np.concatenate(inten),
                        stats=json.dumps(stats))
    log.info("wrote %s", out_path)


if __name__ == "__main__":
    try:
        a = sys.argv[sys.argv.index("--") + 1:]
        main(a[0], int(a[1]) if len(a) > 1 else 4)
    except Exception:
        log.exception("raycast failed")
        raise
