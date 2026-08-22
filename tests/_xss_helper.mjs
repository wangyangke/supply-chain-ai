// XSS verification helper for the supply-chain-ai dashboard.
// Runs the REAL esc()/safeUrl() functions extracted from dashboard.html,
// plus an optional end-to-end DOM render via linkedom.
//
// Usage:
//   node _xss_helper.mjs <PROJECT_ROOT> functions
//   node _xss_helper.mjs <PROJECT_ROOT> dom
//
// Emits a JSON object on stdout. Non-zero exit => environment problem
// (caller should treat as SKIP, not FAIL).

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const ROOT = process.argv[2] || process.cwd();
const MODE = process.argv[3] || 'functions';
const HELP = path.join(ROOT, 'dashboard.html');

let html;
try {
  html = fs.readFileSync(HELP, 'utf8');
} catch (e) {
  console.log(JSON.stringify({ error: 'cannot read dashboard.html: ' + e.message }));
  process.exit(2);
}

// Grab the largest inline <script> (the app code) — ignore <script src=...>.
const scripts = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (!scripts.length) {
  console.log(JSON.stringify({ error: 'no inline script found' }));
  process.exit(2);
}
const app = scripts.sort((a, b) => b.length - a.length)[0];

// Extract a top-level `function NAME(...) { ... }` body by brace counting.
function extractFn(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\([^)]*\\)\\s*\\{');
  const m = app.match(re);
  if (!m) return null;
  let i = m.index + m[0].length;
  let depth = 1;
  while (i < app.length && depth > 0) {
    const ch = app[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    i++;
  }
  return app.slice(m.index, i);
}

if (MODE === 'functions') {
  const escSrc = extractFn('esc');
  const safeUrlSrc = extractFn('safeUrl');
  if (!escSrc || !safeUrlSrc) {
    console.log(JSON.stringify({ error: 'esc/safeUrl not found', hasEsc: !!escSrc, hasSafeUrl: !!safeUrlSrc }));
    process.exit(2);
  }
  // safeUrl() uses location.href as the URL base; provide it like a browser.
  globalThis.location = { href: 'http://localhost/' };
  // eslint-disable-next-line no-eval
  const factory = new Function('location', escSrc + '\n' + safeUrlSrc + '\nreturn { esc, safeUrl };');
  const { esc, safeUrl } = factory(globalThis.location);

  // A payload is neutralized iff esc() left NO raw dangerous chars behind
  // (< > " '). An escaped "&lt;img onerror=&quot;...&quot;&gt;" is inert text
  // and must NOT count as dangerous, even though "onerror=" appears literally.
  const RAW_DANGER = /[<>'"]/;
  const payloads = {
    img_onerror: '<img src=x onerror="window.__pwned=1">',
    script_tag: '<script>alert(1)</script>',
    svg_onload: '<svg/onload=alert(1)>',
    iframe: '<iframe src=javascript:alert(1)>',
    attribute_breakout: '" onmouseover="alert(1)',
    event_handler: 'x" onclick="alert(1)',
    amp_quote: "AT&T said 'hi' & <b>bold</b>",
  };

  const out = {};
  for (const [k, p] of Object.entries(payloads)) {
    const e = esc(p);
    out[k] = { neutralized: !RAW_DANGER.test(e), escaped: e };
  }
  out.javascript_url_blocked = safeUrl('javascript:alert(1)') === '#';
  out.https_preserved = safeUrl('https://example.com/a?b=1') === 'https://example.com/a?b=1';
  out.relative_preserved = /^https?:\/\//.test(safeUrl('/targets/nvidia'));
  out.all_neutralized = Object.values(out)
    .filter(v => v && typeof v === 'object')
    .every(v => v.neutralized);
  console.log(JSON.stringify(out, null, 2));
  process.exit(0);
}

if (MODE === 'dom') {
  let linkedom;
  try {
    linkedom = await import('linkedom');
  } catch (e) {
    console.log(JSON.stringify({ skipped: 'linkedom unavailable: ' + e.message }));
    process.exit(3);
  }
  const { parseHTML } = linkedom;
  const { window, document } = parseHTML(html);

  globalThis.window = window;
  globalThis.document = document;
  globalThis.location = { href: 'http://localhost/', pathname: '/' };
  globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  globalThis.alert = () => {};
  try {
    globalThis.localStorage = window.localStorage || { getItem: () => null, setItem: () => {} };
  } catch (_) { /* ignore */ }

  // Expose internals so we can inject a malicious dataset and trigger a render.
  const expose = `
    ;globalThis.__api = {
      setDATA: function(d){ DATA = d; },
      showRelDetail: (typeof showRelDetail !== 'undefined') ? showRelDetail : null,
      hasAllData: (typeof ALL_DATA !== 'undefined')
    };`;
  let api;
  try {
    const factory = new Function(
      'window', 'document', 'fetch', 'location', 'requestAnimationFrame', 'alert', 'localStorage',
      app + expose
    );
    factory(window, document, globalThis.fetch, globalThis.location, globalThis.requestAnimationFrame, globalThis.alert, globalThis.localStorage);
    api = globalThis.__api;
  } catch (e) {
    console.log(JSON.stringify({ error: 'app execution failed: ' + e.message }));
    process.exit(2);
  }

  if (!api || !api.showRelDetail) {
    console.log(JSON.stringify({ error: 'showRelDetail not exposed' }));
    process.exit(2);
  }

  const X = '<img src=x onerror="window.__pwned=1">';
  const rel = {
    id: 'rel_xss',
    type: 'supplier',
    status: 'confirmed',
    confidence_score: 80,
    source_company_id: 'co_x',
    target_company_id: 'co_y',
    summary: X,
    valid_from: '2020-01-01',
    valid_to: '2030-01-01',
    evidence_ids: ['ev_x'],
    evidence_items: [{
      id: 'ev_x',
      source_type: 'sec_filing',
      publisher: X,
      published_at: X,
      quote: X,
      source_url: 'javascript:alert(1)',
      evidence_locator: X,
      license_note: X,
      accessed_at: X,
      access_restriction: 'public',
      support_level: 'indirect',
      independence_group: X,
      access_notes: X,
    }],
  };
  const coX = {
    id: 'co_x', name: X, stock_code: X, exchange: X, country: X,
    entity_type: 'company', sector: X, isin: X, description: X,
  };
  const coY = { id: 'co_y', name: 'Y Corp' };

  try {
    api.setDATA({
      dataset: { as_of: '2024-01-01', schema_version: '2.0' },
      companies: [coX, coY],
      relationships: [rel],
      evidence: {},
    });
    api.showRelDetail('rel_xss');
  } catch (e) {
    console.log(JSON.stringify({ error: 'render failed: ' + e.message }));
    process.exit(2);
  }

  const modal = document.getElementById('modal-content');
  const rendered = modal ? modal.innerHTML : '';
  // Active payload = a real <img>/<script>/<svg>/<iframe> or an on* handler,
  // but NOT the escaped &lt;img form.
  const active = /<img[\s>]|<script[\s>]|<svg[\s>]|<iframe[\s>]|on(error|load|mouseover|click|focus)\s*=|<a[^>]+href=["']javascript:/i.test(rendered)
    && !/&lt;img/.test(rendered);
  console.log(JSON.stringify({
    active_payload: !!active,
    linked: rendered.includes('javascript:') ? 'FOUND_RAW_JS_URL' : 'no_raw_js_url',
    length: rendered.length,
    snippet: rendered.slice(0, 160),
  }));
  process.exit(0);
}

console.log(JSON.stringify({ error: 'unknown mode: ' + MODE }));
process.exit(2);
