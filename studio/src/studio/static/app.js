"use strict";
// tracemodel studio: the grid. The model, its cells and its checks come from the API (see app.py);
// this file lays them out: rows, columns, groups, filters, the cell panel.

// ---- What the API gave: the definition, every cell, the checks ----
var DEF = null, HEAD = '';
var DIMS = {}, DESC = {}, ITEMS = {}, REFS = {}, DIM_ANC = {};
var CELLS = {};            // item -> 'key|of|coords' -> { v, nil, err }
var PENDING = new Set();   // 'item|key' of cells with changes not yet committed
var CHANGES = 0;           // every uncommitted change: values, rows (members) and columns (items)
var DETAIL = {};           // 'item|key' -> what /api/cell said, until the next reload
var CHECKS = [];
var SECTIONS = [], GROUPS = {}, DEFAULT_ROWS = {};
var commitMsg = '';

function api(method, url, body) {
  return fetch(url, { method: method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
    .then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : (j.detail ? JSON.stringify(j.detail) : r.statusText));
        return j;
      });
    });
}

function hintOf(it) {
  if (it.dtype === 'ref') return 'a ' + it.ref + ': ' + (DIMS[it.ref] || []).join(', ');
  if (it.dtype === 'date') return 'YYYY-MM-DD';
  if (it.dtype === 'int') return 'a whole number';
  if (it.dtype === 'float') return 'a number';
  return 'text';
}

// Relations: an item on the child dimension whose value is a label of the parent dimension.
function parentsOf(dim) { return Object.keys(REFS).filter(function (i) { return REFS[i].from === dim; }).map(function (i) { return { item: i, to: REFS[i].to }; }); }
function buildRelations() {
  REFS = {};
  Object.keys(ITEMS).forEach(function (i) { if (ITEMS[i].ref) REFS[i] = { from: ITEMS[i].dims[0], to: ITEMS[i].ref }; });
  // Dimensions reachable upward from each dimension through the relations.
  DIM_ANC = {};
  Object.keys(DIMS).forEach(function (d) {
    var seen = [];
    (function up(x) { parentsOf(x).forEach(function (p) { if (seen.indexOf(p.to) < 0) { seen.push(p.to); up(p.to); } }); })(d);
    DIM_ANC[d] = seen;
  });
}

// The sidebar: a model table holds the items that share a set of dimensions; a view is a saved layout over items.
function buildSections(def) {
  SECTIONS = []; GROUPS = {}; DEFAULT_ROWS = {};
  def.tables.forEach(function (t) {
    // A levels table holds every item whose dimensions are some of its rows': one for the model, one per project, one per project and unit type.
    var items = Object.keys(ITEMS).filter(function (n) {
      var d = ITEMS[n].dims;
      if (t.levels) return d.every(function (x) { return t.rows.indexOf(x) >= 0; });
      return d.length === t.rows.length && t.rows.every(function (x) { return d.indexOf(x) >= 0; });
    });
    if (items.length) SECTIONS.push({ kind: 'model', title: t.title, group: t.id, only: true, rows: t.rows, items: items, levels: !!t.levels });
  });
  def.views.forEach(function (v) { SECTIONS.push({ kind: 'view', title: v.title, group: v.id, items: v.items, preset: v.preset }); });
  SECTIONS.forEach(function (sec) {
    GROUPS[sec.group] = { title: sec.title, kind: sec.kind, items: sec.items, dims: sec.levels ? sec.rows : ITEMS[sec.items[0]].dims, only: !!sec.only, preset: sec.preset || null, levels: !!sec.levels };
    DEFAULT_ROWS['group:' + sec.group] = sec.rows || (sec.preset && sec.preset.rows);
  });
}

function load(def) {
  DEF = def; DIMS = {}; DESC = {}; ITEMS = {};
  Object.keys(def.dimensions).forEach(function (d) {
    DIMS[d] = def.dimensions[d].labels.map(function (l) { return l.code; });
    DESC[d] = {};
    def.dimensions[d].labels.forEach(function (l) { if (l.description) DESC[d][l.code] = l.description; });
  });
  Object.keys(def.items).forEach(function (n) {
    var it = def.items[n];
    ITEMS[n] = Object.assign({}, it, { date: it.dtype === 'date', agg: false, required: !!it.ref });
  });
  Object.keys(ITEMS).forEach(function (n) { ITEMS[n].hint = hintOf(ITEMS[n]); });
  buildRelations();
  buildSections(def);
}

// A check's failing cells, as the grid reads them: { item, coords, why }.
function adaptCheck(c) {
  var cells = [], seen = {};
  c.failing.forEach(function (run) {
    run.cells.forEach(function (x) {
      var k = x.item + '|' + JSON.stringify(x.coords);
      if (seen[k]) return;
      seen[k] = true;
      cells.push({ item: x.item, coords: x.coords, why: run.why });
    });
  });
  return { name: c.id, text: c.description, cells: function () { return cells; }, why: function (x) { return x.why; } };
}

function reload() {
  return Promise.all([api('GET', '/api/cells'), api('GET', '/api/checks')]).then(function (r) {
    CELLS = r[0].cells; PENDING = new Set(r[0].pending); CHANGES = r[0].changes; HEAD = r[0].head; DETAIL = {};
    CHECKS = r[1].map(adaptCheck);
  });
}

// The model's shape may have changed (a row or column added or removed, a discard): read it all again.
function refreshAll() {
  return api('GET', '/api/model').then(function (def) { load(def); pruneLayouts(); return reload(); }).then(function () {
    if (sel && !(ITEMS[sel.item] && ITEMS[sel.item].dims.every(function (d) { return (DIMS[d] || []).indexOf(sel.coords[d]) >= 0; }))) sel = null;
    bumpGrid(); render();
  });
}
// A saved layout that names a dimension, item or date part that is gone starts again.
function pruneLayouts() {
  Object.keys(layouts).forEach(function (id) {
    var L = layouts[id], g = groupOf(id), ok = !!g;
    if (ok) ['rows', 'columns', 'unused'].forEach(function (w) {
      L[w].forEach(function (d) { if (d !== IT && !(isDt(d) ? ITEMS[dtSplit(d).item] : DIMS[d])) ok = false; });
    });
    if (!ok) delete layouts[id];
  });
}

function notify(msg, ok) {
  var t = document.getElementById('toast');
  t.className = 'notice' + (ok ? ' ok' : '');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(function () { t.hidden = true; }, 7000);
}

// A cell's key is its coordinates in the item's order; the item and key name it everywhere.
function cellKey(item, coords) { return ITEMS[item].dims.map(function (d) { return coords[d]; }).join('|'); }
function key(item, coords) { return item + '|' + cellKey(item, coords); }
// NA is a cell entered as nil: the source shows nothing. It counts as defined, unlike a blank.
function entered(item, coords) {
  var c = (CELLS[item] || {})[cellKey(item, coords)];
  return !c ? null : (c.nil ? NA : c.v);
}
function get(item, coords) { var v = entered(item, coords); return v === NA ? null : v; }

function labelOf(d, c) { return DESC[d] && DESC[d][c] ? c + ' · ' + DESC[d][c] : c; }
function validDate(t) { return /^\d{4}-\d{2}-\d{2}$/.test(t) && !isNaN(Date.parse(t)); }

// Where a label sits above: its ancestors, found by following the pointer values now.
function ancestors(dim, label) {
  var out = {};
  (function up(d, l) {
    parentsOf(d).forEach(function (p) {
      var c = {}; c[d] = l;
      var v = entered(p.item, c);
      if (v !== null && DIMS[p.to].indexOf(v) >= 0) { out[p.to] = v; up(p.to, v); }
    });
  })(dim, label);
  return out;
}
// Is this label compatible with the coordinates already chosen? Related dimensions must agree through the pointers.
function consistent(dim, label, partial) {
  var A = null;
  return Object.keys(partial).every(function (pd) {
    if (pd === dim) return partial[pd] === label;
    if (DIM_ANC[dim].indexOf(pd) >= 0) { A = A || ancestors(dim, label); return A[pd] === partial[pd]; }
    if (DIM_ANC[pd] && DIM_ANC[pd].indexOf(dim) >= 0) return ancestors(pd, partial[pd])[dim] === label;
    return true;
  });
}
function membersFor(dim, partial) { return DIMS[dim].filter(function (l) { return consistent(dim, l, partial); }); }
// Do all these coordinates agree through the pointers? A column for project B has no cell for a line under project A.
function fits(co) { return dtOk(co) && pickOk(co) && Object.keys(co).every(function (d) { if (isDt(d)) return true; var rest = Object.assign({}, co); delete rest[d]; return consistent(d, co[d], rest); }); }
function pick(item, co) { var o = {}; ITEMS[item].dims.forEach(function (d) { if (d in co) o[d] = co[d]; }); return o; }

