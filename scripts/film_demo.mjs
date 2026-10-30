// SPDX-License-Identifier: GPL-3.0-or-later
// VecBake -- Copyright (C) 2026 Abbas Alshalchi
/*
 * Film lerpa-website's /demo frame by frame.
 *
 *   python scripts/devserver.py 8765
 *   node scripts/film_demo.mjs <out_dir> [--only <first> <last>] [--fps 30]
 *
 * Runs the page in headless Chrome on a clock this script owns: every frame moves
 * the time on by exactly 1/fps, then takes a screenshot. So the clip has no dropped
 * frames however long a screenshot takes. The pointer is a script, but it reaches the
 * page as real input events, so hit tests, clicks and the hero's own logic all run
 * as they do for a visitor.
 *
 * Writes <out_dir>/frames/000001.png onward, ready for scripts/encode_frames.py.
 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const argv = process.argv.slice(2);
const OUT = argv[0];
const flag = (name, n = 1) => {
  const i = argv.indexOf(name);
  return i < 0 ? null : argv.slice(i + 1, i + 1 + n);
};
const FPS = +(flag('--fps')?.[0] ?? 30);
const only = flag('--only', 2)?.map(Number) ?? null;
const SIZE = 1080, PORT = 9333;
const URL_ = 'http://localhost:8765/demo.html?film';
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const FRAMES = join(OUT, 'frames');
mkdirSync(FRAMES, { recursive: true });

// ---- the page's clock --------------------------------------------------------
// Installed before any of the page runs. rAF callbacks wait for __advance, and
// CSS animations are stepped by hand, so nothing in the page moves on its own.
const CLOCK = `(() => {
  let now = 0, id = 0, queue = [];
  const real = performance.now.bind(performance);
  window.__realNow = real;
  performance.now = () => now;
  window.requestAnimationFrame = cb => { queue.push(cb); return ++id; };
  window.cancelAnimationFrame = () => {};
  window.__advance = ms => {
    now += ms;
    const q = queue; queue = [];
    for (const cb of q) cb(now);
    for (const a of document.getAnimations()) {
      a.pause();
      const end = a.effect.getComputedTiming().endTime;
      const t = (a.currentTime || 0) + ms;
      if (t >= end) a.finish(); else a.currentTime = t;
    }
  };
})();`;

// ---- a small CDP client --------------------------------------------------------
const profile = join(tmpdir(), 'lerpa-film-profile');
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`, `--window-size=${SIZE},${SIZE}`, '--hide-scrollbars',
  '--force-device-scale-factor=1', '--no-first-run', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());

async function target() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = list.find(t => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error('Chrome did not come up');
}
const ws = new WebSocket(await target());
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0;
const waiting = new Map();
ws.addEventListener('message', e => {
  const m = JSON.parse(e.data);
  if (!m.id) return;
  const w = waiting.get(m.id); waiting.delete(m.id);
  m.error ? w.reject(new Error(JSON.stringify(m.error))) : w.resolve(m.result);
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  waiting.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});
const ev = async expression => {
  const r = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};

await send('Page.enable');
await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: SIZE, height: SIZE, deviceScaleFactor: 1, mobile: false });
await send('Page.addScriptToEvaluateOnNewDocument', { source: CLOCK });
await send('Page.navigate', { url: URL_ });
for (let i = 0; i < 240; i++) {
  if (await ev(`!!window.__film && !document.getElementById('big-size').textContent.includes('…')`)) break;
  await new Promise(r => setTimeout(r, 250));
}
await ev('document.fonts.ready.then(() => 1)');
if (!(await ev('!!window.__film'))) throw new Error('the page did not mount');

// ---- the performance -------------------------------------------------------------
let pointer = [SIZE * 0.2, SIZE * 0.8], frame = 0;

const mouse = (type, x, y, extra = {}) => send('Input.dispatchMouseEvent', { type, x, y, button: 'none', ...extra });
async function shoot() {
  await ev(`__advance(${1000 / FPS})`);
  frame++;
  if (!only || (frame >= only[0] && frame <= only[1])) {
    const { data } = await send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
    writeFileSync(join(FRAMES, String(frame).padStart(6, '0') + '.png'), Buffer.from(data, 'base64'));
  }
  if (frame % 30 === 0) console.log(`[film] frame ${frame} (${(frame / FPS).toFixed(1)} s)`);
}
const ease = u => u * u * (3 - 2 * u);
const hold = async s => { for (let i = 0, n = Math.round(s * FPS); i < n; i++) await shoot(); };
async function glide(x, y, s) {
  const [x0, y0] = pointer, n = Math.max(1, Math.round(s * FPS));
  for (let i = 1; i <= n; i++) {
    const u = ease(i / n);
    pointer = [x0 + (x - x0) * u, y0 + (y - y0) * u];
    await mouse('mouseMoved', pointer[0], pointer[1]);
    await shoot();
  }
}
async function click() {
  const [x, y] = pointer;
  await mouse('mousePressed', x, y, { button: 'left', clickCount: 1, buttons: 1 });
  await shoot();
  await mouse('mouseReleased', x, y, { button: 'left', clickCount: 1 });
}
// The point nearest the upper right that is on the canvas with nothing drawn near it.
const emptySpot = () => ev(`(() => {
  const p = __film.hero.player, c = document.getElementById('ship');
  const r = c.getBoundingClientRect(), w = c.width / p.dpr, a = Math.PI / 4;
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let best = null, bestD = Infinity;
  for (let y = 40; y < ${SIZE}; y += 20) for (let x = 40; x < ${SIZE}; x += 20) {
    const dx = x - cx, dy = y - cy;
    const ux = dx * Math.cos(a) - dy * Math.sin(a), uy = dx * Math.sin(a) + dy * Math.cos(a);
    if (Math.abs(ux) > w / 2 - 8 || Math.abs(uy) > w / 2 - 8) continue;
    if (p.pick(r.left + ux + w / 2, r.top + uy + w / 2, 40)) continue;
    const d = Math.hypot(x - ${SIZE} * 0.85, y - ${SIZE} * 0.2);
    if (d < bestD) { bestD = d; best = [x, y]; }
  }
  return best;
})()`);

// A click that lands somewhere else is still a working click, and looks like a
// slip in the finished clip. So stop rather than film it.
async function expectNode(want) {
  await shoot();
  const node = await ev('__film.hero.node');
  if (node !== want) throw new Error(`frame ${frame}: expected node "${want}", got "${node}"`);
}
const key = (code, text) => send('Input.dispatchKeyEvent', { type: 'keyDown', key: text, code, text: text.length === 1 ? text : undefined, windowsVirtualKeyCode: text.length === 1 ? text.toUpperCase().charCodeAt(0) : 39 })
  .then(() => send('Input.dispatchKeyEvent', { type: 'keyUp', key: text, code }));
const scene = i => ev(`__film.show(${i}); 1`);
// Where a part is on screen right now, through the same un-tilt the page uses.
const spot = group => ev(`(() => {
  const h = __film.hero, p = h.player, c = document.getElementById('ship');
  const r = c.getBoundingClientRect(), w = c.width / p.dpr, a = -Math.PI / 4;
  const b = p.groupBounds(${JSON.stringify(group)}, h.time);
  const dx = (b.x + b.w / 2) * p.scaleX + p.offX - w / 2, dy = (b.y + b.h / 2) * p.scaleY + p.offY - w / 2;
  return [r.left + r.width / 2 + dx * Math.cos(a) - dy * Math.sin(a),
          r.top + r.height / 2 + dx * Math.sin(a) + dy * Math.cos(a)];
})()`);

// 1. It turns.
await scene(0);
await mouse('mouseMoved', SIZE * 0.1, SIZE * 0.9); pointer = [SIZE * 0.1, SIZE * 0.9];
await hold(0.5);
await glide(SIZE * 0.9, SIZE * 0.1, 2.0);
await glide(SIZE * 0.3, SIZE * 0.68, 1.7);

// 2. Real parts.
await scene(1);
await hold(0.3);
for (const g of ['apollo_003', 'dock_001']) {
  const [x, y] = await spot(g);
  await glide(x, y, 0.8);
  await hold(0.25);
}
const [ox, oy] = await spot('soyuz_005');
await glide(ox, oy, 0.7);
await hold(0.35);
await click();
await expectNode('orbital');
await hold(2.2);
// Back out through empty canvas. The canvas is a diamond once tilted, so the
// stage's own corners are off it and a click there reaches nothing.
const [ex, ey] = await emptySpot();
await glide(ex, ey, 0.5);
await click();
await expectNode('');
await hold(0.8);

// 3. Recolour, while the pointer keeps it turning.
await scene(2);
const drift = async (s, a, b) => {
  const n = Math.round(s * FPS);
  for (let i = 1; i <= n; i++) {
    const u = i / n;
    pointer = [SIZE * (a + (b - a) * u), SIZE * (1 - (a + (b - a) * u))];
    await mouse('mouseMoved', pointer[0], pointer[1]);
    await shoot();
  }
};
await drift(0.9, 0.25, 0.4);
await ev('__film.preset(1); 1'); await drift(0.9, 0.4, 0.55);
await ev('__film.preset(2); 1'); await drift(0.9, 0.55, 0.7);
await ev('__film.preset(3); 1'); await drift(0.9, 0.7, 0.55);
await ev('__film.preset(0); 1'); await drift(0.5, 0.55, 0.45);

// 4. Zoom against video.
await scene(3);
await glide(SIZE * 0.5, SIZE * 0.86, 0.3);
await hold(1.0);
await ev('__film.zoomIn(); 1');
await hold(4.2);
await hold(1.2);

// 5. Where it comes from.
await scene(4);
await hold(1.2);
// The pointer goes to where Bake & Export is in the frame, and presses as the
// real bake started. The button is at (1264, 747) of the 1400 x 960 shot.
const pressAt = await ev('__film.blenderClip()');
const scale = SIZE * 0.94 / 1400;
const bx = SIZE * 0.03 + 1264 * scale, by = SIZE * 0.97 - (960 - 747) * scale;
await glide(SIZE * 0.5, SIZE * 0.6, 0.01);
const CLIP = 4.6;
for (let i = 0, n = Math.round(CLIP * FPS); i < n; i++) {
  const s = i / FPS;
  // After the press the bake plays at 4x, and the frame says so.
  const fast = s > pressAt;
  await ev(`__film.blenderAt(${fast ? pressAt + (s - pressAt) * 4 : s}, ${fast})`);
  const u = Math.min(1, s / Math.max(0.1, pressAt - 0.1));
  pointer = [SIZE * 0.5 + (bx - SIZE * 0.5) * ease(u), SIZE * 0.6 + (by - SIZE * 0.6) * ease(u)];
  await mouse('mouseMoved', pointer[0], pointer[1]);
  if (Math.abs(s - pressAt) < 0.5 / FPS) await click(); else await shoot();
}

// 6. Two files.
await scene(5);
await hold(4.0);

// 7. A real SVG.
await scene(6);
await hold(1.8);
await ev('__film.setJs(true); 1');
await hold(2.8);

// 8. End card.
await scene(7);
await hold(3.0);

console.log(`[film] done: ${frame} frames, ${(frame / FPS).toFixed(1)} s`);
writeFileSync(join(OUT, 'film.json'), JSON.stringify({ frames: frame, fps: FPS, size: SIZE }));
ws.close();
chrome.kill();
if (existsSync(profile)) { try { rmSync(profile, { recursive: true, force: true }); } catch { /* still held */ } }
process.exit(0);
