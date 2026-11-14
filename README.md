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

## Honest scope
A Blender render of the Isaac warehouse with a LiDAR *visualisation* (material override on a dedicated view layer). Not an Isaac Sim sensor run, no point-cloud output. 54 source textures lived on an Omniverse Nucleus server and were not localised.

## Rebuild the page
`python tools/build_page.py`