function groupOf(id) { return id.indexOf('group:') === 0 ? GROUPS[id.slice(6)] : null; }
function viewOf(item) {
  var ids = Object.keys(GROUPS);
  for (var i = 0; i < ids.length; i++) if (GROUPS[ids[i]].only && GROUPS[ids[i]].items.indexOf(item) >= 0) return 'group:' + ids[i];
  return item;
}
function dimsOf(id) { var g = groupOf(id); return g ? g.dims : ITEMS[id].dims; }
// Dimensions a view can lay out: its own only. A pointer such as engagement_contract is an item, not a grouping level (for now).
// Plus the parts of every date item on those dimensions, like the pandas dt accessor: payment_date.dt.month.
function availDims(id) {
  var own = dimsOf(id), out = own.slice();
  Object.keys(ITEMS).forEach(function (i) {
    if (ITEMS[i].date && ITEMS[i].dims.every(function (d) { return own.indexOf(d) >= 0; })) DT_PARTS.forEach(function (p) { out.push(i + '.dt.' + p); });
  });
  return out;
}
function totalsOn(item) { return !!ITEMS[item].agg; }
function totalsAny(id) { var g = groupOf(id); return g ? g.items.some(totalsOn) : totalsOn(id); }
var ALL = '__all';
// Items is a field of its own in a multi-item view: it sits in Rows or Columns like a dimension, at any nesting level.
var IT = '__items';
// An explicit "not applicable". It counts as defined, unlike a blank, and reads as no value in formulas.
var NA = '__na';
function dimName(d) { return d === IT ? 'Items' : d; }
function strip(co) { var o = Object.assign({}, co); delete o[IT]; return o; }
// The members a row or column shows: those that agree with the coordinates above them, then the chip's filter.
function memberList(d, partial) {
  var pk = layoutFor(current).pick[d];
  return memberListAll(d, partial).filter(function (c) { return !pk || pk.indexOf(c) >= 0; });
}
// A chip's filter, checked once a cell's coordinates are known: in Rows or Columns it drops members, in Not shown it narrows what sits under them.
function pickOk(co, skip) {
  var L = layouts[current];
  if (!L) return true;
  return Object.keys(L.pick).every(function (d) {
    var pk = L.pick[d];
    if (d === skip || d === IT) return true;
    if (d in co) return pk.indexOf(co[d]) >= 0;
    if (isDt(d)) {
      var sp = dtSplit(d);
      if (!ITEMS[sp.item].dims.every(function (x) { return x in co; })) return true;
      var v = get(sp.item, pick(sp.item, co));
      return typeof v === 'string' && validDate(v) && pk.indexOf(dtPart(v, sp.part)) >= 0;
    }
    return true;
  });
}
function memberListAll(d, partial) {
  if (d === IT) { var g = groupOf(current); return g ? g.items.slice() : []; }
  if (isDt(d)) return dtMembers(d, partial).filter(function (c) { var p = Object.assign({}, strip(partial)); p[d] = c; return dtOk(p) && pickOk(p, d); });
  return membersFor(d, strip(partial)).filter(function (c) { var p = Object.assign({}, strip(partial)); p[d] = c; return dtOk(p) && pickOk(p, d); });
}
function memberLabel(d, c) { return d === IT ? c : isDt(d) ? dtLabel(d, c) : labelOf(d, c); }
function isDefined(item, v) { return v !== null && v !== undefined && !(ITEMS[item].zeroEmpty && v === 0); }
function rawValue(item, co) { return ITEMS[item].kind === 'entered' ? entered(item, co) : get(item, co); }
// Is any cell of the item under these coordinates defined? A value, or an explicit n/a. For a cash-flow cell, 0 means no payment, so it does not count.
function anyDefined(item, fixed) {
  var dims = ITEMS[item].dims, found = false;
  (function walk(i, co) {
    if (found) return;
    if (i === dims.length) { if (fits(Object.assign({}, fixed, co)) && isDefined(item, rawValue(item, co))) found = true; return; }
    var d = dims[i];
    (d in fixed ? [fixed[d]] : DIMS[d]).forEach(function (c) { var n = Object.assign({}, co); n[d] = c; walk(i + 1, n); });
  })(0, {});
  return found;
}
// A row or column member is defined when any cell under it is, across everything not pinned by its coordinates.
// Defined somewhere the other axis can show it. A date part on the other axis only shows dated cells,
// so a line with an amount but no payment date has nothing to show under the months.
function definedAcross(co, other) {
  var dts = other.filter(function (d) { return isDt(d) && !(d in co); });
  if (!dts.length) return definedAt(co);
  return (function tryDt(i, c) {
    if (i === dts.length) return definedAt(c);
    return memberList(dts[i], c).some(function (m) { var n = Object.assign({}, c); n[dts[i]] = m; return dtOk(strip(n)) && tryDt(i + 1, n); });
  })(0, co);
}
function definedAt(co) {
  var g = groupOf(current), items = co[IT] ? [co[IT]] : (g ? g.items : [current]), f = strip(co);
  return items.some(function (it) { return anyDefined(it, f); });
}

// ---- Date parts. Every date item has pseudo dimensions, like the pandas dt accessor: <item>.dt.year, .quarter, .month,
// .fiscal_year and .fiscal_quarter. They are read from the date's value, so they can be laid out like a dimension.
// month and quarter are of the year (1-12, 1-4), as in pandas: nest them under .dt.year for a timeline.
var DT_PARTS = ['year', 'quarter', 'month', 'fiscal_year', 'fiscal_quarter'];
var FY_START = 4;   // example setting: the fiscal year starts in April
var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function isDt(d) { return typeof d === 'string' && d.indexOf('.dt.') > 0; }
function dtSplit(d) { var i = d.indexOf('.dt.'); return { item: d.slice(0, i), part: d.slice(i + 4) }; }
function dtPart(iso, part) {
  var y = Number(iso.slice(0, 4)), m = Number(iso.slice(5, 7)), fy = m >= FY_START ? y : y - 1;
  if (part === 'year') return String(y);
  if (part === 'quarter') return String(Math.ceil(m / 3));
  if (part === 'month') return String(m);
  if (part === 'fiscal_year') return String(fy);
  if (part === 'fiscal_quarter') return String(Math.floor(((m - FY_START + 12) % 12) / 3) + 1);
  return null;
}
function dtLabel(d, c) {
  var part = dtSplit(d).part;
  if (part === 'month') return MON[Number(c) - 1];
  if (part === 'quarter' || part === 'fiscal_quarter') return 'Q' + c;
  if (part === 'fiscal_year') return 'FY' + String(c).slice(2) + '/' + String(Number(c) + 1).slice(2);
  return c;
}
// Every month from the item's earliest date to its latest, as YYYY-MM.
function dtMonths(item) {
  var vals = [];
  (function walk(i, co) {
    var dims = ITEMS[item].dims;
    if (i === dims.length) { var v = get(item, co); if (typeof v === 'string' && validDate(v)) vals.push(v.slice(0, 7)); return; }
    DIMS[dims[i]].forEach(function (c) { var n = Object.assign({}, co); n[dims[i]] = c; walk(i + 1, n); });
  })(0, {});
  if (!vals.length) return [];
  vals.sort();
  var out = [], y = Number(vals[0].slice(0, 4)), m = Number(vals[0].slice(5, 7)), end = vals[vals.length - 1];
  while (true) { var k = y + '-' + (m < 10 ? '0' : '') + m; out.push(k + '-01'); if (k >= end) break; m++; if (m > 12) { m = 1; y++; } }
  return out;
}
// Members of a date part: the values that occur over the item's range, in order, agreeing with any other part of the same date already chosen.
function dtMembers(d, partial) {
  var sp = dtSplit(d), others = Object.keys(partial || {}).filter(function (k) { return k !== d && isDt(k) && dtSplit(k).item === sp.item; });
  var out = [];
  dtMonths(sp.item).forEach(function (mo) {
    if (!others.every(function (k) { return dtPart(mo, dtSplit(k).part) === partial[k]; })) return;
    var v = dtPart(mo, sp.part);
    if (out.indexOf(v) < 0) out.push(v);
  });
  return out.sort(function (a, b) { return Number(a) - Number(b); });
}
// Do the date parts in these coordinates agree with the dates? Checked once the date item's own dimensions are all known.
function dtOk(co) {
  return Object.keys(co).every(function (k) {
    if (!isDt(k)) return true;
    var sp = dtSplit(k), it = ITEMS[sp.item];
    if (!it.dims.every(function (d) { return d in co; })) return true;
    var v = get(sp.item, pick(sp.item, co));
    return typeof v === 'string' && validDate(v) && dtPart(v, sp.part) === co[k];
  });
}
function allMembers(d) { return isDt(d) ? dtMembers(d, {}) : DIMS[d]; }
// Every member a chip can have, before any filter: what its filter list offers and counts against.
function fullMembers(d) { if (d === IT) { var g = groupOf(current); return g ? g.items.slice() : []; } return allMembers(d); }

// ---- State. Layout is a view setting kept in the page, per view; it is not part of the model. ----
var layouts = {};        // view -> { columns, rows, unused: [dim], pick: {dim: [members shown]}, dense: {rows, columns} }
var pickOpen = null;     // the chip whose filter list is open
var current = 'group:lines';
var sel = null;          // { item, coords }
var menu = null;         // the dimension whose move menu is open
var layoutOpen = false;
var dragDim = null;
var WELLS = ['rows', 'columns', 'unused'];   // where a dimension can sit; any of them can be filtered in place
var PANEL_WELLS = WELLS;
var WELL_NAME = { filters: 'Filters', columns: 'Columns', rows: 'Rows', unused: 'Not shown' };
var WELL_HINT = { filters: 'one member each', columns: 'across the top', rows: 'first is outermost', unused: 'available to lay out' };
var layoutNotice = '';   // why the last move was refused
var FILTER_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 3h12l-4.5 5.5V13l-3-1.5V8.5z"/></svg>';

function layoutFor(id) {
  if (!layouts[id]) {
    // A view starts from its saved layout. A model table starts with its dimensions down and its items across.
    // A table or view with one item has no Items field: every cell is that item.
    var g = groupOf(id), d = dimsOf(id), pre = g.preset || {}, cols;
    if (pre.columns) cols = pre.columns.slice();
    else if (g.items.length > 1) cols = [IT];
    else cols = [];
    var rows = (pre.rows || DEFAULT_ROWS[id] || d).filter(function (x) { return cols.indexOf(x) < 0; });
    var dz = pre.dense || {};
    layouts[id] = { columns: cols, rows: rows, pick: {}, detail: null, dense: { rows: !!dz.rows, columns: !!dz.columns },
      unused: availDims(id).filter(function (x) { return cols.indexOf(x) < 0 && rows.indexOf(x) < 0; }) };
  }
  return layouts[id];
}
function wellOf(L, d) { return WELLS.filter(function (w) { return L[w].indexOf(d) >= 0; })[0]; }
function defaultSlice(id, d) { return allMembers(d)[0]; }
// Items can go in Rows or Columns, never Filters or Not shown: every cell has to know which item it shows.
function itemsOnlyAxis(d, well) { return d === IT && well === 'unused'; }

