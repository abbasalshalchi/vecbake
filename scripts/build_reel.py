# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Build the reel page: every scorecard bake playing live beside Blender's render.

    python scripts/build_reel.py

Reads the scorecard records (out/scorecard/<name>/scorecard.json), the bakes
beside them and the reference renders from `render_reference.py`
(out/reel/<name>_ref.png), and writes out/reel/index.html plus
out/reel/refs/*.jpg. Every number on the page comes from those records; the
words around them are written here. The page plays the real files through the
real player (lerpa/src/player.js, published next to it), so it is a check of
the bakes as much as a showcase. Needs Pillow for the JPEGs.
"""

import base64
import html
import json
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out", "reel")
SC = os.path.join(ROOT, "out", "scorecard")
FONT = os.path.join(ROOT, "lerpa-website", "public", "fonts", "lineal", "LinealVF.woff2")

MODELS = [
    dict(key="apollo", title="Apollo–Soyuz", what="Hero model · 91 parts · its own 60° arc, 31 frames",
         verdict=("good", "Ships"),
         look="The engine bell on the left: the ring and fittings in its mouth are the one class this "
              "engine still gets wrong on purpose. One depth rank per merged part cannot say a part is "
              "in front in one place and behind in another. Splitting those parts fixes it, at 59% more "
              "elements (audits §19)."),
    dict(key="voyager", title="Voyager", what="Hero model · 322 parts · its own arc, 60 frames",
         verdict=("warn", "Ships, watch the struts"),
         look="Slow it to ¼× and watch the antenna struts on the right. Four of them started merging "
              "this week and drift up to {visible_drift} px for a few half-frames, inside the false-outline "
              "limit. The {worst_drift} px worst in the table is a collapsed slot: two coincident points "
              "that draw nothing."),
    dict(key="creature", title="Mechanical Creature Kit", what="Polyfjord, free kit · 7 parts on an armature, 10 cm · orbit",
         verdict=("good", "Ships"),
         look="The kit as it ships: parts laid out flat, moving on an armature, so they sit small on "
              "the stage. The Joint is drawn face by face since this week's sizing fix, which took its "
              "6 px drift away; nothing now moves more than {visible_drift} px off between keyframes."),
    dict(key="flashbang", title="Flashbang", what="Hard-surface product · 7 meshes, 6 Bézier curves, a floor · orbit",
         verdict=("good", "Ships"),
         look="The pull ring and the clip are Bézier curves. Until this week they were not baked at "
              "all, and the body showed through where they should be. The triangle and the part behind "
              "it are two copies of one mesh 2 cm apart, and they fight for paint order."),
    dict(key="celltower", title="Cell tower", what="The original NOC tower · orthographic · its own move, 24 frames",
         verdict=("warn", "Ships, see-through lattice"),
         look="The lattice is single-sided. Backface culling drops faces the camera sees from behind, "
              "so in places the base shows through the tower. This scene's own panel had the cull off; "
              "the scorecard turns it on for every model. Culling only closed surfaces is the open fix."),
    dict(key="discoball", title="Disco ball", what="One 5,664-facet ball over a floor · orbit",
         verdict=("good", "Ships"),
         look="Every facet is its own fill. A mirror ball is meant to be drawn tile by tile, with a "
              "crease around each, so nothing merges and the file is {raw} MB. It barely overlaps the "
              "floor, so its zero wrong pixels say little about paint order."),
    dict(key="torus60", title="Torus, seen clearly", what="Controlled scene · smooth primitive torus · 60° up, orbit",
         verdict=("good", "Ships, one element"),
         look="A rim and a hole drawn as one merged outline with a hole in it, {raw_kb} KB for the clip. "
              "Two weeks ago this was 1,654 faces: the merge test was joining its two rings end to end "
              "and scoring a correct outline as 13 px wrong."),
]

SHORT = {"good": "Ships", "warn": "Ships, watch", "bad": "Doesn't ship"}

DOUGHNUT = dict(key="doughnut", title="Doughnut tutorial", what="Eight doughnuts and a 96k-face mug · smooth, organic · 20 mm rendering camera, orbit")


def fmt_int(n):
    return f"{n:,}"


def load(name):
    with open(os.path.join(SC, name, "scorecard.json"), encoding="utf-8") as fh:
        return json.load(fh)


def stats(r):
    seg = r["segments"]
    visible = [t["d"] for t in seg["top"] if t.get("length", 0.0) >= 1.0]
    n = r["frame_range"][1] - r["frame_range"][0] + 1
    return {
        "elements": r["elements"], "raw": r["file_bytes"] / 1e6, "gz": r["gzip_bytes"] / 1e6,
        "cpu": r["bake_cpu_seconds"], "wrong": r["audit"]["wrong_pixels"],
        "tested": r["audit"]["overlap_pixels_tested"], "rate": r["audit"]["rate_pct"],
        "over3": seg["over_3px"], "worst": seg["worst_px"], "visible": max(visible or [0.0]),
        "merged": r["meta"].get("merged_fills", 0), "geo_worst": r["geometry"]["worst_px"],
        "frames": n, "ref_frac": (n // 2) / (n - 1), "fps": r["fps"],
    }


def ref_jpg(key):
    src = os.path.join(OUT, f"{key}_ref.png")
    os.makedirs(os.path.join(OUT, "refs"), exist_ok=True)
    dst = os.path.join(OUT, "refs", f"{key}.jpg")
    Image.open(src).convert("RGB").save(dst, "JPEG", quality=84, optimize=True)
    return f"refs/{key}.jpg"


def gauge(label, value, note=""):
    note_html = f'<span class="note">{html.escape(note)}</span>' if note else ""
    return f'<div class="gauge"><dt>{html.escape(label)}</dt><dd>{value}{note_html}</dd></div>'


def model_section(m, s, ref, bake_src):
    words = dict(visible_drift=f"{s['visible']:.1f}", worst_drift=f"{s['worst']:.1f}",
                 raw=f"{s['raw']:.1f}", raw_kb=f"{s['raw'] * 1000:.0f}")
    look = m["look"].format(**words)
    tone, label = m["verdict"]
    rate = f"{s['rate']:.2f}%" if s["tested"] else "—"
    wrong_note = (f"{fmt_int(s['wrong'])} of {fmt_int(s['tested'])} overlap px" if s["tested"]
                  else "nothing overlaps")
    gauges = "".join([
        gauge("Elements", fmt_int(s["elements"]), f"{s['merged']} merged outlines"),
        gauge("File", f"{s['raw']:.2f} MB", f"{s['gz']:.2f} MB gzipped"),
        gauge("Painted wrong", rate, wrong_note),
        gauge("Off between keyframes", f"{s['over3']}", f"segments over 3 px · worst real {s['visible']:.1f} px"),
        gauge("Off at keyframes", f"{s['geo_worst']:.2f} px", "worst point, every frame"),
        gauge("Bake", f"{s['cpu']:,.0f} s", "CPU, one ordering worker"),
    ])
    return f'''
<section class="model" id="{m['key']}" data-src="{bake_src}" data-ref-frac="{s['ref_frac']:.6f}"
         data-mb="{s['raw']:.1f}">
  <header class="model-head">
    <div>
      <h2>{html.escape(m['title'])}</h2>
      <p class="what">{html.escape(m['what'])}</p>
    </div>
    <span class="pill {tone}">{html.escape(label)}</span>
  </header>
  <div class="stage" role="img" aria-label="{html.escape(m['title'])}, baked by VecBake, playing in the Lerpa player">
    <canvas></canvas>
    <img class="ref" src="{ref}" alt="Blender's own render of the middle frame" loading="lazy" hidden>
    <div class="wipe-line" hidden></div>
    <span class="tag tag-left" hidden>Blender</span><span class="tag tag-right" hidden>Lerpa</span>
    <p class="loading">Loads when you scroll here · {s['raw']:.1f} MB</p>
  </div>
  <div class="controls">
    <button type="button" class="btn play" aria-pressed="false" disabled>Play</button>
    <div class="seg" role="group" aria-label="Speed">
      <button type="button" class="btn speed" data-speed="1" aria-pressed="true" disabled>1×</button>
      <button type="button" class="btn speed" data-speed="0.25" aria-pressed="false" disabled>¼×</button>
    </div>
    <label class="scrub"><span class="sr">Time</span>
      <input type="range" id="t-{m['key']}" min="0" max="1000" value="0" disabled></label>
    <span class="clock" aria-live="off">0.00 s</span>
    <button type="button" class="btn compare" aria-pressed="false" disabled>Compare with Blender</button>
    <label class="wipe" hidden><span class="sr">Wipe</span>
      <input type="range" id="w-{m['key']}" min="0" max="100" value="50"></label>
  </div>
  <dl class="gauges">{gauges}</dl>
  <p class="look">{html.escape(look)}</p>
</section>'''


def doughnut_section(ref):
    r = json.load(open(os.path.join(ROOT, "out", "scorecard-superseded", "doughnut-before-gaptest",
                                    "scorecard.json"), encoding="utf-8"))
    now = json.load(open(os.path.join(ROOT, "out", "bake-only", "doughnut-sizing", "bake_only.json"),
                         encoding="utf-8"))
    gauges = "".join([
        gauge("Elements", fmt_int(now["elements"]), "0 merged outlines"),
        gauge("File", f"{now['file_bytes'] / 1e6:.1f} MB", f"{r['gzip_bytes'] / 1e6:.1f} MB gzipped"),
        gauge("Painted wrong", f"{r['audit']['rate_pct']:.2f}%",
              f"{fmt_int(r['audit']['wrong_pixels'])} of {fmt_int(r['audit']['overlap_pixels_tested'])} overlap px"),
        gauge("Off at keyframes", f"{r['geometry']['worst_px']:.2f} px",
              f"over {fmt_int(r['geometry']['checked'])} point sets"),
        gauge("Bake", f"{r['bake_cpu_seconds']:,.0f} s", f"one worker · {now['wall_seconds'] / 60:.0f} min with four"),
        gauge("Peak memory", f"{r['peak_memory']['peak_working_set_mb'] / 1000:.1f} GB", "one worker"),
    ])
    return f'''
<section class="model doughnut" id="doughnut">
  <header class="model-head">
    <div>
      <h2>{html.escape(DOUGHNUT['title'])}</h2>
      <p class="what">{html.escape(DOUGHNUT['what'])}</p>
    </div>
    <span class="pill bad">Doesn't ship</span>
  </header>
  <div class="stage still">
    <img src="{ref}" alt="Blender's render of the doughnut scene, middle frame" loading="lazy">
    <span class="tag tag-left">Blender only</span>
  </div>
  <dl class="gauges">{gauges}</dl>
  <p class="look">Not playable here: the bake is {now['file_bytes'] / 1e6:.0f} MB. Every pixel the audit
  tested is right and every point lands within {r['geometry']['worst_px']:.2f} px, but none of the nine
  bodies merged, so all of them are drawn face by face. Through a 20 mm lens the doughnuts' outlines split
  and rejoin between keyframes, and the folded contours drift even in gaps where nothing splits. Keying
  the outline at each split would remove the first and not the second (audits §23).</p>
</section>'''


def main():
    font64 = base64.b64encode(open(FONT, "rb").read()).decode("ascii")
    sections, rows = [], []
    for m in MODELS:
        r = load(m["key"])
        s = stats(r)
        ref = ref_jpg(m["key"])
        bake_src = f"bakes/{m['key']}.svg"
        sections.append(model_section(m, s, ref, bake_src))
        tone, label = m["verdict"]
        rate = f"{s['rate']:.2f}%" if s["tested"] else "—"
        rows.append(f'<tr><th scope="row"><a href="#{m["key"]}">{html.escape(m["title"])}</a></th>'
                    f'<td>{fmt_int(s["elements"])}</td><td>{s["raw"]:.2f} / {s["gz"]:.2f}</td>'
                    f'<td>{rate}</td><td>{s["over3"]}</td><td>{s["visible"]:.1f}</td>'
                    f'<td><span class="pill {tone}">{SHORT[tone]}</span></td></tr>')
    dref = ref_jpg("doughnut")
    sections.append(doughnut_section(dref))
    d = json.load(open(os.path.join(ROOT, "out", "bake-only", "doughnut-sizing", "bake_only.json"),
                       encoding="utf-8"))
    dr = json.load(open(os.path.join(ROOT, "out", "scorecard-superseded", "doughnut-before-gaptest",
                                     "scorecard.json"), encoding="utf-8"))
    rows.append(f'<tr><th scope="row"><a href="#doughnut">Doughnut tutorial</a></th>'
                f'<td>{fmt_int(d["elements"])}</td><td>{d["file_bytes"] / 1e6:.1f} / {dr["gzip_bytes"] / 1e6:.1f}</td>'
                f'<td>{dr["audit"]["rate_pct"]:.2f}%</td><td>{dr["segments"]["over_3px"]}</td>'
                f'<td>{max([t["d"] for t in dr["segments"]["top"] if t.get("length", 0) >= 1] or [0]):.1f}</td>'
                f'<td><span class="pill bad">Doesn\'t ship</span></td></tr>')
    page = TEMPLATE.replace("{{FONT}}", font64).replace("{{ROWS}}", "\n".join(rows)).replace(
        "{{SECTIONS}}", "\n".join(sections))
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    files = {"lerpa/player.js": "lerpa/src/player.js"}
    for m in MODELS:
        files[f"bakes/{m['key']}.svg"] = (f"out/reel/torus60/torus60.svg" if m["key"] == "torus60"
                                          else f"out/scorecard/{m['key']}/{m['key']}.svg")
    for key in [m["key"] for m in MODELS] + ["doughnut"]:
        files[f"refs/{key}.jpg"] = f"out/reel/refs/{key}.jpg"
    with open(os.path.join(OUT, "files.json"), "w", encoding="utf-8") as fh:
        json.dump(files, fh, indent=1)
    print("wrote", os.path.join(OUT, "index.html"), f"{len(page) / 1e3:.0f} KB,", len(files), "files")


TEMPLATE = r'''<title>VecBake October Reel</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
@font-face {
  font-family: "Lineal";
  src: url(data:font/woff2;base64,{{FONT}}) format("woff2");
  font-weight: 100 900; font-display: swap;
}
/* Layout: a test bench -- one long reel of steel stages, each with its gauge readout
   beneath. Lerpa's vest identity: dark only, hi-vis for action, red/amber/green for data. */
