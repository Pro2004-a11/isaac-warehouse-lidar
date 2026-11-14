"""Encode rendered frame folders into docs/media MP4s and small GIFs with ffmpeg.

Usage: python tools/encode_media.py <frames_root> [fps]
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("encode_media")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "media"
CLIPS = {"frames_room_beauty": "aisle", "frames_top_beauty": "topdown", "frames_room_lidar": "lidar_room",
         "frames_scan_lidar": "sensor_lidar", "frames_scan_beauty": "sensor_beauty"}
GIF_WIDTH = {"aisle": 560, "topdown": 480, "lidar_room": 560}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main(frames_root: Path, fps: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for folder, name in CLIPS.items():
        src = frames_root / folder
        if not src.exists():
            log.warning("skip %s (missing)", folder)
            continue
        mp4 = OUT / f"{name}.mp4"
        run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(src / "%04d.png"),
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "-c:v", "libx264", "-crf", "24",
             "-preset", "slow", "-movflags", "+faststart", str(mp4)])
        if name in GIF_WIDTH:
            w = GIF_WIDTH[name]
            run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-vf",
                 f"fps=8,scale={w}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=64[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                 str(OUT / f"{name}.gif")])
        log.info("encoded %s", name)


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 12)
    except Exception:
        log.exception("encode failed")
        raise