// Move a dimension to a well at an index. Every available dimension sits in exactly one well.
function moveDim(id, d, well, index) {
  layoutNotice = '';
  if (itemsOnlyAxis(d, well)) { layoutNotice = 'Items stay in Rows or Columns: every cell needs to know which item it shows. To show fewer items, filter the Items chip.'; return; }
  var L = layoutFor(id), from = wellOf(L, d), fromIdx = L[from].indexOf(d);
  L[from].splice(fromIdx, 1);
  if (index === undefined || index > L[well].length) index = L[well].length;
  if (from === well && fromIdx < index) index--;
  L[well].splice(index, 0, d);
}
function nudge(id, d, delta) {
  var L = layoutFor(id), w = wellOf(L, d), i = L[w].indexOf(d), j = i + delta;
  if (j < 0 || j >= L[w].length) return;
  L[w][i] = L[w][j]; L[w][j] = d;
}

// ---- Values ----
function isLeafCell(item, co) { return ITEMS[item].dims.every(function (d) { return d in co; }); }
function failing(item, coords) {
  return CHECKS.some(function (c) { return c.cells().some(function (x) { return x.item === item && sameCoords(x.coords, coords); }); });
}
function failingCells(c) { return c.cells(); }
function fmt(item, v, raw) {
  if (raw === NA) return '<span class="none na" title="marked not applicable">n/a</span>';
  if (v === null) return '<span class="none">–</span>';
  if (typeof v === 'string') return esc(v);
  if (v === 0 && item === 'payment') return '<span class="none">·</span>';
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
}
function show(v) { if (v === NA) return 'n/a'; return typeof v === 'string' ? esc(v) : v.toLocaleString('en-US', { maximumFractionDigits: 2 }); }
function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
function isPending(item, coords) { return PENDING.has(key(item, coords)); }
function sameCoords(a, b) { return Object.keys(a).every(function (k) { return a[k] === b[k]; }) && Object.keys(b).length === Object.keys(a).length; }
function coordText(item, co) { return ITEMS[item].dims.map(function (d) { return labelOf(d, co[d]); }).join(' · '); }

// ---- Render ----
function render() {
  renderHeader(); renderChecks(); renderItems(); renderBar(); renderLayout(); renderGrid(); renderDetail();
}

function renderHeader() {
  var n = CHANGES;
  document.getElementById('pending').innerHTML = n
    ? '<span class="pend-pill">' + n + ' pending change' + (n === 1 ? '' : 's') + '</span>' +
      '<span class="commit"><input id="commit-msg" type="text" placeholder="What changed and why" aria-label="Commit message" value="' + esc(commitMsg) + '">' +
      '<button class="link" type="button" data-commit="1">Commit</button><button class="link" type="button" data-discard="1">Discard</button></span>'
    : '<span class="sub">No pending changes</span>';
  document.getElementById('subtitle').innerHTML = 'at <span class="num">' + esc(HEAD) + '</span> · amounts in thousands · example data';
}

function renderChecks() {
  var el = document.getElementById('checks');
  var any = false;
  var html = '<h2>Checks</h2>';
  CHECKS.forEach(function (c) {
    var f = failingCells(c);
    if (f.length) any = true;
    html += '<span><span class="chk-name">' + c.name + '</span> <span class="sub">(' + c.text + ')</span> ' +
      (f.length ? '<span class="chk-bad">' + f.length + ' cell' + (f.length === 1 ? '' : 's') + ' failing</span> <button class="link" type="button" data-show-fail="' + c.name + '">Show</button>' : '<span class="chk-ok">passes</span>') + '</span>';
  });
  el.className = 'checks' + (any ? ' fail' : '');
  el.innerHTML = html;
}

function viewIdOf(sec) { return 'group:' + sec.group; }
function sourcesOf(sec) {
  var t = [];
  sec.items.forEach(function (i) { var m = GROUPS[viewOf(i).slice(6)]; if (m && t.indexOf(m.title) < 0) t.push(m.title); });
  return t.join(', ');
}
function renderItems() {
  [['model', 'Model', 'The data: items on dimensions'], ['view', 'Views', 'Saved layouts over model items']].forEach(function (k) {
    var html = '<div class="sec">' + k[1] + '</div><div class="sechint">' + k[2] + '</div>';
    SECTIONS.filter(function (sec) { return sec.kind === k[0]; }).forEach(function (sec) {
      var id = viewIdOf(sec), dims = (sec.levels ? sec.rows : ITEMS[sec.items[0]].dims).join(' · ') + (sec.levels ? ' · and above' : '');
      var bad = CHECKS.some(function (c) { return c.cells().some(function (x) { return sec.items.indexOf(x.item) >= 0; }); });
      var ds = sec.kind === 'view'
        ? sec.items.length + (sec.items.length === 1 ? ' item' : ' items') + ' from ' + sourcesOf(sec)
        : sec.items.length + (sec.items.length === 1 ? ' item' : ' items') + ' · ' + dims;
      html += '<button class="item" type="button" data-item="' + id + '" aria-current="' + (current === id) + '">' +
        '<span class="nm">' + sec.title + (bad ? '<span class="dot-bad" title="a check fails"></span>' : '') + '</span>' +
        '<span class="ds">' + esc(ds) + '</span></button>';
    });
    document.getElementById('items-' + k[0]).innerHTML = html;
  });
}

function filterOptions(d, cur) {
  return allMembers(d).map(function (c) { return '<option value="' + esc(c) + '"' + (cur === c ? ' selected' : '') + '>' + esc(memberLabel(d, c)) + '</option>'; }).join('');
}

// The slim bar: the Layout button, then one dropdown per filtered dimension.
function renderBar() {
  var L = layoutFor(current);
  var gk = groupOf(current);
  var html = '<button class="btn" type="button" data-layout="1" aria-expanded="' + layoutOpen + '">Layout</button>';
  html += '<span class="kindtag">' + (gk.kind === 'view' ? 'View · no data of its own, edits go to the model' : 'Model table') + '</span>';
  html += '<span class="fgroup">';
  var fd = Object.keys(L.pick);
  if (fd.length) html += '<span class="fsum">' + FILTER_ICON + ' Filtered: ' + fd.map(function (d) { return esc(dimName(d)) + ' (' + L.pick[d].length + ' of ' + fullMembers(d).length + ')'; }).join(', ') + '</span>';
  var dn = ['rows', 'columns'].filter(function (w) { return L.dense[w]; });
  if (dn.length) html += '<span class="densenote">Defined ' + dn.join(' and ') + ' only</span>';
  if (levelsOn(gk, L) && L.rows.length) {
    var det = detailOf(gk, L), lo = hasModelLevel(gk) ? 0 : 1;
    html += '<span class="seg" role="group" aria-label="Level of detail"><span class="seg-l">Detail</span>';
    for (var k = lo; k <= L.rows.length; k++) html += '<button type="button" class="seg-b" data-detail="' + k + '" aria-pressed="' + (k === det) + '">' + esc(k === 0 ? 'Model' : dimName(L.rows[k - 1])) + '</button>';
    html += '</span>';
  }
  html += '</span><span class="sp"></span><button class="link" type="button" data-expandall="1">Expand all</button><button class="link" type="button" data-collapseall="1">Collapse all</button>';
  document.getElementById('controls').innerHTML = html;
}

// The filter on a chip: a funnel that shows how many members are kept, and a checklist of the members.
function filterBtn(L, d) {
  var pk = L.pick[d], n = fullMembers(d).length;
  return '<button class="fb' + (pk ? ' on' : '') + '" type="button" data-pick="' + d + '" aria-haspopup="true" aria-expanded="' + (pickOpen === d) + '" aria-label="Filter ' + esc(dimName(d)) + '" title="Filter ' + esc(dimName(d)) + '">' +
    FILTER_ICON + (pk ? pk.length + '/' + n : '') + '</button>';
}
function pickHtml(L, d, w) {
  var all = fullMembers(d), pk = L.pick[d];
  var hint = w === 'unused' && d !== IT
    ? (isDt(d) ? 'Keeps the cells whose date falls in the checked members.' : 'Check one member to show its cells. With more than one, rows and columns keep only cells under them.')
    : 'Shows only the checked members.';
  return '<div class="pickpop" role="dialog" aria-label="Filter ' + esc(dimName(d)) + '"><div class="mh"><span>Filter ' + esc(dimName(d)) + '</span><span><button class="link" type="button" data-pickall="' + d + '">All</button> · <button class="link" type="button" data-picknone="' + d + '">None</button></span></div>' +
    '<div class="list">' + all.map(function (c) {
      return '<label><input type="checkbox" data-pickval="' + esc(c) + '" data-pickdim="' + d + '"' + (!pk || pk.indexOf(c) >= 0 ? ' checked' : '') + '>' + esc(memberLabel(d, c)) + '</label>';
    }).join('') + '</div><div class="ph">' + hint + '</div></div>';
}

