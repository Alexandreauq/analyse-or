// tools/capture.mjs
// Captures d'écran du site (docs/) à une taille d'écran exacte, via Chrome
// headless piloté en CDP — outil de dev, jamais servi par GitHub Pages.
// Contrairement à `chrome --screenshot`, la largeur n'est pas plafonnée à
// ~500px : Emulation.setDeviceMetricsOverride donne un vrai viewport iPhone.
//
// Usage :
//   node tools/capture.mjs --path "#or" --width 393 --height 852 \
//     [--theme light|dark] [--safe-top 59] [--safe-bottom 34] \
//     [--eval-file tools/stub_ask.js] [--eval "js"] [--wait 2500] [--after 800] \
//     --out capture.png
//
// Safe areas : headless ne connaît pas l'encoche, env(safe-area-inset-*)
// vaut toujours 0. Le site passe donc par --safe-top/--safe-bottom/... (voir
// :root de docs/index.html) que cet outil surcharge, puis dessine un voile
// noir de la même hauteur pour représenter l'encoche et l'indicateur
// d'accueil : tout ce qui apparaît sous ce voile serait masqué sur iPhone.

import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'docs');
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.css': 'text/css',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json' };

function args() {
  const a = process.argv.slice(2);
  const o = { path: '', width: 1440, height: 900, theme: 'dark', safeTop: 0, safeBottom: 0,
    evalFile: null, eval: null, wait: 2500, after: 800, out: 'capture.png' };
  for (let i = 0; i < a.length; i += 2) {
    const k = a[i].replace(/^--/, '').replace(/-(\w)/g, (_, c) => c.toUpperCase());
    o[k] = ['width', 'height', 'safeTop', 'safeBottom', 'wait', 'after'].includes(k) ? Number(a[i + 1]) : a[i + 1];
  }
  return o;
}

function freePort() {
  return new Promise(res => { const s = net.createServer(); s.listen(0, () => { const p = s.address().port; s.close(() => res(p)); }); });
}

function serve(port) {
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(req.url.split('?')[0]);
    const file = path.join(ROOT, url === '/' ? 'index.html' : url);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise(r => server.listen(port, '127.0.0.1', () => r(server)));
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const o = args();
  const sitePort = await freePort();
  const cdpPort = await freePort();
  const server = await serve(sitePort);
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'capture-'));
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
    `--remote-debugging-port=${cdpPort}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
  try {
    let target = null;
    for (let i = 0; i < 50 && !target; i++) {
      try {
        const list = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
        target = list.find(t => t.type === 'page');
      } catch { await sleep(200); }
    }
    if (!target) throw new Error('Chrome ne répond pas en CDP');
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
    let id = 0;
    const pending = new Map();
    const events = [];
    ws.onmessage = m => {
      const d = JSON.parse(m.data);
      if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d.method);
    };
    const send = (method, params = {}) => new Promise((r, j) => {
      const n = ++id;
      pending.set(n, d => d.error ? j(new Error(method + ': ' + d.error.message)) : r(d.result));
      ws.send(JSON.stringify({ id: n, method, params }));
    });

    await send('Page.enable');
    // Sans fenêtre active, focus() ne déclenche ni :focus ni l'événement focus.
    await send('Emulation.setFocusEmulationEnabled', { enabled: true });
    const mobile = o.width < 1024;
    await send('Emulation.setDeviceMetricsOverride', { width: o.width, height: o.height, deviceScaleFactor: 1, mobile });
    if (mobile) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
    const preScript = `
      try { localStorage.setItem('theme', ${JSON.stringify(o.theme)}); } catch (e) {}
      document.addEventListener('DOMContentLoaded', () => {
        const st = document.createElement('style');
        st.textContent = 'html:root{--safe-top:${o.safeTop}px;--safe-bottom:${o.safeBottom}px}'
          + '.__cap-veil{position:fixed;left:0;right:0;background:#000;opacity:.85;z-index:2147483647;pointer-events:none}';
        document.head.appendChild(st);
        for (const [pos, h] of [['top', ${o.safeTop}], ['bottom', ${o.safeBottom}]]) {
          if (!h) continue;
          const v = document.createElement('div'); v.className = '__cap-veil'; v.style[pos] = '0'; v.style.height = h + 'px';
          document.body.appendChild(v);
        }
      });`;
    await send('Page.addScriptToEvaluateOnNewDocument', { source: preScript });
    await send('Page.navigate', { url: `http://127.0.0.1:${sitePort}/index.html${o.path}` });
    for (let i = 0; i < 100 && !events.includes('Page.loadEventFired'); i++) await sleep(100);
    await sleep(o.wait);
    const scripts = [];
    if (o.evalFile) scripts.push(fs.readFileSync(o.evalFile, 'utf8'));
    if (o.eval) scripts.push(o.eval);
    for (const s of scripts) {
      const r = await send('Runtime.evaluate', { expression: `(async () => { ${s} })()`, awaitPromise: true });
      if (r.exceptionDetails) console.error('eval:', r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    }
    if (scripts.length) await sleep(o.after);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(o.out, Buffer.from(shot.data, 'base64'));
    console.log(`capture: ${o.out} (${o.width}x${o.height}, ${o.theme})`);
    ws.close();
  } finally {
    chrome.kill();
    server.close();
    setTimeout(() => { try { fs.rmSync(profile, { recursive: true, force: true }); } catch {} }, 500);
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
