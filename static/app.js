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
  }

  /* ------------------------------------------------------------------------ the cart */
  function mergeInto(target, add) {
    Object.keys(add).forEach(function (key) {
      var value = add[key];
      if (Array.isArray(value)) target[key] = (target[key] || []).concat(value);
      else if (value && typeof value === 'object') {
        target[key] = target[key] || {};
        mergeInto(target[key], value);
      } else target[key] = (target[key] || 0) + value;
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
      return unit.name.toLowerCase().indexOf(query) >= 0 || unit.master_id.indexOf(query) >= 0;
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
        + '<div><span class="nm">' + esc(unit.name) + '</span><span class="sub">No. ' + esc(unit.master_id)
        + ' · ' + unit.rarity + '★</span></div><span class="owned" style="color:var(--dim)">grant</span>';
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
        + '<span class="umeta">No. ' + esc(unit.master_id) + (unit.nv_ex_level ? ' · EX +' + unit.nv_ex_level : '') + '</span></div>'
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
    $('#portrait-no').textContent = 'No. ' + S.form;
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
      });
      root.appendChild(form);
    });
  }

  function paintSpec() {
    var row = selectedRow();
    if (!row) return;
    var spec = { level: row.level, lb: row.lb_level, ex: S.ex };
    $('#spec-level').value = spec.level;
    $('#spec-level-cap').textContent = '/ ' + (S.maxLevel || '—');
    $('#spec-lb').value = spec.lb;
    $('#spec-lb-cap').textContent = '/ ' + (S.lbCap || '—');
    $$('[data-ex]').forEach(function (chip) {
      chip.classList.toggle('on', Number(chip.dataset.ex) === S.ex);
    });
    paintTrust();
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
      slider.querySelector('.track2').addEventListener('click', function (event) {
        var cap = channel === 0 ? (S.trustCap || 0) : (S.superCap || 0);
        if (!cap) return;
        var rect = slider.querySelector('.track2').getBoundingClientRect();
        var ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
        var value = Math.round(ratio * cap);
        if (channel === 0) S.trust = value; else S.superTrust = value;
        paintTrust();
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
      });
    });
    $$('[data-flag]').forEach(function (chip) {
      chip.addEventListener('click', function () { chip.classList.toggle('on'); });
    });
    $$('.chain').forEach(function (root) { root.addEventListener('click', function () { }); });
    $('#preview').addEventListener('click', previewStats);
    $('[data-add="build"]').addEventListener('click', function () {
      var spec = currentSpec();
      if (!spec) return;
      var row = selectedRow();
      addEdit(unitName(spec.master_id) + ' → Lv ' + spec.level
        + (spec.nv_ex_level ? ' · EX ' + spec.nv_ex_level : ''),
        { units: [{ instance_id: row.instance_id, spec: spec }] });
    });
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

  function previewStats() {
    var spec = currentSpec();
    if (!spec) return;
    api('/api/preview', { spec: spec }).then(function (payload) {
      var unit = payload.unit || {};
      S.maxLevel = unit.max_level;
      S.lbCap = unit.lb_cap;
      S.trustCap = unit.trust_cap;
      S.superCap = unit.super_trust_cap;
      $('#spec-level-cap').textContent = '/ ' + unit.max_level;
      $('#spec-lb-cap').textContent = '/ ' + (unit.lb_cap || '—');
      paintTrust();
      renderAttrs(unit.stats || []);
      renderBars(unit.stats || []);
    }).catch(function (error) {
      setStatus('preview: ' + error.message, true);
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
      option.textContent = unit.name + ' · ' + unit.master_id + ' · Lv ' + unit.level;
      select.appendChild(option);
    });
    select.addEventListener('change', loadKit);
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

  function kitRecipe() {
    var select = $('#kit-unit');
    if (!select || !select.selectedOptions[0] || !S.catalog) return null;
    var instance = select.selectedOptions[0].value;
    var unit = S.summary.roster.filter(function (u) { return u.instance_id === instance; })[0];
    if (!unit) return null;
    var group = unit.master_id;
    var catalogRow = S.catalog.units.filter(function (u) { return u.master_id === unit.master_id; })[0];
    if (catalogRow) group = catalogRow.nv_group;
    if (S.kitType === 'unit_class_up') return { table: 'unit_class_up', id: unit.master_id };
    if (S.kitType === 'ex_class_up') return { table: 'ex_class_up', id: group + ':1' };
    return null;
  }

  function loadKit() {
    var root = $('#kit-mats');
    var recipe = kitRecipe();
    $('#kit-recipe').textContent = recipe ? (recipe.table + ' ' + recipe.id) : 'no recipe for this type';
    if (!recipe) { root.innerHTML = '<p class="note">Ability kits are granted from the Ability Awakening view.</p>'; return; }
    api('/api/recipe?table=' + encodeURIComponent(recipe.table) + '&id=' + encodeURIComponent(recipe.id))
      .then(function (kit) {
        S.kit = kit;
        renderKit(kit);
      })
      .catch(function (error) {
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
        entry.textContent = 'tier ' + recipe.tier + ' · recipe ' + recipe.recipe_id
          + ' · ' + fmt(recipe.gil) + ' gil' + (recipe.needs_exbonus ? ' · EX-bonus' : '');
        recipes.appendChild(entry);
      });
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
          detail: S.pending.length + ' edits · ' + (result.backup || 'no backup'),
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
        $('#apply').disabled = false;
        return loadSummary().catch(function () { });
      });
    });
  }

  /* ------------------------------------------------------------------------- boot */
  function boot() {
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