// The Layout panel: four wells. Drag a chip, or open its menu to move it without dragging.
function renderLayout() {
  var el = document.getElementById('layout');
  el.hidden = !layoutOpen;
  if (!layoutOpen) { el.innerHTML = ''; return; }
  var L = layoutFor(current), g = groupOf(current), own = dimsOf(current);
  var html = '<div class="wells">';
  PANEL_WELLS.forEach(function (w) {
    var dense = w === 'rows' || w === 'columns'
      ? '<label class="dense" title="Hide ' + w + ' with no value under them. A cell marked n/a counts as defined."><input type="checkbox" data-dense="' + w + '"' + (L.dense[w] ? ' checked' : '') + '>Defined only</label>' : '';
    html += '<div class="well' + (w === 'unused' ? ' spare' : '') + '" data-well="' + w + '"><div class="well-h"><h2>' + WELL_NAME[w] + '</h2><span class="hint">' + WELL_HINT[w] + '</span>' + dense + '</div><div class="well-body">';
    if (!L[w].length) html += '<div class="well-empty">Drop a dimension here</div>';
    L[w].forEach(function (d, i) {
      var groups = false;
      html += '<div class="wchip' + (d === IT ? ' items-chip' : '') + (dragDim === d ? ' dragging' : '') + '" draggable="true" data-wdim="' + d + '" tabindex="0">' +
        '<span class="grip" aria-hidden="true">⋮⋮</span><span class="nm">' + esc(dimName(d)).replace(/\./g, '.<wbr>') + (groups ? '<span class="lvl">groups</span>' : '') + (d === IT ? '<span class="lvl">' + g.items.length + '</span>' : '') + '</span>' +
        (L[w].length > 1 && w !== 'unused' ? '<span class="ord" title="position in ' + WELL_NAME[w] + '">' + (i + 1) + '</span>' : '') +
        filterBtn(L, d) +
        '<button class="mb" type="button" data-wmenu="' + d + '" aria-haspopup="true" aria-expanded="' + (menu === d) + '" aria-label="Move ' + dimName(d) + '">⋯</button>';
      if (pickOpen === d) html += pickHtml(L, d, w);
      if (menu === d) {
        html += '<div class="wmenu" role="menu"><div class="mh">Move ' + dimName(d) + '</div>' +
          WELLS.filter(function (x) { return x !== w; }).map(function (x) {
            var no = itemsOnlyAxis(d, x);
            return '<button type="button" role="menuitem" data-act="to:' + x + '" data-wdim="' + d + '"' + (no ? ' disabled title="Items stay in Rows or Columns"' : '') + '>To ' + WELL_NAME[x] + '</button>';
          }).join('') + '<hr>' +
          '<button type="button" role="menuitem" data-act="up" data-wdim="' + d + '"' + (i === 0 || w === 'unused' ? ' disabled' : '') + '>Move up in ' + WELL_NAME[w] + '</button>' +
          '<button type="button" role="menuitem" data-act="down" data-wdim="' + d + '"' + (i === L[w].length - 1 || w === 'unused' ? ' disabled' : '') + '>Move down in ' + WELL_NAME[w] + '</button></div>';
      }
      html += '</div>';
    });
    html += '</div></div>';
  });
  html += '</div>' + (layoutNotice ? '<div class="notice" role="status">' + esc(layoutNotice) + '</div>' : '') + '<div class="lp-foot"><span>' + (g.items.length > 1 ? 'Items is a field like any other: put a dimension above it in Columns to get a band per member over the items, or below it for a band per item. ' : '') + 'Pointers such as engagement_contract are items like the others, so they show under Items, not here. Date items add their parts (<span class="num">.dt.year</span>, <span class="num">.dt.month</span> …) to Not shown.</span></div>';
  el.innerHTML = html;
}

function filterFixed(L) {
  var f = {};
  // A dimension in Not shown filtered to one member pins every cell to it, so its cells show instead of a blank.
  L.unused.forEach(function (d) { if (!isDt(d) && d !== IT && L.pick[d] && L.pick[d].length === 1) f[d] = L.pick[d][0]; });
  return f;
}

// Column tree: every column dimension adds a header band. A collapsed group is one column that totals its children.
// Items can sit at any level. Defined only drops members with nothing defined under them.
function buildCols(L, fixed) {
  var C = L.columns, leaves = [], bands = C.map(function () { return []; });
  function walk(depth, partial, path) {
    var count = 0, d = C[depth];
    memberList(d, Object.assign({}, fixed, partial)).forEach(function (c) {
      var p = Object.assign({}, partial); p[d] = c;
      if (L.dense.columns && !definedAcross(Object.assign({}, fixed, p), L.rows)) return;
      var pk = path + '/' + c, k = current + '|c|' + pk, last = depth === C.length - 1;
      if (last) {
        leaves.push({ coords: p });
        bands[depth].push({ dim: d, label: c, colspan: 1, rowspan: C.length - depth, key: last ? null : k, open: false });
        count += 1;
      } else {
        var i = bands[depth].push({ dim: d, label: c, colspan: 0, rowspan: 1, key: k, open: true }) - 1;
        var n = walk(depth + 1, p, pk);
        if (!n) { bands[depth].splice(i, 1); return; }
        bands[depth][i].colspan = n; count += n;
      }
    });
    return count;
  }
  if (C.length) walk(0, {}, '');
  return { leaves: leaves, bands: bands };
}

// Rows in order. Each row dimension nests under the one before it, keeping only the members that agree through the pointers.
function eachRow(R, base, isCollapsed, emit, dense) {
  function gkey(p) { return current + '|r|' + JSON.stringify(p); }
  function level(idx, partial, depth) {
    var d = R[idx], last = idx === R.length - 1;
    memberList(d, Object.assign({}, base, partial)).forEach(function (c) {
      var p = Object.assign({}, partial); p[d] = c;
      if (dense && !definedAcross(Object.assign({}, base, p), layoutFor(current).columns)) return;
      var calc = d === IT && ITEMS[c].kind === 'calc' ? ' <span class="kind calc">calc</span>' : '';
      if (last) { emit({ group: false, partial: p, label: memberLabel(d, c), extra: calc, depth: depth, tag: d === IT ? 'item' : d }); return; }
      var k = gkey(p);
      emit({ group: true, partial: p, label: memberLabel(d, c), extra: calc, depth: depth, key: k, tag: d === IT ? 'item' : d });
      if (!isCollapsed(k)) level(idx + 1, p, depth + 1);
    });
  }
  if (R.length) level(0, {}, 0);
}

// ---- The grid: Tabulator draws and edits it. The layout engine above decides which rows and columns there are;
// this turns them into Tabulator's tree of rows and nested column groups. ----
var table = null, gridSig = '', gridVersion = 0, treeState = {}, selCell = null;
var lastCols = null;
var adding = null;   // a row or column being added in the grid: {row: {dim, ctx}} or {col: dims}

function bumpGrid() { gridVersion++; }
function layoutSig() {
  var L = layoutFor(current);
  return JSON.stringify([current, gridVersion, adding, HEAD, L.detail, L.columns, L.rows, L.unused, L.pick, L.dense]);
}

// The meta of a leaf cell in a row: which item and coordinates it shows, or null for a blank or a group row.
function metaOf(cell) { return cell.getData()['m' + cell.getField().slice(1)] || null; }

function cellEditor(cell, onRendered, success, cancel) {
  var m = metaOf(cell), it = ITEMS[m.item], raw = entered(m.item, m.coords), el, done = false;
  function finish(v) { if (done) return; done = true; success(v); }
  function abort() { if (done) return; done = true; cancel(); }
  if (it.dtype === 'ref') {  // a pointer: pick from the labels of what it points at
    el = document.createElement('select');
    DIMS[it.ref].forEach(function (c) {
      var o = document.createElement('option');
      o.value = c; o.textContent = labelOf(it.ref, c); if (c === raw) o.selected = true;
      el.appendChild(o);
    });
    el.addEventListener('change', function () { finish(el.value); });
    el.addEventListener('blur', abort);
  } else {
    el = document.createElement('input');
    el.type = 'text'; el.placeholder = it.hint;
    el.value = raw === null || raw === undefined ? '' : (raw === NA ? 'n/a' : String(raw));
    el.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); finish(el.value); } });
    el.addEventListener('blur', function () { finish(el.value); });
  }
  el.addEventListener('keydown', function (e) { if (e.key === 'Escape') { e.preventDefault(); abort(); } });
  el.style.cssText = 'width:100%;box-sizing:border-box;font:inherit;';
  onRendered(function () { el.focus(); if (el.select) el.select(); });
  return el;
}

function cellFormatter(cell) {
  var m = metaOf(cell), el = cell.getElement();
  ['t-entered', 't-calc', 't-fail', 't-pend', 't-sel', 't-blank', 't-rep'].forEach(function (c) { el.classList.remove(c); });
  if (!m) { if (!cell.getData()._group || cell.getData()._lv) el.classList.add('t-blank'); return ''; }
  var it = ITEMS[m.item];
  el.classList.add(it.kind === 'calc' ? 't-calc' : 't-entered');
  if (m.rep) el.classList.add('t-rep');
  if (failing(m.item, m.coords)) el.classList.add('t-fail');
  if (isPending(m.item, m.coords)) el.classList.add('t-pend');
  if (sel && sel.item === m.item && sameCoords(sel.coords, m.coords)) { el.classList.add('t-sel'); selCell = cell; }
  return fmt(m.item, get(m.item, m.coords), it.kind === 'entered' ? entered(m.item, m.coords) : null);
}

// ---- Levels: one grid whose rows are the model, each project, each unit type under it. A quantity sits on the row
// of its own level; where it can't apply the cell is shaded; one that varies by the deepest dimension only is repeated. ----
function levelsOn(g, L) { return !!g.levels && L.columns.length === 1 && L.columns[0] === IT; }
function hasModelLevel(g) { return g.items.some(function (i) { return ITEMS[i].dims.length === 0; }); }
function detailOf(g, L) {
  var lo = hasModelLevel(g) ? 0 : 1, n = L.detail == null ? L.rows.length : L.detail;
  return Math.max(lo, Math.min(L.rows.length, n));
}
function shownDims(g, L) { return L.rows.slice(0, detailOf(g, L)); }
// Is the item's column shown at this level of detail? Only if every dimension it varies by is a row level shown.
function itemShown(g, L, item) { var R = shownDims(g, L); return ITEMS[item].dims.every(function (d) { return R.indexOf(d) >= 0; }); }
// What a row shows for an item: its own value, a repeat of it, or nothing.
function levelCell(item, partial) {
  var D = ITEMS[item].dims, S = Object.keys(partial), last = S[S.length - 1];
  if (!D.every(function (d) { return d in partial; })) return null;
  if (D.length === S.length) return { item: item, coords: pick(item, partial) };
  if (last && D.indexOf(last) >= 0) return { item: item, coords: pick(item, partial), rep: true };
  return null;
}