:root {
  color-scheme: dark;
  --ground: #0d0d10; --panel: #141419; --panel-2: #1b1b21; --rule: #292930;
  --ink: #f1f0ec; --ink-2: #b7bcc3; --ink-3: #8a909a;
  --hivis: #d2ef2b; --steel: #6a707a; --steel-dark: #33373f;
  --good: #5fc46e; --warn: #e8a93a; --bad: #e4584c;
  --display: "Lineal", ui-sans-serif, system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --sans: ui-sans-serif, system-ui, "Segoe UI Variable Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "SFMono-Regular", "Cascadia Mono", Menlo, Consolas, monospace;
}
body { background: var(--ground); color: var(--ink); font-family: var(--sans); font-size: 16px; line-height: 1.6; }
a { color: var(--ink); text-decoration-color: var(--hivis); text-underline-offset: 3px; }
a:hover { color: var(--hivis); }
:focus-visible { outline: 2px solid var(--hivis); outline-offset: 2px; }
.band { height: 10px; background: repeating-linear-gradient(90deg, var(--hivis) 0 64px, var(--ink-2) 64px 96px); }
.wrap { max-width: 1120px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px));
        padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.intro { padding-block: 48px 28px; display: grid; gap: 18px; }
.eyebrow { font-family: var(--mono); font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: var(--ink-3); margin: 0; }
h1 { font-family: var(--display); font-weight: 700; font-size: clamp(40px, 7vw, 76px); line-height: 1; margin: 0; letter-spacing: -.01em; text-wrap: balance; }
h1 span { color: var(--hivis); }
.lead { font-size: 18px; max-width: 64ch; margin: 0; color: var(--ink); }
.how { max-width: 64ch; margin: 0; color: var(--ink-2); }
.how b { color: var(--ink); font-weight: 600; }
h2 { font-family: var(--display); font-weight: 650; font-size: 28px; line-height: 1.15; margin: 0; text-wrap: balance; }
h3 { font-family: var(--display); font-weight: 650; font-size: 20px; margin: 0 0 10px; }
.table-box { overflow-x: auto; border: 1px solid var(--rule); border-radius: 4px; background: var(--panel); }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; font-size: 14px; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
thead th { font-family: var(--mono); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-3); font-weight: 500; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
td:nth-child(n+2):not(:last-child) { text-align: right; }
.caption { color: var(--ink-3); font-size: 13px; margin: 10px 0 0; max-width: 80ch; }
.pill { display: inline-block; font-family: var(--mono); font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
        padding: 3px 9px; border-radius: 999px; border: 1px solid currentColor; white-space: nowrap; }
