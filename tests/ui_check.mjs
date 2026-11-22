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
const VIEWS = ['grants', 'units', 'kit', 'ability', 'vision', 'log', 'progress'];
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

  // the Awakening Kit offers all three EX levels, and only for the EX type
  const exKit = await evaluate(`(function () {
    document.querySelector('.rail a[data-view="kit"]').click();
    const chips = [...document.querySelectorAll('[data-kit-type]')];
    const ex = chips.find((c) => c.dataset.kitType === 'ex_class_up');
    ex.click();
    const visible = document.querySelector('#kit-ex-row').style.display !== 'none';
    const select = document.querySelector('#kit-ex-level');
    select.value = '3';
    select.dispatchEvent(new Event('change'));
    return new Promise((resolve) => setTimeout(function () {
      resolve({ visible, recipes: document.querySelector('#kit-recipe').textContent,
                unit: document.querySelector('#kit-unit').selectedOptions[0]?.textContent,
                exOff: ex.classList.contains('off'),
                levels: [...document.querySelectorAll('#kit-ex-level option')].map((o) => o.value),
                mats: document.querySelector('#kit-mats').textContent.slice(0, 60) });
    }, 2500));
  })()`);
  check('the kit offers EX +1/+2/+3', !!exKit && exKit.visible
    && /^ex_class_up/.test((exKit.recipes || '').trim())
    && (exKit.levels || []).join() === '1,2,3', JSON.stringify(exKit));

  // the picker must find a unit by the number the game shows
  const byNumber = await evaluate(`(function () {
    document.querySelector('.rail a[data-view="grants"]').click();
    const search = document.querySelector('#unit-search');
    search.value = '2706';
    search.dispatchEvent(new Event('input'));
    return new Promise(function (resolve) {
      setTimeout(function () {
        const rows = [...document.querySelectorAll('.unitgrant .res')].map((r) => r.textContent.trim());
        resolve({ rows });
      }, 800);
    });
  })()`);
  check('searching 2706 finds Azure Knight Lasswell', !!byNumber
    && byNumber.rows.some((t) => /Azure Knight Lasswell/.test(t) && /2706/.test(t)),
    JSON.stringify(byNumber));

  // "65 + 32": the pot ceiling rises with the door value, never past pot_cap + door_cap
  const capPair = await evaluate(`(function () {
    document.querySelector('.rail a[data-view="units"]').click();
    const row = [...document.querySelectorAll('#roster .urow')]
      .find((r) => r.textContent.indexOf('Lightning') >= 0);
    if (!row) return null;
    row.click();
    return new Promise(function (resolve) {
      setTimeout(function () {
        const door = document.querySelector('[data-spec="door2"]');   // ATK
        const pot = document.querySelector('[data-spec="pot2"]');
        const before = { label: pot.parentElement.querySelector('i').textContent, max: pot.max };
        door.value = '32';
        door.dispatchEvent(new Event('input'));
        const after = { label: pot.parentElement.querySelector('i').textContent, max: pot.max };
        resolve({ before, after });
      }, 3500);
    });
  })()`);
  check('pot caps follow the door value (65 + 32)',
    !!capPair && capPair.before.label === '65 + 0' && capPair.after.label === '65 + 32'
    && Number(capPair.after.max) === 97, JSON.stringify(capPair));

  // Grant max stats fills every field with the form's ceilings
  const maxStats = await evaluate(`(function () {
    document.querySelector('.rail a[data-view="units"]').click();
    const row = [...document.querySelectorAll('#roster .urow')]
      .find((r) => r.textContent.indexOf('Lightning') >= 0);
    row.click();
    return new Promise(function (resolve) {
      setTimeout(function () {
        // pin the 5-star form: the owned row is whatever the save holds (it may already be NV)
        const forms = [...document.querySelectorAll('#chain .form')];
        forms[0].click();
        document.querySelector('#max-stats').click();
        setTimeout(function () {
          const doors = [...document.querySelectorAll('[data-spec^="door"]')].map((i) => i.value);
          const pots = [...document.querySelectorAll('[data-spec^="pot"]')].map((i) => i.value);
          resolve({
            level: document.querySelector('#spec-level').value,
            levelCap: document.querySelector('#spec-level-cap').textContent,
            lb: document.querySelector('#spec-lb').value,
            ex: [...document.querySelectorAll('[data-ex]')].find((c) => c.classList.contains('on')).dataset.ex,
            doors, pots,
            trust: document.querySelector('#trust-readout').textContent,
            reward: document.querySelector('#flag-trust').classList.contains('on')
          });
        }, 600);
      }, 3500);
    });
  })()`);
  check('Grant max stats fills level, LB, EX, pots, doors and TMR',
    !!maxStats && maxStats.level === '80' && maxStats.lb === '20' && maxStats.ex === '0'
    && maxStats.doors.join() === '450,75,32,32,32,32'
    && maxStats.pots.join() === '1350,225,97,97,97,97'
    && maxStats.trust.trim() === '100.0 %' && maxStats.reward === true,
    JSON.stringify(maxStats));

  // the same button on the NV form: 120 / LB 30 / EX 3, and the EX reward box ticked
  const nvMax = await evaluate(`(function () {
    const forms = [...document.querySelectorAll('#chain .form')];
    forms[forms.length - 1].click();
    return new Promise(function (resolve) {
      setTimeout(function () {
        document.querySelector('#max-stats').click();
        setTimeout(function () {
          resolve({
            level: document.querySelector('#spec-level').value,
            lb: document.querySelector('#spec-lb').value,
            ex: [...document.querySelectorAll('[data-ex]')].find((c) => c.classList.contains('on')).dataset.ex,
            exReward: document.querySelector('#flag-ex').classList.contains('on'),
            stmr: document.querySelector('#super-trust-readout').textContent
          });
        }, 600);
      }, 3000);
    });
  })()`);
  check('Grant max stats on the NV form sets 120 / LB 30 / EX 3',
    !!nvMax && nvMax.level === '120' && nvMax.lb === '30' && nvMax.ex === '3'
    && nvMax.exReward === true && nvMax.stmr.trim() === '100.0 %', JSON.stringify(nvMax));

  // the ability view follows the unit being built, and lists SKILLS not recipe ids
  const abilityView = await evaluate(`(function () {
    document.querySelector('.rail a[data-view="units"]').click();
    const row = [...document.querySelectorAll('#roster .urow')]
      .find((r) => r.textContent.indexOf('Lightning') >= 0);
    row.click();
    return new Promise(function (resolve) {
      setTimeout(function () {
        document.querySelector('.rail a[data-view="ability"]').click();
        setTimeout(function () {
          const select = document.querySelector('#ability-unit');
          const chosen = select.selectedOptions[0];
          const recipes = [...document.querySelectorAll('#ability-recipe option')].map((o) => o.textContent);
          resolve({ unit: chosen ? chosen.textContent : null, recipes: recipes.slice(0, 3) });
        }, 3000);
      }, 2500);
    });
  })()`);
  check('the ability view follows the built unit and names skills',
    !!abilityView && /Lightning/.test(abilityView.unit || '')
    && abilityView.recipes.some((t) => /Blitz|Lightspeed|Crushing Blow|Blade of Thunder/.test(t)),
    JSON.stringify(abilityView));

  // a failed apply leaves a dismissible footer message that also clears itself
  const failedStatus = await evaluate(`(function () {
    const original = window.fetch;
    window.fetch = function (path, options) {
      if (String(path).indexOf('/api/apply') >= 0) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({
          ok: false, log: ['ROLLBACK: restoring the pre-apply save'], error: 'skill recipe is stale',
          backup: null, status: 'failed' }) });
      }
      return original.apply(this, arguments);
    };
    window.confirm = function () { return true; };
    document.querySelector('.rail a[data-view="grants"]').click();
    document.querySelector('#gil-input').value = '5';
    document.querySelector('[data-add-currency="gil"]').click();
    document.querySelector('#apply').click();
    return new Promise(function (resolve) {
      setTimeout(function () {
        const el = document.querySelector('#apply-status');
        const shown = el.textContent;
        el.click();
        resolve({ shown, afterClick: el.textContent });
      }, 900);
    });
  })()`);
  check('a failed apply shows a dismissible reason',
    !!failedStatus && /apply failed/.test(failedStatus.shown) && failedStatus.afterClick === '',
    JSON.stringify(failedStatus));

  // the Progress view: every region, side rows per world, and a checkpoint that previews
  const progress = await evaluate(`(async function () {
    document.querySelector('.rail a[data-view="progress"]').click();
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    document.querySelector('#side-link').click();        // side content renders on demand
    for (let i = 0; i < 60 && !document.querySelectorAll('#side-list .qrow').length; i += 1) {
      await sleep(500);
    }
    const sideWorlds = [...new Set([...document.querySelectorAll('#side-list [data-world]')]
      .map((n) => n.dataset.world))].sort();
    const sideRows = document.querySelectorAll('#side-list .qrow').length;
    document.querySelector('.rail a[data-view="progress"]').click();
    for (let i = 0; i < 40 && !/Missions in the span/.test(document.querySelector('#plan-summary').textContent); i += 1) {
      await sleep(500);
    }
    return { regions: document.querySelectorAll('#region-list .reg').length,
             areas: document.querySelectorAll('#area-list .darea').length,
             worlds: sideWorlds, rows: sideRows,
             summary: document.querySelector('#plan-summary').textContent,
             rewards: document.querySelector('#plan-rewards').textContent,
             sections: document.querySelectorAll('section.view').length };
  })()`);
  check('the progress view lists all 42 regions', progress && progress.regions === 42,
    JSON.stringify(progress && progress.regions));
  check('side quests are grouped per world', !!progress && (progress.worlds || []).join() === '1,2,3,5',
    JSON.stringify(progress && progress.worlds));
  check('a checkpoint previews its plan and rewards', !!progress
    && /Missions in the span/.test(progress.summary || '') && /Lapis paid/.test(progress.summary || ''),
    JSON.stringify(progress && progress.summary));
  check('the page has seven views', !!progress && progress.sections === 7,
    JSON.stringify(progress && progress.sections));

  // the awakening kit must speak stars, grey out impossible forms, and chain the steps.
  // Data-driven: which unit can still awaken depends on the LIVE save, so pick the roster row with
  // the longest remaining chain and derive every expectation from the catalog.
  const kit = await evaluate(`(async function () {
    const catalog = await fetch('/api/catalog').then((r) => r.json());
    const summary = await fetch('/api/summary').then((r) => r.json());
    const units = Object.fromEntries(catalog.units.map((u) => [u.master_id, u]));
    const formsAfter = function (master) {
      const info = units[master];
      const chain = catalog.awaken_chain[info.nv_group] || [];
      const all = [info.nv_group].concat(chain);
      const at = all.indexOf(master);
      return at >= 0 ? all.slice(at + 1) : chain;
    };
    const label = function (master) {
      const info = units[master];
      return info.nv ? 'NV' : String(info.rarity) + '\\u2605';
    };
    const pick = summary.roster.map((u) => ({ u, forms: formsAfter(u.master_id) }))
      .filter((row) => row.forms.length)
      .sort((a, b) => b.forms.length - a.forms.length)[0];
    if (!pick) return { error: 'no roster unit can awaken further' };
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    document.querySelector('.rail a[data-view="kit"]').click();
    const select = document.querySelector('#kit-unit');
    select.value = pick.u.instance_id;
    select.dispatchEvent(new Event('change'));
    await sleep(2000);
    const ex = [...document.querySelectorAll('[data-kit-type]')]
      .find((c) => c.dataset.kitType === 'ex_class_up');
    const target = document.querySelector('#kit-target');
    const stars = [...target.options].map((o) => o.textContent);
    target.value = target.options[target.options.length - 1].value;
    target.dispatchEvent(new Event('change'));
    await sleep(2500);
    const recipe = document.querySelector('#kit-recipe').textContent;
    document.querySelector('[data-add="kit"]').click();
    const pill = [...document.querySelectorAll('#cart .pill')].map((p) => p.textContent.trim()).pop();
    return { labelText: select.selectedOptions[0].textContent, expectedLabel: label(pick.u.master_id),
             stars, expected: pick.forms.map(label), steps: pick.forms.length, recipe, pill,
             exGreyed: ex.classList.contains('off'),
             nv: !!units[pick.u.master_id].nv,
             from: pick.u.master_id, to: pick.forms[pick.forms.length - 1] };
  })()`);
  check('the kit shows the current stars', !!kit && (kit.labelText || '').includes(kit.expectedLabel || '?'),
    JSON.stringify(kit && [kit.labelText, kit.expectedLabel]));
  check('EX is greyed out unless the form is NV', !!kit && kit.exGreyed === !kit.nv,
    JSON.stringify(kit && [kit.exGreyed, kit.nv]));
  check('the kit offers every later star form', !!kit
    && (kit.stars || []).join() === (kit.expected || []).join(),
    JSON.stringify(kit && [kit.stars, kit.expected]));
  check('choosing the top form chains every step', !!kit
    && String(kit.recipe || '').includes('· ' + kit.steps + ' step'),
    JSON.stringify(kit && kit.recipe));
  check('the chained kit reaches the cart', !!kit
    && String(kit.pill || '').includes(kit.from + '->' + kit.to),
    JSON.stringify(kit && kit.pill));

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