function treeRows(g, L, fixed, leaves) {
  var roots = [], stack = [], n = 0, lv = levelsOn(g, L), R = lv ? shownDims(g, L) : L.rows;
  function cellsOf(partial, isGroup) {
    var o = { _group: isGroup, _lv: lv };
    leaves.forEach(function (lf, i) {
      var co = Object.assign({}, fixed, partial, lf.coords), item = co[IT] || (g.items.length === 1 ? g.items[0] : null);
      co = strip(co);
      if (lv) o['m' + i] = item ? levelCell(item, partial) : null;
      else o['m' + i] = (!isGroup && item && isLeafCell(item, co) && fits(co)) ? { item: item, coords: pick(item, co) } : null;
      o['c' + i] = '';
    });
    return o;
  }
  var modelRow = null;
  if (lv && hasModelLevel(g)) {
    modelRow = Object.assign({ id: ++n, label: 'Model', tag: 'model', _k: 'model', _partial: {}, _dim: null, _childDim: R[0] || null }, cellsOf({}, true));
    roots.push(modelRow); stack.push({ depth: -1, row: modelRow });
  }
  if (!R.length && !modelRow) roots.push(Object.assign({ id: ++n, label: g.title, tag: '' }, cellsOf({}, false)));
  eachRow(R, fixed, function () { return false; }, function (r) {
    var dim = lastKey(r.partial), di = R.indexOf(dim);
    var row = Object.assign({ id: ++n, label: r.label, extra: r.extra, tag: r.tag, _k: r.key, _partial: r.partial, _dim: dim,
      _childDim: r.group ? R[di + 1] : null }, cellsOf(r.partial, r.group));
    while (stack.length && stack[stack.length - 1].depth >= r.depth) stack.pop();
    var parent = stack.length ? stack[stack.length - 1].row : null;
    if (parent) (parent._children = parent._children || []).push(row); else roots.push(row);
    if (r.group) stack.push({ depth: r.depth, row: row });
  }, L.dense.rows);
  // Each group ends with a ghost row to add to it, and so does the table: "+ Add line to E03".
  function ghosts(nodes) {
    nodes.forEach(function (node) {
      if (!node._group || !node._childDim || !isRealDim(node._childDim)) return;
      node._children = node._children || [];
      ghosts(node._children);
      node._children.push({ id: ++n, label: '', _ghost: true, _dim: node._childDim, _ctx: node._partial });
    });
  }
  if (roots.length) {
    ghosts(roots);
    if (R.length && isRealDim(R[0]) && !modelRow) roots.push({ id: ++n, label: '', _ghost: true, _dim: R[0], _ctx: {} });
  }
  return roots;
}

function lastKey(o) { var k = Object.keys(o); return k[k.length - 1]; }
// A dimension with labels of its own, as opposed to Items or the part of a date.
function isRealDim(d) { return d !== IT && !isDt(d) && !!DIMS[d]; }

// Column bands become nested column groups: one level per column dimension.
function columnDefs(g, L, cols) {
  var corner = L.rows.length ? L.rows.map(dimName).join(' › ') : g.title;
  var defs = [{
    title: '', field: 'grip', width: 34, frozen: true, headerSort: false, resizable: false, hozAlign: 'center', editable: false,
    formatter: function (cell) { return cell.getData()._ghost ? '' : '<button type="button" class="grip-btn" aria-label="Row actions" title="Row actions">⋮</button>'; },
    cellClick: function (e, cell) {
      var btn = e.target.closest('.grip-btn');
      if (!btn) return;
      var r = btn.getBoundingClientRect();   // the same menu as a right-click
      cell.getRow().getElement().dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left, clientY: r.bottom }));
    }
  }, {
    title: corner, field: 'label', frozen: true, width: 300, headerSort: false, editable: false,
    formatter: function (cell) {
      var d = cell.getData();
      if (d._ghost && isAddingRow(d)) return addRowHtml(d);
      if (d._ghost) return '<span class="ghost-link">+ Add ' + esc(d._dim) + (Object.keys(d._ctx).length ? ' to ' + esc(d._ctx[lastKey(d._ctx)]) : '') + '</span>';
      return esc(d.label) + (d.extra || '') + (d.tag ? '<span class="lvl">' + esc(d.tag) + '</span>' : '');
    }
  }];
  function leafDef(i, title, what) {
    return { title: title, field: 'c' + i, hozAlign: 'right', headerSort: false, headerHozAlign: 'right', minWidth: 90,
      formatter: cellFormatter, formatterParams: {}, editor: cellEditor, _what: what,
      headerMenu: function (e, column) { return columnMenu(column.getDefinition()._what, g); },
      editable: function (cell) { var m = metaOf(cell); return !!m && !m.rep && ITEMS[m.item].kind === 'entered'; } };
  }
  var top = [];
  cols.leaves.forEach(function (lf, i) {
    var path = L.columns.map(function (d) { return lf.coords[d]; }), level = top;
    if (!path.length) { level.push(leafDef(i, g.items.length === 1 ? g.items[0] : 'Value', g.items.length === 1 ? { item: g.items[0] } : null)); return; }
    path.forEach(function (c, depth) {
      var d = L.columns[depth], title = memberLabel(d, c) + (d === IT && ITEMS[c].kind === 'calc' ? ' <span class="kind calc">calc</span>' : '') +
        (d === IT && levelsOn(g, L) ? '<small class="per">' + (ITEMS[c].dims.length ? 'per ' + ITEMS[c].dims.join(', ') : 'per model') + '</small>' : '');
      if (depth === path.length - 1) {
        var what = d === IT ? { item: c } : (g.items.length === 1 && !isRealDim(d) ? { item: g.items[0] } : (isRealDim(d) ? { dim: d, member: c } : null));
        level.push(leafDef(i, title, what)); return;
      }
      var group = level.filter(function (x) { return x._k === c && x.columns; })[0];
      if (!group) { group = { title: title, _k: c, columns: [], headerHozAlign: 'center' }; level.push(group); }
      level = group.columns;
    });
  });
  // a model table ends with a "+": a new column is a new item on its dimensions
  var plus = g.kind === 'model' ? [{ title: '+', field: 'plus', width: adding && adding.col ? (g.levels ? 470 : 330) : 48, headerSort: false, resizable: false, hozAlign: 'center', editable: false,
    headerTooltip: 'Add a column: a new item on ' + g.dims.join(' · '), formatter: function () { return ''; },
    titleFormatter: function () { return adding && adding.col ? addColEl(g.dims, g.levels) : '+'; },
    headerClick: function (e) { if (!adding && !e.target.closest('.add-inline')) startAddCol(g.dims, g.levels); } }] : [];
  return defs.concat(top, plus);
}

function eachTreeRow(rows, fn) { rows.forEach(function (r) { fn(r); eachTreeRow(r.getTreeChildren ? r.getTreeChildren() : [], fn); }); }

function markSelection() {
  if (!table) return;
  selCell = null;
  eachTreeRow(table.getRows(), function (row) {
    row.getCells().forEach(function (cell) {
      var m = metaOf(cell), el = cell.getElement();
      if (!el || cell.getField().charAt(0) !== 'c') return;
      var on = !!m && sel && sel.item === m.item && sameCoords(sel.coords, m.coords);
      el.classList.toggle('t-sel', !!on);
      if (on) selCell = cell;
    });
  });
}

function renderGrid() {
  var sig = layoutSig();
  if (table && sig === gridSig) { markSelection(); return; }   // nothing the grid shows has changed
  gridSig = sig;
  var host = document.getElementById('grid');
  if (table) { table.destroy(); table = null; }
  host.innerHTML = '';
  var g = groupOf(current), L = layoutFor(current), fixed = filterFixed(L);
  var cols = L.columns.length ? buildCols(L, fixed) : { leaves: [{ coords: {} }], bands: [] };
  if (levelsOn(g, L)) cols.leaves = cols.leaves.filter(function (lf) { return itemShown(g, L, lf.coords[IT]); });
  var rows = treeRows(g, L, fixed, cols.leaves);
  if (L.columns.length && !cols.leaves.length) { host.innerHTML = '<div class="empty-grid">No defined columns.</div>'; return; }
  if (L.rows.length && !rows.length && !levelsOn(g, L)) { host.innerHTML = '<div class="empty-grid">No defined rows.</div>'; return; }
  lastCols = cols;
  selCell = null;
  table = new Tabulator(host, {
    data: rows, columns: columnDefs(g, L, cols), layout: 'fitData', maxHeight: '72vh', index: 'id',
    dataTree: true, dataTreeStartExpanded: function (row) { return treeState[current + '|' + row.getData()._k] !== false; },
    dataTreeChildIndent: 16, editTriggerEvent: 'dblclick', movableColumns: false,
    rowContextMenu: function (e, row) { return row.getData()._ghost ? false : rowMenu(row.getData()); },
    rowFormatter: function (row) { var d = row.getData(); row.getElement().classList.toggle('t-group', !!d._group); row.getElement().classList.toggle('t-ghost', !!d._ghost); }
  });
  table.on('cellClick', function (e, cell) {
    var d = cell.getRow().getData();
    if (d._ghost) { if (!isAddingRow(d)) startAddRow(d._dim, d._ctx); return; }
    var m = metaOf(cell);
    if (!m) return;
    sel = { item: m.item, coords: m.coords };
    markSelection(); selCell = cell; renderDetail();
  });
  table.on('cellEdited', function (cell) { var m = metaOf(cell); if (m) saveEdit(m, cell.getValue()); });
  table.on('dataTreeRowExpanded', function (row) { treeState[current + '|' + row.getData()._k] = true; });
  table.on('dataTreeRowCollapsed', function (row) { treeState[current + '|' + row.getData()._k] = false; });
}

