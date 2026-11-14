"""Voxel-downsample the raw LiDAR scan, write a PLY, and render a 360 deg orbit clip.

python tools/pointcloud_orbit.py out/lidar_raw.npz [--frames 240] [--voxel 0.02]

Writes (local, not committed): out/lidar_scan.ply
Writes (committed): data/pointcloud_stats.json, docs/media/pointcloud_orbit.{mp4,gif},
                    docs/media/stills/pointcloud_oblique.jpg
Pure numpy z-buffer renderer: points far-to-near, 2x2 px each, coloured by height, shaded by incidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from matplotlib import colormaps
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("pointcloud_orbit")

ROOT = Path(__file__).resolve().parent.parent
W, H, FOCAL = 1280, 720, 1050.0
BG = np.array([7, 9, 10], np.uint8)


def voxelise(pts: np.ndarray, inten: np.ndarray, voxel: float) -> tuple[np.ndarray, np.ndarray]:
    keys = np.floor(pts / voxel).astype(np.int64)
    _, idx = np.unique(keys, axis=0, return_index=True)
    return pts[idx], inten[idx]


def colours(pts: np.ndarray, inten: np.ndarray) -> np.ndarray:
    z = np.clip((pts[:, 2] - pts[:, 2].min()) / max(float(np.ptp(pts[:, 2])), 1e-6), 0, 1)
    rgb = colormaps["turbo"](0.08 + 0.84 * z)[:, :3]
    shade = 0.55 + 0.45 * np.clip(inten, 0, 1)
    return np.clip(rgb * shade[:, None] * 255, 0, 255).astype(np.uint8)


def write_ply(path: Path, pts: np.ndarray, rgb: np.ndarray, inten: np.ndarray) -> None:
    rec = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                    ("red", "u1"), ("green", "u1"), ("blue", "u1"), ("intensity", "<f4")])
    rec["x"], rec["y"], rec["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
    rec["red"], rec["green"], rec["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    rec["intensity"] = inten
    header = ("ply\nformat binary_little_endian 1.0\n"
              f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nproperty float intensity\n"
              "end_header\n")
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(rec.tobytes())


def render(pts, rgb, target, radius, theta, phi, path_xyz, label) -> Image.Image:
    c = target + radius * np.array([math.cos(phi) * math.cos(theta), math.cos(phi) * math.sin(theta), math.sin(phi)])
    fwd = target - c
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)

    def project(p):
        d = p - c
        z = d @ fwd
        return W / 2 + FOCAL * (d @ right) / z, H / 2 - FOCAL * (d @ up) / z, z

    u, v, z = project(pts)
    ok = (z > 0.3) & (u >= 0) & (u < W - 2) & (v >= 0) & (v < H - 2)
    order = np.argsort(-z[ok])
    ui, vi, col = u[ok][order].astype(np.int32), v[ok][order].astype(np.int32), rgb[ok][order]
    img = np.empty((H, W, 3), np.uint8)
    img[:] = BG
    for dy in (0, 1):
        for dx in (0, 1):
            img[vi + dy, ui + dx] = col
    im = Image.fromarray(img)
    dr = ImageDraw.Draw(im)
    pu, pv, pz = project(path_xyz)
    seg = [(float(a), float(b)) for a, b, k in zip(pu, pv, pz) if k > 0.3]
    if len(seg) > 1:
        dr.line(seg, fill=(93, 255, 160), width=3)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    dr.text((24, 20), label, fill=(230, 239, 233), font=font)
    dr.text((24, H - 38), f"orbit {math.degrees(theta) % 360:5.1f} deg   green = sensor path", fill=(138, 154, 146), font=font)
    return im


def main(raw: Path, n_frames: int, voxel: float) -> None:
    d = np.load(raw)
    pts_all, inten_all = d["pts"], d["inten"]
    stats = json.loads(str(d["stats"]))
    pts, inten = voxelise(pts_all, inten_all, voxel)
    rgb = colours(pts, inten)
    log.info("raw %d pts -> %d after %.0f cm voxels", len(pts_all), len(pts), voxel * 100)

    ply = ROOT / "out" / "lidar_scan.ply"
    write_ply(ply, pts, rgb, inten)

    with open(ROOT / "data" / "robot_path.csv") as fh:
        rows = list(csv.DictReader(fh))
    path_xyz = np.array([[float(r["sensor_x"]), float(r["sensor_y"]), float(r["sensor_z"])] for r in rows])

    lo, hi = pts.min(0), pts.max(0)
    target = (lo + hi) / 2
    radius = float(np.linalg.norm(hi - lo)) * 0.62
    label = f"LiDAR point cloud  {len(pts):,} pts  ({len(stats)} scans x 19,200 rays)"

    out_media = ROOT / "docs" / "media"
    (out_media / "stills").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n_frames):
            th = 2 * math.pi * i / n_frames + math.radians(35)
            render(pts, rgb, target, radius, th, math.radians(28), path_xyz, label).save(f"{tmp}/{i:04d}.png")
            if i == 0:
                render(pts, rgb, target, radius, th, math.radians(28), path_xyz, label).convert("RGB").save(
                    out_media / "stills" / "pointcloud_oblique.jpg", quality=90)
            if i % 40 == 0:
                log.info("rendered %d/%d", i, n_frames)
        mp4 = out_media / "pointcloud_orbit.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "24", "-i", f"{tmp}/%04d.png",
                        "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "31", "-preset", "slow",
                        "-movflags", "+faststart", str(mp4)], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-vf",
                        "fps=8,scale=560:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                        str(out_media / "pointcloud_orbit.gif")], check=True)

    hit = [s["hit_rate"] for s in stats]
    summary = dict(
        scans=len(stats), rays_per_scan=stats[0]["rays"], frame_step=stats[1]["frame"] - stats[0]["frame"],
        raw_points=int(len(pts_all)), voxel_m=voxel, points_after_voxel=int(len(pts)),
        mean_hit_rate=round(float(np.mean(hit)), 4),
        mean_range_m=round(float(d["rng"].mean()), 3), max_range_m=round(float(d["rng"].max()), 3),
        extent_m=[round(float(x), 2) for x in (hi - lo)], bbox_min=[round(float(x), 2) for x in lo],
        bbox_max=[round(float(x), 2) for x in hi], ply_mb=round(ply.stat().st_size / 1e6, 1),
        model="480 az x 40 el, 360 deg ring per scan, -38/+14 deg, 14 m max, robot meshes hidden",
        per_scan=[[s["frame"], s["hits"], s["mean_range_m"]] for s in stats],
    )
    (ROOT / "data" / "pointcloud_stats.json").write_text(json.dumps(summary, indent=1))
    log.info("done: %d pts, ply %.1f MB", len(pts), summary["ply_mb"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", type=Path)
    ap.add_argument("--frames", type=int, default=240)
    ap.add_argument("--voxel", type=float, default=0.02)
    a = ap.parse_args()
    try:
        main(a.raw, a.frames, a.voxel)
    except Exception:
        log.exception("orbit failed")
        raise
