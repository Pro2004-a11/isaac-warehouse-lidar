"""Build docs/index.html from data/scene_stats.json + the rendered media folder.

Usage: python tools/build_page.py   (run from the repo root)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_page")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

TEMPLATES = [("omni_pbr", 73), ("constant", 12), ("ue4_basic", 5), ("ue4_mask_lerp", 5),
             ("ue4_desat_tint", 3), ("ue4_color_mask4", 2), ("ue4_emissive_from_orm_alpha", 1)]

CURATED = [
    ("Room_Camera_beauty_f300", "Room camera, frame 300: robot vacuum between the racks and crates"),
    ("Aisle_Cam_beauty_f150", "Aisle camera, frame 150: far-end view down the scan route"),
    ("Room_Camera_lidar_f300", "Same room through the LiDAR layer, frame 300"),
    ("Room_Camera_lidar_f600", "LiDAR layer, frame 600, on the way back"),
    ("Scan_Camera_lidar_f450", "Sensor-mounted fisheye, LiDAR layer, frame 450"),
    ("Scan_Camera_beauty_f450", "Sensor-mounted fisheye, beauty, frame 450"),
]

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Isaac Warehouse LiDAR</title>
<style>
:root{--bg:#07090a;--fg:#e6efe9;--mut:#8a9a92;--acc:#5dffa0;--acc2:#2ad4ff;--card:#0e1412;--line:#1b2a24}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px}
.mono{font-family:ui-monospace,Consolas,monospace}
header.hero{position:relative;min-height:88vh;display:grid;align-items:end;overflow:hidden;border-bottom:1px solid var(--line)}
header.hero video{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.55}
header.hero:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,transparent 30%,var(--bg) 98%)}
.hero .wrap{position:relative;z-index:2;padding-bottom:56px}
.tag{color:var(--acc);letter-spacing:.18em;text-transform:uppercase;font-size:12px}
h1{font-size:clamp(40px,8vw,92px);line-height:.95;margin:.2em 0;letter-spacing:-.03em}
h1 em{font-style:normal;color:var(--acc)}
.lede{max-width:60ch;color:var(--mut);font-size:18px}
section{padding:72px 0;border-bottom:1px solid var(--line)}
h2{font-size:clamp(26px,4vw,40px);margin:0 0 8px;letter-spacing:-.02em}
.sub{color:var(--mut);max-width:70ch;margin:0 0 28px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.stat b{display:block;font-size:30px;color:var(--acc);font-variant-numeric:tabular-nums}
.stat span{color:var(--mut);font-size:13px}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.fig{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.fig video,.fig img{display:block;width:100%;height:auto}
.fig figcaption{padding:10px 14px;color:var(--mut);font-size:13px}
.gal{columns:3 300px;column-gap:12px}
.gal .fig{break-inside:avoid;margin-bottom:12px;cursor:zoom-in}
.gal img{transition:transform .3s}.gal .fig:hover img{transform:scale(1.03)}
.gal .fig{overflow:hidden}
.bars .row{display:grid;grid-template-columns:200px 1fr 40px;gap:10px;align-items:center;margin:6px 0;font-size:14px}
.bars .bar{height:10px;border-radius:5px;background:linear-gradient(90deg,var(--acc),var(--acc2))}
#path{width:100%;max-width:520px;height:auto;display:block;margin:0 auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
.pipe{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;counter-reset:s}
.pipe div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;counter-increment:s}
.pipe div:before{content:"0" counter(s);color:var(--acc);font-family:ui-monospace,Consolas,monospace}
.pipe b{display:block;margin:4px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}td{padding:10px 14px;border-bottom:1px solid var(--line);font-size:14px}td:first-child{color:var(--mut);width:34%}a{color:var(--acc2)}
.honest{border:1px dashed var(--acc);border-radius:10px;padding:16px;color:var(--mut)}
footer{padding:40px 0;color:var(--mut);font-size:13px}
#lb{position:fixed;inset:0;background:#000d;display:none;place-items:center;z-index:9;cursor:zoom-out}
#lb img{max-width:96vw;max-height:94vh;border-radius:8px}
@media(max-width:700px){.duo{grid-template-columns:1fr}.bars .row{grid-template-columns:120px 1fr 32px}}
@media(prefers-reduced-motion:reduce){header.hero video{display:none}}
</style></head><body>
<header class="hero">
<video autoplay muted loop playsinline poster="media/stills/Room_Camera_beauty_f300.jpg"><source src="media/lidar_room.mp4" type="video/mp4"></video>
<div class="wrap"><div class="tag mono">Isaac Sim 5.0 → Blender 5.0 · Cycles</div>
<h1>A robot, a warehouse,<br>and a <em>LiDAR wave</em>.</h1>
<p class="lede">Digital Twin Warehouse exported from Isaac Sim USD to Blender with materials intact, then scanned by an animated LiDAR sensor riding a robot vacuum. Every image, clip and number below comes from the source scene.</p></div>
</header>

<section><div class="wrap">
<h2>The scene in numbers</h2><p class="sub">Read straight from <span class="mono">warehouse2_Lidar.blend</span> and the exporter report.</p>
<div class="stats" id="stats"></div></div></section>

<section><div class="wrap">
<h2>Same frame, two sensors</h2><p class="sub">Left: ordinary beauty render. Right: the same instant through the LiDAR view layer, where a material override paints the scan wave across every surface.</p>
<div class="duo">
<figure class="fig"><video autoplay muted loop playsinline src="media/aisle.mp4"></video><figcaption>Aisle camera · beauty · frames 120–800</figcaption></figure>
<figure class="fig"><video autoplay muted loop playsinline src="media/lidar_room.mp4"></video><figcaption>Room camera · LiDAR layer (<span class="mono">LiDAR_Pass</span> override)</figcaption></figure>
<figure class="fig"><video autoplay muted loop playsinline src="media/topdown.mp4"></video><figcaption>Top-down follow · robot path</figcaption></figure>
<figure class="fig"><video autoplay muted loop playsinline src="media/sensor_lidar.mp4"></video><figcaption>Sensor-mounted camera · LiDAR layer · 1920² source</figcaption></figure>
</div></div></section>

<section><div class="wrap">
<h2>Robot path</h2><p class="sub">Robot and sensor positions sampled every 4 frames from the animation (metres, scene XY). The sensor is parented to the robot, about 12 cm off its centre.</p>
<svg id="path" viewBox="0 0 520 640" role="img" aria-label="Robot path"></svg></div></section>

<section><div class="wrap">
<h2>The run, in numbers</h2><p class="sub">Computed from the per-frame robot transform (680 frames, 28.3 s at 24 fps). Raw data: <a href="data/robot_path.csv">robot_path.csv</a>, <a href="data/metrics.json">metrics.json</a>.</p>
<div class="stats" id="mstats"></div>
<p class="sub" style="margin-top:20px">The robot accelerates from 0.12 to 0.69 m/s over frames 120&ndash;449, reaching y = &minus;4.95 m. At frame 449 it reverses with no deceleration (speed drops from 0.69 to 0.29 m/s in one frame; this is an animation keyframe step, not a physical turn) and returns at a steady 0.29 m/s. Heading changes only 12.9&deg; over the whole run, so it reverses along one axis rather than turning round (inferred from the yaw track).</p>
<svg id="spd" viewBox="0 0 900 220" role="img" aria-label="Speed and distance over time" style="width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px"></svg>
</div></section>

<section><div class="wrap">
<h2>The LiDAR sensor model</h2><p class="sub">The on-screen scan look is a procedural shader. These values are read from the <span class="mono">LiDAR Wave</span> node group in <span class="mono">LiDAR_Pass</span> (<a href="data/lidar_params.json">lidar_params.json</a>).</p>
<table id="lt"></table></div></section>

<section><div class="wrap">
<h2>Real LiDAR point cloud</h2>
<p class="sub">The shader above is only a look. To get actual data, the sensor's own rays (480 &times; 40, &minus;38&deg; to +14&deg;, 14 m) were cast against the scene geometry at 171 frames along the robot path, hits merged into one cloud and voxel-filtered. Colour is height, brightness is incidence angle, the green line is the sensor path.</p>
<figure class="fig"><video autoplay muted loop playsinline controls poster="media/stills/pointcloud_oblique.jpg" src="media/pointcloud_orbit.mp4"></video><figcaption>360&deg; orbit around the accumulated scan</figcaption></figure>
<div class="stats" id="pcstats" style="margin-top:16px"></div>
<p class="sub" style="margin-top:16px">Method notes: each sampled frame is a full 360&deg; snapshot ring, not a rotating sweep. The robot's own meshes are hidden so beams do not hit it. The room is closed, so every ray hits something; real sensors lose returns on glass, dark or distant surfaces and add noise, none of which is modelled. Data: <a href="data/pointcloud_stats.json">pointcloud_stats.json</a>. The PLY itself is generated locally by <span class="mono">tools/lidar_raycast.py</span> and <span class="mono">tools/pointcloud_orbit.py</span> and is not in the repo because it is sampled from NVIDIA's scene geometry.</p>
</div></section>

<section><div class="wrap">
<h2>SLAM</h2>
<p class="honest">No SLAM was run for this demo, so there is no SLAM trajectory, map or error figure here. What exists is the ground-truth robot path above (exact, from the animation) and five AprilTags in the scene (ids 14, 15, 16, 18, 19) that a SLAM or tag-based localisation test could use. The next step would be to run scan matching or visual tracking on the point-cloud scans or the Scan_Camera frames and compare the estimated trajectory against <span class="mono">robot_path.csv</span>.</p></div></section>

<section><div class="wrap">
<h2>Gallery</h2><p class="sub">Click any frame to enlarge.</p>
<div class="gal" id="gal"></div></div></section>

<section><div class="wrap">
<h2>Materials converted</h2><p class="sub">101 Omniverse MDL materials rebuilt as Blender node graphs; 63 carry textures, 38 are constants only.</p>
<div class="bars" id="bars"></div></div></section>

<section><div class="wrap">
<h2>How the demo was made</h2>
<p class="sub">Two halves: getting Isaac Sim's warehouse into Blender without losing its look, then adding a LiDAR that looks like a LiDAR. Code for the first half is in <span class="mono">tools/</span> and <span class="mono">addon/</span>. The second half lives in the .blend, which is not in this repo.</p>
<div class="pipe">
<div><b>Flatten the USD</b>The warehouse is <span class="mono">World0.usd</span> from Isaac Sim 5.0 (Digital Twin Warehouse). <span class="mono">usd_flatten_collect.py</span> flattens the stage into one <span class="mono">warehouse.usdc</span> and copies every texture it can find (88 collected, 0 copy failures).</div>
<div><b>Read the MDL materials</b>Omniverse materials are MDL. <span class="mono">mdl_parse.py</span> pulls texture paths and parameters out of the MDL files so they can be rebuilt, rather than guessed from a viewport colour.</div>
<div><b>Map to Blender shaders</b><span class="mono">material_spec.py</span> classifies each material into a template (73 omni_pbr, 12 constant, 5 ue4_basic, 5 ue4_mask_lerp, 3 ue4_desat_tint, 2 ue4_color_mask4, 1 ue4_emissive) and <span class="mono">base_materials.py</span> builds the matching Principled BSDF graph.</div>
<div><b>Import and patch</b><span class="mono">blender_import.py</span> (or the <span class="mono">isaac_usd_importer</span> add-on) imports geometry and applies the graphs. <span class="mono">overrides.json</span> patches what USD cannot supply, such as the chain-link fence opacity (made by <span class="mono">make_chainlink_alpha.py</span>).</div>
<div><b>Verify</b><span class="mono">verify_export.py</span> and <span class="mono">render_check.py</span> compare the export with the source, with lit and unlit previews. Result: 101 materials converted, 63 textured, 38 constants, 15 left as authored.</div>
<div><b>Add robot and sensor</b>A robot-vacuum model animated along the aisle (<span class="mono">robotAction</span>, frames 0&ndash;800). A <span class="mono">LiDAR_Sensor</span> empty is parented to it, with its own action from frame 120. A fisheye camera (<span class="mono">Scan_Camera</span>, equisolid, 21 mm) sits at the sensor.</div>
<div><b>Paint the scan</b>A second view layer, <span class="mono">LiDAR</span>, forces the <span class="mono">LiDAR_Pass</span> material on everything. Its shader turns distance and angle from the sensor into a ring-and-dot pattern: 480 &times; 40 returns, 14 m range, 14&deg; up and 38&deg; down, with a 140&deg; trailing glow behind the sweep.</div>
<div><b>Render</b>Cycles with the OptiX denoiser on an RTX 4070 Ti. Each clip frame is rendered once per camera and layer, then encoded to MP4 and GIF with ffmpeg. Clips use every 8th frame (86 frames at 12 fps), so they play faster than real time.</div>
</div>
<p class="honest" style="margin-top:24px"><b>Honest labels.</b> A Blender render of the Isaac warehouse with an animated LiDAR visualisation. Not an Isaac Sim sensor run. The point cloud comes from Blender ray casts, not Isaac's RTX LiDAR. 54 source textures lived on an Omniverse Nucleus server and were not localised, so those materials fall back to constants. Clip frames were rendered at 12 samples for speed.</p>
</div></section>

<footer><div class="wrap">NVIDIA warehouse assets and textures are not included in this repo and keep their own licence.</div></footer>
<div id="lb"><img alt=""></div>
<script>
const D=__DATA__, T=__TEMPL__, G=__GAL__;
const $=s=>document.querySelector(s);
const M=__MET__, L=__LID__, PC=__PC__;
$("#pcstats").innerHTML=[["scans",PC.scans],["raw points",(PC.raw_points/1e6).toFixed(2)+"M"],["after "+(PC.voxel_m*100)+" cm voxels",(PC.points_after_voxel/1e3).toFixed(0)+"k"],["mean range",PC.mean_range_m+" m"],["max range",PC.max_range_m+" m"],["room extent",PC.extent_m.join(" x ")+" m"],["hit rate",(PC.mean_hit_rate*100).toFixed(0)+"%"],["PLY size",PC.ply_mb+" MB"]].map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
$("#mstats").innerHTML=[["path length",M.path_length_m+" m"],["duration",M.duration_s+" s"],["mean speed",M.mean_speed_mps+" m/s"],["max speed",M.max_speed_mps+" m/s"],["route extent",M.extent_m[1]+" x "+M.extent_m[0]+" m"],["net displacement",M.net_displacement_m+" m"],["total rotation",M.yaw_total_rotation_deg+"\u00b0"],["stops",M.stops.length]].map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
$("#lt").innerHTML=[["Max range",L.max_range_m+" m (fade from "+L.range_fade_start_m+" m)"],["Vertical FOV","+"+L.fov_up_deg+"\u00b0 / -"+L.fov_down_deg+"\u00b0"],["Returns",L.azimuth_divisions+" azimuth x "+L.elevation_divisions+" elevation"],["Beam width",L.beam_width_deg+"\u00b0"],["Trail",L.trail_angle_deg+"\u00b0 glow, x"+L.trail_boost+" (beam x"+L.beam_boost+")"],["Dot duty / fade",L.dot_duty+" / "+L.dot_fade_m[0]+"-"+L.dot_fade_m[1]+" m"],["Exposure",L.exposure],["Scan camera",L.camera.type+" "+L.camera.fisheye+", "+L.camera.lens.toFixed(1)+" mm, "+L.render.res.join("x")+" source"],["Sensor mount","parented to robot, "+M.sensor_xy_offset_m[0]+" m off centre, "+M.sensor_height_above_robot_m+" m up"]].map(([k,v])=>`<tr><td>${k}</td><td class="mono">${v}</td></tr>`).join("");
(()=>{const sp=M.speed_series,cu=M.cum_series,n=sp.length,W=900,H=220,pl=44,pb=26,mxs=Math.max(...sp)*1.15,mxc=Math.max(...cu);
const X=i=>pl+i/(n-1)*(W-pl-12),Ys=v=>H-pb-v/mxs*(H-pb-12),Yc=v=>H-pb-v/mxc*(H-pb-12);
const ln=(a,Y)=>a.map((v,i)=>(i?"L":"M")+X(i).toFixed(1)+" "+Y(v).toFixed(1)).join("");
$("#spd").innerHTML=`<path d="${ln(cu,Yc)}" fill="none" stroke="var(--acc2)" stroke-width="2"/><path d="${ln(sp,Ys)}" fill="none" stroke="var(--acc)" stroke-width="2"/><text x="${pl+6}" y="16" font-size="12" fill="var(--acc)" font-family="monospace">speed (max ${M.max_speed_mps} m/s)</text><text x="${pl+190}" y="16" font-size="12" fill="var(--acc2)" font-family="monospace">distance (${M.path_length_m} m)</text><text x="${pl}" y="${H-6}" font-size="11" fill="var(--mut)" font-family="monospace">0 s</text><text x="${W-60}" y="${H-6}" font-size="11" fill="var(--mut)" font-family="monospace">${M.duration_s} s</text>`;})();
const st=[["objects",D.objects],["meshes",D.meshes],["triangles",(D.tris/1e6).toFixed(2)+"M"],["materials",D.materials],["textures",D.images],["lights",D.lights],["AprilTags",D.apriltags.length],["frames",D.f0+"–"+D.f1+" @"+D.fps]];
$("#stats").innerHTML=st.map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
const mx=Math.max(...T.map(t=>t[1]));
$("#bars").innerHTML=T.map(([n,c])=>`<div class="row"><span class="mono">${n}</span><div class="bar" style="width:${c/mx*100}%"></div><span>${c}</span></div>`).join("");
$("#gal").innerHTML=G.map(([f,c])=>`<figure class="fig"><img loading="lazy" src="${f}" alt="${c}"><figcaption>${c}</figcaption></figure>`).join("");
document.querySelectorAll(".gal img").forEach(i=>i.onclick=()=>{$("#lb img").src=i.src;$("#lb").style.display="grid"});
$("#lb").onclick=()=>$("#lb").style.display="none";
(()=>{const P=D.path,xs=P.flatMap(p=>[p[1],p[3]]),ys=P.flatMap(p=>[p[2],p[4]]);
const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys),pad=40;
const s=Math.min((520-2*pad)/Math.max(x1-x0,.01),(640-2*pad)/Math.max(y1-y0,.01));
const ox=(520-(x1-x0)*s)/2,X=x=>ox+(x-x0)*s,Y=y=>640-pad-(y-y0)*s;
const line=P.map((p,i)=>(i?"L":"M")+X(p[1]).toFixed(1)+" "+Y(p[2]).toFixed(1)).join("");
const dots=P.filter((_,i)=>i%10==0).map(p=>`<circle cx="${X(p[1]).toFixed(1)}" cy="${Y(p[2]).toFixed(1)}" r="3" fill="var(--acc)"/><text x="${X(p[1])+8}" y="${Y(p[2])+4}" font-size="11" fill="var(--mut)" font-family="monospace">f${p[0]}</text>`).join("");
$("#path").innerHTML=`<path d="${line}" fill="none" stroke="var(--acc2)" stroke-width="2" opacity=".8"/>${dots}`;})();
</script></body></html>
"""