function setTree(open) {
  if (!table) return;
  eachTreeRow(table.getRows(), function (row) { if (row.getTreeChildren().length) open ? row.treeExpand() : row.treeCollapse(); });
}

// ---- Changing the shape. A row is a member of a dimension, a column an item. In a model table that changes the model;
// in a view it only changes the layout. Everything here is uncommitted until you commit it, and Discard undoes it. ----
function rowMenu(d) {
  var dim = d._dim, code = d._partial[dim], items = [], parent = {};
  if (!dim) return false;   // the model row has nothing to delete or hide
  Object.keys(d._partial).forEach(function (k) { if (k !== dim) parent[k] = d._partial[k]; });
  if (d._childDim && isRealDim(d._childDim)) items.push({ label: 'Add ' + d._childDim + ' under ' + code + '…', action: function () { startAddRow(d._childDim, d._partial); } });
  if (isRealDim(dim)) items.push({ label: 'Add another ' + dim + (Object.keys(parent).length ? ' to ' + parent[lastKey(parent)] : '') + '…', action: function () { startAddRow(dim, parent); } });
  items.push({ separator: true });
  items.push({ label: 'Hide this row', action: function () { hideMember(dim, code); } });
  if (isRealDim(dim)) items.push({ label: 'Delete ' + dim + ' ' + code + '…', action: function () { openDeleteMember(dim, code); } });
  return items;
}

function columnMenu(what, g) {
  if (!what) return false;
  var L = layoutFor(current), items = [];
  if (what.item) {
    var it = ITEMS[what.item], model = g.kind === 'model';
    items.push({ label: 'Edit description and unit…', action: function () { openEditItem(what.item); } });
    if (it.kind === 'calc') items.push({ label: 'Edit formula…', action: function () { openEditFormula(what.item); } });
    items.push({ separator: true });
    if (model) items.push({ label: 'Add a column after this…', action: function () { startAddCol(g.dims, g.levels); } });
    items.push({ label: 'Hide this column', disabled: !(L.columns.indexOf(IT) >= 0 || L.rows.indexOf(IT) >= 0), action: function () { hideMember(IT, what.item); } });
    items.push({ separator: true });
    items.push({ label: model ? 'Delete ' + what.item + '…' : 'Delete ' + what.item + ' (in its model table)', disabled: !model, action: function () { openDeleteItem(what.item); } });
  } else {
    items.push({ label: 'Add another ' + what.dim + '…', action: function () { startAddRow(what.dim, {}); } });
    items.push({ label: 'Hide this column', action: function () { hideMember(what.dim, what.member); } });
    items.push({ separator: true });
    items.push({ label: 'Delete ' + what.dim + ' ' + what.member + '…', action: function () { openDeleteMember(what.dim, what.member); } });
  }
  return items;
}

// Show everything but one member: the chip's filter.
function hideMember(dim, code) {
  var L = layoutFor(current), all = fullMembers(dim), keep = (L.pick[dim] ? L.pick[dim] : all).filter(function (c) { return c !== code; });
  L.pick[dim] = keep;
  bumpGrid(); render();
}

// The next code in a dimension's style: L11 -> L12.
function nextCode(dim) {
  var labels = DIMS[dim] || [], last = labels[labels.length - 1] || '', m = /^(.*?)(\d+)$/.exec(last);
  if (!m) return '';
  var n = String(Number(m[2]) + 1);
  while (n.length < m[2].length) n = '0' + n;
  return m[1] + n;
}
// The pointers a new member gets from where it is added: a line under E03 points at E03.
function pointersFor(dim, ctx) {
  var out = [];
  Object.keys(REFS).forEach(function (i) { if (REFS[i].from === dim && ctx[REFS[i].to] !== undefined) out.push({ item: i, value: ctx[REFS[i].to] }); });
  return out;
}

// -- a small dialog --
function openDialog(title, html, mount) {
  var back = document.getElementById('dlg');
  document.getElementById('dlg-title').textContent = title;
  document.getElementById('dlg-body').innerHTML = html;
  back.hidden = false;
  if (mount) mount(document.getElementById('dlg-body'));
  var first = document.querySelector('#dlg-body input:not([type=radio]), #dlg-body textarea, #dlg-body select');
  if (first) { first.focus(); if (first.select) first.select(); }
}
function closeDialog() { var b = document.getElementById('dlg'); b.hidden = true; document.getElementById('dlg-body').innerHTML = ''; }
function dlgError(msg) { var e = document.getElementById('dlg-err'); if (e) { e.textContent = msg; e.hidden = !msg; } }
function field(label, input, hint) { return '<label class="fld"><span>' + label + '</span>' + input + (hint ? '<small>' + hint + '</small>' : '') + '</label>'; }
function done(p, message) {
  return p.then(refreshAll).then(function () { closeDialog(); notify(message + ' It is not committed yet.', true); }).catch(function (e) { dlgError(e.message); });
}

function openDeleteMember(dim, code) {
  openDialog('Delete ' + dim + ' ' + code, '<p class="hint">Checking what depends on it…</p>');
  api('GET', '/api/impact/member?dim=' + encodeURIComponent(dim) + '&code=' + encodeURIComponent(code)).then(function (r) {
    var blocked = r.pointed_by.length > 0, who = r.pointed_by.slice(0, 6).map(function (p) { return p.item + ' of ' + labelOf(Object.keys(p.coords)[0], Object.values(p.coords)[0]); }).join(', ') + (r.pointed_by.length > 6 ? ' and ' + (r.pointed_by.length - 6) + ' more' : '');
    document.getElementById('dlg-body').innerHTML =
      '<p>' + (blocked ? 'Can\'t delete ' + esc(dim) + ' ' + esc(code) + ': it is pointed at by ' + esc(who) + '. Move or delete those first.'
                       : 'Delete ' + esc(labelOf(dim, code)) + '? It removes ' + r.values + ' value' + (r.values === 1 ? '' : 's') + '. Nothing points at it.') + '</p>' +
      '<div class="notice" id="dlg-err" hidden></div><div class="dlg-actions"><button type="button" class="btn" data-dlg-cancel>Cancel</button>' +
      '<button type="button" class="btn danger" id="dlg-go"' + (blocked ? ' disabled' : '') + '>Delete ' + esc(code) + '</button></div>';
    document.getElementById('dlg-go').addEventListener('click', function () {
      done(api('DELETE', '/api/members?dim=' + encodeURIComponent(dim) + '&code=' + encodeURIComponent(code)), 'Deleted ' + dim + ' ' + code + '.');
    });
  }).catch(function (e) { document.getElementById('dlg-body').innerHTML = '<div class="notice">' + esc(e.message) + '</div>'; });
}

// -- adding in the grid: the ghost row turns into inputs in place, the "+" header into a column to name --
function sameCtx(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function isAddingRow(d) { return !!(adding && adding.row && adding.row.dim === d._dim && sameCtx(adding.row.ctx, d._ctx)); }
function addRowHtml(d) {
  return '<span class="add-inline"><input class="add-code" type="text" value="' + esc(nextCode(d._dim)) + '" aria-label="Code" autocomplete="off">' +
    '<input class="add-name" type="text" placeholder="Name" aria-label="Name" autocomplete="off">' +
    '<span class="add-hint">Enter to add · Esc to cancel</span></span>';
}
// focus after the click that started it has finished, or Tabulator takes it back
function startAddRow(dim, ctx) { adding = { row: { dim: dim, ctx: ctx } }; bumpGrid(); renderGrid(); setTimeout(focusAdding, 0); }
function startAddCol(dims, levels) { adding = { col: dims, levels: !!levels }; bumpGrid(); renderGrid(); setTimeout(focusAdding, 0); }
function stopAdding() { adding = null; bumpGrid(); renderGrid(); }
function focusAdding() {
  var el = document.querySelector('#grid .add-inline input');
  if (!el) return;
  if (adding && adding.row) { var r = el.closest('.tabulator-row'); if (r) r.scrollIntoView({ block: 'nearest' }); }
  el.focus(); el.select();
}
function perText(sel, all) {
  if (!sel.length) return 'One value for the whole model. It shows on the Model row.';
  var rest = all.filter(function (d) { return sel.indexOf(d) < 0; });
  return 'One value for each ' + sel.join(' and ') + (rest.length ? ', the same in every ' + rest.join(' and ') + '.' : '.');
}
function addColEl(dims, levels) {
  var types = [['float', 'Number'], ['int', 'Whole number'], ['str', 'Text'], ['date', 'Date']];
  var opts = types.map(function (t) { return '<option value="' + t[0] + '">' + t[1] + '</option>'; }).join('') +
    Object.keys(DIMS).map(function (d) { return '<option value="ref:' + esc(d) + '">Pointer to ' + esc(d) + '</option>'; }).join('');
  var el = document.createElement('span');
  el.className = 'add-inline';
  el.innerHTML = '<input class="add-name" type="text" placeholder="New column" aria-label="Column name" autocomplete="off"><select class="add-type" aria-label="Type">' + opts + '</select>' +
    (levels ? '<span class="add-per">One for each: ' + dims.map(function (d) { return '<label><input type="checkbox" class="add-dim" value="' + esc(d) + '" checked> ' + esc(d) + '</label>'; }).join(' ') +
      '</span><span class="add-hint add-per-text">' + esc(perText(dims, dims)) + '</span>' : '') +
    '<span class="add-hint">Enter to add · Esc to cancel</span>';
  return el;
}

// keys and clicks inside the inline inputs belong to them, not to the grid
document.addEventListener('keydown', function (e) {
  var box = e.target.closest && e.target.closest('.add-inline');
  if (!box || !adding) return;
  e.stopPropagation();
  if (e.key === 'Escape') { e.preventDefault(); stopAdding(); return; }
  if (e.key !== 'Enter') return;
  e.preventDefault();
  var body, url, msg;
  if (adding.row) {
    var dim = adding.row.dim, ctx = adding.row.ctx, code = box.querySelector('.add-code').value.trim(), values = {};
    pointersFor(dim, ctx).forEach(function (p) { values[p.item] = p.value; });
    body = { dim: dim, code: code, description: box.querySelector('.add-name').value, values: values };
    url = '/api/members'; msg = 'Added ' + dim + ' ' + code + '.';
  } else {
    var title = box.querySelector('.add-name').value.trim(), type = box.querySelector('.add-type').value;
    var boxes = box.querySelectorAll('.add-dim'), dimsOn = boxes.length ? adding.col.filter(function (d) { return box.querySelector('.add-dim[value="' + d + '"]').checked; }) : adding.col;
    body = { name: title.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, ''), dims: dimsOn, dtype: type.indexOf('ref:') === 0 ? 'ref' : type,
      ref: type.indexOf('ref:') === 0 ? type.slice(4) : null, unit: '', description: title, formula: '' };
    url = '/api/items'; msg = 'Added column ' + body.name + '.';
  }
  api('POST', url, body).then(function () { adding = null; return refreshAll(); })
    .then(function () { notify(msg + ' It is not committed yet.', true); })
    .catch(function (err) { notify(err.message, false); });
}, true);
document.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('.add-inline')) e.stopPropagation(); }, true);
document.addEventListener('change', function (e) {
  var box = e.target.closest && e.target.closest('.add-inline');
  if (!box || !e.target.classList.contains('add-dim') || !adding || !adding.col) return;
  var on = adding.col.filter(function (d) { return box.querySelector('.add-dim[value="' + d + '"]').checked; });
  box.querySelector('.add-per-text').textContent = perText(on, adding.col);
});

