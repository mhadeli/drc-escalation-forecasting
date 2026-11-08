const BT = DATA.backtest, XC = DATA.xcheck, FC = DATA.forecast, LIVE = DATA.live;
const SERIES = {
  actual: { label: 'What ACLED recorded', color: 'var(--actual)' },
  baseline: { label: 'Baseline: ACLED history', color: 'var(--baseline)' },
  x: { label: 'Baseline + X', color: 'var(--x)' },
  views: { label: 'VIEWS', color: 'var(--views)' },
  views_scaled: { label: 'VIEWS rescaled to ACLED', color: 'var(--views)', dash: true },
};
const H = ['30', '60', '90'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* helpers */
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
const deaths = v => v === null || v === undefined ? 'n/a' : v === 0 ? '0' : v < 10 ? Number(v).toFixed(1) : Math.round(v).toLocaleString('en');
const num = (v, d = 2) => v === null || v === undefined ? 'n/a' : Number(v).toFixed(d);
const factor = e => e === null || e === undefined ? 'n/a' : `×${Math.exp(e).toFixed(2)}`;
const worse = s => s === null || s === undefined ? 'n/a' : s < 0 ? `${Math.round(-s * 100)}% higher` : `${Math.round(s * 100)}% lower`;
const band = iv => iv ? `${worse(iv[1])} to ${worse(iv[0])}` : 'too few issues';
const monthName = key => `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`;
const dayName = iso => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
function width(id) {
  const node = document.getElementById(id), style = getComputedStyle(node);
  return Math.max(280, (node.clientWidth || 900) - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
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
function legend(keys, kind = 'line') {
  return el('div', { class: 'legend' }, keys.map(k => el('span', {},
    el('i', { class: `${kind === 'dot' ? 'dot' : ''} ${SERIES[k].dash ? 'dash' : ''}`, style: `background:${SERIES[k].color}` }), SERIES[k].label)));
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), m = v / p;
  return [1, 1.2, 1.6, 2, 2.4, 3, 4, 5, 6, 8, 10].find(step => m <= step) * p;
}
function stageOf(name) {
  if (name === 'Ingest') return DATA.ingest;
  return DATA.status ? DATA.status.stages.find(s => s.stage === name) : null;
}
function empty(root, text) { root.append(el('div', { class: 'notice' }, text)); }

/* Line chart over months with a crosshair tooltip. rows: [{month, key: value...}] */
function lineChart(id, rows, keys, opts = {}) {
  const w = width(id), h = opts.height || 300, left = 52, right = 16, top = 14, bottom = 34;
  const top_ = niceMax(Math.max(...rows.flatMap(r => keys.map(k => r[k] ?? 0))));
  const x = i => left + i * (w - left - right) / Math.max(rows.length - 1, 1);
  const y = v => top + (1 - v / top_) * (h - top - bottom);
  const svg = svgEl('svg', { width: w, height: h, role: 'img', 'aria-label': opts.label || 'Line chart' });
  for (let t = 0; t <= 4; t++) {
    const v = top_ * t / 4;
    svg.append(svgEl('line', { x1: left, x2: w - right, y1: y(v), y2: y(v), stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, Math.round(v).toLocaleString('en')));
  }
  rows.forEach((r, i) => {
    if (r.month.endsWith('-01') || i === 0) svg.append(svgEl('text', { x: x(i), y: h - 10, 'text-anchor': i === 0 ? 'start' : 'middle' }, r.month.endsWith('-01') ? r.month.slice(0, 4) : monthName(r.month)));
  });
  for (const k of keys) {
    let d = '', pen = false;
    rows.forEach((r, i) => {
      if (r[k] === null || r[k] === undefined) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`; pen = true;
    });
    svg.append(svgEl('path', { d, fill: 'none', stroke: SERIES[k].color, 'stroke-width': k === 'actual' ? 2.25 : 2,
      'stroke-dasharray': SERIES[k].dash ? '5 4' : 'none', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  }
  const cross = svgEl('line', { y1: top, y2: h - bottom, stroke: 'var(--neutral)', visibility: 'hidden' });
  svg.append(cross);
  const hit = svgEl('rect', { x: left, y: 0, width: w - left - right, height: h, fill: 'transparent' });
  hit.addEventListener('mousemove', e => {
    const box = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(rows.length - 1, Math.round((e.clientX - box.left - left) / (w - left - right) * (rows.length - 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    showTip(e, monthName(rows[i].month), keys.map(k => `${SERIES[k].label}: ${deaths(rows[i][k])}`));
  });
  hit.addEventListener('mouseleave', () => { hideTip(); cross.setAttribute('visibility', 'hidden'); });
  svg.append(hit);
  return svg;
}

/* The opening answers the question; methods stay in their own tabs. */
document.getElementById('lead').textContent = BT
  ? 'A simple baseline closely matches the recorded national toll. It is a little better at ranking provinces, too. Whether X improves a forecast remains an open question.'
  : 'A comparison of a simple ACLED-history baseline with VIEWS and X signals. Results will appear when the backtest has run.';
const studyMeta = document.getElementById('study-meta');
if (BT) [
  `${monthName(BT.evaluation.first_issue)} – ${monthName(BT.evaluation.last_issue)}`,
  `${BT.evaluation.scores['30'].issues} issue months · 26 provinces`,
  'Exploratory comparison · ACLED / VIEWS',
].forEach(text => studyMeta.append(el('span', {}, text)));

function chapter(number, topic, title) {
  return el('article', { class: 'chapter' }, el('div', { class: 'chapter-heading' },
    el('p', { class: 'eyebrow' }, `${number} / ${topic}`), el('h2', {}, title)));
}
function comparePair(aLabel, a, bLabel, b) {
  return el('dl', { class: 'compare-pair' },
    el('div', {}, el('dt', {}, aLabel), el('dd', { class: 'baseline' }, a)),
    el('div', {}, el('dt', {}, bLabel), el('dd', { class: 'views' }, b)));
}
function jump(label, tab) {
  const button = el('button', { class: 'text-link' }, `${label} ↗`);
  button.addEventListener('click', () => { select(tab); document.getElementById(tab).scrollIntoView({ block: 'start' }); });
  return button;
}

/* 1. Overview */
function renderOverview() {
  const root = document.getElementById('overview');
  root.replaceChildren();
  if (!BT || !BT.evaluation.scores['30']) return empty(root, 'The research results will appear after the backtest runs.');
  const score = BT.evaluation.scores['30'];
  const gap = 100 * Math.abs(score.total_baseline - score.total_actual) / Math.max(score.total_actual, 1);

  const level = chapter('01', 'National level', 'The total is close.');
  const toll = el('figure', { class: 'chart', id: 'tollChart' });
  level.append(el('div', { class: 'brief-grid' },
    el('div', {}, el('p', { class: 'finding' }, 'Within ', el('strong', {}, `${gap.toFixed(1)}%`), ' of the recorded national toll.'),
      el('p', { class: 'reading' }, 'The baseline carries forward each province’s most recent available monthly death rate. Across the scored months, its total is close to ACLED’s.'),
      el('p', { class: 'reading' }, 'VIEWS predicts a different definition of conflict deaths. Its lower total is not a like-for-like failure.'),
      jump('Definitions and information cutoff', 'how')), toll));
  root.append(level);
  toll.append(hBars('tollChart', [
    { label: 'ACLED recorded', value: score.total_actual, color: 'var(--actual)' },
    { label: 'Simple baseline', value: score.total_baseline, color: 'var(--baseline)' },
    { label: 'VIEWS', value: score.total_views, color: 'var(--views)' },
  ], { label: 'National deaths: ACLED recorded, simple baseline and VIEWS, summed over scored months' }),
  el('figcaption', {}, 'Recorded and predicted deaths, summed over one-month issues. A national-level match does not establish accuracy in each province or month.'));
  const trend = el('details', {}, el('summary', {}, 'See how the national totals change month by month'));
  level.append(trend);
  trend.addEventListener('toggle', () => {
    if (!trend.open || trend.querySelector('svg')) return;
    trend.append(legend(['actual', 'baseline', 'views', 'views_scaled']), el('div', { class: 'card scroll', id: 'natChart' }));
    const rows = BT.national.filter(r => r.month >= BT.evaluation.first_issue);
    document.getElementById('natChart').append(lineChart('natChart', rows, ['actual', 'baseline', 'views', 'views_scaled'], { label: 'Monthly national deaths and retrospective forecasts' }));
  });

  const location = chapter('02', 'Geographic ranking', 'A little better at finding where.');
  location.append(el('div', { class: 'brief-grid equal' },
    el('div', {}, el('p', { class: 'reading' }, 'Can the forecast put higher-risk provinces ahead of quieter ones? On average, the simple baseline orders provinces slightly more closely to the observed deaths.'),
      jump('Explore individual provinces', 'backtest')),
    el('div', {}, comparePair('Simple baseline', num(score.rank_baseline), 'VIEWS', num(score.rank_views)),
      el('p', { class: 'small' }, 'Mean Spearman rank correlation across provinces. Higher is better; 1 would rank all provinces perfectly.'))));
  root.append(location);
  location.append(shareStrip());

  const error = chapter('03', 'Province-month error', 'Lower error. Important qualifications.');
  const weightedGap = score.death_weighted_error_baseline
    ? Math.round(100 * (score.death_weighted_error_views / score.death_weighted_error_baseline - 1)) : null;
  error.append(el('div', { class: 'brief-grid equal' },
    el('div', {}, comparePair('Baseline log error', num(score.error_baseline), 'VIEWS log error', num(score.error_views)),
      el('p', { class: 'small' }, 'Mean absolute error on log(1 + deaths). Lower is better. These values describe a different question from the national total.')),
    el('div', {}, el('p', { class: 'finding' }, 'VIEWS’s log error is ', el('span', { class: 'views' }, worse(score.skill_views)), '.'),
      el('p', { class: 'reading' }, 'VIEWS is worse in Nord-Kivu and Sud-Kivu, but better in Ituri and Mai-Ndombe. Every province-month counts equally, including quiet provinces.'),
      el('p', { class: 'reading' }, weightedGap === null ? 'Death-weighted sensitivity is not available.' : `Weighting province errors by their death toll narrows the gap to ${weightedGap}%. Rescaling VIEWS by ${num(BT.views_scale)} increases its log error to ${num(score.error_views_scaled)}.`),
      jump('Intervals, scores and sensitivity checks', 'backtest'))));
  error.append(el('p', { class: 'small' }, `90% interval: VIEWS error ${band(score.skill_views_90)}. The longer horizons reuse outcome months; they are not three independent tests.`));
  root.append(error);

  const xSection = chapter('04', 'The open question', 'Do posts add forecasting value?');
  const kinshasa = FC && FC.rows.find(r => r.province === 'Kinshasa');
  const panel = el('div', { class: 'open-question' }, el('p', { class: 'eyebrow' }, 'Not established'),
    el('p', { class: 'finding' }, 'Attention is not forecasting skill.'),
    el('p', { class: 'reading' }, XC ? `The same-week correlation is ${num(XC.rank_event_authors_vs_state_deaths)} across ${XC.provinces_n} provinces the search was aimed at. It does not test whether posts lead violence or improve a forecast.` : 'The X check has not run.'),
    el('p', { class: 'reading' }, 'The frozen formula multiplies the baseline. It can make a small adjustment where deaths are already predicted, but cannot detect a new outbreak where the baseline is zero.'));
  if (kinshasa) panel.append(el('div', { class: 'example' },
    el('div', {}, el('strong', {}, String(kinshasa.x_event_authors)), el('span', {}, 'Kinshasa event authors')),
    el('span', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
    el('div', {}, el('strong', {}, deaths(kinshasa.baseline_30)), el('span', {}, 'baseline deaths')),
    el('span', { class: 'arrow', 'aria-hidden': 'true' }, '→'),
    el('div', {}, el('strong', {}, deaths(kinshasa.x_30)), el('span', {}, 'deaths after X adjustment'))));
  panel.append(el('p', { class: 'small' }, FC ? `The November example was saved after its target period began. Its first window can be scored from ${dayName(FC.resolves['30'])} if ACLED is complete; it remains retrospective.` : 'A prospective evaluation still needs forecasts saved before their outcome windows.'), jump('Inspect the X signal', 'xcheck'));
  xSection.append(panel);
  root.append(xSection);
}
/* A bottom axis has its own gutter; no tick label shares a bar row. */
function hBars(id, items, opts = {}) {
  const w = width(id), compact = w < 520, rowH = compact ? 65 : 48;
  const left = compact ? 5 : 137, right = 63, top = 10, bottom = 36;
  const max = niceMax(Math.max(...items.map(item => item.value), 1));
  const height = top + items.length * rowH + bottom, axisY = height - bottom;
  const x = value => left + value / max * (w - left - right);
  const svg = svgEl('svg', { width: w, height, viewBox: `0 0 ${w} ${height}`, role: 'img', 'aria-label': opts.label || 'Bar chart' });
  const ticks = compact ? 2 : 4;
  for (let i = 0; i <= ticks; i++) {
    const value = max * i / ticks;
    svg.append(svgEl('line', { x1: x(value), x2: x(value), y1: top, y2: axisY, stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: x(value), y: axisY + 23, 'text-anchor': i === 0 ? 'start' : 'middle', 'data-axis-label': 'true' }, Math.round(value).toLocaleString('en')));
  }
  items.forEach((item, i) => {
    const y = top + i * rowH + (compact ? 39 : 24);
    svg.append(svgEl('text', { x: compact ? left : left - 13, y: compact ? y - 17 : y + 4, 'text-anchor': compact ? 'start' : 'end' }, item.label));
    svg.append(svgEl('rect', { x: left, y: y - 8, width: Math.max(0, x(item.value) - left), height: 16, rx: 1,
      fill: item.color, 'data-bar': 'true' }));
    svg.append(svgEl('text', { x: x(item.value) + 10, y: y + 4, style: 'fill:var(--text);font-weight:650;font-variant-numeric:tabular-nums' }, Math.round(item.value).toLocaleString('en')));
  });
  return svg;
}
function shareStrip() {
  const entries = Object.entries(BT.provinces).sort((a, b) => b[1].actual - a[1].actual);
  const total = entries.reduce((sum, [, p]) => sum + p.actual, 0) || 1;
  const named = entries.filter(([, p]) => p.actual / total >= 0.03);
  const rest = total - named.reduce((sum, [, p]) => sum + p.actual, 0);
  const parts = [...named.map(([name, p]) => [name, p.actual]), ...(rest > 1 ? [['All other provinces', rest]] : [])];
  const colors = ['#275b49', '#4c7258', '#8b6b41', '#69756a', '#5e6e7c', '#796b63'];
  const topShare = Math.round(100 * named.reduce((sum, [, p]) => sum + p.actual, 0) / total);
  return el('div', {},
    el('h3', {}, 'Where the recorded deaths concentrate'),
    el('p', { class: 'muted' }, `${named.map(([name]) => name).join(', ')} account for ${topShare}% of deaths in the scored months. The log score still treats the other provinces as equal cases.`),
    el('div', { class: 'share', role: 'img', 'aria-label': 'Share of recorded deaths by province' }, parts.map(([name, value], i) => {
      const pct = 100 * value / total;
      return el('div', { style: `width:${pct}%;background:${colors[i]}`, title: `${name}: ${deaths(value)} (${Math.round(pct)}%)` }, pct >= 12 ? `${name} ${Math.round(pct)}%` : pct >= 5 ? `${Math.round(pct)}%` : '');
    })),
    el('div', { class: 'legend' }, parts.map(([name, value], i) => el('span', {}, el('i', { class: 'sw', style: `background:${colors[i]}` }), `${name} ${Math.round(100 * value / total)}%`))));
}

/* 2. Backtest */
function skillChart(id) {
  const w = Math.max(width(id), 520), rowH = 30, left = 150, right = 48, top = 28;
  const rows = H.flatMap(h => [['views', h], ['views_scaled', h]]).filter(([k, h]) => BT.evaluation.scores[h] && BT.evaluation.scores[h][`skill_${k}`] !== null);
  const vals = rows.flatMap(([k, h]) => [BT.evaluation.scores[h][`skill_${k}`], ...(BT.evaluation.scores[h][`skill_${k}_90`] || [])]);
  const lo = Math.min(-0.2, Math.floor(Math.min(...vals) * 10) / 10), hi = Math.max(0.2, Math.ceil(Math.max(...vals) * 10) / 10);
  const height = top + rows.length * rowH + 24;
  const x = v => left + (v - lo) / (hi - lo) * (w - left - right);
  const svg = svgEl('svg', { width: w, height, role: 'img', 'aria-label': 'VIEWS error relative to the baseline, with 90% intervals' });
  for (let t = Math.ceil(lo * 10); t <= Math.floor(hi * 10); t += 1) {
    const v = t / 10;
    if (t % 2 !== 0 && (hi - lo) > 1) continue;
    svg.append(svgEl('line', { x1: x(v), x2: x(v), y1: top - 6, y2: height - 22, stroke: v === 0 ? 'var(--text-2)' : 'var(--grid)', 'stroke-width': v === 0 ? 1.5 : 1 }));
    svg.append(svgEl('text', { x: x(v), y: 14, 'text-anchor': 'middle' }, v === 0 ? 'baseline' : `${v < 0 ? '+' : '−'}${Math.abs(Math.round(v * 100))}%`));
  }
  // Direction labels below the plot; the section heading supplies the axis meaning.
  svg.append(svgEl('text', { x: x(lo), y: height - 6, 'text-anchor': 'start' }, '← VIEWS worse'));
  svg.append(svgEl('text', { x: x(hi), y: height - 6, 'text-anchor': 'end' }, 'VIEWS better →'));
  rows.forEach(([k, h], i) => {
    const s = BT.evaluation.scores[h], y = top + i * rowH + rowH / 2, iv = s[`skill_${k}_90`];
    svg.append(svgEl('text', { x: left - 10, y: y + 4, 'text-anchor': 'end' }, `${h} days · ${k === 'views' ? 'VIEWS' : 'rescaled'}`));
    if (iv) svg.append(svgEl('line', { x1: x(iv[0]), x2: x(iv[1]), y1: y, y2: y, stroke: SERIES[k].color, 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-dasharray': SERIES[k].dash ? '4 3' : 'none' }));
    svg.append(svgEl('circle', { cx: x(s[`skill_${k}`]), cy: y, r: 5.5, fill: k === 'views' ? SERIES[k].color : 'var(--surface)', stroke: SERIES[k].color, 'stroke-width': 2 }));
    const hit = svgEl('rect', { x: 0, y: y - rowH / 2, width: w, height: rowH, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, `${SERIES[k].label}, ${h} days`, [`Error ${worse(s[`skill_${k}`])} than the baseline`, `90% interval: ${band(iv)}`]));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}
function provinceChart(id) {
  const entries = Object.entries(BT.provinces).sort((a, b) => b[1].actual - a[1].actual);
  const w = Math.max(width(id), 520), rowH = 22, left = 130, right = 36, top = 26;
  const height = top + entries.length * rowH + 8;
  const ticks = [1, 1.5, 2, 3, 4, 5, 6, 8].filter((t, i, a) => i === 0 || Math.log(a[i - 1]) < Math.max(...entries.flatMap(([, p]) => [p.error_baseline, p.error_views])));
  const top_ = Math.log(ticks[ticks.length - 1]);
  const x = v => left + v / top_ * (w - left - right);
  const svg = svgEl('svg', { width: w, height, role: 'img', 'aria-label': 'Average error by province, baseline and VIEWS' });
  for (const t of ticks) {
    const v = Math.log(t);
    svg.append(svgEl('line', { x1: x(v), x2: x(v), y1: top - 6, y2: height - 4, stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: x(v), y: 14, 'text-anchor': 'middle' }, t === 1 ? 'exact' : `×${t}`));
  }
  entries.forEach(([name, p], i) => {
    const y = top + i * rowH + rowH / 2;
    svg.append(svgEl('text', { x: left - 10, y: y + 4, 'text-anchor': 'end' }, name));
    svg.append(svgEl('line', { x1: x(Math.min(p.error_baseline, p.error_views)), x2: x(Math.max(p.error_baseline, p.error_views)), y1: y, y2: y, stroke: 'var(--border)', 'stroke-width': 2 }));
    for (const k of ['baseline', 'views']) svg.append(svgEl('circle', { cx: x(p[`error_${k}`]), cy: y, r: 5, fill: SERIES[k].color, stroke: 'var(--surface)', 'stroke-width': 2 }));
    const hit = svgEl('rect', { x: 0, y: y - rowH / 2, width: w, height: rowH, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, name, [`Recorded deaths over the period: ${deaths(p.actual)}`, `Baseline typical error: ${factor(p.error_baseline)}`, `VIEWS typical error: ${factor(p.error_views)}`]));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}
function provinceNote() {
  const all = Object.entries(BT.provinces);
  const total = all.reduce((sum, [, p]) => sum + p.actual, 0) || 1;
  const quiet = all.filter(([, p]) => p.actual < 5 && p.error_views > p.error_baseline).length;
  const busy = [...all].sort((a, b) => b[1].actual - a[1].actual).slice(0, 5);
  const share = Math.round(100 * busy.reduce((sum, [, p]) => sum + p.actual, 0) / total);
  const worst = busy.reduce((a, b) => (b[1].error_views - b[1].error_baseline > a[1].error_views - a[1].error_baseline ? b : a));
  const better = busy.filter(([, p]) => p.error_views < p.error_baseline).map(([n]) => n);
  return `The five deadliest provinces account for ${share}% of recorded deaths. In ${quiet} provinces with almost no recorded deaths, VIEWS still expects a few and the simple forecast does better on the log score. ` +
    `Among those five, the gap is largest in ${worst[0]}` + (better.length ? `, and VIEWS does better in ${better.join(' and ')}.` : '.');
}
function xShiftNote(h) {
  const rows = FC.rows.filter(r => r[`x_${h}`] !== null);
  const zeros = rows.filter(r => r[`baseline_${h}`] === 0);
  const shifted = rows.filter(r => r[`baseline_${h}`] > 0).map(r => ({ name: r.province, pct: (r[`x_${h}`] - r[`baseline_${h}`]) / r[`baseline_${h}`] }));
  const big = shifted.filter(s => Math.abs(s.pct) >= 0.1);
  const small = shifted.filter(s => Math.abs(s.pct) < 0.1);
  const parts = [];
  if (zeros.length) parts.push(`${zeros.map(r => `${r.province} (${r.x_event_authors} authors)`).join(', ')} ${zeros.length === 1 ? 'stays' : 'stay'} at zero, because X multiplies the baseline.`);
  if (small.length) parts.push(`${small.map(s => s.name).join(' and ')} ${small.length === 1 ? 'moves' : 'move'} by less than 10%.`);
  if (big.length) parts.push(big.map(s => `${s.name} ${s.pct > 0 ? '+' : '−'}${Math.round(Math.abs(s.pct) * 100)}%`).join(', ') + '.');
  parts.push('A dot in the table means that province has no X data, so the forecast stays the baseline.');
  return parts.join(' ');
}
function renderBacktest() {
  const root = document.getElementById('backtest');
  root.replaceChildren();
  if (!BT) return empty(root, 'The backtest has not run. It needs the ingested ACLED and VIEWS aggregates.');
  const sc = BT.evaluation.scores;
  root.append(el('p', { class: 'muted' }, `${BT.evaluation.scores['30'].forecasts} province forecasts per horizon, ${monthName(BT.evaluation.first_issue)} to ${monthName(BT.evaluation.last_issue)}. The baseline uses the most recent available month, ending at M−2; VIEWS uses a run already published at the issue date. ACLED is a revised snapshot, so this is a retrospective comparison.`));
  root.append(el('h2', {}, 'Scoreboard'),
    el('p', { class: 'muted' }, 'Lower log error is better; higher rank correlation is better. Each province-month counts equally, regardless of its death toll. Intervals use blocks of consecutive issue months. The horizons overlap, so these are not independent tests.'),
    table(['Calendar months', 'Log error: baseline', 'VIEWS', 'VIEWS error vs baseline', '90% interval', 'Rescaled VIEWS', 'Rank: baseline', 'VIEWS'],
      H.filter(h => sc[h]).map(h => [String(Number(h) / 30), num(sc[h].error_baseline, 3), num(sc[h].error_views, 3), worse(sc[h].skill_views), band(sc[h].skill_views_90),
        worse(sc[h].skill_views_scaled), num(sc[h].rank_baseline), num(sc[h].rank_views)])),
    el('p', { class: 'small' }, `Each row covers ${sc['30'].issues} monthly issues × 26 provinces.`));
  root.append(el('h2', {}, 'Equal-weighted log error, VIEWS against the simple forecast'),
    el('p', { class: 'muted' }, `Dots left of the line mean VIEWS’s log error is larger. Bars are 90% intervals from resampling blocks of consecutive months. The hollow dots multiply every VIEWS forecast by ${num(BT.views_scale)}, fitted on 2023 so the totals match. That lift does not move the dots onto the line.`),
    el('div', { class: 'card scroll', id: 'skillChart' }));
  document.getElementById('skillChart').append(skillChart('skillChart'));
  root.append(el('h2', {}, 'By province, 30 days'), el('p', { class: 'muted' }, provinceNote()),
    legend(['baseline', 'views'], 'dot'), el('div', { class: 'card scroll', id: 'provChart' }));
  document.getElementById('provChart').append(provinceChart('provChart'));
  const names = Object.keys(BT.province_series).sort((a, b) => (BT.provinces[b]?.actual || 0) - (BT.provinces[a]?.actual || 0));
  const pick = el('select', { 'aria-label': 'Province' });
  names.forEach(n => pick.append(el('option', { value: n }, n)));
  const holder = el('div', { class: 'card scroll', id: 'drill' });
  root.append(el('h2', {}, 'One province over time'), el('div', { class: 'controls' }, el('label', {}, 'Province ', pick)), legend(['actual', 'baseline', 'views']), holder);
  const draw = () => {
    holder.replaceChildren(lineChart('drill', BT.province_series[pick.value].map(([month, actual, baseline, views]) => ({ month, actual, baseline, views })),
      ['actual', 'baseline', 'views'], { height: 260, label: `${pick.value}: monthly deaths and 30-day forecasts` }));
  };
  pick.addEventListener('change', draw);
  draw();
  const windowErrors = Object.entries(BT.selection.errors).sort((a, b) => a[1] - b[1]);
  const windowTie = windowErrors.length > 1 && windowErrors[1][1] - windowErrors[0][1] < 0.01
    ? ` The ${windowErrors[1][0]}-month window was essentially tied with it: log error ${windowErrors[0][1].toFixed(3)} against ${windowErrors[1][1].toFixed(3)}. The frozen choice uses the selected averaging window.`
    : '';
  root.append(el('h2', {}, 'How the baseline was chosen'),
    el('p', { class: 'muted' }, `Four windows were compared on the original 2023 issue dates. The ${BT.window}-month reference stays frozen.${windowTie} Some late-2023 issues used early-2024 outcomes at longer horizons, so this is exploratory. Restricting outcome months to 2023 chooses ${BT.selection_sensitivity ? BT.selection_sensitivity.window : "a separate"} months; that check does not replace the reference.`),
    table(['Window', 'Selection log error'], Object.entries(BT.selection.errors).map(([w, e]) => [`${w} month${w === '1' ? '' : 's'}${Number(w) === BT.window ? ', chosen' : ''}`, num(e, 3)])));
  const sens = BT.sensitivity_no_gap, sel = BT.selection_year_scores;
  root.append(el('details', {}, el('summary', {}, 'Sensitivity checks'),
    el('p', {}, 'The baseline skips the month just before the issue date, because ACLED is still coding it. If it could use that month, its advantage would grow.'),
    table(['Horizon', 'VIEWS against baseline, as scored', 'If the baseline used the latest month', 'In 2023, the selection year'],
      H.filter(h => sc[h]).map(h => [`${h} days`, worse(sc[h].skill_views), sens[h] ? worse(sens[h].skill_views) : 'n/a', sel[h] ? worse(sel[h].skill_views) : 'n/a']))));
}

/* 3. X check */
function scatter(id) {
  const rows = XC.rows, w = Math.min(width(id), 760), h = 340, left = 50, right = 120, top = 16, bottom = 44;
  const mx = niceMax(Math.max(...rows.map(r => r.event_authors), 1)), my = niceMax(Math.max(...rows.map(r => r.deaths_state), 1));
  const x = v => left + v / mx * (w - left - right), y = v => top + (1 - v / my) * (h - top - bottom);
  const svg = svgEl('svg', { width: w, height: h, role: 'img', 'aria-label': 'X authors reporting violence against ACLED deaths, by province' });
  const parts = v => (v % 4 === 0 ? 4 : 5);
  for (let t = 0; t <= parts(my); t++) {
    const v = my * t / parts(my);
    svg.append(svgEl('line', { x1: left, x2: w - right, y1: y(v), y2: y(v), stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, Math.round(v)));
  }
  for (let t = 0; t <= parts(mx); t++) svg.append(svgEl('text', { x: x(mx * t / parts(mx)), y: h - bottom + 18, 'text-anchor': 'middle' }, Math.round(mx * t / parts(mx))));
  const placed = [];
  svg.append(svgEl('text', { x: (left + w - right) / 2, y: h - 6, 'text-anchor': 'middle' }, 'X authors posting about violence'));
  svg.append(svgEl('text', { x: 12, y: top + (h - top - bottom) / 2, transform: `rotate(-90 12 ${top + (h - top - bottom) / 2})`, 'text-anchor': 'middle' }, 'ACLED deaths that week'));
  [...rows].sort((a, b) => b.event_authors - a.event_authors).forEach(r => {
    const cx = x(r.event_authors), cy = y(r.deaths_state);
    const quiet = r.deaths_state === 0 && r.event_authors > 0;
    svg.append(svgEl('circle', { cx, cy, r: 6, fill: quiet ? 'var(--views)' : 'var(--x)', stroke: 'var(--surface)', 'stroke-width': 2 }));
    const dy = [0, -15, 15, -30, 30].find(d => !placed.some(([px, py]) => Math.abs(px - cx) < 90 && Math.abs(py - (cy + d)) < 13)) ?? 0;
    placed.push([cx, cy + dy]);
    svg.append(svgEl('text', { x: cx + 10, y: cy + 4 + dy, style: 'fill:var(--text)' }, r.province));
    const hit = svgEl('circle', { cx, cy, r: 14, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, r.province, [`X authors posting about violence: ${r.event_authors}`, `All X authors naming it: ${r.authors}`, `ACLED deaths, battles and remote violence: ${r.deaths_state}`, `ACLED deaths, including violence against civilians: ${r.deaths_organised}`]));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}
function renderX() {
  const root = document.getElementById('xcheck');
  root.replaceChildren();
  if (!XC) return empty(root, 'The X check has not run. It needs the X counts and the ACLED weekly totals.');
  root.append(el('p', { class: 'muted' }, `X’s search reaches back only 7 days, so X cannot be backtested. Instead, this checks the one week we have. Posts from ${dayName(XC.posts_from)} to ${dayName(XC.posts_to)} are compared with ACLED deaths in the ACLED week that contains them, ${dayName(XC.week_start)} to ${dayName(XC.week_end)}.`));
  root.append(el('div', { class: 'tiles' },
    el('div', { class: 'tile' }, el('div', { class: 'label' }, 'Rank correlation, authors posting about violence'), el('div', { class: 'value' }, num(XC.rank_event_authors_vs_state_deaths)), el('div', { class: 'note' }, 'Against deaths in battles and remote violence. 1 means X ranks the six provinces exactly as ACLED does.')),
    el('div', { class: 'tile' }, el('div', { class: 'label' }, 'Including violence against civilians'), el('div', { class: 'value' }, num(XC.rank_event_authors_vs_organised_deaths)), el('div', { class: 'note' }, 'The same X counts against a wider death definition.')),
    el('div', { class: 'tile' }, el('div', { class: 'label' }, 'All authors naming the province'), el('div', { class: 'value' }, num(XC.rank_all_authors_vs_state_deaths)), el('div', { class: 'note' }, 'Without the violence filter, attention dilutes the signal.'))));
  const quiet = XC.rows.filter(r => r.deaths_state === 0 && r.event_authors > 0).map(r => r.province);
  root.append(el('div', { class: 'card scroll', id: 'scatter' }));
  document.getElementById('scatter').append(scatter('scatter'));
  root.append(el('p', { class: 'small' }, `Blue marks a province with both posts and deaths. Orange marks posts and no state-based deaths${quiet.length ? `: ${quiet.join(', ')}` : ''}. The vertical axis is the same week as the posts, so this chart does not show whether X leads violence.`));
  root.append(table(['Province', 'X authors posting about violence', 'All X authors', 'ACLED deaths: battles, remote violence', 'Including violence against civilians'],
    [...XC.rows].sort((a, b) => b.deaths_state - a.deaths_state).map(r => [r.province, r.event_authors, r.authors, r.deaths_state, r.deaths_organised])));
  root.append(el('h2', {}, 'Read with care'), el('ul', { class: 'takeaways' }, [
    'Six provinces give very little evidence. A correlation this size can occur by chance.',
    'The X search terms named Goma, the Kivus and Ituri, so X attention there is partly by design.',
    'Kinshasa draws posts about politics and the government, not deaths, which is why attention and violence differ.',
    'Only counts per province are published here. No post text or account is stored on this page.',
  ].map(t => el('li', {}, t))));
}

/* 4. Forecast and map */
const BINS = [0, 1, 5, 20, 50, 100, 200];
const RAMP = ['var(--zero)', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const binColor = v => v === null || v === undefined ? 'transparent' : v === 0 ? RAMP[0] : RAMP[Math.min(BINS.filter(b => v >= b).length, RAMP.length - 1)];
let map, layer;
function renderForecast() {
  const root = document.getElementById('forecast');
  root.replaceChildren();
  if (!FC) return empty(root, 'No live forecast yet. It needs the backtest’s frozen baseline window.');
  root.append(el('p', { class: 'muted' }, `Issued ${dayName(FC.issued)}. This saved example uses its original ${FC.window_months}-month baseline through ${monthName(FC.acled_through)}; it uses the same retained primary reference as the backtest. X posts are from ${dayName(FC.x_posts[0])} to ${dayName(FC.x_posts[1])}. VIEWS run ${FC.views_run.run} was released ${dayName(FC.views_run.released)}. The forecast was saved on ${dayName(FC.written_at)} and is not changed afterwards. Evaluation type: ${FC.evaluation_kind || 'unverified'}. A reconstruction saved after the target starts is not a prospective forecast.`));
  const hSel = el('select', { 'aria-label': 'Horizon' }), mSel = el('select', { 'aria-label': 'Forecast shown on the map' });
  H.forEach(h => hSel.append(el('option', { value: h }, `${h} days: ${FC.months[h].map(monthName).join(' to ').replace(/ to .* to /, ' to ')}`)));
  [['baseline', 'Baseline: ACLED history'], ['x', 'Baseline + X'], ['views', 'VIEWS'], ['views_scaled', 'VIEWS rescaled']].forEach(([v, t]) => mSel.append(el('option', { value: v }, t)));
  root.append(el('div', { class: 'controls' }, el('label', {}, 'Horizon ', hSel), el('label', {}, 'Map shows ', mSel)));
  root.append(el('div', { id: 'map' }), el('div', { class: 'legend', id: 'mapLegend' }));
  const lower = el('div', {});
  root.append(lower);
  const draw = () => {
    const h = hSel.value, m = mSel.value;
    drawMap(h, m);
    const rows = [...FC.rows].sort((a, b) => b[`baseline_${h}`] - a[`baseline_${h}`] || b[`views_${h}`] - a[`views_${h}`]);
    lower.replaceChildren(
      el('h2', {}, `Expected deaths by province, next ${h} days`),
      el('p', { class: 'muted' }, 'Filled dots are the simple forecast and VIEWS. The blue ring is the simple forecast after the X multiplier. Where the ring sits on the green dot, X barely changed the number.'),
      legend(['baseline', 'x', 'views'], 'dot'),
      el('div', { class: 'card scroll', id: 'dots' }),
      table(['Province', 'Baseline', 'Baseline + X', 'X multiplier', 'X authors', 'VIEWS', 'VIEWS rescaled'],
        rows.map(r => [r.province, deaths(r[`baseline_${h}`]), r[`x_${h}`] === null ? '·' : deaths(r[`x_${h}`]), r.x_multiplier === null ? '·' : `×${num(r.x_multiplier)}`,
          r.x_event_authors ?? '·', deaths(r[`views_${h}`]), deaths(r[`views_scaled_${h}`])])),
      el('p', { class: 'small' }, xShiftNote(h)),
      el('h2', {}, 'When each horizon is scored'),
      table(['Horizon', 'Months covered', 'Ready about', 'Status'], H.map(k => {
        const s = LIVE && LIVE.horizons[k];
        return [`${k} days`, FC.months[k].map(monthName).join(', '), dayName(FC.resolves[k]),
          !s ? 'Not checked' : s.status === 'pending' ? 'Waiting for ACLED' : `Scored: baseline ${factor(s.error_baseline)}, VIEWS ${factor(s.error_views)}, X ${factor(s.error_x)}`];
      }), 9));
    document.getElementById('dots').append(dotPlot('dots', rows.filter(r => Math.max(r[`baseline_${h}`], r[`views_${h}`] || 0, r[`x_${h}`] || 0) >= 1), h));
  };
  hSel.addEventListener('change', draw);
  mSel.addEventListener('change', draw);
  initMap();
  draw();
}
function dotPlot(id, rows, h) {
  const w = width(id), rowH = 26, left = 120, right = 30, top = 26, height = top + rows.length * rowH + 8;
  const mx = niceMax(Math.max(...rows.flatMap(r => [r[`baseline_${h}`], r[`x_${h}`] || 0, r[`views_${h}`] || 0])));
  const x = v => left + v / mx * (w - left - right);
  const svg = svgEl('svg', { width: w, height, role: 'img', 'aria-label': `Expected deaths by province over ${h} days` });
  for (let t = 0; t <= 4; t++) {
    svg.append(svgEl('line', { x1: x(mx * t / 4), x2: x(mx * t / 4), y1: top - 6, y2: height - 4, stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: x(mx * t / 4), y: 14, 'text-anchor': 'middle' }, Math.round(mx * t / 4)));
  }
  rows.forEach((r, i) => {
    const y = top + i * rowH + rowH / 2;
    const vals = { baseline: r[`baseline_${h}`], x: r[`x_${h}`], views: r[`views_${h}`] };
    const present = Object.values(vals).filter(v => v !== null);
    svg.append(svgEl('text', { x: left - 10, y: y + 4, 'text-anchor': 'end' }, r.province));
    svg.append(svgEl('line', { x1: x(Math.min(...present)), x2: x(Math.max(...present)), y1: y, y2: y, stroke: 'var(--border)', 'stroke-width': 2 }));
    for (const k of ['baseline', 'views', 'x']) if (vals[k] !== null) {
      const ring = k === 'x';
      svg.append(svgEl('circle', { cx: x(vals[k]), cy: y, r: ring ? 7 : 5.5, fill: ring ? 'var(--surface)' : SERIES[k].color, stroke: SERIES[k].color, 'stroke-width': 2 }));
    }
    const hit = svgEl('rect', { x: 0, y: y - rowH / 2, width: w, height: rowH, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, r.province, Object.keys(vals).map(k => `${SERIES[k].label}: ${vals[k] === null ? 'no X data' : deaths(vals[k])}`)));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}
function initMap() {
  if (!window.L) { document.getElementById('map').textContent = 'The map library could not load.'; return; }
  if (map) { map.remove(); map = null; }
  // No tile layer: the province shapes are the map, so no third-party tile service or key is needed.
  map = L.map('map', { scrollWheelZoom: false, zoomSnap: .25, attributionControl: true }).setView([-4, 23.6], 5.25);
  map.attributionControl.addAttribution('Boundaries: geoBoundaries, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors');
}
function drawMap(h, m) {
  if (!map || !DATA.geo) return;
  if (layer) layer.remove();
  const rows = Object.fromEntries(FC.rows.map(r => [r.province, r]));
  layer = L.geoJSON(DATA.geo, {
    style: f => {
      const r = rows[f.properties.province], v = r ? r[`${m}_${h}`] : null;
      return { color: v === null ? 'var(--border)' : '#ffffff', weight: 1.2, fillColor: binColor(v), fillOpacity: v === null ? 0 : .92 };
    },
    onEachFeature: (f, l) => {
      const r = rows[f.properties.province];
      if (!r) return;
      const v = r[`${m}_${h}`];
      l.bindTooltip(`<b>${f.properties.province}</b><br>${SERIES[m].label}: ${v === null ? 'no X data' : deaths(v)}<br>` +
        `Baseline ${deaths(r[`baseline_${h}`])} · VIEWS ${deaths(r[`views_${h}`])}${r[`x_${h}`] !== null ? ` · + X ${deaths(r[`x_${h}`])}` : ''}`, { sticky: true });
    },
  }).addTo(map);
  const key = document.getElementById('mapLegend');
  key.replaceChildren(el('span', {}, `Expected deaths, next ${h} days:`),
    ...['0', '<1', '1–5', '5–20', '20–50', '50–100', '100–200', '200+'].map((t, i) => el('span', {}, el('i', { class: 'sw', style: `background:${RAMP[i]};border:1px solid var(--border)` }), t)),
    ...(m === 'x' ? [el('span', {}, 'Provinces without X data are left blank.')] : []));
}

/* 5. How it works */
const STEPS = [
  { title: 'Ingest', sub: 'ACLED, VIEWS, X', stage: 'Ingest', text: 'Three sources are loaded on a private machine. Only aggregates leave it.', points: [
    () => DATA.acled ? `ACLED: weekly deaths by province and event type, ${dayName(DATA.acled.first_week)} to ${dayName(DATA.acled.coverage_end)}. The export also held the Republic of Congo, whose rows are dropped by country.` : 'ACLED: weekly deaths by province and event type.',
    'VIEWS: published forecasts of state-based deaths for each 0.5° grid cell and month, for each dated run.',
    () => DATA.x ? `X: ${DATA.x.posts.toLocaleString('en')} public posts about conflict in the DRC, ${dayName(DATA.x.first_post)} to ${dayName(DATA.x.last_post)}, in French and English. Rules find the province and whether a post reports violence. Each author counts once.` : 'X: public posts about conflict in the DRC.'] },
  { title: 'Align', sub: 'Places, months, deaths', stage: 'Inputs', text: 'All three sources are put on the same footing.', points: [
    'ACLED weeks start on Saturday. A week that crosses a month end is split by days, so 3 days in October send 3/7 of its deaths to October.',
    () => DATA.views ? `Each of the ${DATA.views.cells} VIEWS grid cells goes to the province containing its centre. ${DATA.views.border_cells} border cells whose centre falls just outside go to the nearest province.` : 'Each VIEWS grid cell goes to the province containing its centre.',
    'Deaths means ACLED battles plus explosions and remote violence. This is an event-type proxy, not verified state participation or the same outcome as VIEWS’s state-based deaths.'] },
  { title: 'Baseline', sub: 'ACLED history only', stage: 'Backtest', text: 'The simple forecast. It uses no text and no VIEWS output.', points: [
    () => BT ? `For each province it takes deaths in ${BT.window === 1 ? 'the most recent complete month' : `the last ${BT.window} complete months`}, as a monthly average, then multiplies by the number of months in the horizon.` : 'It carries forward recent deaths.',
    'On the first of a month, the previous month is still being coded, so the window stops at the month before that. VIEWS is held to a run whose inputs are at least that old.'] },
  { title: 'Backtest', sub: 'Replay 2024 onward', stage: 'Backtest', text: 'Each past month is replayed as if it were today.', points: [
    'The original reference stays frozen. Late-2023 selection horizons included early-2024 outcomes; the stricter boundary check is separate and the comparison is exploratory.',
    'VIEWS is taken from the run that had been published by that date, never a later one.'] },
  { title: 'X check', sub: 'Same week', stage: 'X check', text: 'There is only one week of posts, so this checks the same week rather than a forecast.', points: [
    'Authors posting about violence are ranked against ACLED deaths in that week. Six provinces, and a search aimed at the east, cannot show that X predicts the next month.'] },
  { title: 'Forecast example', sub: 'Baseline × X', stage: 'Forecast', text: 'One reconstruction targeting November 2026, saved on November 7 after the target period began.', points: [
    'X multiplies the baseline by how the province’s share of authors compares with its share of expected deaths. A baseline of zero stays zero: Kinshasa has 15 event authors and still predicts zero. The multipliers are about 0.58 in Ituri, 1.08 in Nord-Kivu and 1.03 in Sud-Kivu. Escalation, control-change and de-escalation tags are extracted but unused. The multiplier fades over three weeks.'] },
  { title: 'Score', sub: 'After ACLED codes', stage: 'Score', text: 'Each horizon is scored once ACLED has coded all its months, about two weeks after the last one ends.', points: [
    'One issue date cannot prove that X helps. It shows the full workflow running end to end.'] },
  { title: 'Publish', sub: 'This page', stage: null, text: 'GitHub Actions reruns steps 2 to 7 from committed aggregates and rebuilds this page.', points: [
    'No post text, account names or raw ACLED rows are published.'] },
];
const STATUS = { done: 'Done', skipped: 'Waiting', failed: 'Failed', notrun: 'Not run' };
const ICON = { done: '✓', skipped: '–', failed: '✕', notrun: '○' };
function stepState(step) {
  if (!step.stage) return 'done';
  const s = stageOf(step.stage);
  return s ? s.status : 'notrun';
}
function flow() {
  const W = 1080, n = STEPS.length, boxW = 116, gap = (W - boxW * n) / (n - 1), top = 70, boxH = 92;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} 190`, width: '100%', style: 'min-width:900px', role: 'img', 'aria-label': 'Eight numbered steps from ingestion to publishing' });
  svg.append(svgEl('defs', {}, svgEl('marker', { id: 'arrow', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' }, svgEl('path', { d: 'M0 0L8 4L0 8z', fill: 'var(--neutral)' }))));
  [['ACLED weekly deaths', 0], ['VIEWS forecasts', 1], ['X posts', 2]].forEach(([label, i]) => {
    const x = i * 150, w = 140;
    svg.append(svgEl('rect', { x, y: 2, width: w, height: 28, rx: 14, fill: 'var(--page)', stroke: 'var(--border)' }));
    svg.append(svgEl('text', { x: x + w / 2, y: 21, 'text-anchor': 'middle' }, label));
    svg.append(svgEl('path', { d: `M${x + w / 2} 30 L${boxW / 2} ${top - 2}`, fill: 'none', stroke: 'var(--neutral)', 'marker-end': 'url(#arrow)' }));
  });
  STEPS.forEach((step, i) => {
    const x = i * (boxW + gap), code = stepState(step);
    svg.append(svgEl('rect', { x, y: top, width: boxW, height: boxH, rx: 10, fill: 'var(--surface)', stroke: 'var(--border)', 'stroke-width': 1.5 }));
    svg.append(svgEl('circle', { cx: x + 18, cy: top + 20, r: 12, fill: 'var(--text)' }));
    svg.append(svgEl('text', { x: x + 18, y: top + 24.5, 'text-anchor': 'middle', style: 'fill:var(--surface);font-weight:700' }, i + 1));
    const words = step.title.split(' ');
    svg.append(svgEl('text', { x: x + 36, y: words.length > 1 ? top + 16 : top + 25, style: 'fill:var(--text);font-weight:620;font-size:13px' }, words[0]));
    if (words.length > 1) svg.append(svgEl('text', { x: x + 36, y: top + 32, style: 'fill:var(--text);font-weight:620;font-size:13px' }, words.slice(1).join(' ')));
    svg.append(svgEl('text', { x: x + 9, y: top + 54, style: 'font-size:11px' }, step.sub.length > 19 ? `${step.sub.slice(0, 18)}…` : step.sub));
    svg.append(svgEl('text', { x: x + 9, y: top + 78, style: 'font-size:11.5px;font-weight:600' }, `${ICON[code]} ${STATUS[code]}`));
    if (i < n - 1) svg.append(svgEl('line', { x1: x + boxW + 1, x2: x + boxW + gap - 2, y1: top + boxH / 2, y2: top + boxH / 2, stroke: 'var(--neutral)', 'stroke-width': 1.5, 'marker-end': 'url(#arrow)' }));
  });
  svg.append(svgEl('text', { x: 0, y: 184, style: 'font-size:11.5px' }, 'Step 1 runs privately because it reads raw data. Steps 2 to 8 run on GitHub Actions from committed aggregates.'));
  return svg;
}
function renderHow() {
  const root = document.getElementById('how');
  root.replaceChildren();
  root.append(el('h2', {}, 'The workflow, from ingestion to evaluation'),
    el('p', { class: 'muted' }, DATA.status ? `Each number is explained below the diagram. Status comes from the last run, finished ${dayName(DATA.status.finished_at)}.` : 'Each number is explained below the diagram.'),
    el('div', { class: 'card scroll' }, flow()));
  root.append(el('div', {}, STEPS.map((step, i) => {
    const s = step.stage ? stageOf(step.stage) : null, code = stepState(step);
    return el('div', { class: 'stepRow' }, el('div', { class: 'stepNum' }, i + 1),
      el('div', {}, el('h3', {}, step.title), el('p', {}, step.text),
        el('ul', {}, step.points.map(p => el('li', {}, typeof p === 'function' ? p() : p))),
        el('p', { class: 'small' }, el('span', { class: 'state' }, `${ICON[code]} ${STATUS[code]}`), s ? ` ${s.detail}` : '')));
  })));
  const f = (...lines) => el('div', { class: 'formula' }, lines.join('\n'));
  const card = el('div', { class: 'card' },
    el('h3', {}, 'Baseline'), el('p', {}, 'For a forecast issued on the first of month M, with a window of w months and horizon k months:'),
    f('rate = mean deaths in months M−1−w … M−2', 'baseline(k) = rate × k          k = 1, 2, 3 for 30, 60, 90 days'),
    el('h3', {}, 'VIEWS'), el('p', {}, 'Use the newest run released before the issue date with input data at least two months old. Sum predicted deaths over the province’s grid cells and the horizon’s months. The rescaled version multiplies by one factor fitted on 2023.'),
    f('VIEWS(k) = Σ cells in province  Σ months M … M+k−1  predicted deaths', `rescaled(k) = ${BT ? num(BT.views_scale) : 'c'} × VIEWS(k)`),
    el('h3', {}, 'X adjustment, fixed before any score'), el('p', {}, 'Across the six provinces with X data, compare each province’s share of X authors posting about violence with its share of baseline deaths. One is added to every count so a quiet province is not divided by zero. The square root and the limits damp the effect, and it fades with a 21-day time constant.'),
    f('m = ( x_share / baseline_share ) ^ 0.5, limited to 0.5 – 2', 'a(h) = 1 + (m − 1) · (21 / h) · (1 − exp(−h / 21))', 'baseline + X (h) = baseline(h) × a(h)'),
    el('h3', {}, 'Scoring'), el('p', {}, 'Error is measured on log deaths, so being 2× too high counts the same in a quiet province as in North Kivu. Skill compares VIEWS with the baseline. Intervals resample blocks of consecutive months, because neighbouring forecasts are not independent.'),
    f('error = mean | ln(1 + forecast) − ln(1 + actual) |', 'typical error = exp(error), the usual multiple a forecast is off by', 'VIEWS against baseline = 1 − error_VIEWS / error_baseline'));
  if (FC) {
    const pick = el('select', { 'aria-label': 'Province' });
    FC.rows.filter(r => r.x_multiplier !== null).forEach(r => pick.append(el('option', { value: r.province }, r.province)));
    const out = el('div', {});
    const draw = () => {
      const r = FC.rows.find(row => row.province === pick.value);
      const a30 = 1 + (r.x_multiplier - 1) * (21 / 30) * (1 - Math.exp(-30 / 21));
      out.replaceChildren(f(`${r.province}, issued ${dayName(FC.issued)}, 30 days`,
        `Baseline:  ${FC.window_months}-month rate through ${monthName(FC.acled_through)} = ${deaths(r.baseline_rate)} deaths a month  →  ${deaths(r.baseline_30)}`,
        `X:         ${r.x_event_authors} authors posting about violence  →  m = ${num(r.x_multiplier, 3)}, a(30) = ${num(a30, 3)}  →  ${deaths(r.x_30)}`,
        `VIEWS:     ${deaths(r.views_30)}   (rescaled ${deaths(r.views_scaled_30)})`));
    };
    pick.addEventListener('change', draw);
    card.append(el('h3', {}, 'Worked forecast example'), el('label', {}, 'Province ', pick), out);
    draw();
  }
  root.append(el('h2', {}, 'How a number is calculated'), card);
}

/* 6. Data and limits */
function term(title, paragraphs) {
  return el('div', { class: 'card term' }, el('h3', {}, title), ...paragraphs.map(text => el('p', {}, text)));
}
function renderData() {
  const root = document.getElementById('data');
  root.replaceChildren();
  const windowText = BT ? `The window is ${BT.window} month${BT.window === 1 ? '' : 's'}, chosen on the original 2023 issue dates.` : 'The window is chosen on the original 2023 issue dates.';
  const scaleText = BT ? ` That factor is ${num(BT.views_scale)}.` : '';
  const acledSpan = DATA.acled ? ` The file used here runs from ${dayName(DATA.acled.first_week)} to ${dayName(DATA.acled.coverage_end)}, and months count as finished through ${monthName(DATA.acled.complete_through)}.` : '';
  const xSpan = DATA.x ? ` This page uses ${DATA.x.posts.toLocaleString('en')} posts by ${DATA.x.authors.toLocaleString('en')} authors, ${dayName(DATA.x.first_post)} to ${dayName(DATA.x.last_post)}, read with ${DATA.x.extractor}.` : '';
  root.append(el('h2', {}, 'What the terms mean'),
    el('p', { class: 'muted' }, 'Each number on this page is one of the calculations below. The formulas are also on How it works.'));
  root.append(term('Simple forecast (baseline)', [
    `For an issue dated the first of month M, take one province and average its ACLED deaths over the last w finished months, ending at M−2. ${windowText} The month M−1 is left out because ACLED is still coding it on the issue date.`,
    'The 30-day forecast is that monthly rate. The 60-day forecast is the rate times two, and the 90-day forecast is the rate times three. There is no trend, no text, and no coefficient fitted on the years being scored.',
    `The chosen window is ${BT ? BT.window : 'not yet available'} months. The original one- and three-month scores were nearly tied (0.579 versus 0.580). The one-month reference is retained; the stricter selection-boundary check is reported separately.`,
  ]));
  root.append(term('ACLED, the outcome and the history', [
    `ACLED’s weekly aggregated export for the Democratic Republic of the Congo. Weeks start on Saturday. The primary death count is fatalities in Battles plus Explosions/Remote violence, summed to a province. A second series adds Violence against civilians and is shown only in the X check.${acledSpan}`,
    'A week that crosses a month end is split by days: three of its seven days in October contribute three sevenths of its deaths to October. Republic of Congo rows in the same download are dropped by country.',
    'A month is complete only when the file continues at least 14 days past its last day. An unfinished month is missing. It is never filled in as zero.',
  ]));
  root.append(term('VIEWS, the published forecast', [
    'The Violence & Impacts Early-Warning System, from Uppsala University and PRIO. It predicts UCDP state-based fatalities for each PRIO-GRID cell of about 0.5°, by month. That is a different coding of political violence from ACLED, which is why the national totals are not expected to match even when both are careful.',
    `For each issue date the page uses the newest VIEWS run that had already been released, and whose last input month is at least two months earlier. Each grid cell is given to the province that contains its centre. A centre that falls just outside the boundary goes to the nearest province.${DATA.views ? ` That assigns ${DATA.views.cells.toLocaleString('en')} cells, ${DATA.views.border_cells} of them by the nearest-province rule.` : ''} The province forecast is those cells, summed over the months in the horizon.`,
    `Rescaled VIEWS multiplies every such sum by one number: ACLED’s 2023 total divided by VIEWS’s 2023 total at 30 days.${scaleText} It asks whether the gap is only a difference in level. It was fit on 2023 and then frozen.`,
  ]));
  root.append(term('X, the posts', [
    `One saved search of public posts, in French and English. The query named places in the east, so posts about the Kivus and Ituri are partly there by design.${xSpan} Rules mark whether a post is about the DRC, whether it is about an old event, and whether it reports violence. Each author counts once per province. Copies of the same text count once. No post text is published.`,
    'The X check compares those author counts with ACLED deaths in the same week. It is a same-week association. It is not a test of whether posts predict a later month.',
    'The live adjustment compares each province’s share of authors who posted about violence with its share of baseline deaths, after adding one to every count. The ratio is square-rooted and limited to 0.5–2, then faded with a 21-day time constant, and multiplied by the baseline. A baseline of zero stays zero, however many authors posted. Kinshasa has 15 event authors but still predicts zero. Escalation, control-change and de-escalation tags do not enter this formula, so it cannot detect a new outbreak in a province with a zero baseline.',
  ]));
  root.append(term('The score', [
    'Log error is the average of |ln(1 + forecast) − ln(1 + actual)| over province-months. “VIEWS is 39% higher” means this average is 39% larger. It does not mean VIEWS missed 39% of the deaths. Because each province-month weighs the same, a quiet province where the simple forecast is already zero pulls the average down.',
    'Rank correlation is the average, over issue months, of the Spearman correlation between the 26 forecasts and the 26 recorded totals. It asks whether the forecast orders provinces correctly.',
    'The toll is the plain sum of deaths. For the 30-day horizon each issue is one month, so the sum does not count the same month twice. The 60- and 90-day scores include those months again.',
  ]));
  root.append(el('h2', {}, 'Sources'), table(['Source', 'What was calculated from it', 'Published here'], [
    [el('a', { href: 'https://acleddata.com', rel: 'noopener' }, 'ACLED'), 'Province-month deaths, used both as history for the simple forecast and as the outcome it is scored against.', 'Monthly totals, and one recent week of totals. No event row.'],
    [el('a', { href: 'https://viewsforecasting.org', rel: 'noopener' }, 'VIEWS'), 'The run that was public on the issue date, summed from grid cells to provinces.', 'Province totals for the months in each horizon.'],
    ['X recent search', 'Authors per province, after the rules above.', 'Counts only. No text and no account name.'],
    [el('a', { href: 'https://www.geoboundaries.org', rel: 'noopener' }, 'geoBoundaries'), 'The province polygon used to place each VIEWS cell, from OpenStreetMap.', 'A simplified outline for the map.'],
  ], 9));
  root.append(el('h2', {}, 'Rules that protect the result'), el('ul', { class: 'takeaways' }, [
    'The original one-month reference and VIEWS scale stay frozen. Selection used 2023 issue dates, including early-2024 outcomes at longer horizons; a stricter check is disclosed separately rather than changing the reference.',
    'Forecasts use only data available on the issue date: ACLED through M−2, and a VIEWS run released before that day with inputs at least two months old.',
    'Each forecast is saved with its actual creation time and evaluation type. Rerunning the pipeline does not change it.',
    'Months ACLED has not finished coding are missing, never zero.',
    'The X multiplier has not been tuned against resolved forecast scores. The November example was saved after its target period began, so it is retrospective.',
  ].map(t => el('li', {}, t))));
  root.append(el('h2', {}, 'Known limits'), el('ul', { class: 'takeaways' }, [
    'VIEWS predicts UCDP state-based deaths. ACLED’s battles and explosions are the closest series in this file, and they are not the same series. A lower VIEWS total is not, by itself, a miss.',
    'The log score rewards a forecast of zero in a quiet province. Most deaths are in a few provinces, and the national toll is the check that matches the question of how many people were killed.',
    'ACLED is a revised snapshot. Leaving out the latest month approximates what was known on each date. It does not reconstruct the file as it stood then, because older months can be recoded later.',
    'Splitting a week across a month end by days assumes the deaths were spread evenly through the week.',
    'The 60- and 90-day scores reuse the 30-day months. Agreement across the three horizons is not three separate results.',
    'One week of posts cannot be backtested. The same-week correlation, on six provinces named in the search, does not show that X improves a forecast. The multiplier also cannot raise a province the baseline has at zero.',
    'These are research estimates, not operational warnings, and should not guide safety decisions.',
  ].map(t => el('li', {}, t))));
  root.append(el('h2', {}, 'Pipeline status'));
  const stages = [DATA.ingest, ...(DATA.status ? DATA.status.stages : [])].filter(Boolean);
  root.append(stages.length ? table(['Stage', 'Status', 'Detail', 'When'], stages.map(s => [s.stage, s.status, s.detail, dayName(s.at)]), 9) : el('p', {}, 'The pipeline has not run yet.'));
}

/* Tabs */
const TABS = [['overview', 'Findings', renderOverview], ['backtest', 'Evaluation', renderBacktest], ['xcheck', 'X signal', renderX],
  ['forecast', 'Frozen forecast', renderForecast], ['how', 'Method', renderHow], ['data', 'Data & limits', renderData]];
let current = null;
function select(id) {
  for (const [tab] of TABS) {
    document.getElementById(tab).classList.toggle('on', tab === id);
    document.getElementById(`tab-${tab}`).setAttribute('aria-selected', tab === id);
    document.getElementById(`tab-${tab}`).tabIndex = tab === id ? 0 : -1;
  }
  history.replaceState(null, '', `#/${id}`);
  current = id;
  TABS.find(([tab]) => tab === id)[2]();
}
const nav = document.getElementById('tabs');
TABS.forEach(([id, label], index) => {
  const b = el('button', { role: 'tab', id: `tab-${id}`, 'aria-controls': id, 'aria-selected': 'false', tabindex: '-1' }, el('span', { class: 'nav-index' }, String(index + 1).padStart(2, '0')), label);
  b.addEventListener('click', () => select(id));
  b.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length;
    select(TABS[next][0]);
    document.getElementById(`tab-${TABS[next][0]}`).focus();
  });
  nav.append(b);
});
let resizeTimer;
addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => current && current !== 'forecast' && select(current), 200); });
const start = location.hash.slice(2);
select(TABS.some(([id]) => id === start) ? start : 'overview');