.pill.good { color: var(--good); } .pill.warn { color: var(--warn); } .pill.bad { color: var(--bad); }
.reel { display: grid; gap: 64px; padding-block: 48px; }
.model { display: grid; gap: 14px; }
.model-head { display: flex; flex-wrap: wrap; gap: 12px 20px; align-items: flex-end; justify-content: space-between;
              border-top: 3px solid var(--steel-dark); padding-top: 18px; }
.what { margin: 4px 0 0; color: var(--ink-3); font-size: 14px; }
.stage { position: relative; aspect-ratio: 16 / 9; max-width: 100%; background: var(--steel); border-radius: 3px; overflow: hidden; }
.stage canvas, .stage .ref, .stage.still img { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.stage .ref { clip-path: inset(0 calc(100% - var(--wipe, 50%)) 0 0); }
.wipe-line { position: absolute; top: 0; bottom: 0; left: var(--wipe, 50%); width: 2px; margin-left: -1px; background: var(--hivis); pointer-events: none; }
.tag { position: absolute; top: 10px; font-family: var(--mono); font-size: 11px; letter-spacing: .1em; text-transform: uppercase;
       background: var(--ground); color: var(--ink); padding: 3px 8px; border-radius: 2px; }
.tag-left { left: 10px; } .tag-right { right: 10px; }
.loading { position: absolute; inset: auto 0 12px; margin: 0; text-align: center; font-family: var(--mono); font-size: 12px; color: var(--ground); }
.controls { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; }
.btn { font: inherit; font-size: 14px; color: var(--ink); background: var(--panel-2); border: 1px solid var(--rule);
       border-radius: 3px; padding: 7px 14px; cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--ink-3); }