function openEditItem(name) {
  var it = ITEMS[name];
  openDialog('Edit ' + name, '<form id="dlg-form" class="dlg-form">' + field('Description', '<input id="f-desc" type="text" value="' + esc(it.description) + '" autocomplete="off">') +
    field('Unit', '<input id="f-unit" type="text" value="' + esc(it.unit) + '" autocomplete="off">') +
    '<div class="notice" id="dlg-err" hidden></div><div class="dlg-actions"><button type="button" class="btn" data-dlg-cancel>Cancel</button><button type="submit" class="btn primary">Save</button></div></form>',
    function (root) {
      root.querySelector('#dlg-form').addEventListener('submit', function (e) {
        e.preventDefault();
        done(api('PATCH', '/api/items/' + encodeURIComponent(name), { description: root.querySelector('#f-desc').value, unit: root.querySelector('#f-unit').value }), 'Saved ' + name + '.');
      });
    });
}

function openEditFormula(name) {
  var it = ITEMS[name];
  openDialog('Formula of ' + name, '<form id="dlg-form" class="dlg-form">' + field('Formula', '<textarea id="f-formula" rows="8" spellcheck="false">' + esc(it.formula || '') + '</textarea>',
    'Clear it to make the column entered. The model checks the formula before it changes anything.') +
    '<div class="notice" id="dlg-err" hidden></div><div class="dlg-actions"><button type="button" class="btn" data-dlg-cancel>Cancel</button><button type="submit" class="btn primary">Save formula</button></div></form>',
    function (root) {
      root.querySelector('#dlg-form').addEventListener('submit', function (e) {
        e.preventDefault();
        done(api('PATCH', '/api/items/' + encodeURIComponent(name), { formula: root.querySelector('#f-formula').value }), 'Changed the formula of ' + name + '.');
      });
    });
}

function openDeleteItem(name) {
  openDialog('Delete ' + name, '<p class="hint">Checking what reads it…</p>');
  api('GET', '/api/impact/item?name=' + encodeURIComponent(name)).then(function (r) {
    var blocked = r.read_by.length > 0;
    document.getElementById('dlg-body').innerHTML =
      '<p>' + (blocked ? 'Can\'t delete ' + esc(name) + ': it is read by ' + esc(r.read_by.join(', ')) + '. Change those first.'
                       : 'Delete the column ' + esc(name) + '? It removes ' + r.values + ' value' + (r.values === 1 ? '' : 's') + '. Nothing reads it.') + '</p>' +
      '<div class="notice" id="dlg-err" hidden></div><div class="dlg-actions"><button type="button" class="btn" data-dlg-cancel>Cancel</button>' +
      '<button type="button" class="btn danger" id="dlg-go"' + (blocked ? ' disabled' : '') + '>Delete ' + esc(name) + '</button></div>';
    document.getElementById('dlg-go').addEventListener('click', function () { done(api('DELETE', '/api/items/' + encodeURIComponent(name)), 'Deleted column ' + name + '.'); });
  }).catch(function (e) { document.getElementById('dlg-body').innerHTML = '<div class="notice">' + esc(e.message) + '</div>'; });
}

document.addEventListener('click', function (e) {
  if (e.target.closest('[data-dlg-cancel]') || e.target.id === 'dlg') closeDialog();
});
document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !document.getElementById('dlg').hidden) closeDialog(); });

var TRAIL = ['project', 'contract', 'engagement'];
function trailHtml(item, co) {
  var dims = ITEMS[item].dims, out = [];
  var anc = {};
  dims.forEach(function (d) { Object.assign(anc, ancestors(d, co[d])); });
  TRAIL.forEach(function (d) { if (anc[d] && dims.indexOf(d) < 0) out.push(d + ' ' + labelOf(d, anc[d])); });
  return out.length ? '<div class="sub">Under ' + esc(out.join(' › ')) + '</div>' : '';
}
function coordChips(co, item) {
  return '<div class="coords">' + ITEMS[item].dims.map(function (d) { return '<span class="coord">' + d + ' = ' + esc(co[d]) + '</span>'; }).join('') + '</div>';
}
function renderDetail() {
  var el = document.getElementById('detail');
  if (!sel) { el.innerHTML = '<p class="empty">Select a cell to see its source, or what it rests on.</p>'; return; }
  var it = ITEMS[sel.item], k = key(sel.item, sel.coords), d = DETAIL[k];
  var v = get(sel.item, sel.coords), isNA = entered(sel.item, sel.coords) === NA;
  var html = '<div><span class="num" style="font-weight:600">' + sel.item + '</span> <span class="kind ' + it.kind + '">' + (it.kind === 'calc' ? 'calculated' : 'entered') + '</span>' + (it.ref ? ' <span class="kind">pointer to ' + it.ref + '</span>' : '') + '</div>' +
    (it.description ? '<div class="sub">' + esc(it.description) + '</div>' : '') +
    trailHtml(sel.item, sel.coords) + coordChips(sel.coords, sel.item) +
    '<div class="big num">' + (isNA ? '<span class="na" style="color:var(--muted)">n/a</span> <span class="sub" style="font-size:13px;font-family:var(--font-body)">marked not applicable</span>' : v === null ? '<span class="none" style="color:var(--muted)">no value</span>' : show(v)) + (it.unit && v !== null ? ' <span class="sub" style="font-size:13px;font-family:var(--font-body)">' + esc(it.unit) + '</span>' : '') + '</div>';
  var bad = [];
  CHECKS.forEach(function (c) { c.cells().forEach(function (x) { if (x.item === sel.item && sameCoords(x.coords, sel.coords)) bad.push([c, x]); }); });
  bad.forEach(function (b) { html += '<div class="warn">Fails <span class="chk-name">' + b[0].name + '</span>: ' + esc(b[0].why(b[1])) + '</div>'; });
  var errCell = (CELLS[sel.item] || {})[cellKey(sel.item, sel.coords)];
  if (errCell && errCell.err) html += '<div class="warn">' + esc(errCell.err) + '</div>';
  if (!d) {
    el.innerHTML = html + '<p class="hint">Loading…</p>';
    api('GET', '/api/cell?item=' + encodeURIComponent(sel.item) + '&coords=' + encodeURIComponent(JSON.stringify(sel.coords)))
      .then(function (r) { DETAIL[k] = r; })
      .catch(function (e) { DETAIL[k] = { error: e.message, history: [], reads: [] }; })
      .then(function () { if (sel && key(sel.item, sel.coords) === k) renderDetail(); });
    return;
  }
  if (d.error) { el.innerHTML = html + '<div class="warn">' + esc(d.error) + '</div>'; return; }
  if (it.kind === 'entered') {
    html += entryHtml(d);
    if (isPending(sel.item, sel.coords)) html += '<div><span class="badge-p">edited, not committed</span></div>';
    html += '<p class="hint">Double-click the cell, or press Enter, to edit. Enter ' + esc(it.hint) + (it.required ? '. A pointer can be changed but not cleared' : '. Clear it to leave it blank, or type n/a to mark it not applicable') + '.</p>';
    if (it.ref) html += '<p class="hint">Changing this pointer moves the row under its new ' + it.ref + ' in every grouped view, and everything that reads through it recalculates.</p>';
  } else {
    html += '<h2>Formula</h2><div class="formula"><code class="fline">' + esc(d.formula) + '</code></div><h2>Rests on</h2><div class="rest">';
    var missing = [];
    d.reads.forEach(function (r) {
      if (r.v === null && !r.nil) missing.push(r.item);
      html += '<button type="button" data-goto="' + r.item + '" data-co="' + esc(JSON.stringify(r.coords)) + '"><span><span class="it">' + r.item + '</span><br><span class="cd">' + esc(coordText(r.item, r.coords)) + '</span></span><span class="num">' + (r.nil ? 'n/a' : r.v === null ? '–' : show(r.v)) + '</span></button>';
    });
    html += '</div>';
    if (v === null && missing.length) html += '<div class="warn">No value, because ' + Array.from(new Set(missing)).join(', ') + ' has none here.</div>';
    html += '<p class="hint">Calculated cells are computed when read, never stored.</p>';
  }
  el.innerHTML = html;
}

