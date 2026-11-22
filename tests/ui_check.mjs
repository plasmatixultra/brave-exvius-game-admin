#!/usr/bin/env node
/* UI acceptance checks for the ported page (plan Task 10, Step 6).
 *
 * Drives a headless Chrome over the DevTools protocol (Node's built-in WebSocket) and asserts the
 * two things the plan calls out but a screenshot cannot show:
 *   - no rendered text below 13.5px in any view
 *   - no horizontal scroll at 1280 and 1440
 * It also collects uncaught exceptions and failed requests, and exercises the cart across views.
 *
 *   node tests/ui_check.mjs [http://127.0.0.1:8750]
 *
 * Exits 0 when every check passes.
 */

import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const BASE = process.argv[2] || 'http://127.0.0.1:8750';
const CHROME = process.env.CHROME_BIN
  || `${process.env.HOME}/.cache/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-linux64/chrome-headless-shell`;
const PORT = 9333;
const VIEWS = ['grants', 'units', 'kit', 'ability', 'vision', 'log'];
const MIN_FONT = 13.5;

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'ok  ' : 'FAIL'} - ${name}${detail ? ' · ' + detail : ''}`);
}

async function main() {
  const chrome = spawn(CHROME, [
    '--headless', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    `--remote-debugging-port=${PORT}`, '--window-size=1280,1400', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', () => {});
  process.on('exit', () => chrome.kill('SIGTERM'));

  let target = null;
  for (let attempt = 0; attempt < 40 && !target; attempt += 1) {
    await sleep(250);
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      target = list.find((t) => t.type === 'page');
    } catch { /* not up yet */ }
  }
  if (!target) throw new Error('chrome did not expose a page target');

  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });

  let id = 0;
  const pending = new Map();
  const consoleErrors = [];
  const failedRequests = [];
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
      return;
    }
    if (message.method === 'Runtime.exceptionThrown') {
      consoleErrors.push(message.params.exceptionDetails.text
        + ' ' + (message.params.exceptionDetails.exception?.description || ''));
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      consoleErrors.push(message.params.args.map((a) => a.value || a.description).join(' '));
    }
    if (message.method === 'Network.loadingFailed') {
      failedRequests.push(message.params.errorText);
    }
  });

  function send(method, params = {}) {
    const messageId = ++id;
    socket.send(JSON.stringify({ id: messageId, method, params }));
    return new Promise((resolve) => pending.set(messageId, resolve));
  }

  async function evaluate(expression) {
    const reply = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (reply.result?.exceptionDetails) {
      throw new Error(reply.result.exceptionDetails.text + ' '
        + (reply.result.exceptionDetails.exception?.description || ''));
    }
    return reply.result?.result?.value;
  }

  async function resize(width) {
    await send('Emulation.setDeviceMetricsOverride',
      { width, height: 1400, deviceScaleFactor: 1, mobile: false });
  }

  await send('Runtime.enable');
  await send('Network.enable');
  await send('Page.enable');

  for (const width of [1280, 1440]) {
    for (const view of VIEWS) {
      await resize(width);
      await send('Page.navigate', { url: `${BASE}/#${view}` });
      await sleep(1500);
      // wait until the app has data (the wallet stops showing the em dash)
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const ready = await evaluate(`document.querySelector('#wallet-gil').textContent !== '—'`);
        if (ready) break;
        await sleep(500);
      }
      await sleep(500);

      const small = await evaluate(`[...document.querySelectorAll('*')]
        .filter(e => e.offsetParent && parseFloat(getComputedStyle(e).fontSize) < ${MIN_FONT})
        .map(e => e.tagName + '.' + (e.className || '(none)') + ':' + getComputedStyle(e).fontSize)`);
      check(`${width}px · ${view} · no text below ${MIN_FONT}px`,
        Array.isArray(small) && small.length === 0, JSON.stringify(small || []).slice(0, 200));

      const overflow = await evaluate(
        `({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})`);
      check(`${width}px · ${view} · no horizontal scroll`,
        overflow.scroll <= overflow.client + 1, `${overflow.scroll} vs ${overflow.client}`);

      const active = await evaluate(
        `document.querySelector('.view.on')?.dataset.view === '${view}'`);
      check(`${width}px · ${view} · view is active`, active === true);
    }
  }

  // cart across views: add a gil edit on grants, a kit edit is server-dependent, so use points + gil
  await resize(1440);
  await send('Page.navigate', { url: `${BASE}/#grants` });
  await sleep(1500);
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const ready = await evaluate(`document.querySelector('#wallet-gil').textContent !== '—'`);
    if (ready) break;
    await sleep(500);
  }
  const cart = await evaluate(`(function () {
    document.querySelector('#gil-input').value = '1000';
    document.querySelector('[data-add-currency="gil"]').click();
    document.querySelector('.rail a[data-view="units"]').click();
    document.querySelector('#point-input').value = '5000';
    document.querySelector('[data-add-point]').click();
    return {
      pills: [...document.querySelectorAll('#cart .pill')].map(p => p.textContent.trim()),
      view: document.querySelector('.view.on').dataset.view,
      hash: location.hash
    };
  })()`);
  check('two edits from two views survive the switch',
    cart.pills.length === 2 && cart.view === 'units' && cart.hash === '#units',
    JSON.stringify(cart));

  const applied = await evaluate(`(function () {
    const original = window.fetch;
    let payload = null;
    window.fetch = function (path, options) {
      if (String(path).indexOf('/api/apply') >= 0) {
        payload = JSON.parse(options.body);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(
          { ok: true, log: ['apply complete'], backup: '/b/campaign-x.json', status: 'ok' }) });
      }
      return original.apply(this, arguments);
    };
    window.confirm = function () { return true; };
    document.querySelector('#apply').click();
    return new Promise((resolve) => setTimeout(() => resolve(payload), 400));
  })()`);
  check('apply payload carries both edits',
    !!applied && applied.edits.currencies.gil === 1000 && applied.edits.points.EXP === 5000,
    JSON.stringify(applied));

  // the level cap belongs to the FORM: clicking a later form must move it
  const caps = await evaluate(`(function () {
    const forms = [...document.querySelectorAll('#chain .form')];
    if (forms.length < 2) return null;
    const before = document.querySelector('#spec-level-cap').textContent;
    forms[forms.length - 1].click();
    const immediate = document.querySelector('#spec-level-cap').textContent;
    return new Promise((resolve) => setTimeout(function () {
      resolve({ before, after: document.querySelector('#spec-level-cap').textContent,
                synchronous: immediate !== before,
                dbs: [...document.querySelectorAll('.pot i')].length });
    }, 3000));
  })()`);
  check('clicking a form updates the level cap', caps && caps.before !== caps.after,
    JSON.stringify(caps));
  check('the form cap is instant (from the catalog, no request)',
    !!caps && caps.synchronous === true, JSON.stringify(caps || {}));
  check('every stat input shows its unit cap', caps && caps.dbs === 12, JSON.stringify(caps || {}));

  // the roster must scroll in place, not push the page down
  const rosterScroll = await evaluate(`(function () {
    const roster = document.querySelector('#roster');
    const style = getComputedStyle(roster);
    return { overflowY: style.overflowY, maxHeight: style.maxHeight,
             scrolls: roster.scrollHeight > roster.clientHeight };
  })()`);
  check('the roster scrolls instead of growing the page',
    rosterScroll.overflowY === 'auto' && rosterScroll.scrolls, JSON.stringify(rosterScroll));

  // dragging the Trust slider to the far right must reach 100% (Lightning has a 1000 cap)
  const drag = await evaluate(`(function () {
    const row = [...document.querySelectorAll('#roster .urow')]
      .find((r) => r.textContent.indexOf('Lightning') >= 0);
    if (!row) return null;
    row.click();
    return new Promise(function (resolve) {
      setTimeout(function () {
        const slider = document.querySelector('[data-trust="0"]');
        const track = slider.querySelector('.track2');
        const rect = track.getBoundingClientRect();
        const opts = { bubbles: true, pointerId: 1, pointerType: 'mouse', button: 0, clientY: rect.top + 5 };
        track.dispatchEvent(new PointerEvent('pointerdown', { ...opts, clientX: rect.left + 10 }));
        track.dispatchEvent(new PointerEvent('pointermove', { ...opts, clientX: rect.left + rect.width / 2 }));
        track.dispatchEvent(new PointerEvent('pointerup', { ...opts, clientX: rect.right - 1 }));
        setTimeout(function () {
          resolve({ readout: document.querySelector('#trust-readout').textContent,
                    label: document.querySelector('#trust-label').textContent });
        }, 300);
      }, 3500);
    });
  })()`);
  check('dragging the Trust slider reaches 100%',
    !!drag && drag.readout.trim() === '100.0 %', JSON.stringify(drag));

  const silhouette = await evaluate(`(function () {
    const img = document.createElement('img');
    return new Promise((resolve) => {
      img.onerror = function () { resolve(true); };
      img.src = '/api/unit-art/123456.png';
      setTimeout(() => resolve(false), 8000);
    });
  })()`);
  check('a missing sprite 404s (the roster falls back to the silhouette)', silhouette === true);

  check('no uncaught exceptions in the console', consoleErrors.length === 0,
    JSON.stringify(consoleErrors).slice(0, 300));
  check('no failed requests', failedRequests.length === 0,
    JSON.stringify(failedRequests).slice(0, 200));

  const failed = results.filter((r) => !r.ok);
  console.log(`----\n${results.length - failed.length} passed, ${failed.length} failed`);
  socket.close();
  chrome.kill('SIGTERM');
  process.exit(failed.length ? 1 : 0);
}

main().catch((error) => { console.error('FAIL - harness:', error.message); process.exit(2); });
