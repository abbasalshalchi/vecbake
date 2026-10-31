// Shoots scripts/og_card.html to the 1200x630 PNG the site's og:image tags name.
//   node scripts/film_og_card.mjs <dev-server-origin> <out.png>
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

const [ORIGIN, OUT] = process.argv.slice(2);
const PORT = 9349, W = 1200, H = 630;
const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', ['--headless=new', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'lerpa-card-profile')}`, `--window-size=${W},${H}`, '--hide-scrollbars',
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
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
await send('Page.enable');
await send('Page.navigate', { url: `${ORIGIN}/scripts/og_card.html` });
for (let i = 0; i < 240; i++) {
  if (await ev('!!window.__card')) break;
  await new Promise(r => setTimeout(r, 250));
}
// Mid-arc, drawn with the line weight the card asks for, and shot before the hero's
// own loop has a reason to repaint.
await ev(`(() => { const h = __card, t = h.player.duration / 2;
  h.time = t; h.player.time = t; h.player.render(t);
  return new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))); })()`);
const { result: { data } } = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(OUT, Buffer.from(data, 'base64'));
console.log(OUT, Buffer.from(data, 'base64').length, 'bytes');
ws.close(); chrome.kill(); process.exit(0);