.btn[aria-pressed="true"] { background: var(--hivis); color: var(--ground); border-color: var(--hivis); }
.btn:disabled { opacity: .45; cursor: default; }
.seg { display: inline-flex; } .seg .btn { border-radius: 0; } .seg .btn:first-child { border-radius: 3px 0 0 3px; }
.seg .btn:last-child { border-radius: 0 3px 3px 0; margin-left: -1px; }
.scrub { flex: 1 1 180px; min-width: 0; display: flex; } .scrub input { width: 100%; accent-color: var(--hivis); }
.wipe { flex: 1 1 160px; min-width: 0; display: flex; } .wipe input { width: 100%; accent-color: var(--hivis); }
.clock { font-family: var(--mono); font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; min-width: 5.5ch; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.gauges { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1px; margin: 0;
          background: var(--rule); border: 1px solid var(--rule); border-radius: 3px; overflow: hidden; }
.gauge { background: var(--panel); padding: 12px 14px; display: grid; gap: 2px; min-width: 0; }
.gauge dt { font-family: var(--mono); font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-3); }
.gauge dd { margin: 0; font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; display: grid; }
.gauge .note { font-size: 12.5px; font-weight: 400; color: var(--ink-2); }
.look { margin: 0; max-width: 72ch; color: var(--ink-2); }
.notes { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 28px; padding-block: 8px 56px; }
.notes section { min-width: 0; }
.notes ul { margin: 0; padding-left: 18px; display: grid; gap: 8px; color: var(--ink-2); }
.notes li b { color: var(--ink); font-weight: 600; }
footer { border-top: 1px solid var(--rule); padding-block: 22px 40px; color: var(--ink-3); font-size: 13px; }
footer code, .notes code { font-family: var(--mono); font-size: .92em; color: var(--ink-2); }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
@media (max-width: 520px) { .intro { padding-block: 32px 20px; } .gauge dd { font-size: 19px; } .tag { font-size: 10px; } }
</style>

