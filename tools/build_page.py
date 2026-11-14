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
<p class="lede">Digital Twin Warehouse exported from Isaac Sim USD to Blender with materials intact, then scanned by an animated LiDAR sensor riding a robot vacuum. Everything below is rendered from the source scene.</p></div>
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
<h2>Gallery</h2><p class="sub">Click any frame to enlarge.</p>
<div class="gal" id="gal"></div></div></section>

<section><div class="wrap">
<h2>Materials converted</h2><p class="sub">101 Omniverse MDL materials rebuilt as Blender node graphs; 63 carry textures, 38 are constants only.</p>
<div class="bars" id="bars"></div></div></section>

<section><div class="wrap">
<h2>Pipeline</h2><p class="sub">Code in this repo: <span class="mono">tools/</span> (CLI) and <span class="mono">addon/isaac_usd_importer</span> (Blender extension).</p>
<div class="pipe">
<div><b>Flatten &amp; collect</b>usd_flatten_collect.py resolves the USD stage and gathers textures.</div>
<div><b>Parse MDL</b>mdl_parse.py reads Omniverse MDL parameters into a material spec.</div>
<div><b>Build node graphs</b>material_spec + base_materials map each template to Principled BSDF graphs.</div>
<div><b>Import &amp; verify</b>blender_import.py, verify_export.py and render_check.py.</div>
<div><b>Add LiDAR</b>A sensor empty parented to the robot, a render layer with the <span class="mono">LiDAR_Pass</span> material override.</div>
</div>
<p class="honest" style="margin-top:24px"><b>Honest labels.</b> This is a Blender render of the Isaac warehouse with an animated LiDAR visualisation. It is not an Isaac Sim sensor run and produces no point cloud. 54 source textures lived on an Omniverse Nucleus server and were not localised; those materials fall back to constants.</p>
</div></section>

<footer><div class="wrap">NVIDIA warehouse assets and textures are not included in this repo and keep their own licence.</div></footer>
<div id="lb"><img alt=""></div>
<script>
const D=__DATA__, T=__TEMPL__, G=__GAL__;
const $=s=>document.querySelector(s);
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
    for p in sorted((DOCS / "media" / "stills").glob("*.jpg")):
        gal.append([f"media/stills/{p.name}", f"{p.stem.replace('_', ' ')}"])
    html = (HTML.replace("__DATA__", json.dumps(data))
                .replace("__TEMPL__", json.dumps(TEMPLATES))
                .replace("__GAL__", json.dumps(gal)))
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    log.info("wrote docs/index.html (%d gallery items)", len(gal))


if __name__ == "__main__":
    try:
        build()
    except Exception:
        log.exception("build failed")
        raise
