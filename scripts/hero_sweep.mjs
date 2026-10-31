// The hero as a visitor meets it: a real Chrome window, the real homepage, a real
// pointer swept across it. Counts how often the hero's canvas actually repainted and
// how long the frames were, at each window size.
//   node hero_sweep.mjs <url> [default|soft] [hp]     DSF=1.25 for a scaled display
// `soft` forces a CPU canvas on a page that does not ask for one; `hp` asks for the discrete GPU.
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const PAGE = process.argv[2], PORT = 9343;
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [`--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'lerpa-sweep-profile')}`, '--window-position=40,40', '--window-size=1400,900',
  '--force-device-scale-factor=1', '--no-first-run', '--no-default-browser-check',
  '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding', '--disable-background-timer-throttling',
  ...(process.argv[4] === 'hp' ? ['--force_high_performance_gpu'] : []),
  'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());
let url;
for (let i = 0; i < 60 && !url; i++) {
  try { url = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === 'page')?.webSocketDebuggerUrl; }
  catch { /* not up */ }
  if (!url) await new Promise(r => setTimeout(r, 250));
}
const ws = new WebSocket(url);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0; const waiting = new Map();
ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id) { waiting.get(m.id)(m); waiting.delete(m.id); } });
const send = (method, params = {}) => new Promise(r => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async expression => {
  const m = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (m.result.exceptionDetails) throw new Error(m.result.exceptionDetails.exception?.description);
  return m.result.result.value;
};
await send('Page.enable');
// `soft` asks for a CPU-rasterised canvas, the way a page that reads pixels back would.
if (process.argv[3] === 'soft') await send('Page.addScriptToEvaluateOnNewDocument', { source: `
  const get = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, o) { return get.call(this, type, type === '2d' ? { ...o, willReadFrequently: true } : o); };
` });
console.log('canvas mode', process.argv[3] || 'default');
await send('Page.bringToFront');
await send('Emulation.setFocusEmulationEnabled', { enabled: true });
console.log('visibility', await ev('document.visibilityState'), await ev('new Promise(r => { let n = 0; const t = setTimeout(() => r("rAF stalled"), 2000); const f = () => { if (++n === 10) { clearTimeout(t); r("rAF running"); } else requestAnimationFrame(f); }; requestAnimationFrame(f); })'));
console.log('renderer', await ev(`(() => { const gl = document.createElement('canvas').getContext('webgl'); const x = gl && gl.getExtension('WEBGL_debug_renderer_info'); return x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : 'none'; })()`));

for (const [W, H] of [[1280, 800], [1536, 864], [1920, 1080]]) {
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: +(process.env.DSF || 1), mobile: false });
  await send('Page.navigate', { url: PAGE });
  await new Promise(r => setTimeout(r, 800));
  for (let i = 0; i < 240; i++) {
    if (await ev('!!(window.__hero && window.__hero.player)')) break;
    await new Promise(r => setTimeout(r, 250));
  }
  await new Promise(r => setTimeout(r, 1500));
  // Wrap the player's render so the count is repaints, not animation frames.
  await ev(`(() => {
    const p = __hero.player, c = p.canvas;
    window.__s = { renders: 0, js: [], raf: [], last: 0, on: false };
    const r = p.render.bind(p), d = p.drawInto.bind(p);
    p.drawInto = (...a) => { const t = performance.now(); d(...a); if (__s.on) { __s.renders++; __s.js.push(performance.now() - t); } };
    const loop = now => { if (__s.on && __s.last) __s.raf.push(now - __s.last); __s.last = now; requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
    return [c.width, c.height, p.dpr, p.scaleX];
  })()`).then(c => console.log(`\nviewport ${W}x${H}: canvas ${c[0]}x${c[1]} dpr ${c[2]} scale ${c[3].toFixed(3)}`));
  const box = await ev(`(() => { const r = document.getElementById('rig').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()`);
  await ev('__s.on = true; 1');
  const T = 5000, start = Date.now();
  while (Date.now() - start < T) {
    const k = (Date.now() - start) / T;
    const x = box[0] + box[2] * (0.5 + 0.45 * Math.sin(k * Math.PI * 6));
    const y = box[1] + box[3] * (0.5 + 0.3 * Math.cos(k * Math.PI * 4));
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none' });
    await new Promise(r => setTimeout(r, 8));
  }
  const s = await ev(`(() => {
    __s.on = false;
    const med = a => { const b = [...a].sort((x, y) => x - y); return b[b.length >> 1] || 0; };
    const p95 = a => { const b = [...a].sort((x, y) => x - y); return b[Math.floor(b.length * 0.95)] || 0; };
    return { renders: __s.renders, frames: __s.raf.length, js: med(__s.js), raf: med(__s.raf), rafP95: p95(__s.raf) };
  })()`);
  console.log(`  ${(s.renders / (T / 1000)).toFixed(0)} repaints/s, ${(s.frames / (T / 1000)).toFixed(0)} frames/s; draw calls ${s.js.toFixed(1)} ms; frame median ${s.raf.toFixed(1)} ms, p95 ${s.rafP95.toFixed(1)} ms`);
}
ws.close(); chrome.kill(); process.exit(0);
