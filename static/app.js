/* FFBE Game Admin — view switching, the pending-edit cart, and the three API calls.
   No framework, no build step. Every number on the page comes from the server. */
(function () {
  'use strict';

  var S = {
    summary: null,
    catalog: null,
    pending: [],
    selected: null,      // the roster row being built
    form: null,          // the master_id of the chosen form
    ex: 0,
    trust: 0,
    superTrust: 0,
    kitType: 'unit_class_up',
    kitMode: 'shortfall',
    unitResults: [],
    unitPick: null,
    log: [],
    backups: []
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function fmt(n) { return Number(n || 0).toLocaleString('en-US'); }
  function intValue(sel) { var el = $(sel); return el ? parseInt(String(el.value).replace(/[^0-9-]/g, ''), 10) || 0 : 0; }
  function stars(n) { return '★★★★★★★'.slice(0, Math.max(0, Math.min(7, n || 0))); }
  var SILHOUETTE = '<span class="uno-img"><svg viewBox="0 0 16 20" aria-hidden="true">'
    + '<circle cx="8" cy="5" r="3.1" fill="currentColor"/>'
    + '<path d="M1.8 19.4c0-4.1 2.8-6.9 6.2-6.9s6.2 2.8 6.2 6.9z" fill="currentColor"/></svg></span>';

  /* ------------------------------------------------------------------ view switching */
  function showView(id) {
    $$('.rail a[data-view]').forEach(function (a) { a.classList.toggle('active', a.dataset.view === id); });
    $$('.view').forEach(function (v) { v.classList.toggle('on', v.dataset.view === id); });
    if (location.hash.slice(1) !== id) history.replaceState(null, '', '#' + id);
    syncUnitPickers(id);
    if (id === 'progress') loadProgress();
  }

  // The kit and the ability view work on ONE unit: keep them on the one being built.
  function syncUnitPickers(id) {
    if (!S.selected) return;
    if (id === 'ability') {
      var select = $('#ability-unit');
      if (select && select.value !== S.selected.instance_id) {
        select.value = S.selected.instance_id;
        loadRecipes();
      }
    }
    if (id === 'kit') {
      var kit = $('#kit-unit');
      if (kit && kit.value !== S.selected.instance_id) {
        kit.value = S.selected.instance_id;
        loadKit();
      }
    }
  }

  function bindViews() {
    $$('.rail a[data-view]').forEach(function (a) {
      a.addEventListener('click', function (ev) { ev.preventDefault(); showView(a.dataset.view); });
    });
    var initial = location.hash.slice(1);
    if (initial && $('.view[data-view="' + initial + '"]')) showView(initial);
    window.addEventListener('hashchange', function () {
      var id = location.hash.slice(1);
      if (id && $('.view[data-view="' + id + '"]')) showView(id);
    });
    $('#to-grants').addEventListener('click', function () { showView('grants'); });
    wireProgress();
  }

  /* ------------------------------------------------------------------------ the cart */
  // Only numbers add up. A structural payload (a checkpoint's area, a pill's explicit null, a string
  // id) must never be marched through the accumulator: `0 + "2210"` is the string "02210", which the
  // agent then refuses as "not a story dungeon area", and `{} + null` is "[object Object]null".
  function mergeInto(target, add) {
    Object.keys(add).forEach(function (key) {
      var value = add[key];
      if (value === null || value === undefined) return;          // "not set by this pill"
      if (Array.isArray(value)) {
        target[key] = (target[key] || []).concat(value);
      } else if (typeof value === 'object') {
        target[key] = (target[key] && typeof target[key] === 'object') ? target[key] : {};
        mergeInto(target[key], value);
      } else if (typeof value === 'number') {
        target[key] = (typeof target[key] === 'number' ? target[key] : 0) + value;
      } else {
        target[key] = value;                                      // strings and booleans: last wins
      }
    });
    return target;
  }

  function pendingEdits() {
    var edits = {};
    S.pending.forEach(function (item) { mergeInto(edits, item.edits); });
    return edits;
  }

  function renderCart() {
    var cart = $('#cart');
    $$('.pill', cart).forEach(function (n) { n.remove(); });
    S.pending.forEach(function (item, index) {
      var pill = document.createElement('span');
      pill.className = 'pill';
      pill.innerHTML = esc(item.label) + ' <i>✕</i>';
      pill.querySelector('i').addEventListener('click', function () {
        S.pending.splice(index, 1);
        renderCart();
        renderAfter();
      });
      cart.appendChild(pill);
    });
    $('#pending-count').textContent = S.pending.length;
  }

  function addEdit(label, edits) {
    S.pending.push({ label: label, edits: edits });
    renderCart();
    renderAfter();
    setStatus('');
  }

  function renderAfter() {
    if (!S.summary) return;
    var delta = { gil: 0, lapis_free: 0 };
    S.pending.forEach(function (item) {
      if (item.edits.currencies) {
        delta.gil += item.edits.currencies.gil || 0;
        delta.lapis_free += item.edits.currencies.lapis_free || 0;
      }
    });
    var pointDelta = 0;
    S.pending.forEach(function (item) {
      if (item.edits.points) pointDelta += item.edits.points.EXP || 0;
    });
    var pointLine = $('#after-points');
    if (pointLine && S.summary) pointLine.textContent = fmt((S.summary.exp_points || 0) + pointDelta);
    var line = $('#after-item');
    if (line) {
      line.textContent = fmt(S.summary.gil + delta.gil) + ' gil · '
        + fmt(S.summary.lapis_free + delta.lapis_free) + ' lapis';
    }
  }

  function setStatus(text, bad) {
    var el = $('#apply-status');
    if (!el) return;
    el.textContent = text;
    el.style.color = bad ? 'var(--warn)' : 'var(--pos)';
    el.title = text ? 'click to dismiss' : '';
    el.onclick = text ? function () { setStatus(''); } : null;
    clearTimeout(S.statusTimer);
    if (text) S.statusTimer = setTimeout(function () { setStatus(''); }, bad ? 20000 : 8000);
  }

  /* ----------------------------------------------------------------------- the API */
  function api(path, body) {
    var options = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    };
    return fetch(path, options).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || (data && data.ok === false && data.error && !data.log)) {
          throw new Error((data && data.error) || ('HTTP ' + response.status));
        }
        return data;
      });
    });
  }

  /* ------------------------------------------------------------------ first paint */
  function loadSummary() {
    return api('/api/summary').then(function (summary) {
      S.summary = summary;
      renderSummary();
      renderAfter();
      return summary;
    });
  }

  function loadCatalog() {
    return api('/api/catalog').then(function (catalog) {
      S.catalog = catalog;
      renderTickets();
      renderCards();
      renderMaterialFamilies();
      renderMatrices();
      renderKitUnits();
      renderAbilityUnits();
      renderGrantCards();
      $('#unit-total').innerHTML = '<b>Find</b> ' + catalog.units.length + ' units';
      renderUnitResults();
      if (!S.selected && S.summary && S.summary.roster.length) selectUnit(S.summary.roster[0].instance_id);
      return catalog;
    });
  }

  function renderSummary() {
    var s = S.summary;
    if (!s) return;
    $('#wallet-gil').textContent = fmt(s.gil);
    $('#wallet-lapis').textContent = fmt(s.lapis_free);
    $('#glance-save').textContent = (s.gil / 1e6).toFixed(2) + 'M gil';
    $('#glance-roster').textContent = s.roster.length + ' units';
    $('#saved-at').textContent = 'campaign.json · ' + s.roster.length + ' units · '
      + s.vision_cards.length + (s.vision_cards.length === 1 ? ' vision card' : ' vision cards');
    $('#glance-server').textContent = 'link ok';
    $('#glance-backup').textContent = S.backups.length ? S.backups[0].split('/').pop() : '—';
    $('#point-bank').textContent = fmt(s.exp_points || 0);
    $('#roster-count').innerHTML = '<b>Roster</b> ' + s.roster.length;
    renderTicketsHeld();
    renderRoster();
    renderVision();
    if (S.selected) selectUnit(S.selected.instance_id);
  }

  function renderTicketsHeld() {
    if (!S.summary) return;
    var held = S.summary.tickets_held || {};
    var total = 0;
    Object.keys(held).forEach(function (key) { total += held[key]; });
    $('#tickets-held').textContent = String(total);
  }

  /* -------------------------------------------------------------------- quick grants */
  function unitNo(masterId) {
    var info = (S.catalog ? S.catalog.units : []).filter(function (u) { return u.master_id === masterId; })[0];
    if (info && info.no) return info.no;
    var row = S.summary ? S.summary.roster.filter(function (u) { return u.master_id === masterId; })[0] : null;
    return (row && row.no) || masterId;
  }

  function unitName(masterId) {
    if (S.summary) {
      for (var i = 0; i < S.summary.roster.length; i++) {
        if (S.summary.roster[i].master_id === masterId) return S.summary.roster[i].name;
      }
    }
    if (S.catalog) {
      for (var j = 0; j < S.catalog.units.length; j++) {
        if (S.catalog.units[j].master_id === masterId) return S.catalog.units[j].name;
      }
    }
    return masterId;
  }

  function artUrl(masterId) { return '/api/unit-art/' + masterId + '.png'; }

  function unitArt(masterId, className) {
    return '<img class="' + (className || '') + '" src="' + artUrl(masterId) + '" alt="" '
      + 'onerror="this.outerHTML=SILHOUETTE">';
  }
  window.SILHOUETTE = SILHOUETTE;

  function formInfo(masterId) {
    if (!S.catalog) return null;
    return S.catalog.units.filter(function (u) { return u.master_id === masterId; })[0] || null;
  }

  function formChain(masterId) {
    if (!S.catalog) return [masterId];
    var group = masterId;
    var unit = S.catalog.units.filter(function (u) { return u.master_id === masterId; })[0];
    if (unit) group = unit.nv_group;
    var chain = (S.catalog.awaken_chain[group] || []).slice();
    return [group].concat(chain);
  }

  function resolveGrantForm() {
    if (!S.unitPick) return null;
    var mode = $('#unit-form').value;
    var chain = formChain(S.unitPick.master_id);
    if (mode === 'base') return chain[0];
    var nv = chain.filter(function (id) {
      var unit = S.catalog.units.filter(function (u) { return u.master_id === id; })[0];
      return unit && unit.nv;
    });
    if (mode === 'nv' && nv.length) return nv[nv.length - 1];
    return chain[chain.length - 1];
  }

  function renderUnitResults() {
    var query = ($('#unit-search').value || '').trim().toLowerCase();
    var list = (S.catalog ? S.catalog.units : []).filter(function (unit) {
      if (!query) return true;
      return unit.name.toLowerCase().indexOf(query) >= 0
        || unit.master_id.indexOf(query) >= 0
        || String(unit.no || '').indexOf(query.replace(/^no\.?\s*/, '')) === 0;
    }).slice(0, 8);
    S.unitResults = list;
    var parent = $('#unit-results-count').parentElement;
    $$('.res', parent).forEach(function (n) { n.remove(); });
    $('#unit-results-count').innerHTML = '<b>Results</b> ' + list.length + ' of '
      + (S.catalog ? S.catalog.units.length : 0);
    if (!list.length) return;
    if (!S.unitPick || !list.some(function (u) { return u.master_id === S.unitPick.master_id; })) {
      S.unitPick = list[0];
    }
    list.forEach(function (unit) {
      var row = document.createElement('div');
      row.className = 'res' + (S.unitPick && S.unitPick.master_id === unit.master_id ? ' on' : '');
      row.innerHTML = unitArt(unit.master_id)
        + '<div><span class="nm">' + esc(unit.name) + '</span><span class="sub">No. ' + esc(unit.no || unit.master_id)
        + ' · ' + (unit.nv ? 'NV' : unit.rarity + '★') + ' · Lv ' + (unit.max_level || '?')
        + '</span></div><span class="owned" style="color:var(--dim)">grant</span>';
      row.addEventListener('click', function () {
        S.unitPick = unit;
        renderUnitResults();
      });
      parent.appendChild(row);
    });
  }

  function bindGrants() {
    $('#unit-search').addEventListener('input', renderUnitResults);
    $('#unit-grant').addEventListener('click', function () {
      var masterId = resolveGrantForm();
      if (!masterId) return;
      addEdit(unitName(masterId) + ' (new, level 1)',
        { units: [{ instance_id: null, spec: { master_id: masterId, level: 1 } }] });
    });
    $$('[data-add-currency]').forEach(function (button) {
      button.addEventListener('click', function () {
        var key = button.dataset.addCurrency;
        var input = key === 'gil' ? $('#gil-input') : $('#lapis-input');
        var amount = parseInt(String(input.value).replace(/[^0-9]/g, ''), 10) || 0;
        if (amount <= 0) return;
        var edits = { currencies: {} };
        edits.currencies[key] = amount;
        addEdit((key === 'gil' ? 'Gil' : 'Lapis') + ' +' + fmt(amount), edits);
      });
    });
    $('[data-add-ticket]').addEventListener('click', function () {
      var option = $('#ticket-select').selectedOptions[0];
      if (!option) return;
      var count = intValue('#ticket-count');
      if (count <= 0) return;
      addEdit(option.textContent + ' ×' + count, {
        tickets: [{ item_type: Number(option.dataset.type), item_id: Number(option.dataset.id), count: count }]
      });
    });
    $('[data-add-point]').addEventListener('click', function () {
      var amount = intValue('#point-input');
      if (amount <= 0) return;
      addEdit('EXP points +' + fmt(amount), { points: { EXP: amount } });
    });
    $('[data-add-card]').addEventListener('click', function () {
      var option = $('#card-select').selectedOptions[0];
      if (!option) return;
      var level = intValue('#card-level');
      addEdit(option.textContent + ' · Lv ' + level, { cards: [{ vc_id: option.value, level: level }] });
    });
  }

  function renderTickets() {
    var select = $('#ticket-select');
    select.innerHTML = '';
    (S.catalog.tickets || []).filter(function (t) { return t.spendable; }).forEach(function (ticket) {
      var option = document.createElement('option');
      option.value = String(ticket.item_id);
      option.dataset.type = ticket.item_type;
      option.dataset.id = ticket.item_id;
      option.textContent = ticket.name + (ticket.pulls > 1 ? ' · ' + ticket.pulls + ' pulls' : '');
      select.appendChild(option);
    });
    function paint() {
      var option = select.selectedOptions[0];
      $('#ticket-id').textContent = option ? (option.dataset.type + '/' + option.dataset.id) : '—';
    }
    select.addEventListener('change', paint);
    paint();
  }

  function renderCards() {
    var select = $('#card-select');
    if (!select) return;
    select.innerHTML = '';
    (S.catalog.vision_cards || []).forEach(function (card) {
      var option = document.createElement('option');
      option.value = card.vc_id;
      option.textContent = card.name + ' · max ' + card.max_level;
      select.appendChild(option);
    });
  }

  /* -------------------------------------------------------------------- unit builder */
  function renderRoster() {
    var roster = $('#roster');
    if (!roster || !S.summary) return;
    roster.innerHTML = '';
    S.summary.roster.forEach(function (unit) {
      var row = document.createElement('div');
      row.className = 'urow' + (S.selected && S.selected.instance_id === unit.instance_id ? ' on' : '');
      row.innerHTML = unitArt(unit.master_id)
        + '<div><span class="uname">' + esc(unit.name) + '</span>'
        + '<span class="umeta">No. ' + esc(unit.no || unit.master_id) + (unit.nv_ex_level ? ' · EX +' + unit.nv_ex_level : '') + '</span></div>'
        + '<div class="uright"><span class="rar">' + stars(unit.rarity) + '</span>'
        + '<span class="ulv">Lv ' + unit.level + '</span></div>';
      row.addEventListener('click', function () { selectUnit(unit.instance_id); });
      roster.appendChild(row);
    });
  }

  function selectedRow() {
    if (!S.summary || !S.selected) return null;
    return S.summary.roster.filter(function (u) { return u.instance_id === S.selected.instance_id; })[0] || null;
  }

  /* ------------------------------------------------------------------------ progress view */
  var progressCache = { regions: [], areas: [], plan: null, side: {}, world: null, region: null };

  function loadProgress() {
    if (progressCache.regions.length) return;
    api('/api/questline/regions').then(function (data) {
      progressCache.regions = data.regions || [];
      $('#region-count').textContent = progressCache.regions.length + ' · 4 arcs';
      renderRegions();
      selectRegion('22');
    }).catch(function (error) { $('#plan-summary').textContent = error.message; });
  }

  function renderRegions() {
    var host = $('#region-list');
    host.innerHTML = '';
    var arc = null;
    progressCache.regions.forEach(function (region) {
      if (region.arc !== arc) {
        arc = region.arc;
        var head = document.createElement('div');
        head.className = 'arc';
        head.innerHTML = esc(arc) + '<span>' + progressCache.regions.filter(function (r) {
          return r.arc === arc;
        }).length + ' chapters</span>';
        host.appendChild(head);
      }
      var row = document.createElement('div');
      row.className = 'reg' + (String(region.region_id) === String(progressCache.region) ? ' on' : '');
      row.dataset.region = region.region_id;
      row.innerHTML = '<span>' + esc(region.name) + '</span><span class="frac">'
        + region.dungeon_count + '</span>';
      row.addEventListener('click', function () { selectRegion(String(region.region_id)); });
      host.appendChild(row);
    });
  }

  function selectRegion(regionId) {
    progressCache.region = regionId;
    $$('#region-list .reg').forEach(function (row) {
      row.classList.toggle('on', row.dataset.region === String(regionId));
    });
    sideMode(false);
    api('/api/questline?region=' + encodeURIComponent(regionId)).then(function (data) {
      progressCache.areas = data.areas || [];
      renderAreas();
      $('#mid-ph').innerHTML = 'Dungeons <span class="tag">' + esc(area_of_region_name(regionId)) + '</span>';
      if (progressCache.areas.length) selectArea(progressCache.areas[0].area_id);
    });
  }

  function area_of_region_name(regionId) {
    var region = progressCache.regions.filter(function (r) { return String(r.region_id) === String(regionId); })[0];
    return region ? region.name : '';
  }

  function renderAreas() {
    var host = $('#area-list');
    host.innerHTML = '';
    var cleared = 0;
    progressCache.areas.forEach(function (area, index) {
      var row = document.createElement('div');
      row.className = 'darea' + (String(area.area_id) === String(progressCache.plan && progressCache.plan.area) ? ' ckpt' : '');
      row.dataset.area = area.area_id;
      row.innerHTML = '<span class="n">' + (index + 1) + '</span><span>' + esc(area.name)
        + (area.shares_with ? ' <span class="gk">shares ' + esc(area.shares_with) + '</span>' : '')
        + '</span><span class="mc">' + area.missions.length + ' missions</span>';
      row.addEventListener('click', function () { selectArea(area.area_id); });
      host.appendChild(row);
    });
    $('#area-count').innerHTML = '<b>' + progressCache.areas.length + ' areas</b> · '
      + progressCache.areas.reduce(function (n, a) { return n + a.missions.length; }, 0) + ' missions';
  }

  function selectArea(areaId) {
    $$('#area-list .darea').forEach(function (row) {
      row.classList.toggle('ckpt', row.dataset.area === String(areaId));
    });
    var region = progressCache.region;
    var token = (progressCache.areaToken = (progressCache.areaToken || 0) + 1);
    $('#checkpoint-apply').disabled = true;   // nothing to add until this area's preview lands
    $('#plan-summary').innerHTML = '<div class="sum-row"><span class="sum-k">Previewing</span>'
      + '<span class="sum-v small">' + esc(areaId) + '</span></div>';
    api('/api/questline?region=' + encodeURIComponent(region) + '&area=' + encodeURIComponent(areaId))
      .then(function (data) {
        if (token !== progressCache.areaToken) return;    // a later click owns the pane now
        progressCache.plan = { region: region, area: areaId, expected: data.expected };
        var plan = data.plan || {};
        var excluded = {};
        (plan.excluded || []).forEach(function (row) { excluded[row.reason] = (excluded[row.reason] || 0) + 1; });
        var expected = data.expected || {};
        var extra = expected.extra || {};
        var missions = (plan.missions || []).length;
        var rows = [
          ['Missions in the span', missions],
          ['Already cleared', Math.max(0, missions - (extra.cleared || 0))],
          ['<b>To clear now</b>', '<b class="g">' + (extra.cleared || 0) + '</b>'],
          ['Lapis paid <span class="mult">× 5</span>', fmt((expected.currencies || {}).lapis_free)],
          ['Gil paid', fmt((expected.currencies || {}).gil)],
          ['Gates opened', fmt((plan.gates || []).length)],
          ['Story units', fmt(expected.roster_added)],
          ['Espers', fmt((plan.espers || []).length)]
        ];
        $('#plan-summary').innerHTML = rows.map(function (pair) {
          return '<div class="sum-row"><span class="sum-k">' + pair[0] + '</span><span class="sum-v">'
            + pair[1] + '</span></div>';
        }).join('');
        var excludedText = Object.keys(excluded).map(function (reason) {
          return excluded[reason] + ' excluded (' + reason + ')';
        }).join(' · ');
        $('#plan-rewards').textContent = excludedText;
        if ($('#sum-tag')) $('#sum-tag').textContent = areaId;   // the pane may have switched to side
        $('#checkpoint-apply').disabled = false;
      }).catch(function (error) {
        if (token !== progressCache.areaToken) return;
        $('#plan-summary').textContent = error.message;
        $('#checkpoint-apply').disabled = true;
      });
  }

  function sideMode(on) {
    $('#pane-story').style.display = on ? 'none' : '';
    $('#pane-side').style.display = on ? '' : 'none';
    $('#sum-story').style.display = on ? 'none' : '';
    $('#sum-side').style.display = on ? '' : 'none';
    $('#mid-ph').innerHTML = on ? 'Side content <span class="tag">quests and esper dungeons</span>'
      : 'Dungeons <span class="tag">' + esc(area_of_region_name(progressCache.region)) + '</span>';
    if (on) { loadSideWorlds(); $('#sum-ph').innerHTML = 'Side selection <span class="tag">worlds</span>'; }
    else { $('#sum-ph').innerHTML = 'Checkpoint <span class="tag" id="sum-tag"></span>'; }
  }

  function loadSideWorlds() {
    if (Object.keys(progressCache.side).length) return;
    api('/api/questline?world=all').then(function (data) {
      (data.worlds || []).forEach(function (world) { renderSideWorld(world.world_id, world); });
      renderSideSummary();
      $('#side-tag').style.display = 'none';   // the tag itself, not its parent: #side-list lives there too
      $('#mid-ph').innerHTML = 'Side content <span class="tag">' + (data.worlds || []).length + ' worlds</span>';
      renderSideSummary();
      $('#side-apply').disabled = false;
    }).catch(function (error) { $('#side-list').textContent = error.message; });
  }

  // `dX6cor8j` is `kind:id:count:...`: render the first entry as the player would read it.
  var REWARD_KINDS = { 10: 'unit', 20: 'item', 21: 'equip', 23: 'ticket', 27: 'card',
                       50: 'Lapis', 60: 'gil', 821: 'esper' };

  function rewardLabel(raw) {
    var token = String(raw || '').split(',')[0];
    var parts = token.split(':');
    if (parts.length < 3) return '';
    var kind = parts[0], count = parts[2];
    var label = REWARD_KINDS[kind] || ('kind ' + kind);
    return label + (count && count !== '1' ? ' ×' + fmt(count) : '');
  }

  function renderSideWorld(world, content) {
    progressCache.side[world] = content;
    var host = $('#side-list');
    var head = document.createElement('div');
    head.className = 'sidehead';
    head.innerHTML = 'World ' + world + ' <span>' + (content.dungeons || []).length + ' dungeons · '
      + (content.quests || []).length + ' quests</span>';
    host.appendChild(head);
    if ((content.dungeons || []).length) {
      var espHead = document.createElement('div');
      espHead.className = 'sidehead';
      espHead.innerHTML = 'Esper dungeons <span>' + content.dungeons.length
        + ' · the esper is granted on the beat clear</span>';
      host.appendChild(espHead);
      content.dungeons.forEach(function (dungeon) {
        var row = document.createElement('div');
        row.className = 'qrow';
        row.innerHTML = '<span class="cb" data-dungeon="' + esc(dungeon.dungeon_key) + '" data-world="'
          + world + '">✓</span><span>' + esc(dungeon.name) + '<br><span class="esp">'
          + esc(dungeon.esper_name || '') + '</span>'
          + (dungeon.grantable ? '<span class="grantbox on" data-grant="' + esc(dungeon.dungeon_key)
            + '">grant</span>' : '<span class="grantbox">no grant</span>')
          + '</span><span class="rw"><span class="trialskip" data-trial="' + esc(dungeon.dungeon_key)
          + '">trial left unplayed</span>' + dungeon.missions.length + ' missions</span>';
        row.querySelector('.cb').addEventListener('click', function (ev) {
          ev.currentTarget.classList.toggle('on');
          renderSideSummary();
          queueSidePreview();
        });
        var box = row.querySelector('.grantbox[data-grant]');
        var skip = row.querySelector('.trialskip');
        if (skip) skip.style.display = box && box.classList.contains('on') ? 'none' : '';
        if (box) box.addEventListener('click', function (ev) {
          ev.currentTarget.classList.toggle('on');
          // Turning the grant off leaves the dungeon's beat mission — the esper trial — unplayed, so
          // the node keeps showing as incomplete. That is a real consequence, not a missing clear.
          if (skip) skip.style.display = ev.currentTarget.classList.contains('on') ? 'none' : '';
          renderSideSummary();
          queueSidePreview();
        });
        host.appendChild(row);
      });
    }
    var questHead = document.createElement('div');
    questHead.className = 'sidehead';
    questHead.innerHTML = 'Quests <span>' + (content.quests || []).length + ' · towns and the vortex</span>';
    host.appendChild(questHead);
    (content.quests || []).forEach(function (quest) {
      var row = document.createElement('div');
      row.className = 'qrow';
      row.innerHTML = '<span class="cb" data-quest="' + esc(quest.quest_id) + '" data-world="' + world
        + '">✓</span><span class="qwrap"><span class="qname">' + esc(quest.name || quest.quest_id)
        + '</span><span class="loc"><span class="pin">◆</span>' + esc(quest.place)
        + (quest.delivery ? ' · delivery' : '') + '</span></span><span class="rw">'
        + esc(rewardLabel(quest.reward)) + '</span>';
      row.querySelector('.cb').addEventListener('click', function (ev) {
        ev.currentTarget.classList.toggle('on');
        renderSideSummary();
        queueSidePreview();
      });
      host.appendChild(row);
    });
  }

  var sideExpected = null;
  var sideExpectedKey = null;      // which selection the cached promise belongs to
  var sidePreviewTimer = null;

  function selectionKey(selection) { return JSON.stringify(selection); }

  // A side selection pays quest rewards, so the cart has to declare them: the agent refuses an apply
  // whose actual currency deltas differ from what was promised. The preview is the promise.
  function requestSidePreview() {
    var selection = sideSelection();
    var key = selectionKey(selection);
    if (!Object.keys(selection).length) {
      sideExpected = null;
      sideExpectedKey = null;
      renderSideSummary();
      return Promise.resolve(null);
    }
    return api('/api/progress/preview', { progress: { checkpoint: null, side: selection } })
      .then(function (data) {
        sideExpected = data.expected || null;
        sideExpectedKey = key;
        renderSideSummary();
        return sideExpected;
      })
      .catch(function (error) {
        if (window.console) console.error('side preview failed', error);   // the pane shows it too
        renderSideSummary(error && error.message);
        sideExpected = null;
        sideExpectedKey = null;
        return null;
      });
  }

  // The promise must belong to the selection being added: a checkbox flipped inside the debounce
  // window would otherwise send the previous selection's figures and be refused.
  function expectedFor(selection) {
    var key = selectionKey(selection);
    if (sideExpected && sideExpectedKey === key) return Promise.resolve(sideExpected);
    clearTimeout(sidePreviewTimer);
    return requestSidePreview().then(function () {
      return sideExpectedKey === key ? sideExpected : null;
    });
  }

  function queueSidePreview() {
    clearTimeout(sidePreviewTimer);
    sidePreviewTimer = setTimeout(requestSidePreview, 350);
  }

  function renderSideSummary(errorText) {
    var selection = sideSelection();
    var worlds = Object.keys(selection);
    var quests = 0, dungeons = 0, granted = 0;
    worlds.forEach(function (world) {
      quests += selection[world].quests.length;
      dungeons += selection[world].dungeons.length;
      selection[world].dungeons.forEach(function (row) { if (row.grant_esper) granted += 1; });
    });
    var rows = '<div class="sum-row"><span class="sum-k">Worlds</span>'
      + '<span class="sum-v small">' + (worlds.length ? worlds.map(function (w) { return 'W' + w; }).join(' · ') : 'none') + '</span></div>'
      + '<div class="sum-row"><span class="sum-k">Quests</span><span class="sum-v">' + quests + '</span></div>'
      + '<div class="sum-row"><span class="sum-k">Esper dungeons</span><span class="sum-v">' + dungeons + '</span></div>'
      + '<div class="sum-row"><span class="sum-k">Espers granted</span><span class="sum-v g">' + granted + '</span></div>';
    var skipped = 0;
    worlds.forEach(function (world) {
      selection[world].dungeons.forEach(function (row) { if (!row.grant_esper) skipped += 1; });
    });
    if (skipped) {
      rows += '<div class="sum-row"><span class="sum-k">Esper trials left</span><span class="sum-v">'
        + skipped + '</span></div>';
    }
    if (errorText) {
      rows += '<div class="sum-row"><span class="sum-k">Preview refused</span><span class="sum-v small">'
        + esc(String(errorText).slice(0, 120)) + '</span></div>';
    }
    if (sideExpected) {
      var currencies = sideExpected.currencies || {};
      rows += '<div class="sum-row"><span class="sum-k">Lapis paid</span><span class="sum-v">' + fmt(currencies.lapis_free || 0) + '</span></div>'
        + '<div class="sum-row"><span class="sum-k">Gil paid</span><span class="sum-v">' + fmt(currencies.gil || 0) + '</span></div>';
    }
    $('#side-summary').innerHTML = rows;
  }

  function sideSelection() {
    var side = {};
    $$('#side-list .cb[data-quest].on').forEach(function (cb) {
      var world = cb.dataset.world;
      side[world] = side[world] || { quests: [], dungeons: [] };
      side[world].quests.push(cb.dataset.quest);
    });
    $$('#side-list .cb[data-dungeon].on').forEach(function (cb) {
      var world = cb.dataset.world;
      side[world] = side[world] || { quests: [], dungeons: [] };
      var box = $('.grantbox[data-grant="' + cb.dataset.dungeon + '"]');
      // No toggle at all (`no grant`, e.g. Bahamut) is not the same as a toggle switched off: the
      // player cannot withhold what the game never offers, so the dungeon clears like any other.
      side[world].dungeons.push({ dungeon_key: cb.dataset.dungeon,
                                  grant_esper: box ? box.classList.contains('on') : true });
    });
    return side;
  }

  function wireProgress() {
    $('#checkpoint-apply').addEventListener('click', function () {
      var plan = progressCache.plan;
      if (!plan) return;
      addEdit('Progress · checkpoint ' + plan.area, { progress: {
        checkpoint: { region: parseInt(plan.region, 10), area: plan.area }, side: null },
        expected: plan.expected });
    });
    $('#side-apply').addEventListener('click', function () {
      var side = sideSelection();
      var worlds = Object.keys(side);
      if (!worlds.length) return;
      var label = 'Progress · side ' + worlds.map(function (w) { return 'W' + w; }).join('+');
      var button = this;
      if (button.dataset.busy === '1') return;
      button.dataset.busy = '1';                 // one add per click, however the preview is timed
      expectedFor(side).then(function (expected) {
        if (!expected) {
          $('#side-summary').insertAdjacentHTML('beforeend',
            '<div class="sum-row"><span class="sum-k">Preview failed</span>'
            + '<span class="sum-v small">nothing added</span></div>');
          return;
        }
        addEdit(label, { progress: { checkpoint: null, side: side }, expected: expected });
      }).then(function () { button.dataset.busy = ''; },
              function () { button.dataset.busy = ''; });
    });
    var sideLink = $('#side-link');
    if (sideLink) sideLink.addEventListener('click', function () { sideMode(true); });
  }

  function selectUnit(instanceId) {
    var unit = S.summary.roster.filter(function (u) { return u.instance_id === instanceId; })[0];
    if (!unit) return;
    S.selected = unit;
    S.form = S.form && formChain(unit.master_id).indexOf(S.form) >= 0 ? S.form : unit.master_id;
    S.ex = unit.nv_ex_level || 0;
    S.trust = unit.trust || 0;
    S.superTrust = unit.super_trust || 0;
    renderRoster();
    renderPortrait();
    renderChain();
    paintSpec();
    previewStats();
  }

  function renderPortrait() {
    var row = selectedRow();
    if (!row) return;
    $('#portrait-no').textContent = 'No. ' + unitNo(S.form);
    $('#portrait-stars').textContent = stars(Math.max(row.rarity, 1));
    $('#portrait-name').textContent = unitName(S.form);
    var sprite = $('#portrait-sprite');
    sprite.src = artUrl(S.form);
    sprite.onerror = function () { sprite.style.visibility = 'hidden'; };
    sprite.onload = function () { sprite.style.visibility = 'visible'; };
  }

  function renderChain() {
    var chain = formChain(S.selected.master_id);
    var root = $('#chain');
    root.innerHTML = '';
    chain.forEach(function (masterId) {
      var form = document.createElement('div');
      form.className = 'form' + (masterId === S.form ? ' on' : '');
      var unit = S.catalog.units.filter(function (u) { return u.master_id === masterId; })[0];
      form.innerHTML = unitArt(masterId) + '<span class="fl">'
        + (unit ? (unit.nv ? 'NV' : unit.rarity + '★') : '★') + '</span>';
      form.addEventListener('click', function () {
        S.form = masterId;
        renderPortrait();
        renderChain();
        paintLimits();      // the caps belong to the FORM and come from the cached catalog
        previewStats();     // the stat NUMBERS still come from the server code (debounced)
      });
      root.appendChild(form);
    });
  }

  function paintLimits() {
    var info = formInfo(S.form) || {};
    S.maxLevel = info.max_level;
    S.lbCap = info.lb_cap;
    S.trustCap = info.trust_cap;
    S.superCap = info.super_trust_cap;
    var level = $('#spec-level');
    var lb = $('#spec-lb');
    level.max = S.maxLevel || 1;
    lb.max = S.lbCap || 1;
    if (S.maxLevel && Number(level.value) > S.maxLevel) level.value = S.maxLevel;
    if (S.lbCap && Number(lb.value) > S.lbCap) lb.value = S.lbCap;
    $('#spec-level-cap').textContent = '/ ' + (S.maxLevel || '—');
    $('#spec-lb-cap').textContent = '/ ' + (S.lbCap || '—');
    $$('[data-ex]').forEach(function (chip) {
      chip.classList.toggle('on', Number(chip.dataset.ex) === S.ex);
    });
    paintCaps((info.stat_caps || []));
    paintTrust();
  }

  function paintSpec() {
    var row = selectedRow();
    if (!row) return;
    $('#spec-level').value = row.level;
    $('#spec-lb').value = row.lb_level;
    paintLimits();
  }

  function paintTrust() {
    [[0, S.trust, $('#trust-readout'), $('#trust-label')],
     [1, S.superTrust, $('#super-trust-readout'), $('#super-trust-label')]].forEach(function (pair) {
      var channel = pair[0], value = pair[1];
      var slider = $('[data-trust="' + channel + '"]');
      var cap = channel === 0 ? (S.trustCap || 0) : (S.superCap || 0);
      var width = cap ? Math.round(Math.min(1, value / cap) * 100) : 0;
      var fill = slider.querySelector('.fillg');
      var knob = slider.querySelector('.knob');
      if (fill) fill.style.width = width + '%';
      if (knob) knob.style.left = 'calc(' + width + '% - 11px)';
      pair[2].textContent = cap ? (Math.round(value / cap * 1000) / 10).toFixed(1) + ' %' : '—';
      pair[3].textContent = channel === 0 ? (cap ? 'cap ' + cap : 'no reward') : (cap ? 'cap ' + cap : 'needs 7★');
    });
  }

  function bindSliders() {
    $$('[data-trust]').forEach(function (slider) {
      var channel = Number(slider.dataset.trust);
      var track = slider.querySelector('.track2');

      function valueAt(clientX) {
        var cap = channel === 0 ? (S.trustCap || 0) : (S.superCap || 0);
        if (!cap) return null;
        var rect = track.getBoundingClientRect();
        var ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        if (ratio >= 0.995) return cap;      // dragging to the end must be able to reach 100 %
        if (ratio <= 0.005) return 0;
        return Math.round(ratio * cap);
      }
      function set(value) {
        if (value === null) return;
        if (channel === 0) S.trust = value; else S.superTrust = value;
        paintTrust();
      }

      // Drag: the value follows the pointer while the button is held (a click is a 1-point drag).
      track.addEventListener('pointerdown', function (event) {
        if (event.button !== undefined && event.button !== 0) return;
        var value = valueAt(event.clientX);
        if (value === null) return;
        event.preventDefault();
        try { track.setPointerCapture(event.pointerId); } catch (error) { /* synthetic pointer */ }
        set(value);
        var move = function (moveEvent) { set(valueAt(moveEvent.clientX)); };
        var up = function (upEvent) {
          track.removeEventListener('pointermove', move);
          track.removeEventListener('pointerup', up);
          track.removeEventListener('pointercancel', up);
          try {
            if (track.hasPointerCapture(upEvent.pointerId)) track.releasePointerCapture(upEvent.pointerId);
          } catch (error) { /* synthetic pointer */ }
          set(valueAt(upEvent.clientX));
        };
        track.addEventListener('pointermove', move);
        track.addEventListener('pointerup', up);
        track.addEventListener('pointercancel', up);
      });
    });
  }

  function flag(name) {
    var chip = $('[data-flag="' + name + '"]');
    return chip ? chip.classList.contains('on') : false;
  }

  function bindBuilder() {
    $$('[data-ex]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        S.ex = Number(chip.dataset.ex);
        $$('[data-ex]').forEach(function (c) { c.classList.toggle('on', c === chip); });
        previewStats();
      });
    });
    $$('[data-flag]').forEach(function (chip) {
      chip.addEventListener('click', function () { chip.classList.toggle('on'); });
    });
    $$('.chain').forEach(function (root) { root.addEventListener('click', function () { }); });
    $('#preview').addEventListener('click', previewStats);
    $('#max-stats').addEventListener('click', fillMaxStats);
    $('[data-add="build"]').addEventListener('click', function () {
      var spec = currentSpec();
      if (!spec) return;
      var row = selectedRow();
      addEdit(unitName(spec.master_id) + ' → Lv ' + spec.level
        + (spec.nv_ex_level ? ' · EX ' + spec.nv_ex_level : ''),
        { units: [{ instance_id: row.instance_id, spec: spec }] });
    });
  }

  // Fill the strip with this form's ceilings: level, LB, EX, pots+doors, TMR/STMR.
  function fillMaxStats() {
    var row = selectedRow();
    var info = formInfo(S.form);
    if (!row || !info) return;
    var caps = info.stat_caps || [];
    $('#spec-level').value = info.max_level;
    $('#spec-lb').value = info.lb_cap;
    S.ex = info.nv ? 3 : 0;
    [0, 1, 2, 3, 4, 5].forEach(function (index) {
      var pair = caps[index] || [0, 0];
      var door = $('[data-spec="door' + index + '"]');
      var pot = $('[data-spec="pot' + index + '"]');
      if (!door || !pot) return;
      door.value = pair[1];                 // doors first: they raise the pot ceiling
      if (door.oninput) door.oninput();
      pot.value = pair[0] + pair[1];
    });
    S.trust = info.trust_cap || 0;
    S.superTrust = (info.rarity >= 7 || String(S.form).indexOf("906") === 0)
      ? (info.super_trust_cap || 0) : 0;
    // Landing exactly on the cap without the reward flag would lose the TMR item for good:
    // `gain_trust` only pays on the crossing, and a maxed row never crosses.
    if (S.trust > 0 || S.superTrust > 0) {
      var reward = $('[data-flag="trust_reward"]');
      if (reward) reward.classList.add('on');
    }
    if (S.ex > 0) {
      var exReward = $('[data-flag="ex_rewards"]');
      if (exReward) exReward.classList.add('on');
    }
    paintLimits();
    paintTrust();
    setStatus('filled with ' + unitName(S.form) + ' max');
    previewStats();
  }

  function currentSpec() {
    var row = selectedRow();
    if (!row) return null;
    var pots = [0, 1, 2, 3, 4, 5].map(function (i) { return intValue('[data-spec="pot' + i + '"]'); });
    var doors = [0, 1, 2, 3, 4, 5].map(function (i) { return intValue('[data-spec="door' + i + '"]'); });
    return {
      master_id: S.form,
      level: intValue('#spec-level') || 1,
      nv_ex_level: S.ex,
      lb_level: intValue('#spec-lb') || 1,
      stat_pots: pots,
      stat_doors: doors,
      trust: S.trust,
      super_trust: S.superTrust,
      grant_trust_reward: flag('trust_reward'),
      grant_ex_rewards: flag('ex_rewards')
    };
  }

  function previewKey(spec) {
    return [spec.master_id, spec.level, spec.nv_ex_level,
            (spec.stat_pots || []).join(','), (spec.stat_doors || []).join(',')].join('|');
  }

  function previewStats() {
    var spec = currentSpec();
    if (!spec) return;
    var key = previewKey(spec);
    if (S.previews && S.previews[key]) {
      paintPreview(S.previews[key]);
      return;
    }
    clearTimeout(S.previewTimer);
    S.previewTimer = setTimeout(function () {
      api('/api/preview', { spec: spec }).then(function (payload) {
        var unit = payload.unit || {};
        S.previews = S.previews || {};
        S.previews[key] = unit;
        paintPreview(unit);
      }).catch(function (error) {
        setStatus('preview: ' + error.message, true);
      });
    }, 250);
  }

  function paintPreview(unit) {
    if (unit.max_level) {
      S.maxLevel = unit.max_level;
      S.lbCap = unit.lb_cap;
      $('#spec-level-cap').textContent = '/ ' + unit.max_level;
      $('#spec-lb-cap').textContent = '/ ' + (unit.lb_cap || '—');
    }
    if (unit.trust_cap !== undefined) {
      S.trustCap = unit.trust_cap;
      S.superCap = unit.super_trust_cap;
    }
    paintTrust();
    if (unit.stat_caps) paintCaps(unit.stat_caps);
    renderAttrs(unit.stats || []);
    renderBars(unit.stats || []);
  }

  function paintCaps(caps) {
    // The server's own rule (unit_use_point): doors <= door_cap, pots <= pot_cap + doors.
    // So the pot ceiling is the wiki's "900 + 450": 450 only becomes reachable by raising doors.
    [0, 1, 2, 3, 4, 5].forEach(function (index) {
      var pair = caps[index] || [0, 0];
      var pot = $('[data-spec="pot' + index + '"]');
      var door = $('[data-spec="door' + index + '"]');
      if (!pot || !door) return;
      var potTag = pot.parentElement.querySelector('i');
      var doorTag = door.parentElement.querySelector('i');
      if (!potTag) { potTag = document.createElement('i'); pot.parentElement.appendChild(potTag); }
      if (!doorTag) { doorTag = document.createElement('i'); door.parentElement.appendChild(doorTag); }
      door.max = pair[1];
      doorTag.textContent = pair[1];
      var sync = function () {
        var doors = Math.max(0, Math.min(Number(door.value) || 0, pair[1]));
        var ceiling = pair[0] + doors;
        pot.max = ceiling;
        potTag.textContent = pair[0] + ' + ' + doors;
        if ((Number(pot.value) || 0) > ceiling) pot.value = ceiling;
      };
      door.oninput = sync;
      sync();
    });
  }

  function renderBars(stats) {
    var bars = $('#portrait-bars');
    if (!bars) return;
    $$('.bar', bars).forEach(function (row, index) {
      var value = stats[index] || 0;
      var fill = row.querySelector('.fill');
      var num = row.querySelector('.num2');
      if (fill) fill.style.width = '100%';
      if (num) num.textContent = fmt(value) + ' / ' + fmt(value);
    });
  }

  var STATS = [['HP', 'i-hp'], ['MP', 'i-mp'], ['ATK', 'i-atk'], ['DEF', 'i-def'],
               ['MAG', 'i-mag'], ['SPR', 'i-spr']];

  function renderAttrs(stats) {
    var root = $('#attrs');
    root.innerHTML = '';
    STATS.forEach(function (stat, index) {
      var value = stats[index] || 0;
      var row = document.createElement('div');
      row.className = 'arow';
      row.innerHTML = '<svg class="ico"><use href="#' + stat[1] + '"/></svg>'
        + '<span class="al">' + stat[0] + '</span><span class="av">' + fmt(value) + '</span>';
      root.appendChild(row);
    });
  }

  /* --------------------------------------------------------------------- awakening kit */
  function renderKitUnits() {
    var select = $('#kit-unit');
    if (!select || !S.summary) return;
    select.innerHTML = '';
    S.summary.roster.forEach(function (unit) {
      var option = document.createElement('option');
      option.value = unit.instance_id;
      option.textContent = unit.name + ' · ' + (formInfo(unit.master_id) ? formLabel(unit.master_id) : '?')
        + ' · Lv ' + unit.level;
      select.appendChild(option);
    });
    select.addEventListener('change', loadKit);
    $('#kit-ex-level').addEventListener('change', loadKit);
    $('#kit-target').addEventListener('change', loadKit);
    $$('[data-kit-type]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        S.kitType = chip.dataset.kitType;
        $$('[data-kit-type]').forEach(function (c) { c.classList.toggle('on', c === chip); });
        loadKit();
      });
    });
    $$('[data-kit-mode]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        S.kitMode = chip.dataset.kitMode;
        $$('[data-kit-mode]').forEach(function (c) { c.classList.toggle('on', c === chip); });
        loadKit();
      });
    });
    loadKit();
  }

  function formLabel(masterId) {
    var info = formInfo(masterId);
    if (!info) return String(masterId);
    return info.nv ? 'NV' : (info.rarity || '?') + '★';
  }

  // every form this one can still awaken into, in order (4★ -> 5★ -> 6★ ...)
  function formsAfter(masterId) {
    var info = formInfo(masterId);
    if (!info || !S.catalog) return [];
    var chain = S.catalog.awaken_chain[info.nv_group] || [];
    var all = [info.nv_group].concat(chain);
    var index = all.indexOf(masterId);
    return index >= 0 ? all.slice(index + 1) : chain.slice();
  }

  function kitUnit() {
    var select = $('#kit-unit');
    if (!select || !select.selectedOptions[0] || !S.summary) return null;
    var instance = select.selectedOptions[0].value;
    return S.summary.roster.filter(function (u) { return u.instance_id === instance; })[0] || null;
  }

  function kitRecipe() {
    var unit = kitUnit();
    if (!unit) return null;
    var info = formInfo(unit.master_id) || {};
    var group = info.nv_group || unit.master_id;
    if (S.kitType === 'unit_class_up') {
      var target = $('#kit-target').value;
      if (!target) return null;
      return { awaken: true, from: unit.master_id, to: target };
    }
    if (S.kitType === 'ex_class_up') {
      if (!info.nv || !info.nv_id) return null;   // EX only exists on an NV row
      var level = $('#kit-ex-level').value || '1';
      return { table: 'ex_class_up', id: info.nv_id + ':' + level };
    }
    return null;
  }

  // a type the unit cannot use is greyed out, not silently unproductive
  function paintKitTypes() {
    var unit = kitUnit();
    var info = unit ? (formInfo(unit.master_id) || {}) : {};
    var canStar = !!(unit && formsAfter(unit.master_id).length);
    var canEx = !!info.nv;
    $$('[data-kit-type]').forEach(function (chip) {
      var kind = chip.dataset.kitType;
      var available = kind === 'sublimation' ? true
        : (kind === 'unit_class_up' ? canStar : canEx);
      chip.classList.toggle('off', !available);
      chip.title = available ? '' : (kind === 'unit_class_up'
        ? 'this unit is already at its top form'
        : 'EX awakening only exists on a Neo Vision form');
      chip.style.opacity = available ? '' : '.4';
      chip.style.pointerEvents = available ? '' : 'none';
    });
    if (!canStar && S.kitType === 'unit_class_up') S.kitType = canEx ? 'ex_class_up' : 'sublimation';
    if (!canEx && S.kitType === 'ex_class_up') S.kitType = canStar ? 'unit_class_up' : 'sublimation';
    $$('[data-kit-type]').forEach(function (chip) {
      chip.classList.toggle('on', chip.dataset.kitType === S.kitType);
    });
  }

  function paintKitTargets() {
    var unit = kitUnit();
    var select = $('#kit-target');
    if (!select) return;
    var previous = select.value;
    select.innerHTML = '';
    var options = unit ? formsAfter(unit.master_id) : [];
    options.forEach(function (masterId) {
      var option = document.createElement('option');
      option.value = masterId;
      option.textContent = formLabel(masterId);
      select.appendChild(option);
    });
    // keep the chosen target across a re-render (otherwise any reload snaps back to the next form)
    if (previous && options.indexOf(previous) >= 0) select.value = previous;
    $('#kit-target-row').style.display = (S.kitType === 'unit_class_up' && options.length) ? '' : 'none';
    if (!options.length && S.kitType === 'unit_class_up') {
      select.innerHTML = '<option value="">already at the top form</option>';
    }
  }

  function loadKit() {
    var root = $('#kit-mats');
    paintKitTypes();
    paintKitTargets();
    $('#kit-ex-row').style.display = S.kitType === 'ex_class_up' ? '' : 'none';
    var recipe = kitRecipe();
    var unit = kitUnit();
    if (S.kitType === 'sublimation') {
      $('#kit-recipe').textContent = 'ability recipes live in the Ability Awakening view';
      root.innerHTML = '<p class="note">Ability awakening kits are granted from the Ability Awakening view.</p>';
      S.kit = null;
      return;
    }
    if (!recipe) {
      $('#kit-recipe').textContent = 'nothing to awaken';
      root.innerHTML = '<p class="note">' + esc(unit ? formLabel(unit.master_id) + ' has no further form to awaken into.'
        : 'pick a unit') + '</p>';
      S.kit = null;
      return;
    }
    var url = recipe.awaken
      ? '/api/awaken?from=' + encodeURIComponent(recipe.from) + '&to=' + encodeURIComponent(recipe.to)
      : '/api/recipe?table=' + encodeURIComponent(recipe.table) + '&id=' + encodeURIComponent(recipe.id);
    api(url).then(function (kit) {
      S.kit = kit;
      if (recipe.awaken && unit) {
        var path = [formLabel(unit.master_id)].concat((kit.steps || []).map(function (step) {
          return formLabel(step.to);
        }));
        $('#kit-recipe').textContent = path.join(' → ') + ' · ' + (kit.steps || []).length + ' step(s)';
      } else {
        $('#kit-recipe').textContent = (kit.table || '') + ' ' + (kit.row_id || '');
      }
      renderKit(kit);
    }).catch(function (error) {
      $('#kit-recipe').textContent = 'no recipe';
      root.innerHTML = '<p class="note">' + esc(error.message) + '</p>';
      S.kit = null;
    });
  }

  function materialName(itemId) {
    var row = (S.catalog.materials || []).filter(function (m) { return m.item_id === String(itemId); })[0];
    return row ? row.name : String(itemId);
  }

  function renderKit(kit) {
    var root = $('#kit-mats');
    var held = (S.summary && S.summary.materials_held) || {};
    var gilHave = (S.summary && S.summary.gil) || 0;
    root.innerHTML = '';
    kit.materials.forEach(function (material, index) {
      var have = held[String(material.item_id)] || 0;
      var short = Math.max(0, material.count - have);
      var need = S.kitMode === 'shortfall' ? short : material.count;
      var div = document.createElement('div');
      div.className = 'mat g' + ((index % 5) + 1) + (need === 0 ? ' dim' : (have < need ? ' short' : ''));
      div.innerHTML = '<span class="mx">×' + fmt(need) + '</span><div class="gem2"></div>'
        + '<div class="mn">' + esc(materialName(material.item_id)) + '</div>'
        + '<div class="have' + (have < need ? ' short' : '') + '">' + fmt(have) + ' held</div>';
      root.appendChild(div);
    });
    var gilNeed = S.kitMode === 'shortfall' ? Math.max(0, kit.gil - gilHave) : kit.gil;
    var gil = document.createElement('div');
    gil.className = 'mat g2' + (gilNeed === 0 ? ' dim' : '');
    gil.innerHTML = '<span class="mx">' + fmt(gilNeed) + '</span><div class="gem2"></div>'
      + '<div class="mn">Gil</div><div class="have">have ' + (gilHave / 1e6).toFixed(2) + 'M</div>';
    root.appendChild(gil);
  }

  function bindKit() {
    $('[data-add="kit"]').addEventListener('click', function () {
      if (!S.kit) return;
      var held = (S.summary && S.summary.materials_held) || {};
      var materials = S.kit.materials.map(function (material) {
        var have = held[String(material.item_id)] || 0;
        var need = S.kitMode === 'shortfall' ? Math.max(0, material.count - have) : material.count;
        return { item_id: Number(material.item_id), count: need };
      }).filter(function (row) { return row.count > 0; });
      var gilHave = (S.summary && S.summary.gil) || 0;
      var gil = S.kitMode === 'shortfall' ? Math.max(0, S.kit.gil - gilHave) : S.kit.gil;
      var edits = {};
      if (materials.length) edits.materials = materials;
      if (gil > 0) edits.currencies = { gil: gil };
      if (!edits.materials && !edits.currencies) return;
      addEdit('Kit · ' + S.kit.table + ' ' + S.kit.row_id, edits);
    });
    $('[data-add-material]').addEventListener('click', function () {
      var option = $('#mat-item').selectedOptions[0];
      var count = intValue('#mat-count');
      if (!option || count <= 0) return;
      addEdit(option.textContent + ' ×' + count, { materials: [{ item_id: Number(option.value), count: count }] });
    });
  }

  function renderMaterialFamilies() {
    var select = $('#mat-family');
    if (!select) return;
    var families = {};
    (S.catalog.materials || []).forEach(function (material) { families[material.family] = true; });
    select.innerHTML = '';
    Object.keys(families).sort().forEach(function (family) {
      var option = document.createElement('option');
      option.value = family;
      option.textContent = family;
      select.appendChild(option);
    });
    select.addEventListener('change', renderFamilyItems);
    renderFamilyItems();
  }

  function renderFamilyItems() {
    var select = $('#mat-item');
    var family = $('#mat-family').value;
    select.innerHTML = '';
    (S.catalog.materials || []).filter(function (m) { return m.family === family; })
      .forEach(function (material) {
        var option = document.createElement('option');
        option.value = material.item_id;
        option.textContent = material.name;
        select.appendChild(option);
      });
  }

  /* ---------------------------------------------------------------- ability awakening */
  function renderMatrices() {
    var byName = {};
    (S.catalog.materials || []).forEach(function (material) { byName[material.name.toLowerCase()] = material; });
    $$('input[data-kind][data-tier]').forEach(function (input) {
      var key = (input.dataset.kind + ' ' + input.dataset.tier).toLowerCase();
      var material = byName[key];
      input.dataset.itemId = material ? material.item_id : '';
      input.title = material ? material.name : 'not in the master data';
      var paint = function () {
        input.classList.toggle('zero', !(parseInt(input.value, 10) > 0));
      };
      input.addEventListener('input', paint);
      if (input.value === '0' || input.value === '') paint();
    });
  }

  function bindAbility() {
    $('[data-add="matrix"]').addEventListener('click', function () {
      var rows = [];
      $$('input[data-kind][data-tier]').forEach(function (input) {
        var count = parseInt(input.value, 10) || 0;
        if (count > 0 && input.dataset.itemId) rows.push({ item_id: Number(input.dataset.itemId), count: count });
      });
      if (!rows.length) return;
      addEdit('Cryst matrix · ' + rows.length + ' entries', { materials: rows });
    });
    $('#ability-unit').addEventListener('change', loadRecipes);
    $('[data-add="ability"]').addEventListener('click', function () {
      var instance = $('#ability-unit').value;
      var option = $('#ability-recipe').selectedOptions[0];
      if (!instance || !option) return;
      addEdit('Ability · ' + option.textContent, {
        abilities: [{ instance_id: instance, recipe_id: option.value, grant_materials: false }]
      });
    });
  }

  function renderAbilityUnits() {
    var select = $('#ability-unit');
    if (!select || !S.summary) return;
    select.innerHTML = '';
    S.summary.roster.forEach(function (unit) {
      var option = document.createElement('option');
      option.value = unit.instance_id;
      option.textContent = unit.name + ' · ' + unit.master_id;
      select.appendChild(option);
    });
    loadRecipes();
  }

  function loadRecipes() {
    var select = $('#ability-unit');
    var option = select.selectedOptions[0];
    if (!option || !S.summary) return;
    var unit = S.summary.roster.filter(function (u) { return u.instance_id === option.value; })[0];
    if (!unit) return;
    api('/api/abilities?master=' + encodeURIComponent(unit.master_id)).then(function (payload) {
      var recipes = $('#ability-recipe');
      recipes.innerHTML = '';
      (payload.recipes || []).forEach(function (recipe) {
        var entry = document.createElement('option');
        entry.value = recipe.recipe_id;
        var name = recipe.before_name === recipe.after_name
          ? recipe.before_name
          : recipe.before_name + ' → ' + recipe.after_name;
        entry.textContent = name + ' · tier ' + recipe.tier + ' · ' + fmt(recipe.gil) + ' gil'
          + (recipe.needs_exbonus ? ' · EX-bonus' : '');
        recipes.appendChild(entry);
      });
      if (!(payload.recipes || []).length) {
        recipes.innerHTML = '<option value="">no awakening recipes for this unit</option>';
      }
    }).catch(function (error) {
      setStatus('recipes: ' + error.message, true);
    });
  }

  /* --------------------------------------------------------------------- vision cards */
  function renderVision() {
    var body = $('#vc-body');
    if (!body || !S.summary) return;
    $('#vc-count').textContent = S.summary.vision_cards.length + (S.summary.vision_cards.length === 1 ? ' card' : ' cards');
    body.innerHTML = '<div class="vhead"><span>Card</span><span>Level</span><span>Set to</span></div>';
    S.summary.vision_cards.forEach(function (card) {
      var row = document.createElement('div');
      row.className = 'cardrow';
      row.style.borderTop = '1px solid rgba(140,190,215,.11)';
      var width = Math.round(card.level / card.cap * 100);
      row.innerHTML = '<div><span class="cname">' + esc(card.name) + '</span> '
        + '<span class="muted">· vc_id ' + esc(card.vc_id) + '</span></div>'
        + '<div class="lvlbar"><div class="track"><div class="fill hp" style="width:' + width + '%"></div></div>'
        + '<span class="n">' + card.level + ' / ' + card.cap + '</span></div>'
        + '<div style="display:flex;gap:6px;justify-content:flex-end">'
        + '<input class="tin" value="' + card.cap + '">'
        + '<button class="btn sm">Set</button></div>';
      row.querySelector('button').addEventListener('click', function () {
        var level = parseInt(row.querySelector('input').value, 10) || 1;
        addEdit(card.name + ' → Lv ' + level, { card_levels: [{ user_vc_id: card.user_vc_id, level: level }] });
      });
      body.appendChild(row);
    });
  }

  function renderGrantCards() {
    var select = $('#vc-grant');
    if (!select) return;
    select.innerHTML = '';
    (S.catalog.vision_cards || []).forEach(function (card) {
      var option = document.createElement('option');
      option.value = card.vc_id;
      option.textContent = card.name + ' · max ' + card.max_level;
      select.appendChild(option);
    });
  }

  function bindVision() {
    $('[data-add-vision]').addEventListener('click', function () {
      var option = $('#vc-grant').selectedOptions[0];
      if (!option) return;
      var level = intValue('#vc-grant-level') || 1;
      addEdit(option.textContent + ' · Lv ' + level, { cards: [{ vc_id: option.value, level: level }] });
    });
  }

  /* ------------------------------------------------------------------------- the log */
  function timeNow() {
    var d = new Date();
    return [d.getHours(), d.getMinutes(), d.getSeconds()]
      .map(function (n) { return String(n).padStart(2, '0'); }).join(':');
  }

  function renderLog() {
    var count = $('#log-count');
    if (count) count.textContent = S.log.length;
    var body = $('#log-rows');
    if (!body) return;
    body.innerHTML = '';
    if (!S.log.length) body.innerHTML = '<tr><td class="muted" colspan="4">no applies yet this session</td></tr>';
    S.log.slice(0, 12).forEach(function (entry) {
      var row = document.createElement('tr');
      row.innerHTML = '<td class="muted">' + entry.time + '</td><td>' + esc(entry.action) + '</td>'
        + '<td>' + esc(entry.detail) + '</td><td class="num" style="color:'
        + (entry.ok ? 'var(--pos)' : 'var(--warn)') + '">' + esc(entry.result) + '</td>';
      body.appendChild(row);
    });
    var backups = $('#backup-rows');
    if (backups) {
      backups.innerHTML = '';
      if (!S.backups.length) {
        backups.innerHTML = '<tr><td class="muted" colspan="5">no backups from this session yet</td></tr>';
      }
      S.backups.slice(0, 10).forEach(function (path) {
        var name = path.split('/').pop();
        var row = document.createElement('tr');
        row.innerHTML = '<td class="cname">' + esc(name) + '</td><td class="num">—</td>'
          + '<td class="num">—</td><td class="num">—</td><td><span class="muted">restore by hand</span></td>';
        backups.appendChild(row);
      });
    }
  }

  /* ------------------------------------------------------------------------ apply */
  function bindApply() {
    $('#clear').addEventListener('click', function () {
      S.pending = [];
      renderCart();
      renderAfter();
      setStatus('');
    });
    $('#apply').addEventListener('click', function () {
      if (!S.pending.length) return;
      var edits = pendingEdits();
      var lines = S.pending.map(function (item) { return '• ' + item.label; }).join('\n');
      if (!window.confirm('Apply these ' + S.pending.length + ' edit(s)?\n\n' + lines
        + '\n\nThe server stops, backs up, edits, validates and restarts.\nThe game disconnects for ~20 s.')) return;
      setStatus('applying…');
      $('#apply').disabled = true;
      api('/api/apply', { edits: edits }).then(function (result) {
        var tail = (result.log || []).slice(-1)[0] || '';
        S.log.unshift({
          time: timeNow(), action: 'apply',
          detail: result.ok
            ? S.pending.length + ' edits · ' + (result.backup || 'no backup')
            : (result.error || 'failed') + ' · nothing written',
          result: result.ok ? 'ok' : 'failed', ok: result.ok
        });
        if (result.backup) S.backups.unshift(result.backup);
        if (result.ok) {
          S.pending = [];
          renderCart();
          setStatus('applied · ' + (result.backup || '').split('/').pop());
        } else {
          setStatus('apply failed: ' + (result.error || tail), true);
        }
        renderLog();
      }).catch(function (error) {
        S.log.unshift({ time: timeNow(), action: 'apply', detail: error.message, result: 'error', ok: false });
        renderLog();
        setStatus('apply failed: ' + error.message, true);
      }).then(function () {
        // Only a healthy app may apply: a failed apply may have been a failed setup.
        return loadHealth();
      }).then(function () {
        var ok = HEALTH_OK;
        $('#apply').disabled = !ok;
        return loadSummary().catch(function () { });
      });
    });
  }

  /* ------------------------------------------------------------------------- boot */
  /* ------------------------------------------------------------------------- setup */
  var HEALTH_OK = false;

  function renderHealth(health) {
    var pill = $('#server-status');
    var strip = $('#setup');
    var ok = health && health.ok;
    HEALTH_OK = !!ok;
    if (pill) {
      pill.innerHTML = '<i class="dot' + (ok ? '' : ' bad') + '"></i> ' +
        (ok ? (health.container + ' · ready') : 'setup needed');
    }
    var apply = $('#apply');
    if (apply) apply.disabled = !ok;
    if (!strip) return;
    var showWarn = !!(health && health.exposed);
    if (ok && !showWarn) { strip.hidden = true; return; }
    strip.hidden = false;
    strip.className = ok ? 'setup ok' : 'setup';
    if (ok) {
      // Healthy, but anyone who can reach the port can apply: say so, once.
      $('#setup-head').textContent = 'This page is reachable from other machines';
      $('#setup-msg').textContent = health.exposure || '';
    } else {
      $('#setup-head').textContent = health && health.error
        ? 'The app cannot reach the game yet' : 'Checking what this app can see…';
      $('#setup-msg').textContent = (health && health.error) ||
        'Set container, server_dir and data_dir in admin.toml, or start the game container.';
    }
    $('#setup-container').textContent = (health && health.container) || '—';
    $('#setup-save').textContent = (health && health.save) || '—';
    $('#setup-backups').textContent = (health && health.backups) || '—';
    $('#setup-engine').textContent = health && health.engine_ok
      ? 'exported from ' + (health.image || 'the image') : 'not exported';
  }

  function loadHealth() {
    return fetch('/api/health', {cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(renderHealth)
      .catch(function (error) {
        renderHealth({ok: false, error: 'the app could not check itself: ' + error.message});
      });
  }

  function boot() {
    loadHealth();
    bindViews();
    bindGrants();
    bindBuilder();
    bindSliders();
    bindKit();
    bindAbility();
    bindVision();
    bindApply();
    renderAttrs([0, 0, 0, 0, 0, 0]);
    renderCart();
    renderLog();
    loadSummary().then(function () {
      return loadCatalog();
    }).then(function () {
      renderUnitResults();
      renderAbilityUnits();
      setStatus('ready');
    }).catch(function (error) {
      setStatus('cannot read the save: ' + error.message, true);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
