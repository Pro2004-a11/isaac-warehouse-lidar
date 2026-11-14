# Isaac Warehouse LiDAR demo

NVIDIA Isaac Sim **Digital Twin Warehouse** exported to **Blender 5.0**, with an animated LiDAR sensor on a robot vacuum. Rendered in Cycles.

**Showcase page:** `docs/index.html` (open locally, or enable GitHub Pages on `/docs`).

## What is here
| Path | Purpose |
|---|---|
| `tools/` | USD flatten/collect, MDL parser, material spec, Blender import and verify scripts |
| `addon/isaac_usd_importer/` | Blender extension that imports Isaac USD stages with materials |
| `data/` | Exporter report, material table, per-frame robot/sensor path, scene stats |
| `docs/` | Showcase page plus rendered stills, MP4s and GIFs |

## What is not here
`.blend` and `.usdc` files (55 MB and 42 MB) and NVIDIA's warehouse assets and textures. Those keep NVIDIA's licence.

## Run summary (from `data/`)
- 680 frames (120-800 @ 24 fps) = 28.3 s; path 9.84 m; mean speed 0.35 m/s, max 0.69 m/s.
- Robot accelerates down the aisle to y = -4.95 m (frame 449), reverses instantly, returns at 0.29 m/s.
- LiDAR shader: 480 x 40 returns, 14 m range, +14 / -38 deg vertical FOV, 2.5 deg beam, 140 deg trail.
- Scene: 1,919 objects, 1.40 M triangles, 132 materials (101 converted from MDL), 5 AprilTags.
- **No SLAM was run.** `data/robot_path.csv` is the ground-truth path a SLAM test could be scored against.

## Pipeline
USD flatten and texture collect -> MDL parse -> material-template mapping -> Blender import with overrides -> verify against source -> add animated robot and sensor -> second view layer with a LiDAR material override -> Cycles render -> ffmpeg. Full write-up on the showcase page.

## Honest scope
A Blender render of the Isaac warehouse with a LiDAR *visualisation* (material override on a dedicated view layer). Not an Isaac Sim sensor run, no point-cloud output. 54 source textures lived on an Omniverse Nucleus server and were not localised.

## Rebuild the page
`python tools/build_page.py`
