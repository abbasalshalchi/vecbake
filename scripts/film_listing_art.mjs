// Shoots the two images the extensions.blender.org listing requires:
// listing/icon.svg at 256x256 and scripts/listing_art.html at 1920x1080.
//   node scripts/film_listing_art.mjs <dev-server-origin> <out-dir> [voyager|apollo] [tilt]
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const [ORIGIN, OUT, MODEL = 'voyager', TILT = '-45'] = process.argv.slice(2);
const PORT = 9351;
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'lerpa-listing-profile')}`, '--window-size=1920,1080', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--no-first-run', 'about:blank'], { stdio: 'ignore' });
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
const shoot = async name => {
  const { result: { data } } = await send('Page.captureScreenshot', { format: 'png' });
  const path = join(OUT, name);
  writeFileSync(path, Buffer.from(data, 'base64'));
  console.log(path, Buffer.from(data, 'base64').length, 'bytes');
};
await send('Page.enable');

await send('Emulation.setDeviceMetricsOverride', { width: 256, height: 256, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: `${ORIGIN}/listing/icon.svg` });
await new Promise(r => setTimeout(r, 800));
await shoot('icon.png');

await send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: `${ORIGIN}/scripts/listing_art.html?model=${MODEL}&tilt=${TILT}` });
let ready = false;
for (let i = 0; i < 480 && !ready; i++) {
  ready = await ev('!!window.__card');
  if (!ready) await new Promise(r => setTimeout(r, 250));
}
if (!ready) throw new Error('the page never mounted its model');
await ev(`(() => { const h = __card, t = h.player.duration / 2;
  h.time = t; h.player.time = t; h.player.render(t);
  return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); })()`);
await shoot(`featured-${MODEL}.png`);
ws.close(); chrome.kill(); process.exit(0);