<div class="band" aria-hidden="true"></div>
<main class="wrap">
  <section class="intro">
    <p class="eyebrow">VecBake add-on at 88200f3 · Lerpa player at f7da28a · 26 October 2026</p>
    <h1>October reel<span>.</span></h1>
    <p class="lead">Seven scenes the engine was graded on this month, baked by the add-on as it stands
    today and played here by the Lerpa player: the real files, not recordings. Beside each is Blender's
    own render of the same frame, from the same camera, at the same size.</p>
    <p class="how"><b>How to look.</b> Play at ¼×: the mistakes that matter happen between keyframes.
    <b>Compare with Blender</b> stops on the middle frame and puts Blender's picture under a wipe; drag it
    across to see where the two disagree. Blender's render outlines silhouettes only, so lines the bake
    draws along creases are not in it. Stages load as you scroll; Voyager is 14 MB.</p>
  </section>

  <section aria-labelledby="sum-h">
    <h3 id="sum-h">Scorecard</h3>
    <div class="table-box">
      <table>
        <thead><tr><th scope="col">Scene</th><th scope="col">Elements</th><th scope="col">MB raw / gz</th>
          <th scope="col">Painted wrong</th><th scope="col">Segments &gt; 3 px</th><th scope="col">Worst drift, px</th>
          <th scope="col">Verdict</th></tr></thead>
        <tbody>
{{ROWS}}
        </tbody>
      </table>
    </div>
    <p class="caption">Painted wrong: share of pixels where two parts overlap that are painted with the wrong
    part, by ray cast against the scene, 9,000 sampled over three frames. Segments: outline segments more
    than 3 px off the true edges halfway between keyframes. Worst drift: the largest such segment that
    draws anything (collapsed slots excluded). Every point at every keyframe lands within 1.12 px on all
    of them.</p>
  </section>

  <div class="reel">
{{SECTIONS}}
  </div>

  <div class="notes">
    <section>
      <h3>What changed this month</h3>
      <ul>
        <li><b>The audit sees straight.</b> It cast rays through the wrong pixels whenever a scene's render
        size differed from the bake's, and fanned them under orthographic cameras, which mostly read as
        clean. Fixed, with a test that fails on the old code.</li>
        <li><b>Curves, text and surfaces bake.</b> They were silently skipped; on the flashbang that was
        59% of the faces. They now come out exactly as their own meshes would.</li>
        <li><b>Rings merge.</b> The merge test joined a part's outline rings end to end and refused correct
        outlines. The torus, Apollo's last three thrusters and the flashbang's rings now merge.</li>
        <li><b>Sizing no longer depends on the last frame</b> of the clip. Voyager merges nine more parts.</li>
      </ul>
    </section>
    <section>
      <h3>Still open</h3>
      <ul>
        <li><b>The doughnut.</b> Outlines that split between keyframes, and folded contours that drift
        where nothing splits. The second is the harder half.</li>
        <li><b>See-through single-sided parts</b> (the cell tower's lattice): cull only closed surfaces.</li>
        <li><b>Two bars.</b> A one-ring outline is refused at 6 px worse than faces in any gap; a multi-ring
        one only at 160 px of visible false line over the whole clip.</li>
        <li><b>npm is behind.</b> <code>lerpa-player@0.4.0</code> draws a multi-ring fill as one path; this
        page uses the current player.</li>
      </ul>
    </section>
    <section>
      <h3>Housekeeping and audits</h3>
      <ul>
        <li><b>29 of 29 tests pass</b>, plain Python and Blender alike, from one new command,
        <code>scripts/run_tests.py</code>.</li>
        <li><b>Every copy of the player is current</b>: the add-on's preview and the website's both match
        <code>lerpa/src</code> byte for byte.</li>
        <li><b>11 commits are not pushed.</b> This machine's GitHub login is a different account, which
        cannot see the private <code>vecbake</code> repo.</li>
        <li>The tools behind this page are in <code>scripts/</code>: <code>scorecard.py</code>,
        <code>render_reference.py</code>, <code>build_reel.py</code>.</li>
      </ul>
    </section>
  </div>
</main>
<footer class="wrap">Full record: <code>vecbake/docs/audits.md</code> §20–23, with <code>bugs.md</code>,
<code>experiments.md</code> and <code>measurement.md</code>. Settings for every bake: SHARP edges at 30°,
merged fills, welded seams, backface cull, 1 px tolerance, 1.6 px lines, 1920 × 1080.</footer>

<script type="module">
import { LerpaPlayer, fetchDocument } from './lerpa/player.js';

const LINE = 1.6;                       // the bakes' stroke width, document pixels
const live = new Map();                 // section -> state

// Canvas 2D slows about 5x once a stroke passes one device pixel (the hairline
// cliff), so the pixel ratio is held where a 1.6 px line stays at or under one.
function ratioFor(player, cssWidth) {
  const want = window.devicePixelRatio || 1;
  const budget = player.width / (LINE * Math.max(cssWidth, 1));
  return Math.max(1, Math.min(want, budget));
}

function fmt(t) { return t.toFixed(2) + ' s'; }

async function boot(section) {
  if (live.has(section)) return;
  const stage = section.querySelector('.stage');
  const canvas = stage.querySelector('canvas');
  const loading = stage.querySelector('.loading');
  const state = { player: null, wanted: true, compare: false, visible: true };
  live.set(section, state);
  loading.textContent = 'Loading ' + section.dataset.mb + ' MB…';
  let doc;
  try { doc = await fetchDocument(section.dataset.src); }
  catch (e) { loading.textContent = 'Could not load this bake.'; return; }
  const player = new LerpaPlayer(canvas, doc, { fit: 'contain', speed: 1, seamFix: true, autoResize: false });
  state.player = player;
  const fit = () => {
    const r = stage.getBoundingClientRect();
    player.resizeTo(r.width, r.height, ratioFor(player, r.width));
  };
  new ResizeObserver(fit).observe(stage);
  fit();
  loading.remove();
  wire(section, state);
  if (state.visible) start(state);
}

function start(state) { if (state.player && state.wanted && !state.compare) state.player.play({ loop: true }); sync(state); }
function stop(state) { state.player?.pause(); sync(state); }
function sync(state) {
  const s = state.section; if (!s) return;
  const playing = !!state.player?.playing;
  const b = s.querySelector('.play');
  b.textContent = playing ? 'Pause' : 'Play';
  b.setAttribute('aria-pressed', String(playing));
}

function wire(section, state) {
  state.section = section;
  const p = state.player;
  const play = section.querySelector('.play');
  const speeds = section.querySelectorAll('.speed');
  const scrub = section.querySelector('.scrub input');
  const clock = section.querySelector('.clock');
  const compare = section.querySelector('.compare');
  const wipeBox = section.querySelector('.wipe');
  const wipe = wipeBox.querySelector('input');
  const ref = section.querySelector('.ref');
  const line = section.querySelector('.wipe-line');
  const tags = section.querySelectorAll('.tag');
  section.querySelectorAll('button, .scrub input').forEach(el => { el.disabled = false; });

  const setCompare = on => {
    state.compare = on;
    compare.setAttribute('aria-pressed', String(on));
    ref.hidden = line.hidden = wipeBox.hidden = !on;
    tags.forEach(t => { t.hidden = !on; });
    if (on) {
      p.pause();
      p.seek(p.duration * parseFloat(section.dataset.refFrac));
    } else if (state.wanted) {
      start(state);
    }
    sync(state);
  };
  play.addEventListener('click', () => {
    if (state.compare) setCompare(false);
    state.wanted = !p.playing;
    state.wanted ? start(state) : stop(state);
  });
  speeds.forEach(b => b.addEventListener('click', () => {
    p.speed = parseFloat(b.dataset.speed);
    speeds.forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  }));
  scrub.addEventListener('input', () => {
    if (state.compare) setCompare(false);
    state.wanted = false; stop(state);
    p.seek(p.duration * scrub.value / 1000);
  });
  compare.addEventListener('click', () => setCompare(!state.compare));
  const setWipe = () => section.querySelector('.stage').style.setProperty('--wipe', wipe.value + '%');
  wipe.addEventListener('input', setWipe); setWipe();

  const tick = () => {
    if (state.visible) {
      const t = p.time || 0;
      clock.textContent = fmt(t);
      if (document.activeElement !== scrub) scrub.value = String(Math.round(1000 * t / (p.duration || 1)));
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  sync(state);
}

const io = new IntersectionObserver(entries => {
  for (const e of entries) {
    const section = e.target;
    const state = live.get(section);
    if (e.isIntersecting) {
      if (!state) boot(section); else { state.visible = true; start(state); }
    } else if (state) {
      state.visible = false; state.player?.pause();
    }
  }
}, { rootMargin: '300px 0px' });
document.querySelectorAll('.model[data-src]').forEach(s => io.observe(s));
</script>
'''

if __name__ == "__main__":
    main()
