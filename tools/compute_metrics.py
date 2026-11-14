"""Turn data/_raw.json (from extract_run_data.py) into the files the page and README use.

Writes data/robot_path.csv, data/metrics.json, data/lidar_params.json, data/scene_stats.json.
Usage: python tools/compute_metrics.py [data/_raw.json]
"""
from __future__ import annotations

import csv
import json
import logging
import math
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("compute_metrics")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FPS = 24


def _unwrap(yaw: list[float]) -> list[float]:
    out = [yaw[0]]
    for v in yaw[1:]:
        out.append(out[-1] + (v - out[-1] + 180) % 360 - 180)
    return out


def main(raw_path: Path) -> None:
    raw = json.loads(raw_path.read_text())
    rows = raw["rows"]

    with open(DATA / "robot_path.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow("frame,t_s,robot_x,robot_y,robot_z,robot_yaw_deg,sensor_x,sensor_y,sensor_z".split(","))
        for r in rows:
            w.writerow([r[0], round((r[0] - rows[0][0]) / FPS, 3)] + r[1:])

    speed, cum = [], [0.0]
    for a, b in zip(rows, rows[1:]):
        d = math.hypot(b[1] - a[1], b[2] - a[2])
        speed.append(d * FPS)
        cum.append(cum[-1] + d)
    yaw = _unwrap([r[4] for r in rows])
    xs, ys = [r[1] for r in rows], [r[2] for r in rows]
    sd = [math.hypot(r[5] - r[1], r[6] - r[2]) for r in rows]
    turn = ys.index(min(ys))
    n = len(rows) - 1

    metrics = dict(
        frames=[rows[0][0], rows[-1][0]], fps=FPS, duration_s=round(n / FPS, 2),
        path_length_m=round(cum[-1], 3), mean_speed_mps=round(sum(speed) / len(speed), 3),
        max_speed_mps=round(max(speed), 3),
        x_range_m=[round(min(xs), 2), round(max(xs), 2)], y_range_m=[round(min(ys), 2), round(max(ys), 2)],
        extent_m=[round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)],
        net_displacement_m=round(math.hypot(rows[-1][1] - rows[0][1], rows[-1][2] - rows[0][2]), 3),
        turnaround_frame=rows[turn][0], turnaround_y_m=round(ys[turn], 3),
        yaw_total_rotation_deg=round(sum(abs(b - a) for a, b in zip(yaw, yaw[1:])), 1),
        sensor_height_above_robot_m=round(rows[0][7] - rows[0][3], 3),
        sensor_xy_offset_m=[round(min(sd), 3), round(max(sd), 3)],
        stops=[], speed_series=[round(s, 3) for s in speed[::4]], cum_series=[round(c, 3) for c in cum[::4]],
    )
    (DATA / "metrics.json").write_text(json.dumps(metrics, indent=1))

    p = raw["params"]
    lidar = dict(
        max_range_m=p["Max Range"], range_fade_start_m=p["Range Fade Start"],
        fov_up_deg=round(math.degrees(p["FOV Up"]), 1), fov_down_deg=round(math.degrees(p["FOV Down"]), 1),
        beam_width_deg=round(math.degrees(p["Beam Width"]), 2),
        trail_angle_deg=round(math.degrees(p["Trail Angle"]), 1),
        azimuth_divisions=int(p["Azimuth Divisions"]), elevation_divisions=int(p["Elevation Divisions"]),
        dot_duty=p["Dot Duty"], dot_fade_m=[p["Dot Fade Start"], p["Dot Fade End"]],
        beam_boost=p["Beam Boost"], trail_boost=p["Trail Boost"], exposure=p["Exposure"],
        camera=raw["cam"], render=raw["render"], raw=p,
    )
    (DATA / "lidar_params.json").write_text(json.dumps(lidar, indent=1))

    s = raw["scene"]
    stats = dict(fps=FPS, f0=rows[0][0], f1=rows[-1][0], engine="CYCLES", **s,
                 path=[[r[0], r[1], r[2], r[5], r[6]] for r in rows[::4]])
    (DATA / "scene_stats.json").write_text(json.dumps(stats))
    log.info("wrote metrics: %.2f m in %.1f s, turnaround f%d", cum[-1], n / FPS, rows[turn][0])


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / "_raw.json")
    except Exception:
        log.exception("compute failed")
        raise
