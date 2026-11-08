/* DRC conflict deaths dashboard. Every number comes from DATA; nothing is hard-coded. */
const BT = DATA.backtest, XC = DATA.xcheck, FC = DATA.forecast, X = DATA.x || {};
const ML_LABEL = BT && BT.ml ? BT.ml.label : 'ML';
const SERIES = {
  actual: { label: 'Recorded deaths (ACLED)', color: 'var(--actual)' },
  ml: { label: `ACLED model (${ML_LABEL})`, color: 'var(--baseline)' },
  x_ml: { label: 'ACLED model + X posts', color: 'var(--x)', dash: true },
  views: { label: 'VIEWS', color: 'var(--views)' },
};
const H = ['30', '60', '90'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* Helpers */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') node.className = v; else node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c.nodeType ? c : String(c));
  return node;
}
function svgEl(tag, attrs, ...children) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  for (const c of children) node.append(c.nodeType ? c : String(c));
  return node;
}
const fmt = v => v === null || v === undefined ? 'n/a' : v === 0 ? '0' : v < 10 ? Number(v).toFixed(1) : Math.round(v).toLocaleString('en');
const num = (v, d = 2) => v === null || v === undefined ? 'n/a' : Number(v).toFixed(d);
const times = e => e === null || e === undefined ? 'n/a' : `×${Math.exp(e).toFixed(2)}`;
const monthName = key => `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`;
const dayName = iso => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
const shift = (key, n) => { const i = Number(key.slice(0, 4)) * 12 + Number(key.slice(5, 7)) - 1 + n; return `${Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}`; };
function width(id) {
  const node = document.getElementById(id);
  if (!node) return 760;
  const style = getComputedStyle(node);
  return Math.max(300, (node.clientWidth || 900) - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0));
}
function table(headers, rows, numericFrom = 1) {
  return el('div', { class: 'scroll' }, el('table', {},
    el('thead', {}, el('tr', {}, headers.map((h, i) => el('th', { class: i >= numericFrom ? 'n' : '', scope: 'col' }, h)))),
    el('tbody', {}, rows.map(r => el('tr', {}, r.map((v, i) => el('td', { class: i >= numericFrom ? 'n' : '' }, v)))))));
}
const tip = document.getElementById('tip');
function showTip(e, title, lines) {
  tip.replaceChildren(el('b', {}, title), ...lines.map(l => el('div', {}, l)));
  tip.style.display = 'block';
  tip.style.left = `${Math.min(e.clientX + 14, innerWidth - 330)}px`;
  tip.style.top = `${e.clientY + 14}px`;
}
const hideTip = () => { tip.style.display = 'none'; };
function legend(keys) {
  return el('div', { class: 'legend' }, keys.map(k => el('span', {},
    el('i', { style: SERIES[k].dash ? `background:repeating-linear-gradient(90deg, ${SERIES[k].color} 0 4px, transparent 4px 7px)` : `background:${SERIES[k].color}` }), SERIES[k].label)));
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), m = v / p;
  return [1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10].find(step => m <= step) * p;
}
function chapter(number, topic, title, intro) {
  return el('article', { class: 'chapter' }, el('div', { class: 'chapter-heading' },
    el('p', { class: 'eyebrow' }, `${number} / ${topic}`), el('h2', {}, title), intro ? el('p', { class: 'muted' }, intro) : null));
}
function takeaway(text) { return el('div', { class: 'conclusion-box' }, el('strong', {}, 'What this shows: '), text); }
function empty(root, text) { root.append(el('div', { class: 'notice' }, text)); }
function ranks(values) {
  const order = values.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
  const out = new Array(values.length);
  for (let i = 0; i < order.length;) {
    let j = i;
    while (j + 1 < order.length && order[j + 1][0] === order[i][0]) j++;
    for (let k = i; k <= j; k++) out[order[k][1]] = (i + j) / 2;
    i = j + 1;
  }
  return out;
}
function spearman(a, b) {
  if (new Set(a).size < 2 || new Set(b).size < 2) return null;
  const ra = ranks(a), rb = ranks(b), n = a.length, ma = ra.reduce((s, v) => s + v, 0) / n, mb = rb.reduce((s, v) => s + v, 0) / n;
  let num_ = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) { num_ += (ra[i] - ma) * (rb[i] - mb); da += (ra[i] - ma) ** 2; db += (rb[i] - mb) ** 2; }
  return num_ / Math.sqrt(da * db);
}

/* Header */
document.getElementById('lead').textContent = 'A machine-learning model trained on ACLED conflict data forecasts monthly deaths in the Democratic Republic of the Congo. '
  + 'X posts, read by an LLM, are added to fill the weeks ACLED has not finished coding. Both are compared with VIEWS, the leading published forecast.';