def build() -> None:
    data = json.loads((ROOT / "data" / "scene_stats.json").read_text())
    gal = []
    for name, cap in CURATED:
        gal.append([f"media/stills/{name}.jpg", cap])
    metrics = json.loads((ROOT / "data" / "metrics.json").read_text())
    lidar = json.loads((ROOT / "data" / "lidar_params.json").read_text())
    pc = json.loads((ROOT / "data" / "pointcloud_stats.json").read_text())
    html = (HTML.replace("__DATA__", json.dumps(data))
                .replace("__TEMPL__", json.dumps(TEMPLATES))
                .replace("__GAL__", json.dumps(gal))
                .replace("__MET__", json.dumps(metrics))
                .replace("__LID__", json.dumps(lidar))
                .replace("__PC__", json.dumps({k: v for k, v in pc.items() if k != "per_scan"})))
    (DOCS / "data").mkdir(exist_ok=True)
    for n in ("robot_path.csv", "metrics.json", "lidar_params.json", "scene_stats.json", "pointcloud_stats.json", "report.txt"):
        (DOCS / "data" / n).write_bytes((ROOT / "data" / n).read_bytes())
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    log.info("wrote docs/index.html (%d gallery items)", len(gal))


if __name__ == "__main__":
    try:
        build()
    except Exception:
        log.exception("build failed")
        raise
