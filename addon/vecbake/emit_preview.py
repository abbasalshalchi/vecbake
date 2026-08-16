# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A bake you can watch, without building anything.

Until now the only way to see a finished animation move was to stand up a page,
serve it, and wire in the runtime -- which is a web job standing between an
animator and their own work. This writes one `.preview.html` next to the bake:
double-click it and the animation plays, with a scrubber, the per-group colours
and a hit-test readout.

Every literal in the page is ASCII or an HTML entity, and the payload is
ASCII too because json.dumps escapes by default. Previews get stripped of
their <head> to be embedded elsewhere, and without a charset declaration a
stray em dash comes back as mojibake.

Self-contained on purpose. The player is inlined rather than linked, because a
module script will not load over `file://` -- browsers refuse the cross-origin
request -- and an animator should not have to run a web server to look at a
render. That means one file, no network, no build step, and it keeps working
when it is emailed to a client.

The inlining strips the ES module syntax so the source can run as a classic
script. That transform is mechanical and it is checked: if the player ever
gains an `import`, the guard below raises at bake time rather than quietly
writing a preview that does not run.
"""

from __future__ import annotations

import json
import os
import re

from . import emit_json

HERE = os.path.dirname(__file__)
PLAYER = os.path.join(HERE, "preview", "player.js")

_IMPORT = re.compile(r"^\s*import[\s({]", re.M)
_EXPORT_LIST = re.compile(r"^\s*export\s*\{[^}]*\}\s*;?\s*$", re.M)
_EXPORT_KW = re.compile(r"^(\s*)export\s+(?=class|function|const|let|var|async)", re.M)


def _player_source() -> str:
    """The vendored player, rewritten to run as a classic inline script.

    `export` is safe to delete -- nothing in the page imports from the player,
    it just needs the classes in scope. An `import` is not: deleting one takes
    a real dependency with it and produces a page that loads, paints nothing,
    and reports the failure only to a console the animator will never open. So
    an import is refused rather than stripped, and whoever added it gets to
    decide how the preview should bundle it.
    """
    with open(PLAYER, encoding="utf-8") as fh:
        src = fh.read()

    found = _IMPORT.findall(src)
    if found:
        raise RuntimeError(
            f"emit_preview: preview/player.js now has {len(found)} import "
            "statement(s). The preview inlines the player as a classic script, "
            "which cannot resolve them, and dropping them would ship a page "
            "that silently does nothing. Bundle the dependency into "
            "preview/player.js, or teach _player_source() to concatenate it."
        )

    src = _EXPORT_LIST.sub("", src)
    src = _EXPORT_KW.sub(r"\1", src)
    left = re.findall(r"^\s*export\s", src, re.M)
    if left:
        raise RuntimeError(
            "emit_preview: the vendored player still has export syntax after "
            f"stripping ({len(left)} line(s)). preview/player.js has changed "
            "shape -- update the transform in emit_preview.py."
        )
    return src


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ &mdash; VecBake preview</title>
<style>
  :root{
    --bg:#131316; --panel:#1B1B20; --panel2:#232329; --rule:#32323A;
    --ink:#EFEFF2; --soft:#9C9CA6; --faint:#6A6A74; --accent:#D2EF2B;
    --sans:ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif;
    --mono:ui-monospace,"Cascadia Mono",Consolas,monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 var(--sans);
       -webkit-font-smoothing:antialiased;display:flex;flex-direction:column}
  header{display:flex;align-items:baseline;gap:14px;padding:12px 18px;
         border-bottom:1px solid var(--rule);flex:none}
  header h1{font-size:14px;margin:0;font-weight:650;letter-spacing:-.01em}
  header .src{font-family:var(--mono);font-size:10.5px;color:var(--faint)}
  main{flex:1;display:grid;grid-template-columns:1fr 268px;min-height:0}
  @media(max-width:820px){main{grid-template-columns:1fr}}

  .stagewrap{display:flex;flex-direction:column;min-width:0;min-height:0}
  .stage{flex:1;position:relative;min-height:0;display:grid;place-items:stretch}
  canvas{display:block;width:100%;height:100%}
  .stage[data-bg="dark"]{background:#131316}
  .stage[data-bg="light"]{background:#F2F2F0}
  .stage[data-bg="check"]{
    background-color:#8a8a90;
    background-image:linear-gradient(45deg,#6e6e75 25%,transparent 25%,transparent 75%,#6e6e75 75%),
                     linear-gradient(45deg,#6e6e75 25%,transparent 25%,transparent 75%,#6e6e75 75%);
    background-size:22px 22px;background-position:0 0,11px 11px}

  .bar{border-top:1px solid var(--rule);padding:10px 14px;display:flex;
       align-items:center;gap:12px;flex-wrap:wrap;flex:none;background:var(--panel)}
  button{font:inherit;background:var(--panel2);color:var(--ink);border:1px solid var(--rule);
         padding:6px 12px;cursor:pointer;border-radius:2px}
  button:hover{border-color:var(--accent)}
  button.on{background:var(--accent);color:#1e2400;border-color:var(--accent)}
  #play{min-width:76px;font-weight:650}
  input[type=range]{accent-color:var(--accent)}
  #scrub{flex:1;min-width:160px}
  .read{font-family:var(--mono);font-size:11.5px;color:var(--soft);
        font-variant-numeric:tabular-nums;white-space:nowrap}
  .seg{display:flex;border:1px solid var(--rule);border-radius:2px;overflow:hidden}
  .seg button{border:0;border-radius:0;padding:6px 10px;font-size:11.5px}
  .seg button+button{border-left:1px solid var(--rule)}
  label.lbl{font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;
            text-transform:uppercase;color:var(--faint)}

  aside{border-left:1px solid var(--rule);overflow-y:auto;padding:14px;background:var(--panel)}
  @media(max-width:820px){aside{border-left:0;border-top:1px solid var(--rule)}}
  aside h2{font-family:var(--mono);font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;
           color:var(--faint);margin:0 0 9px;font-weight:400}
  aside section{margin-bottom:22px}
  .kv{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;padding:3px 0}
  .kv span:last-child{font-family:var(--mono);color:var(--ink);font-variant-numeric:tabular-nums}
  .kv span:first-child{color:var(--soft)}
  .grp{display:flex;align-items:center;gap:9px;padding:3px 0;font-size:12.5px}
  .grp input{width:30px;height:22px;padding:0;border:1px solid var(--rule);background:none;
             cursor:pointer;flex:none;border-radius:2px}
  .grp span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .grp.hit{color:var(--accent)}
  #picked{font-family:var(--mono);font-size:11.5px;color:var(--accent);min-height:1.4em;
          word-break:break-all}
  .hint{color:var(--faint);font-size:11.5px;line-height:1.45}
</style>
</head>
<body>

<header>
  <h1>__NAME__</h1>
  <span class="src">__SOURCE__</span>
</header>

<main>
  <div class="stagewrap">
    <div class="stage" id="stage" data-bg="dark"><canvas id="cv"></canvas></div>
    <div class="bar">
      <button id="play">Pause</button>
      <input type="range" id="scrub" min="0" max="1000" value="0" step="1">
      <span class="read" id="time">0.00 / 0.00 s</span>
      <button id="loop" class="on">Loop</button>
      <div class="seg" id="speed">
        <button data-v="0.25">&frac14;&times;</button><button data-v="0.5">&frac12;&times;</button>
        <button data-v="1" class="on">1&times;</button><button data-v="2">2&times;</button>
      </div>
      <div class="seg" id="bg">
        <button data-v="dark" class="on">Dark</button>
        <button data-v="light">Light</button>
        <button data-v="check">Checker</button>
      </div>
      <div class="seg" id="fit">
        <button data-v="contain" class="on">Contain</button>
        <button data-v="cover">Cover</button>
      </div>
    </div>
  </div>

  <aside>
    <section>
      <h2>This bake</h2>
      <div class="kv"><span>Elements</span><span id="s-els">&mdash;</span></div>
      <div class="kv"><span>Drawn now</span><span id="s-drawn">&mdash;</span></div>
      <div class="kv"><span>Peak drawn</span><span id="s-peak">&mdash;</span></div>
      <div class="kv"><span>Duration</span><span id="s-dur">&mdash;</span></div>
      <div class="kv"><span>Frames</span><span id="s-frames">&mdash;</span></div>
      <div class="kv"><span>Canvas</span><span id="s-size">&mdash;</span></div>
    </section>

    <section>
      <h2>Identify</h2>
      <div id="picked">click the artwork</div>
      <p class="hint">Every element stays a real object while it moves &mdash; this is
        the same hit test your page would use.</p>
    </section>

    <section>
      <h2>Groups <button id="reset" style="float:right;padding:2px 8px;font-size:10.5px">Reset</button></h2>
      <div id="groups"></div>
      <p class="hint">Colours are set at runtime on the file as baked. Nothing
        here is re-exported.</p>
    </section>
  </aside>
</main>

<script>
// ---- the Lerpa runtime, inlined so this file works from disk -------------
__PLAYER__
</script>

<script>
(() => {
  const doc = __DOC__;
  const cv = document.getElementById('cv');
  const stage = document.getElementById('stage');
  const player = new LerpaPlayer(cv, doc, { fit: 'contain', background: null });

  const $ = id => document.getElementById(id);
  const fmt = n => n.toFixed(2);

  // ---- stats ----
  $('s-els').textContent = player.elements.length;
  $('s-dur').textContent = fmt(player.duration) + ' s';
  // Plain ASCII on purpose. This page gets embedded elsewhere with its <head>
  // stripped, and any non-ASCII literal then decodes as mojibake.
  $('s-size').textContent = doc.w + 'x' + doc.h;
  $('s-frames').textContent = doc.fps
    ? Math.round(player.duration * doc.fps) + ' @ ' + doc.fps + 'fps' : '&mdash;';
  let peak = 0;

  // ---- transport ----
  let looping = true;
  player.play({ loop: looping });

  $('play').addEventListener('click', () => {
    if (player.playing) { player.pause(); $('play').textContent = 'Play'; }
    else { player.play({ loop: looping }); $('play').textContent = 'Pause'; }
  });
  $('loop').addEventListener('click', e => {
    looping = !looping;
    e.target.classList.toggle('on', looping);
    if (player.playing) player.play({ loop: looping });
  });
  $('scrub').addEventListener('input', e => {
    if (player.playing) { player.pause(); $('play').textContent = 'Play'; }
    player.seek(player.duration * e.target.value / 1000);
  });

  const seg = (id, fn) => $(id).addEventListener('click', e => {
    if (e.target.tagName !== 'BUTTON') return;
    [...$(id).children].forEach(b => b.classList.toggle('on', b === e.target));
    fn(e.target.dataset.v);
  });
  seg('speed', v => { player.speed = parseFloat(v); });
  seg('bg', v => { stage.dataset.bg = v; });
  seg('fit', v => { player.fit = v; player.resize(); });

  // ---- identify ----
  cv.addEventListener('pointerdown', e => {
    const hit = player.pick(e.clientX, e.clientY);
    document.querySelectorAll('.grp').forEach(g => g.classList.remove('hit'));
    if (!hit) { $('picked').textContent = 'nothing there'; return; }
    $('picked').textContent = (hit.id || '(no id)') + '\n' + hit.group;
    const row = document.querySelector(`.grp[data-g="${CSS.escape(hit.group)}"]`);
    if (row) row.classList.add('hit');
  });

  // ---- group colours ----
  const bank = $('groups');
  const original = new Map();
  (doc.groups || []).forEach(g => {
    const row = document.createElement('label');
    row.className = 'grp';
    row.dataset.g = g;
    row.innerHTML = `<input type="color" value="#cccccc"><span>${g}</span>`;
    row.querySelector('input').addEventListener('input', ev => {
      player.setGroupColor(g, ev.target.value);
      original.set(g, ev.target.value);
    });
    bank.appendChild(row);
  });
  if (!(doc.groups || []).length) {
    bank.innerHTML = '<p class="hint">This bake has no named groups.</p>';
  }
  $('reset').addEventListener('click', () => {
    (doc.groups || []).forEach(g => player.setGroupColor(g, null));
    original.clear();
  });

  // ---- readouts ----
  function tick() {
    const d = player.visible;
    if (typeof d === 'number') {
      if (d > peak) peak = d;
      $('s-drawn').textContent = d;
      $('s-peak').textContent = peak;
    }
    $('time').textContent = fmt(player.time) + ' / ' + fmt(player.duration) + ' s';
    if (player.playing) {
      $('scrub').value = Math.round(1000 * player.time / (player.duration || 1));
    }
    requestAnimationFrame(tick);
  }
  tick();
  addEventListener('resize', () => player.resize());
})();
</script>
</body>
</html>
"""


def to_html(vec, name="", source="", include_ids=True, coord_decimals=2) -> str:
    doc = emit_json.to_dict(vec, include_ids=include_ids,
                            coord_decimals=coord_decimals)
    payload = json.dumps(doc, separators=(",", ":"))
    # `</script>` inside a string literal would close the block early
    payload = payload.replace("</", "<\\/")
    return (TEMPLATE
            .replace("__PLAYER__", _player_source())
            .replace("__DOC__", payload)
            .replace("__NAME__", name or "Untitled bake")
            .replace("__SOURCE__", source or ""))


def write(vec, path, name="", source="", include_ids=True, coord_decimals=2):
    text = to_html(vec, name=name, source=source,
                   include_ids=include_ids, coord_decimals=coord_decimals)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return len(text.encode("utf-8"))