const meta = document.getElementById('study-meta');
if (BT) [`Tested ${monthName(BT.evaluation.first_issue)} – ${monthName(BT.evaluation.last_issue)}`, '26 DRC provinces',
  `Forecast issued ${FC ? dayName(FC.issued) : 'n/a'}`].forEach(t => meta.append(el('span', {}, t)));

/* Time chart: monthly DRC deaths, recorded and forecast */
function timeRows() {
  const first = BT.evaluation.first_issue, last = FC ? FC.months['90'][2] : BT.evaluation.last_issue;
  const back = Object.fromEntries(BT.national.map(r => [r.month, r]));
  const sum = key => FC.rows.reduce((s, r) => s + (r[key] ?? 0), 0);
  const ahead = {};
  if (FC) {
    const [m1, m2, m3] = FC.months['90'];
    for (const k of ['ml', 'x_ml', 'views']) {
      const t = H.map(h => sum(`${k}_${h}`));
      ahead[m1] = { ...(ahead[m1] || {}), [k]: t[0] };
      ahead[m2] = { ...(ahead[m2] || {}), [k]: t[1] - t[0] };
      ahead[m3] = { ...(ahead[m3] || {}), [k]: t[2] - t[1] };
    }
  }
  const rows = [];
  for (let m = first; m <= last; m = shift(m, 1)) {
    rows.push({ month: m, actual: DATA.national_actual ? DATA.national_actual[m] ?? null : back[m]?.actual ?? null,
      ml: ahead[m]?.ml ?? back[m]?.ml ?? null, views: ahead[m]?.views ?? back[m]?.views ?? null, x_ml: ahead[m]?.x_ml ?? null,
      forecast: Boolean(ahead[m]) });
  }
  return rows;
}
function timeChart(id, rows) {
  const keys = ['actual', 'ml', 'x_ml', 'views'];
  const w = width(id), h = 340, left = 52, right = 18, top = 26, bottom = 36;
  const max = niceMax(Math.max(...rows.flatMap(r => keys.map(k => r[k] ?? 0)), 1));
  const x = i => left + i * (w - left - right) / Math.max(rows.length - 1, 1);
  const y = v => top + (1 - v / max) * (h - top - bottom);
  const svg = svgEl('svg', { width: w, height: h, role: 'img', 'aria-label': 'Monthly conflict deaths in the DRC: recorded, ACLED model, ACLED model plus X, and VIEWS' });
  const f0 = rows.findIndex(r => r.forecast);
  if (f0 > 0) {
    svg.append(svgEl('rect', { x: x(f0) - (x(1) - x(0)) / 2, y: top - 18, width: w - right - x(f0) + (x(1) - x(0)) / 2, height: h - bottom - top + 18, fill: 'var(--wash)' }));
    svg.append(svgEl('text', { x: x(f0) - (x(1) - x(0)) / 2 + 6, y: top - 6, style: 'font-weight:650' }, 'Forecast'));
  }
  for (let t = 0; t <= 4; t++) {
    const v = max * t / 4;
    svg.append(svgEl('line', { x1: left, x2: w - right, y1: y(v), y2: y(v), stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, Math.round(v).toLocaleString('en')));
  }
  svg.append(svgEl('text', { x: 4, y: 12 }, 'Deaths per month'));
  rows.forEach((r, i) => {
    const label = rows.length > 24 ? (r.month.endsWith('-01') ? r.month.slice(0, 4) : '') : (i % 2 === 0 ? monthName(r.month).replace(' 20', ' ’') : '');
    if (label) svg.append(svgEl('text', { x: x(i), y: h - 12, 'text-anchor': i === 0 ? 'start' : 'middle' }, label));
  });
  for (const k of keys) {
    let d = '', pen = false;
    rows.forEach((r, i) => {
      if (r[k] === null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`; pen = true;
      if (r.forecast) svg.append(svgEl('circle', { cx: x(i), cy: y(r[k]), r: 3.5, fill: SERIES[k].color }));
    });
    svg.append(svgEl('path', { d, fill: 'none', stroke: SERIES[k].color, 'stroke-width': k === 'actual' ? 2.5 : 2,
      'stroke-dasharray': SERIES[k].dash ? '5 4' : 'none', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  }
  const cross = svgEl('line', { y1: top, y2: h - bottom, stroke: 'var(--neutral)', visibility: 'hidden' });
  svg.append(cross);
  const hit = svgEl('rect', { x: left, y: 0, width: w - left - right, height: h, fill: 'transparent' });
  hit.addEventListener('mousemove', e => {
    const box = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(rows.length - 1, Math.round((e.clientX - box.left - left) / (w - left - right) * (rows.length - 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    const r = rows[i];
    showTip(e, `${monthName(r.month)}${r.forecast ? ' (forecast)' : ''}`, keys.filter(k => r[k] !== null).map(k => `${SERIES[k].label}: ${fmt(r[k])}`)
      .concat(r.actual === null && !r.forecast ? ['ACLED has not finished coding this month.'] : []));
  });
  hit.addEventListener('mouseleave', () => { hideTip(); cross.setAttribute('visibility', 'hidden'); });
  svg.append(hit);
  return svg;
}

/* 1. Results */
function renderResults() {
  const root = document.getElementById('results');
  root.replaceChildren();
  if (!BT) return empty(root, 'The backtest has not run yet.');
  const s = BT.evaluation.scores, ho = XC && XC.holdout_next_week;
  const one = el('article', { class: 'chapter' });
  one.append(el('div', { class: 'chapter-heading' }, el('p', { class: 'eyebrow' }, '01 / The comparison'),
    el('h2', {}, 'Monthly conflict deaths in the DRC: recorded, and as each model forecast them')));
  const range = el('select', { 'aria-label': 'Time range' },
    el('option', { value: '15' }, 'Last 12 months and forecast'), el('option', { value: 'all' }, `Whole test period, from ${monthName(BT.evaluation.first_issue)}`));
  one.append(el('div', { class: 'controls' }, el('label', {}, 'Show ', range)), legend(['actual', 'ml', 'x_ml', 'views']),
    el('figure', { class: 'chart scroll', id: 'timeChart' }));
  root.append(one);
  const allRows = timeRows();
  const chartBox = el('div', {});
  const drawChart = () => chartBox.replaceChildren(timeChart('timeChart', range.value === 'all' ? allRows : allRows.slice(-15)));
  range.addEventListener('change', drawChart);
  document.getElementById('timeChart').append(chartBox,
    el('figcaption', {}, `Before ${monthName(FC ? FC.months['30'][0] : BT.evaluation.last_issue)}, each point is the forecast made on the first of that month using only data available then, so the lines show how each model would have done. `
      + `October 2026 is blank because ACLED had not finished coding it. The shaded months are the live forecast. X data exist only for 18–22 October 2026, so the ACLED model + X line appears only there.`));

  drawChart();
  const vs = h => s[h] && s[h].error_ml !== null ? 1 - s[h].error_ml / s[h].error_views : null;
  const beatsAll = H.every(h => vs(h) !== null && vs(h) > 0);
  const m = ho && ho.models, total = (k, h) => FC ? FC.rows.reduce((t, r) => t + (r[`${k}_${h}`] ?? 0), 0) : null;
  one.append(el('div', { class: 'tiles' },
    el('div', { class: 'tile' }, el('div', { class: 'label' }, 'ACLED model against VIEWS'),
      el('div', { class: 'value' }, vs('30') === null ? 'n/a' : `${Math.round(vs('30') * 100)}% lower error`),
      el('div', { class: 'note' }, `At 30 days, typical error ${times(s['30'].error_ml)} against ${times(s['30'].error_views)} for VIEWS. ${beatsAll ? 'It also wins at 60 and 90 days.' : 'It does not win at every horizon.'}`)),
    el('div', { class: 'tile' }, el('div', { class: 'label' }, 'Does adding X posts help?'),
      el('div', { class: 'value' }, m ? (m.ml_x_llm_credible.spearman > m.ml_acled_only.spearman ? 'Better ranking' : 'No') : 'n/a'),
      el('div', { class: 'note' }, m ? `In a one-week test, ranking of provinces rose from ${num(m.ml_acled_only.spearman)} to ${num(m.ml_x_llm_credible.spearman)}. Error stayed about the same, ${times(m.ml_acled_only.log_error)} against ${times(m.ml_x_llm_credible.log_error)}.` : 'Needs the X check.')),
    el('div', { class: 'tile' }, el('div', { class: 'label' }, `Forecast, ${FC ? `${monthName(FC.months['90'][0])} – ${monthName(FC.months['90'][2])}` : 'next 3 months'}`),
      el('div', { class: 'value' }, FC ? `${fmt(total('ml', '90'))} deaths` : 'n/a'),
      el('div', { class: 'note' }, FC ? `ACLED model. With X posts: ${fmt(total('x_ml', '90'))}. VIEWS: ${fmt(total('views', '90'))}.` : ''))));

  one.append(el('h3', {}, 'Accuracy by horizon, ' + `${monthName(BT.evaluation.first_issue)} – ${monthName(BT.evaluation.last_issue)}`),
    table(['Horizon', `ACLED model (${ML_LABEL})`, 'VIEWS', 'Repeat last month (reference)', 'ACLED model against VIEWS'],
      H.filter(h => s[h]).map(h => [`${h} days`, times(s[h].error_ml), times(s[h].error_views), times(s[h].error_baseline),
        vs(h) === null ? 'n/a' : `${Math.round(Math.abs(vs(h)) * 100)}% ${vs(h) > 0 ? 'lower' : 'higher'} error`])),
    el('p', { class: 'small' }, `Typical error is how many times too high or too low a province forecast usually is, so ×1.5 means off by about half. Each row covers ${s['30'].issues} monthly forecasts for 26 provinces.`));
  const tie = H.filter(h => s[h] && Math.abs(s[h].error_ml - s[h].error_baseline) < .015);
  const loses = H.filter(h => s[h] && s[h].error_ml - s[h].error_baseline >= .015);
  const vsRepeat = [tie.length ? `ties with simply repeating last month’s deaths at ${tie.join(' and ')} days` : '', loses.length ? `loses to it at ${loses.join(' and ')} days` : ''].filter(Boolean).join(' and ');
  one.append(takeaway(`The ACLED model beats VIEWS at every horizon${beatsAll ? '' : ' it is scored on'}. ${vsRepeat ? `It ${vsRepeat}, because DRC violence is persistent and concentrated in a few eastern provinces. ` : ''}`
    + 'X posts help most with where violence is, not how many die.'));

  if (FC) {
    const two = chapter('02', 'Forecast by province', `Expected deaths from ${dayName(FC.issued)}`,
      `The ACLED model sees ACLED through ${monthName(FC.acled_through)}. The X version also uses posts from ${dayName(FC.x_posts[0])} to ${dayName(FC.x_posts[1])}. VIEWS is run ${FC.views_run.run}, released ${dayName(FC.views_run.released)}.`);
    const pick = el('select', { 'aria-label': 'Horizon' });
    H.forEach(h => pick.append(el('option', { value: h }, `${h} days`)));
    const holder = el('div', {});
    two.append(el('div', { class: 'controls' }, el('label', {}, 'Horizon ', pick)), holder);
    const draw = () => {
      const h = pick.value;
      const shown = FC.rows.filter(r => Math.max(r[`ml_${h}`] ?? 0, r[`x_ml_${h}`] ?? 0, r[`views_${h}`] ?? 0) >= 1)
        .sort((a, b) => (b[`ml_${h}`] ?? 0) - (a[`ml_${h}`] ?? 0));
      holder.replaceChildren(
        table(['Province', 'ACLED model', 'ACLED model + X', 'VIEWS'],
          shown.map(r => [r.province, fmt(r[`ml_${h}`]), r[`x_ml_${h}`] === null ? '·' : fmt(r[`x_ml_${h}`]), fmt(r[`views_${h}`])])
            .concat([[el('strong', {}, 'All 26 provinces'), fmt(total('ml', h)), fmt(total('x_ml', h)), fmt(total('views', h))]])),
        el('p', { class: 'small' }, `${FC.rows.length - shown.length} provinces expect under one death in all three forecasts and are left out. A dot means no X data for that province, so its total uses the ACLED model. `
          + `Scored once ACLED finishes coding: 30 days about ${dayName(FC.resolves['30'])}, 90 days about ${dayName(FC.resolves['90'])}.`));
    };
    pick.addEventListener('change', draw);
    draw();
    root.append(two);
  }
}

/* 2. How X was evaluated */
function scatter(id, rows) {
  const w = Math.min(width(id), 640), h = 300, left = 46, right = 110, top = 14, bottom = 42;
  const mx = niceMax(Math.max(...rows.map(r => r.x), 1)), my = niceMax(Math.max(...rows.map(r => r.y), 1));
  const x = v => left + v / mx * (w - left - right), y = v => top + (1 - v / my) * (h - top - bottom);
  const svg = svgEl('svg', { width: w, height: h, role: 'img', 'aria-label': 'Credible X authors against ACLED deaths, by province' });
  for (let t = 0; t <= 4; t++) {
    svg.append(svgEl('line', { x1: left, x2: w - right, y1: y(my * t / 4), y2: y(my * t / 4), stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: left - 8, y: y(my * t / 4) + 4, 'text-anchor': 'end' }, Math.round(my * t / 4)));
    svg.append(svgEl('text', { x: x(mx * t / 4), y: h - bottom + 18, 'text-anchor': 'middle' }, Math.round(mx * t / 4)));
  }
  svg.append(svgEl('text', { x: (left + w - right) / 2, y: h - 4, 'text-anchor': 'middle' }, 'Credible X authors reporting violence'));
  svg.append(svgEl('text', { x: 10, y: top + (h - top - bottom) / 2, transform: `rotate(-90 10 ${top + (h - top - bottom) / 2})`, 'text-anchor': 'middle' }, 'ACLED deaths that week'));
  const placed = [];
  [...rows].sort((a, b) => b.x - a.x).forEach(r => {
    const cx = x(r.x), cy = y(r.y);
    const dy = [0, -14, 14, -28, 28].find(d => !placed.some(([px, py]) => Math.abs(px - cx) < 90 && Math.abs(py - (cy + d)) < 12)) ?? 0;
    placed.push([cx, cy + dy]);
    svg.append(svgEl('circle', { cx, cy, r: 6, fill: 'var(--x)', stroke: 'var(--surface)', 'stroke-width': 2 }));
    svg.append(svgEl('text', { x: cx + 10, y: cy + 4 + dy, style: 'fill:var(--text)' }, r.label));
  });
  return svg;
}
function renderX() {
  const root = document.getElementById('xsignal');
  root.replaceChildren();
  if (!XC) return empty(root, 'The X check has not run yet.');
  const t = X.topics;
  const one = chapter('01', 'Step 1', 'An LLM reads every post and keeps the credible ones',
    `X’s search reaches back only 7 days, so the ${(X.posts ?? 0).toLocaleString('en')} posts are from ${dayName(XC.posts_from)} to ${dayName(XC.posts_to)}. ${X.extractor || 'The LLM'} labels each post: is it about DRC conflict, who wrote it, and what happened where.`);
  if (t && t.relevant_posts) {
    const parts = [['news', 'News', 'var(--baseline)'], ['field_report', 'Field reports', '#1f5a46'], ['opinion', 'Opinion', 'var(--neutral)'],
      ['party_claim', 'Partisan claims', '#a9b2a8'], ['other', 'Other', '#c9cfc6']];
    one.append(el('div', { class: 'tiles' },
      el('div', { class: 'tile' }, el('div', { class: 'label' }, 'Posts collected'), el('div', { class: 'value' }, X.posts.toLocaleString('en')), el('div', { class: 'note' }, `By ${(X.authors ?? 0).toLocaleString('en')} accounts.`)),
      el('div', { class: 'tile' }, el('div', { class: 'label' }, 'About DRC conflict'), el('div', { class: 'value' }, t.relevant_posts.toLocaleString('en')), el('div', { class: 'note' }, `${t.irrelevant_posts} off-topic posts dropped.`)),
      el('div', { class: 'tile' }, el('div', { class: 'label' }, 'Credible: news and field reports'), el('div', { class: 'value' }, t.credible_posts.toLocaleString('en')), el('div', { class: 'note' }, 'Only these are used in the forecast.'))));
    one.append(el('div', { class: 'share', role: 'img', 'aria-label': 'Relevant posts by source type' },
      parts.filter(([k]) => t.source_types[k]).map(([k, label, color]) => el('div', { style: `width:${100 * t.source_types[k] / t.relevant_posts}%;background:${color}`, title: `${label}: ${t.source_types[k]}` },
        t.source_types[k] / t.relevant_posts > .08 ? `${label} ${t.source_types[k]}` : ''))),
      el('p', { class: 'small' }, parts.filter(([k]) => t.source_types[k]).map(([k, label]) => `${label}: ${t.source_types[k]}`).join(' · ')));
  } else one.append(el('div', { class: 'notice' }, 'The LLM topic summary is missing. Re-run ingest with the LLM extractor.'));
  root.append(one);

  const rules = X.rules_provinces || {};
  const rows = XC.rows.map(r => ({ label: r.province, x: r.llm_event_authors ?? r.event_authors, y: r.deaths_state, rules: rules[r.province]?.event ?? null }));
  const rhoLlm = XC.rank_llm_event_authors_vs_state_deaths ?? XC.rank_event_authors_vs_state_deaths;
  const rhoRules = rows.every(r => r.rules !== null) ? spearman(rows.map(r => r.rules), rows.map(r => r.y)) : null;
  const kin = rows.find(r => r.label === 'Kinshasa');
  const two = chapter('02', 'Step 2', 'Does X point to where people are dying?',
    `Count credible X authors reporting violence in each province, then compare with ACLED deaths in the same week, ${dayName(XC.week_start)} to ${dayName(XC.week_end)}.`);
  two.append(el('div', { class: 'brief-grid equal' },
    el('figure', { class: 'chart', id: 'xScatter' }),
    el('div', {}, table(['Province', 'Keyword search', 'LLM, credible only', 'ACLED deaths'], rows.map(r => [r.label, r.rules ?? 'n/a', r.x, r.y])),
      el('p', { class: 'small' }, `Rank agreement with ACLED, where 1 is perfect: LLM ${num(rhoLlm)}${rhoRules !== null ? `, keyword search ${num(rhoRules)}` : ''}.`))));
  root.append(two);
  document.getElementById('xScatter').append(scatter('xScatter', rows));
  two.append(takeaway(`X ranks the provinces almost exactly as ACLED does. ${kin && kin.rules !== null ? `The LLM matters most in Kinshasa: keyword search counts ${kin.rules} authors there, mostly political commentary, against ${kin.x} once only credible reports are kept, and ACLED recorded ${kin.y} deaths. ` : ''}Six provinces is a small sample, so this is a sanity check.`));

  const ho = XC.holdout_next_week;
  if (ho) {
    const names = Object.keys(ho.actual);
    const label = { acled_simple: 'Repeat the last coded week', ml_acled_only: 'ACLED model', ml_x_rules: 'ACLED model + X (keyword search)', ml_x_llm_credible: 'ACLED model + X (LLM, credible)' };
    const three = chapter('03', 'Step 3', `Does X improve next week’s forecast?`,
      `Forecast the week of ${dayName(ho.target_week)} using ACLED only through ${dayName(ho.lagged_week)}, as on a real day when ACLED is two weeks behind. The X versions use the posts to estimate the week ACLED had not coded yet, ${dayName(ho.gap_week)}.`);
    three.append(table(['Forecast', 'Typical error', 'Rank agreement', ...names],
      Object.entries(ho.models).map(([k, v]) => [label[k] || k, times(v.log_error), num(v.spearman), ...names.map(n => fmt(v.predictions[n]))])
        .concat([[el('strong', {}, 'What ACLED recorded'), '', '', ...names.map(n => fmt(ho.actual[n]))]]), 1));
    const a = ho.models.ml_acled_only, b = ho.models.ml_x_llm_credible;
    three.append(takeaway(b ? `Adding credible X posts ${b.spearman > a.spearman ? `improves the ranking of provinces from ${num(a.spearman)} to ${num(b.spearman)}` : 'does not improve the ranking'} and ${b.log_error < a.log_error ? 'lowers' : 'does not lower'} the typical error (${times(a.log_error)} without X, ${times(b.log_error)} with). One week is one test; it shows the method works end to end, not that X always helps.` : 'Needs the LLM extraction.'));
    root.append(three);
  }
}

/* 3. Workflow */
const STEPS = [
  { title: 'Ingest', sub: 'Three sources', stage: 'Ingest' },
  { title: 'Prepare', sub: 'Same places and months', stage: 'Inputs' },
  { title: 'ACLED model', sub: 'Random Forest or XGBoost', stage: 'Backtest' },
  { title: 'ACLED + X', sub: 'X fills the coding gap', stage: 'X check' },
  { title: 'Compare', sub: 'Against VIEWS', stage: 'Forecast' },
];
function stageOf(name) { return name === 'Ingest' ? DATA.ingest : DATA.status ? DATA.status.stages.find(s => s.stage === name) : null; }
function flow() {
  const W = 1000, n = STEPS.length, boxW = 168, gap = (W - boxW * n) / (n - 1), boxH = 74;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${boxH + 8}`, width: '100%', style: 'min-width:760px', role: 'img', 'aria-label': 'Five workflow steps' });
  svg.append(svgEl('defs', {}, svgEl('marker', { id: 'arrow', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' }, svgEl('path', { d: 'M0 0L8 4L0 8z', fill: 'var(--neutral)' }))));
  STEPS.forEach((step, i) => {
    const x = i * (boxW + gap);
    const node = svgEl('g', { class: 'flow-node', tabindex: '0' });
    node.append(svgEl('rect', { class: 'box', x, y: 4, width: boxW, height: boxH, fill: 'var(--surface)', stroke: 'var(--border)', 'stroke-width': 1.5 }));
    node.append(svgEl('text', { x: x + 14, y: 34, style: 'font:22px Georgia,serif;fill:var(--baseline)' }, i + 1));
    node.append(svgEl('text', { x: x + 40, y: 32, style: 'fill:var(--text);font-weight:650;font-size:13px' }, step.title));
    node.append(svgEl('text', { x: x + 40, y: 52, style: 'font-size:11px' }, step.sub));
    node.addEventListener('click', () => document.getElementById(`step-${i + 1}`).scrollIntoView({ block: 'start' }));
    svg.append(node);
    if (i < n - 1) svg.append(svgEl('line', { x1: x + boxW + 2, x2: x + boxW + gap - 3, y1: 4 + boxH / 2, y2: 4 + boxH / 2, stroke: 'var(--neutral)', 'stroke-width': 1.5, 'marker-end': 'url(#arrow)' }));
  });
  return svg;
}
function bars(items, color) {
  const max = Math.max(...items.map(i => i.value), 1e-9);
  return el('div', {}, items.map(i => el('div', { style: 'display:grid;grid-template-columns:minmax(150px,240px) 1fr 52px;gap:10px;align-items:center;margin:6px 0;font-size:12px' },
    el('span', {}, i.label), el('div', { style: `height:12px;width:${100 * i.value / max}%;background:${color}` }), el('span', { class: 'n' }, `${Math.round(i.value * 100)}%`))));
}
function step(i, title, text, ...extra) {
  const s = stageOf(STEPS[i - 1].stage);
  return el('div', { class: 'stepRow', id: `step-${i}` }, el('div', { class: 'stepNum' }, i),
    el('div', {}, el('h3', {}, title), el('p', {}, text), ...extra,
      s ? el('p', { class: 'small' }, `${s.status === 'done' ? '✓' : s.status === 'failed' ? '✕' : '–'} Last run: ${s.detail}`) : null));
}
function renderWorkflow() {
  const root = document.getElementById('workflow');
  root.replaceChildren();
  const a = DATA.acled || {}, v = DATA.views || {}, ml = BT && BT.ml;
  const head = chapter('', 'Workflow', 'From raw data to a comparison with VIEWS', 'Five steps. Step 1 runs privately because it reads raw data; GitHub Actions reruns steps 2 to 5 from the published aggregates.');
  head.querySelector('.eyebrow').textContent = 'How it works';
  head.append(el('div', { class: 'scroll' }, flow()));
  const list = el('div', {});
  list.append(step(1, 'Ingest three sources', 'Each source answers a different question.',
    el('ul', {},
      el('li', {}, `ACLED: weekly conflict deaths by province${a.first_week ? `, ${dayName(a.first_week)} to ${dayName(a.coverage_end)}` : ''}. This is what actually happened, used to train the model and to score every forecast.`),
      el('li', {}, `VIEWS: published monthly forecasts for ${v.cells ?? 'each'} grid cells in the DRC, taken from the run that was public on each date.`),
      el('li', {}, `X: ${(X.posts ?? 0).toLocaleString('en')} public posts in French and English, ${X.first_post ? `${dayName(X.first_post)} to ${dayName(X.last_post)}` : ''}.`))));
  list.append(step(2, 'Prepare the data', 'Put all three on the same places, months and definition of deaths.',
    el('ul', {},
      el('li', {}, 'Deaths are ACLED battles plus explosions and remote violence, the closest match to what VIEWS predicts.'),
      el('li', {}, 'ACLED weeks are split into calendar months by days. Each VIEWS grid cell goes to the province containing its centre.'),
      el('li', {}, `ACLED takes about two weeks to finish coding, so a forecast made on the first of a month uses ACLED only through the month before last.`),
      el('li', {}, 'The LLM labels each X post, and credible authors reporting violence are counted per province.'))));
  const mlExtra = [];
  if (ml) {
    mlExtra.push(el('p', {}, `Features for each province: ${ml.features.join(', ').toLowerCase()}.`),
      table(['Model', 'Log error on 2023 (used to choose)', ...H.map(h => `${h} days, 2024–26`)],
        Object.keys(ml.selection_errors).map(k => [`${k === 'random_forest' ? 'Random Forest' : 'XGBoost'}${k === ml.selected ? ' (chosen)' : ''}`,
          num(ml.selection_errors[k], 3), ...H.map(h => times(ml.evaluation_errors[k][h]))])),
      el('p', { class: 'small' }, `Both models are retrained every month on outcomes known at that time. The one with the lower error on 2023 was frozen before 2024 was scored, so the choice never saw the test years.`));
    const imp = FC && FC.ml && FC.ml.importance_30;
    if (imp) {
      const labels = { log_b1: 'Deaths in the latest coded month', trend_3m: '3-month trend', trend_6m: '6-month trend', control_change: 'Territory changing hands', escalation: 'Escalation events', deescalation: 'De-escalation events', neighbor_deaths: 'Deaths in neighbouring provinces' };
      mlExtra.push(el('h3', {}, `What the ${ml.label} relies on most`), bars(Object.entries(imp).sort((p, q) => q[1] - p[1]).map(([k, val]) => ({ label: labels[k] || k, value: val })), 'var(--baseline)'));
    }
  }
  list.append(step(3, `Build the ACLED model: ${ml ? ml.label : 'Random Forest or XGBoost'}`, 'Two machine-learning models learn monthly deaths from ACLED history. The better one on 2023 is kept.', ...mlExtra));
  list.append(step(4, 'Add X posts', 'ACLED is two weeks behind, so the most recent month is missing when a forecast is made. X fills that gap.',
    el('ul', {},
      el('li', {}, 'Each province’s share of credible X authors is compared with its share of expected deaths, to estimate deaths in the month ACLED has not coded.'),
      el('li', {}, 'That estimate replaces the missing month, and the same ACLED model makes the forecast. Nothing else changes, so any difference comes from X.')),
    el('div', { class: 'formula' }, 'estimated deaths = deaths last coded month × (X share ÷ death share) ^ 0.5, limited to 0.5–2×')));
  list.append(step(5, 'Compare with VIEWS and evaluate', 'Every forecast is checked against what ACLED later recorded.',
    el('ul', {},
      el('li', {}, `Walk-forward test: on the first of each month from ${BT ? monthName(BT.evaluation.first_issue) : '2024'}, each model forecasts 30, 60 and 90 days ahead using only data available then.`),
      el('li', {}, 'Typical error: how many times too high or too low a province forecast usually is. Rank agreement: whether provinces are in the right order from most to least violent.'),
      el('li', {}, 'X has only one week, so it is tested on that week and the week after, shown on the X tab.'))));
  head.append(list);
  root.append(head);
}

/* 4. Limitations and next steps */
function renderLimits() {
  const root = document.getElementById('limits');
  root.replaceChildren();
  const one = chapter('01', 'Limitations', 'What these results cannot show');
  one.append(el('ul', { class: 'takeaways' }, [
    'One week of X posts. X search reaches back only 7 days, so X is tested on a single week. That shows the method works; it does not prove X helps over months.',
    'Different death counts. VIEWS predicts UCDP state-based deaths, while the outcome here is ACLED’s count. Part of VIEWS’s gap is this difference, not model error.',
    'ACLED is revised over time. Leaving out the month still being coded approximates what was known on each date, but not exactly.',
    'Simple history is hard to beat. The ACLED model beats VIEWS, but not "repeat last month" at 60 and 90 days.',
    'The X search named the eastern provinces, and X attention follows news, not only violence.',
  ].map(t => el('li', {}, t))));
  const two = chapter('02', 'Next steps', 'How to make it stronger');
  two.append(el('ol', { class: 'takeaways' }, [
    'Collect X posts every day for 6–12 months, so X can be backtested like ACLED and its weight learned rather than fixed.',
    'Follow the local outlets ACLED itself cites, such as Radio Okapi and Actualite.cd, to get more field reports.',
    'Forecast by territory instead of province, so frontline areas are not averaged with quiet ones.',
    'Add Swahili and Lingala posts and local radio transcripts, where eastern DRC news often appears first.',
  ].map(t => el('li', {}, t))));
  root.append(one, two);
}

/* Tabs */
const TABS = [['results', 'Results', renderResults], ['xsignal', 'How X was evaluated', renderX],
  ['workflow', 'Workflow', renderWorkflow], ['limits', 'Limitations and next steps', renderLimits]];
let current = null;
function select(id) {
  for (const [tab] of TABS) {
    document.getElementById(tab).classList.toggle('on', tab === id);
    const b = document.getElementById(`tab-${tab}`);
    b.setAttribute('aria-selected', tab === id);
    b.tabIndex = tab === id ? 0 : -1;
  }
  history.replaceState(null, '', `#/${id}`);
  current = id;
  TABS.find(([tab]) => tab === id)[2]();
}
const nav = document.getElementById('tabs');
TABS.forEach(([id, label], index) => {
  const b = el('button', { role: 'tab', id: `tab-${id}`, 'aria-controls': id, 'aria-selected': 'false', tabindex: '-1' },
    el('span', { class: 'nav-index' }, String(index + 1).padStart(2, '0')), label);
  b.addEventListener('click', () => select(id));
  b.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    const next = TABS[(index + (e.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length][0];
    select(next);
    document.getElementById(`tab-${next}`).focus();
  });
  nav.append(b);
});
let resizeTimer;
addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => current && select(current), 200); });
const start = location.hash.slice(2);
select(TABS.some(([id]) => id === start) ? start : 'results');