// An entered value's source, who entered it and what it replaced: the model's own log.
function entryHtml(d) {
  var rows = [], last = d.history[0];
  if (d.source) {
    rows.push(['Source', esc(d.source.document)]);
    rows.push(['Section', esc(d.source.section)]);
    rows.push(['Link', '<code>' + esc(d.source.url) + '</code>']);
  }
  if (last) {
    rows.push(['Entered by', esc(last.author) + ' · ' + esc(last.at.replace('T', ' ').slice(0, 16))]);
    rows.push(['Committed', '<span class="num">' + esc(last.commit) + '</span> · ' + esc(last.message)]);
    rows.push(['Approved by', d.approved_by ? esc(d.approved_by) : 'nobody yet']);
    rows.push(['At commit', '<span class="num">' + (last.removed ? 'no value' : last.nil ? 'n/a' : show(last.value)) + '</span>']);
  } else {
    rows.push(['At commit', '<span class="num">not committed yet</span>']);
  }
  var html = '<dl class="kv">' + rows.map(function (r) { return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>';
  var earlier = d.history.slice(1);
  if (earlier.length) {
    html += '<h2>Earlier values</h2><div class="rest">' + earlier.map(function (x) {
      return '<div class="hrow"><span class="num">' + (x.removed ? 'removed' : x.nil ? 'n/a' : esc(x.value)) + '</span><span class="cd">' + esc(x.source ? x.source.document : x.message) + ' · ' + esc(x.commit) + ' · ' + esc(x.at.slice(0, 10)) + '</span></div>';
    }).join('') + '</div>';
  }
  return html;
}

// ---- Events ----
function saveEdit(m, raw) {
  var it = ITEMS[m.item], txt = String(raw).trim(), body = { item: m.item, coords: m.coords };
  function again(msg) { if (msg) notify(msg); bumpGrid(); render(); }
  if (txt === '' || /^n\/?a$/i.test(txt)) {
    if (it.required) { again('A pointer can be changed but not cleared.'); return; }
    if (txt === '') body.value = null; else body.nil = true;
  } else if (it.dtype === 'int' || it.dtype === 'float') {
    var n = Number(txt.replace(/,/g, ''));
    if (isNaN(n)) { again(m.item + ' takes ' + it.hint + '.'); return; }
    body.value = n;
  } else body.value = txt;
  api('PUT', '/api/value', body).then(reload).then(function () { bumpGrid(); render(); }).catch(function (err) { again(err.message); });
}

function commitChanges() {
  var input = document.getElementById('commit-msg');
  var msg = input ? input.value : commitMsg;
  api('POST', '/api/commit', { message: msg })
    .then(function (c) { commitMsg = ''; return refreshAll().then(function () { notify('Committed ' + c.id + ': ' + c.message, true); }); })
    .catch(function (err) { notify(err.message); });
}

function discardChanges() {
  api('POST', '/api/discard').then(refreshAll).catch(function (err) { notify(err.message); });
}

document.addEventListener('click', function (e) {
  var t = e.target;
  var closed = false;
  if (menu && !t.closest('.wmenu') && !t.closest('[data-wmenu]')) { menu = null; closed = true; }
  if (pickOpen && !t.closest('.pickpop') && !t.closest('[data-pick]')) { pickOpen = null; closed = true; }
  var b = t.closest('button');
  if (b) {
    var ds = b.dataset;
    if (ds.item) { current = ds.item; menu = null; var gg = groupOf(current); if (sel && !(gg ? gg.items.indexOf(sel.item) >= 0 : sel.item === current)) sel = null; render(); return; }
    if (ds.discard) { discardChanges(); return; }
    if (ds.commit) { commitChanges(); return; }
    if (ds.layout) { layoutOpen = !layoutOpen; menu = null; layoutNotice = ''; render(); return; }
    if (ds.wmenu) { menu = menu === ds.wmenu ? null : ds.wmenu; pickOpen = null; render(); return; }
    if (ds.pick) { pickOpen = pickOpen === ds.pick ? null : ds.pick; menu = null; render(); return; }
    if (ds.pickall) { delete layoutFor(current).pick[ds.pickall]; render(); return; }
    if (ds.picknone) { layoutFor(current).pick[ds.picknone] = []; render(); return; }
    if (ds.act) {
      if (b.disabled) return;
      if (ds.act === 'up') nudge(current, ds.wdim, -1);
      else if (ds.act === 'down') nudge(current, ds.wdim, 1);
      else moveDim(current, ds.wdim, ds.act.slice(3));
      menu = null; render(); return;
    }
    if (ds.detail !== undefined) { layoutFor(current).detail = +ds.detail; bumpGrid(); render(); return; }
    if (ds.expandall) { setTree(true); return; }
    if (ds.collapseall) { setTree(false); return; }
    if (ds.goto) { var cg = groupOf(current); if (!(cg && cg.items.indexOf(ds.goto) >= 0)) current = viewOf(ds.goto); sel = { item: ds.goto, coords: JSON.parse(ds.co) }; render(); return; }
    if (ds.showFail) {
      var c = CHECKS.filter(function (x) { return x.name === ds.showFail; })[0];
      var f = failingCells(c)[0];
      var fg = groupOf(current); if (!(fg && fg.items.indexOf(f.item) >= 0)) current = viewOf(f.item);
      sel = { item: f.item, coords: f.coords };
      var LL = layoutFor(current);
      Object.keys(f.coords).forEach(function (d) { if (LL.pick[d] && LL.pick[d].indexOf(f.coords[d]) < 0) delete LL.pick[d]; });
      Object.keys(treeState).forEach(function (k) { if (k.indexOf(current + '|') === 0) delete treeState[k]; });
      bumpGrid(); render();
      if (selCell) selCell.getElement().scrollIntoView({ block: 'nearest', inline: 'nearest' });
      return;
    }
  }
  if (closed) render();
});
document.addEventListener('change', function (e) {
  var pc = e.target.closest('input[data-pickdim]');
  if (pc) {
    var PL = layoutFor(current), pdim = pc.dataset.pickdim, allm = fullMembers(pdim);
    var cur = PL.pick[pdim] ? PL.pick[pdim].slice() : allm.slice();
    var v = pc.dataset.pickval, ix = cur.indexOf(v);
    if (pc.checked && ix < 0) cur.push(v); if (!pc.checked && ix >= 0) cur.splice(ix, 1);
    cur = allm.filter(function (c) { return cur.indexOf(c) >= 0; });
    if (cur.length === allm.length) delete PL.pick[pdim]; else PL.pick[pdim] = cur;
    render(); return;
  }
  var dc = e.target.closest('input[data-dense]');
  if (dc) { layoutFor(current).dense[dc.dataset.dense] = dc.checked; render(); return; }
});
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && (menu || pickOpen)) { menu = null; pickOpen = null; render(); return; }
  var tag = e.target.tagName;
  if (e.key === 'Enter' && selCell && tag !== 'INPUT' && tag !== 'SELECT' && tag !== 'BUTTON' && !e.target.closest('.wchip')) selCell.edit();
});
document.addEventListener('input', function (e) { if (e.target.id === 'commit-msg') commitMsg = e.target.value; });
// Drag and drop between and within the wells. The chip's menu does the same without dragging.
document.addEventListener('dragstart', function (e) {
  var chip = e.target.closest && e.target.closest('.wchip');
  if (!chip) return;
  dragDim = chip.dataset.wdim; menu = null;
  e.dataTransfer.effectAllowed = 'move';
  try { e.dataTransfer.setData('text/plain', dragDim); } catch (err) { /* some viewers refuse */ }
  chip.classList.add('dragging');
});
document.addEventListener('dragover', function (e) {
  var w = e.target.closest && e.target.closest('.well');
  if (!w || !dragDim) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  document.querySelectorAll('.well.over').forEach(function (x) { if (x !== w) x.classList.remove('over'); });
  w.classList.add('over');
});
document.addEventListener('drop', function (e) {
  var w = e.target.closest && e.target.closest('.well');
  if (!w || !dragDim) return;
  e.preventDefault();
  var chips = Array.prototype.slice.call(w.querySelectorAll('.wchip')), idx = chips.length;
  for (var i = 0; i < chips.length; i++) {
    var r = chips[i].getBoundingClientRect();
    if (e.clientY < r.top + r.height / 2) { idx = i; break; }
  }
  var d = dragDim; dragDim = null;
  moveDim(current, d, w.dataset.well, idx);
  render();
});
document.addEventListener('dragend', function () { dragDim = null; render(); });

function boot() {
  return api('GET', '/api/model').then(function (def) { load(def); return reload(); }).then(function () {
    document.getElementById('title').textContent = DEF.title;
    document.getElementById('branch').textContent = DEF.branch;
    document.title = DEF.title + ' · tracemodel studio';
    current = GROUPS.lines ? 'group:lines' : 'group:' + SECTIONS[0].group;
    sel = ITEMS.payment_amount ? { item: 'payment_amount', coords: { line: 'L05' } } : null;
    render();
  }).catch(function (e) {
    document.getElementById('grid').innerHTML = '<tbody><tr><td class="empty">Could not load the model: ' + esc(e.message) + '</td></tr></tbody>';
  });
}
boot();
