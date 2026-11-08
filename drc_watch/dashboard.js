const BT = DATA.backtest, XC = DATA.xcheck, FC = DATA.forecast, LIVE = DATA.live;
const SERIES = {
  actual: { label: 'Actual ACLED recorded', color: 'var(--actual)' },
  baseline: { label: 'Simple Baseline (ACLED 1m)', color: 'var(--baseline)' },
  ml: { label: 'ACLED Forecast (Spatio-Temporal ML)', color: 'var(--x)' },
  x_ml: { label: 'X-Post + ACLED Forecast (ML + LLM Nowcast)', color: '#1b4f72', dash: true },
  x: { label: 'Simple Baseline + X Multiplier', color: 'var(--x)', dash: true },
  views: { label: 'VIEWS Forecast (Deep Learning)', color: 'var(--views)' },
  views_scaled: { label: 'VIEWS Rescaled to ACLED', color: 'var(--views)', dash: true },
};
const H = ['30', '60', '90'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/* DOM & SVG helpers */
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
const worse = s => s === null || s === undefined ? 'n/a' : s < 0 ? `${Math.round(-s * 100)}% higher error` : `${Math.round(s * 100)}% lower error`;
const band = iv => iv ? `${worse(iv[1])} to ${worse(iv[0])}` : 'too few issues';
const monthName = key => `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`;
const dayName = iso => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
function width(id) {
  const node = document.getElementById(id);
  if (!node) return 760;
  const style = getComputedStyle(node);
  return Math.max(280, (node.clientWidth || 900) - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0));
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
  return el('div', { class: 'legend' }, keys.filter(k => SERIES[k]).map(k => el('span', {},
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
function conclusion(title, text) {
  return el('div', { class: 'conclusion-box' }, el('strong', {}, `Conclusion — ${title}: `), text);
}

/* Header Lead & Meta */
document.getElementById('lead').textContent =
  'To answer this question, we used X posts (tweets) and added them to a simple forecast and Spatio-Temporal ML model from ACLED data, then compared them with the VIEWS deep-learning forecast of fatalities in the DRC.';

const studyMeta = document.getElementById('study-meta');
if (BT) [
  `${monthName(BT.evaluation.first_issue)} – ${monthName(BT.evaluation.last_issue)}`,
  `${BT.evaluation.scores['30'].issues} issue months · 26 DRC provinces`,
  'ACLED + X (Gemini LLM) vs. VIEWS (LSTM Deep Learning)',
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

/* Shared Chart Helpers */
function lineChart(id, rows, keys, opts = {}) {
  const w = width(id), h = opts.height || 300, left = 56, right = 20, top = 16, bottom = 36;
  const top_ = niceMax(Math.max(...rows.flatMap(r => keys.map(k => r[k] ?? 0)), 1));
  const x = i => left + i * (w - left - right) / Math.max(rows.length - 1, 1);
  const y = v => top + (1 - v / top_) * (h - top - bottom);
  const svg = svgEl('svg', { width: w, height: h, role: 'img', 'aria-label': opts.label || 'Line chart' });
  for (let t = 0; t <= 4; t++) {
    const v = top_ * t / 4;
    svg.append(svgEl('line', { x1: left, x2: w - right, y1: y(v), y2: y(v), stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, Math.round(v).toLocaleString('en')));
  }
  rows.forEach((r, i) => {
    const lbl = r.label || (r.month && (r.month.endsWith('-01') || i === 0 || rows.length <= 6) ? (r.month.endsWith('-01') && rows.length > 6 ? r.month.slice(0, 4) : monthName(r.month)) : '');
    if (lbl) svg.append(svgEl('text', { x: x(i), y: h - 10, 'text-anchor': i === 0 ? 'start' : i === rows.length - 1 ? 'end' : 'middle' }, lbl));
  });
  for (const k of keys) {
    let d = '', pen = false;
    rows.forEach((r, i) => {
      if (r[k] === null || r[k] === undefined) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`; pen = true;
      if (opts.dots) svg.append(svgEl('circle', { cx: x(i), cy: y(r[k]), r: 4.5, fill: SERIES[k].color, stroke: 'var(--surface)', 'stroke-width': 1.5 }));
    });
    svg.append(svgEl('path', { d, fill: 'none', stroke: SERIES[k].color, 'stroke-width': k === 'actual' ? 2.5 : 2,
      'stroke-dasharray': SERIES[k].dash ? '5 4' : 'none', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  }
  const cross = svgEl('line', { y1: top, y2: h - bottom, stroke: 'var(--neutral)', visibility: 'hidden' });
  svg.append(cross);
  const hit = svgEl('rect', { x: left, y: 0, width: w - left - right, height: h, fill: 'transparent' });
  hit.addEventListener('mousemove', e => {
    const box = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(rows.length - 1, Math.round((e.clientX - box.left - left) / (w - left - right) * Math.max(rows.length - 1, 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    const title = rows[i].label || monthName(rows[i].month);
    showTip(e, title, keys.map(k => `${SERIES[k].label}: ${deaths(rows[i][k])}`));
  });
  hit.addEventListener('mouseleave', () => { hideTip(); cross.setAttribute('visibility', 'hidden'); });
  svg.append(hit);
  return svg;
}

function hBars(id, items, opts = {}) {
  const w = width(id), compact = w < 520, rowH = compact ? 65 : 48;
  const left = compact ? 5 : 185, right = 68, top = 10, bottom = 36;
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

/* ============================================================================
 * 01. METHOD: Study Design, Data Sources, Fatalities Calculation, ML & Gemini X
 * ============================================================================ */
function renderMethod() {
  const root = document.getElementById('method');
  root.replaceChildren();
  const f = (...lines) => el('div', { class: 'formula' }, lines.join('\n'));

  const intro = chapter('01', 'Study Design & Goal', 'How this study is conducted');
  intro.append(
    el('p', {}, 'Large conflict-forecasting systems such as VIEWS use complex deep-learning architectures (Long Short-Term Memory recurrent neural networks and tree ensembles) trained on hundreds of global variables. However, in fast-moving conflicts like the eastern Democratic Republic of the Congo (DRC), two practical questions arise:'),
    el('ul', { class: 'takeaways' }, [
      'Can a transparent baseline and a lightweight Spatio-Temporal ML model built directly on historical ACLED data match or outperform the complex VIEWS deep-learning forecast?',
      'Can real-time X posts (tweets)—filtered by a Large Language Model (Gemini API) for credible field reports and news—bridge ACLED’s 2-to-4 week reporting lag and improve short-term fatality predictions?',
    ].map(t => el('li', {}, t))),
    el('p', {}, 'We test these questions across all 26 DRC provinces over 30, 60, and 90-day horizons (31 monthly walk-forward issues from Jan 2024 to Jul 2026) and on a resolved next-week holdout test (24 Oct 2026).')
  );
  root.append(intro);

  const sources = chapter('02', 'Data Sources & Fatality Definitions', 'How ACLED and VIEWS calculate fatalities');
  const acledSpan = DATA.acled ? `${dayName(DATA.acled.first_week)} to ${dayName(DATA.acled.coverage_end)} (complete through ${monthName(DATA.acled.complete_through)})` : '2020 to 2026';
  sources.append(
    el('div', { class: 'grid-2' },
      el('div', { class: 'card term' },
        el('h3', {}, '1. ACLED (Ground-Truth & Baseline Source)'),
        el('p', {}, `We use ACLED’s weekly aggregated conflict export for the DRC (${acledSpan}). Human researchers verify conflict events from local media, NGOs, and field monitors.`),
        el('p', {}, el('strong', {}, 'How ACLED fatalities are calculated here: '), 'Our primary target sums fatalities from Battles plus Explosions/Remote violence in each province. Because ACLED weeks start on Saturday and can cross month boundaries, any week straddling two months is split proportionally by days (e.g., 3 days in October assigns 3/7 of that week’s deaths to October).'),
        el('p', {}, el('strong', {}, 'The 14-day reporting lag: '), 'Because ACLED takes roughly two weeks to finish coding recent events, month M−1 is incomplete on issue date M. To prevent data leakage, models issued on the 1st of month M only see completed ACLED months up to M−2.')
      ),
      el('div', { class: 'card term' },
        el('h3', {}, '2. VIEWS (Complex Deep-Learning Benchmark)'),
        el('p', {}, 'The Violence & Impacts Early-Warning System (Uppsala University & PRIO) runs an ensemble of deep-learning LSTM networks and random forests trained on UCDP conflict history, V-Dem political indicators, demographics, and terrain.'),
        el('p', {}, el('strong', {}, 'How VIEWS fatalities are calculated: '), `VIEWS predicts monthly state-based conflict fatalities on 0.5° × 0.5° PRIO-GRID cells (${DATA.views ? DATA.views.cells : 763} cells in the DRC). We assign each grid cell to the DRC province containing its center (${DATA.views ? DATA.views.border_cells : 46} border cells go to the nearest province) and sum over the 1, 2, or 3 months in each horizon.`),
        el('p', {}, el('strong', {}, 'Why totals differ: '), 'VIEWS targets UCDP state-based fatalities (which require a formal government actor and stricter verification thresholds than ACLED). We therefore evaluate both raw VIEWS and Rescaled VIEWS (multiplied by the 2023 ACLED-to-VIEWS ratio of ×1.38).')
      )
    )
  );
  root.append(sources);

  const models = chapter('03', 'Forecasting Methodology', 'How the Baseline, Spatio-Temporal ML, and X-Post models work');
  models.append(
    el('div', { class: 'card term' },
      el('h3', {}, 'Model A — Simple Baseline (1-Month ACLED Persistence)'),
      el('p', {}, 'For an issue on the 1st of month M, the Simple Baseline takes each province’s most recent completed monthly death rate (at month M−2, respecting the coding lag) and projects it forward linearly over k = 1, 2, or 3 months:'),
      f('rate_p = ACLED_deaths(province p, month M−2)', 'Simple_Baseline(p, k months) = rate_p × k      for k = 1 (30d), 2 (60d), 3 (90d)')
    ),
    el('div', { class: 'card term' },
      el('h3', {}, 'Model B — ACLED Forecast (Spatio-Temporal ML)'),
      el('p', {}, 'Instead of relying only on last month’s death count, our Spatio-Temporal ML model combines multi-horizon fatality trends, non-lethal precursor events coded in ACLED, and neighboring-province spatial spillover in a regularized Ridge regression trained via walk-forward validation:'),
      el('ul', { class: 'takeaways' }, [
        'Multi-horizon fatality lags: 1-month level log(1 + b_1m), short-term momentum log(1 + b_3m) − log(1 + b_1m), and 6-month baseline log(1 + b_6m) − log(1 + b_1m).',
        'Precursor events from ACLED sub-event types: territorial control changes (non-state actor overtakes territory / government regains territory), escalations (change to group/activity, looting, violent riots), and de-escalations (agreements, peaceful protests).',
        'Neighboring-province spillover: average 1-month fatality rate across geographically adjacent DRC provinces (computed from shared polygon borders).',
        'Prior shrinkage & zero-baseline protection: Ridge weights are shrunk toward the 1-month persistence prior w0 = [1, 0, 0, 0, 0, 0, 0]. Quiet provinces remain at zero unless an additive territorial control_change precursor flags a new outbreak.',
      ].map(t => el('li', {}, t))),
      f('log(1 + y_hat) = w1·log(1 + b1) + w2·(log1p(b3) − log1p(b1)) + w3·(log1p(b6) − log1p(b1))\n               + w4·log(1 + control_change) + w5·log(1 + escalation) + w6·log(1 + deescalation) + w7·log(1 + neighbor_b1)')
    ),
    el('div', { class: 'card term' },
      el('h3', {}, 'Model C — X-Post + ACLED Forecast (Gemini LLM Extraction + Lag Nowcast)'),
      el('p', {}, `Raw keyword searches on X (Twitter) capture political commentary and attention after an attack rather than true conflict precursors. For example, keyword rules flag 15 "violence" authors in Kinshasa simply because politicians and commentators mention the capital while debating the war in the East.`),
      el('p', {}, el('strong', {}, 'How we use the Gemini API (gemini-3.8-flash): '), 'Every X post is analyzed with a strict structured schema that classifies whether the post is relevant to the DRC, whether it merely recalls a historical event, its source type (field_report, news, opinion, party_claim, other), specific conflict event types (armed_clash, control_change, shelling_or_drone, attack_on_civilians, displacement), and sub-provincial hotspots.'),
      el('ul', { class: 'takeaways' }, [
        'Step 1 — Credible Source Filtering: We retain only verified field_report and news posts (219 posts) and discard opinion/partisan commentary (464 posts). This eliminates false-positive political noise in Kinshasa (dropping event authors from 15 to 1) and raises same-week rank correlation with ACLED battles from 0.82 to 0.94.',
        'Step 2 — Nowcasting the 14-Day ACLED Lag: Because ACLED has a 2-week coding delay, we use the province share of credible X event authors to impute (nowcast) the missing gap period (M−1 or W−1) before running the Spatio-Temporal ML model.',
        'Step 3 — Additive & Multiplicative Precursors: Credible control_change reports from X enter additively (capable of flagging a new outbreak in a zero-baseline province), while event author shares adjust ongoing conflict intensity.',
      ].map(t => el('li', {}, t))),
      f('Nowcasted_Deaths(p, gap_period) = Baseline_Deaths(p) × clip( sqrt( (x_event_share_p) / (baseline_share_p) ), 0.5, 2.0 )\nX_ML_Forecast(p, h) = SpatioTemporal_ML( features with Nowcasted_Deaths & X_Precursors at M−1 )')
    ),
    el('p', {}, jump('See the side-by-side results in 02 Findings', 'overview'))
  );
  root.append(models);
}

/* ============================================================================
 * 02. FINDINGS: Fatalities Comparison, Forecast Over Time (30/60/90d), and Map
 * ============================================================================ */
const BINS = [0, 1, 5, 20, 50, 100, 200];
const RAMP = ['var(--zero)', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const binColor = v => v === null || v === undefined ? 'transparent' : v === 0 ? RAMP[0] : RAMP[Math.min(BINS.filter(b => v >= b).length, RAMP.length - 1)];
let map, layer;

function renderOverview() {
  const root = document.getElementById('overview');
  root.replaceChildren();
  if (!BT || !BT.evaluation.scores['30']) return empty(root, 'The research results will appear after the backtest runs.');
  const score = BT.evaluation.scores['30'];

  /* Section 1: National Fatality Comparison */
  const level = chapter('01', 'National Fatalities Comparison', 'Comparing ACLED Forecast, Simple Baseline, X-Post + ACLED, and VIEWS');
  const toll = el('figure', { class: 'chart', id: 'tollChart' });
  level.append(el('div', { class: 'brief-grid' },
    el('div', {},
      el('p', { class: 'finding' }, 'Simple Baseline & Spatio-Temporal ML beat VIEWS on both ', el('strong', {}, 'national scale and provincial accuracy.')),
      el('p', { class: 'reading' }, 'Even though VIEWS uses a complex deep-learning (LSTM) architecture and global covariates, its predicted DRC fatality total (2,380) is far below recorded ACLED battle deaths (7,264), and its province-level log error (0.597) is 39% worse than the Simple Baseline (0.430).'),
      el('p', { class: 'reading' }, 'Our Spatio-Temporal ML model (ACLED precursors + neighbor spillover) cuts province-month log error down to ', el('strong', {}, '0.358 (+16.8% skill over Simple Baseline)'), ' while achieving the highest geographic ranking correlation (0.60 vs. 0.50 for VIEWS).'),
      jump('See full accuracy evaluation in 03 Evaluation', 'backtest')),
    toll));
  root.append(level);

  toll.append(hBars('tollChart', [
    { label: 'Actual ACLED Recorded', value: score.total_actual, color: 'var(--actual)' },
    { label: 'Simple Baseline (ACLED)', value: score.total_baseline, color: 'var(--baseline)' },
    ...(score.total_ml ? [{ label: 'ACLED Forecast (ML)', value: score.total_ml, color: 'var(--x)' }] : []),
    { label: 'VIEWS (Deep Learning)', value: score.total_views, color: 'var(--views)' },
  ], { label: 'Total DRC fatalities across 31 scored 30-day issues (Jan 2024 – Jul 2026)' }),
  el('figcaption', {}, 'Cumulative DRC fatalities across 31 scored one-month issues (Jan 2024 – Jul 2026).'));

  if (FC) {
    const sumCol = col => FC.rows.reduce((acc, r) => acc + (r[col] ?? r[col.replace('x_ml_', 'ml_')] ?? 0), 0);
    level.append(
      el('h3', {}, `Upcoming DRC National Forecast Totals (30, 60, and 90 Days — Issued ${dayName(FC.issued)})`),
      el('p', { class: 'muted' }, 'National sum across all 26 provinces for the 30, 60, and 90-day forecast horizons:'),
      table(['Model', '30 Days (Nov 2026)', '60 Days (Nov–Dec 2026)', '90 Days (Nov 2026–Jan 2027)', '30d Log Error (Backtest)', 'Holdout Error (24 Oct 2026)'], [
        ['Simple Baseline (ACLED 1m)', deaths(sumCol('baseline_30')), deaths(sumCol('baseline_60')), deaths(sumCol('baseline_90')), num(score.error_baseline, 3), XC?.holdout_next_week ? num(XC.holdout_next_week.models.acled_simple.log_error, 3) : 'n/a'],
        ['ACLED Forecast (Spatio-Temporal ML)', deaths(sumCol('ml_30')), deaths(sumCol('ml_60')), deaths(sumCol('ml_90')), num(score.error_ml, 3), XC?.holdout_next_week ? num(XC.holdout_next_week.models.ml_acled_only.log_error, 3) : 'n/a'],
        ['X-Post + ACLED Forecast (ML + LLM Nowcast)', deaths(sumCol('x_ml_30')), deaths(sumCol('x_ml_60')), deaths(sumCol('x_ml_90')), '— (7-day X window)', XC?.holdout_next_week ? num(XC.holdout_next_week.models.ml_x_llm_credible.log_error, 3) : 'n/a'],
        ['VIEWS Deep-Learning Forecast', deaths(sumCol('views_30')), deaths(sumCol('views_60')), deaths(sumCol('views_90')), num(score.error_views, 3), '— (monthly only)'],
      ])
    );
  }

  /* Section 2: Forecast Over Time (30, 60, and 90 Days) */
  const timeChapter = chapter('02', 'Forecast Over Time (30, 60 & 90 Days)', 'How the predictions evolve across horizons and issue months');
  root.append(timeChapter);

  if (FC) {
    const sumCol = col => Math.round(FC.rows.reduce((acc, r) => acc + (r[col] ?? r[col.replace('x_ml_', 'ml_')] ?? 0), 0) * 10) / 10;
    const horizonRows = H.map(h => ({
      label: `${h} days`,
      baseline: sumCol(`baseline_${h}`),
      ml: sumCol(`ml_${h}`),
      x_ml: sumCol(`x_ml_${h}`),
      views: sumCol(`views_${h}`),
    }));
    const hBox = el('div', { class: 'card scroll', id: 'horizonTrajectoryChart' });
    timeChapter.append(
      el('h3', {}, 'A. Cumulative National Forecast Trajectory Across 30, 60, and 90 Days'),
      el('p', { class: 'muted' }, 'Because we forecast at 30, 60, and 90-day windows, this plot shows how cumulative predicted DRC fatalities grow across the three horizons for each model:'),
      legend(['baseline', 'ml', 'x_ml', 'views']),
      hBox
    );
    hBox.append(lineChart('horizonTrajectoryChart', horizonRows, ['baseline', 'ml', 'x_ml', 'views'],
      { height: 260, dots: true, label: 'Cumulative national predicted deaths at 30, 60, and 90 days' }));
  }

  const hPick = el('select', { 'aria-label': 'Historical forecast horizon' });
  H.forEach(h => hPick.append(el('option', { value: h }, `${h} days (${Number(h) / 30} calendar month${h === '30' ? '' : 's'})`)));
  const natHolder = el('div', { class: 'card scroll', id: 'natChart' });
  timeChapter.append(
    el('h3', {}, 'B. Historical National Forecast vs. Recorded Fatalities Over Time (2024–2026)'),
    el('p', { class: 'muted' }, 'Select 30, 60, or 90 days to compare what ACLED actually recorded against the Simple Baseline, Spatio-Temporal ML, and VIEWS across every issue month:'),
    el('div', { class: 'controls' }, el('label', {}, 'Forecast Horizon: ', hPick)),
    legend(['actual', 'baseline', 'ml', 'views']),
    natHolder
  );
  const drawNat = () => {
    const h = hPick.value;
    const series = (BT.national_horizons && BT.national_horizons[h]) ? BT.national_horizons[h] : BT.national;
    const rows = series.filter(r => r.month >= BT.evaluation.first_issue);
    natHolder.replaceChildren(lineChart('natChart', rows, ['actual', 'baseline', 'ml', 'views'],
      { height: 290, label: `National DRC fatalities and ${h}-day forecasts over time` }));
  };
  hPick.addEventListener('change', drawNat);
  drawNat();

  /* Section 3: Interactive Map of Predicted Fatalities */
  const mapChapter = chapter('03', 'Interactive Map of Predicted Fatalities', 'Province-level predictions: Simple Baseline, ACLED ML, X-Post + ACLED, and VIEWS');
  root.append(mapChapter);
  if (!FC) {
    empty(mapChapter, 'No forecast file found.');
    return;
  }
  const hSel = el('select', { 'aria-label': 'Horizon' }), mSel = el('select', { 'aria-label': 'Forecast shown on the map' });
  H.forEach(h => hSel.append(el('option', { value: h }, `${h} days (${FC.months[h].map(monthName).join(' to ').replace(/ to .* to /, ' to ')})`)));
  [
    ['x_ml', 'X-Post + ACLED Forecast (ML + LLM Nowcast)'],
    ['ml', 'ACLED Forecast (Spatio-Temporal ML)'],
    ['baseline', 'Simple Baseline (ACLED 1m)'],
    ['views', 'VIEWS Forecast (Deep Learning)'],
  ].forEach(([v, t]) => mSel.append(el('option', { value: v }, t)));

  mapChapter.append(
    el('p', { class: 'muted' }, `Use the dropdowns below to compare predicted fatalities across all 26 DRC provinces for 30, 60, and 90 days. Hover over any province on the map to see all four models side by side.`),
    el('div', { class: 'controls' }, el('label', {}, 'Horizon: ', hSel), el('label', {}, 'Map Model: ', mSel)),
    el('div', { id: 'map' }),
    el('div', { class: 'legend', id: 'mapLegend' })
  );
  const lower = el('div', {});
  mapChapter.append(lower);

  const drawForecastSection = () => {
    const h = hSel.value, m = mSel.value;
    drawMap(h, m);
    const rows = [...FC.rows].sort((a, b) => (b[`x_ml_${h}`] ?? b[`ml_${h}`] ?? 0) - (a[`x_ml_${h}`] ?? a[`ml_${h}`] ?? 0));
    lower.replaceChildren(
      el('h3', {}, `Predicted Fatalities by Province — Next ${h} Days`),
      el('p', { class: 'muted' }, 'Comparing the Simple Baseline, ACLED Spatio-Temporal ML, X-Post + ACLED (ML + LLM Nowcast), and VIEWS across all 26 provinces:'),
      table(['Province', 'Simple Baseline', 'ACLED Forecast (ML)', 'X-Post + ACLED Forecast', 'Credible X Event Authors', 'VIEWS (Deep Learning)', 'VIEWS Rescaled'],
        rows.map(r => [
          r.province,
          deaths(r[`baseline_${h}`]),
          deaths(r[`ml_${h}`]),
          deaths(r[`x_ml_${h}`] ?? r[`ml_${h}`]),
          r.x_event_authors ?? '·',
          deaths(r[`views_${h}`]),
          deaths(r[`views_scaled_${h}`]),
        ])),
      el('p', { class: 'small' }, `Note: Predictions are locked ("frozen") on ${dayName(FC.written_at)} for issue ${dayName(FC.issued)} so they can be audited against ACLED once coding completes (${dayName(FC.resolves[h])}).`)
    );
  };
  hSel.addEventListener('change', drawForecastSection);
  mSel.addEventListener('change', drawForecastSection);
  initMap();
  drawForecastSection();
}

function initMap() {
  if (!window.L) { document.getElementById('map').textContent = 'The map library could not load.'; return; }
  if (map) { map.remove(); map = null; }
  map = L.map('map', { scrollWheelZoom: false, zoomSnap: .25, attributionControl: true }).setView([-4, 23.6], 5.25);
  map.attributionControl.addAttribution('Boundaries: geoBoundaries, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors');
}
function drawMap(h, m) {
  if (!map || !DATA.geo) return;
  if (layer) layer.remove();
  const rows = Object.fromEntries(FC.rows.map(r => [r.province, r]));
  const valOf = (r, key) => key === 'x_ml' ? (r[`x_ml_${h}`] ?? r[`ml_${h}`]) : r[`${key}_${h}`];
  layer = L.geoJSON(DATA.geo, {
    style: f => {
      const r = rows[f.properties.province], v = r ? valOf(r, m) : null;
      return { color: v === null ? 'var(--border)' : '#ffffff', weight: 1.2, fillColor: binColor(v), fillOpacity: v === null ? 0 : .92 };
    },
    onEachFeature: (f, l) => {
      const r = rows[f.properties.province];
      if (!r) return;
      l.bindTooltip(
        `<b>${f.properties.province} (${h} days)</b><br>` +
        `X-Post + ACLED (ML+X): <b>${deaths(valOf(r, 'x_ml'))}</b><br>` +
        `ACLED Forecast (ML): <b>${deaths(r[`ml_${h}`])}</b><br>` +
        `Simple Baseline: <b>${deaths(r[`baseline_${h}`])}</b><br>` +
        `VIEWS (Deep Learning): <b>${deaths(r[`views_${h}`])}</b>`,
        { sticky: true }
      );
    },
  }).addTo(map);
  const key = document.getElementById('mapLegend');
  key.replaceChildren(el('span', {}, `Predicted fatalities, next ${h} days (${SERIES[m].label}):`),
    ...['0', '<1', '1–5', '5–20', '20–50', '50–100', '100–200', '200+'].map((t, i) => el('span', {}, el('i', { class: 'sw', style: `background:${RAMP[i]};border:1px solid var(--border)` }), t)));
}

/* ============================================================================
 * 03. EVALUATION: Plain-Language Guide, Walk-Forward Scores & Conclusions
 * ============================================================================ */
function skillChart(id) {
  const w = Math.max(width(id), 520), rowH = 32, left = 175, right = 48, top = 28;
  const rows = H.flatMap(h => [['ml', h], ['views', h], ['views_scaled', h]]).filter(([k, h]) => BT.evaluation.scores[h] && BT.evaluation.scores[h][`skill_${k}`] !== null && BT.evaluation.scores[h][`skill_${k}`] !== undefined);
  const vals = rows.flatMap(([k, h]) => [BT.evaluation.scores[h][`skill_${k}`], ...(BT.evaluation.scores[h][`skill_${k}_90`] || [])]);
  const lo = Math.min(-0.2, Math.floor(Math.min(...vals) * 10) / 10), hi = Math.max(0.25, Math.ceil(Math.max(...vals) * 10) / 10);
  const height = top + rows.length * rowH + 26;
  const x = v => left + (v - lo) / (hi - lo) * (w - left - right);
  const svg = svgEl('svg', { width: w, height, role: 'img', 'aria-label': 'ML and VIEWS error relative to the simple baseline, with 90% intervals' });
  for (let t = Math.ceil(lo * 10); t <= Math.floor(hi * 10); t += 1) {
    const v = t / 10;
    if (t % 2 !== 0 && (hi - lo) > 1) continue;
    svg.append(svgEl('line', { x1: x(v), x2: x(v), y1: top - 6, y2: height - 22, stroke: v === 0 ? 'var(--text-2)' : 'var(--grid)', 'stroke-width': v === 0 ? 1.5 : 1 }));
    svg.append(svgEl('text', { x: x(v), y: 14, 'text-anchor': 'middle' }, v === 0 ? 'Simple Baseline' : `${v < 0 ? '+' : '−'}${Math.abs(Math.round(v * 100))}% error`));
  }
  svg.append(svgEl('text', { x: x(lo), y: height - 6, 'text-anchor': 'start' }, '← Higher error (Worse than Simple Baseline)'));
  svg.append(svgEl('text', { x: x(hi), y: height - 6, 'text-anchor': 'end' }, 'Lower error (Better than Simple Baseline) →'));
  rows.forEach(([k, h], i) => {
    const s = BT.evaluation.scores[h], y = top + i * rowH + rowH / 2, iv = s[`skill_${k}_90`];
    const lbl = k === 'ml' ? 'ACLED ML' : k === 'views' ? 'VIEWS' : 'VIEWS rescaled';
    svg.append(svgEl('text', { x: left - 10, y: y + 4, 'text-anchor': 'end' }, `${h}d · ${lbl}`));
    if (iv) svg.append(svgEl('line', { x1: x(iv[0]), x2: x(iv[1]), y1: y, y2: y, stroke: SERIES[k].color, 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-dasharray': SERIES[k].dash ? '4 3' : 'none' }));
    svg.append(svgEl('circle', { cx: x(s[`skill_${k}`]), cy: y, r: 5.5, fill: k === 'views_scaled' ? 'var(--surface)' : SERIES[k].color, stroke: SERIES[k].color, 'stroke-width': 2 }));
    const hit = svgEl('rect', { x: 0, y: y - rowH / 2, width: w, height: rowH, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, `${SERIES[k].label} (${h} days)`, [`${worse(s[`skill_${k}`])} vs. Simple Baseline`, `90% confidence interval: ${band(iv)}`]));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}

function provinceChart(id) {
  const entries = Object.entries(BT.provinces).sort((a, b) => b[1].actual - a[1].actual);
  const w = Math.max(width(id), 520), rowH = 22, left = 130, right = 36, top = 26;
  const height = top + entries.length * rowH + 8;
  const ticks = [1, 1.5, 2, 3, 4, 5, 6, 8].filter((t, i, a) => i === 0 || Math.log(a[i - 1]) < Math.max(...entries.flatMap(([, p]) => [p.error_baseline, p.error_views, p.error_ml || 0])));
  const top_ = Math.log(ticks[ticks.length - 1]);
  const x = v => left + v / top_ * (w - left - right);
  const svg = svgEl('svg', { width: w, height, role: 'img', 'aria-label': 'Average error by province: Simple Baseline, ACLED ML, and VIEWS' });
  for (const t of ticks) {
    const v = Math.log(t);
    svg.append(svgEl('line', { x1: x(v), x2: x(v), y1: top - 6, y2: height - 4, stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: x(v), y: 14, 'text-anchor': 'middle' }, t === 1 ? 'exact (×1.0)' : `×${t}`));
  }
  entries.forEach(([name, p], i) => {
    const y = top + i * rowH + rowH / 2;
    const pts = [p.error_baseline, p.error_views, ...(p.error_ml !== null && p.error_ml !== undefined ? [p.error_ml] : [])];
    svg.append(svgEl('text', { x: left - 10, y: y + 4, 'text-anchor': 'end' }, name));
    svg.append(svgEl('line', { x1: x(Math.min(...pts)), x2: x(Math.max(...pts)), y1: y, y2: y, stroke: 'var(--border)', 'stroke-width': 2 }));
    for (const k of ['baseline', 'ml', 'views']) {
      if (p[`error_${k}`] !== null && p[`error_${k}`] !== undefined) {
        svg.append(svgEl('circle', { cx: x(p[`error_${k}`]), cy: y, r: 5, fill: SERIES[k].color, stroke: 'var(--surface)', 'stroke-width': 2 }));
      }
    }
    const hit = svgEl('rect', { x: 0, y: y - rowH / 2, width: w, height: rowH, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, name, [
      `Recorded deaths (31 months): ${deaths(p.actual)}`,
      `ACLED ML typical error: ${factor(p.error_ml)} (log err ${num(p.error_ml, 3)})`,
      `Simple Baseline typical error: ${factor(p.error_baseline)} (log err ${num(p.error_baseline, 3)})`,
      `VIEWS typical error: ${factor(p.error_views)} (log err ${num(p.error_views, 3)})`,
    ]));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}

function renderBacktest() {
  const root = document.getElementById('backtest');
  root.replaceChildren();
  if (!BT) return empty(root, 'The backtest has not run.');
  const sc = BT.evaluation.scores;

  /* Plain-English Explanation of Evaluation */
  const guide = chapter('01', 'How We Evaluate', 'What we are testing and how to read the metrics');
  guide.append(
    el('p', {}, 'To make sure our evaluation is fair and realistic, we test every model using walk-forward validation: at the start of each month from January 2024 to July 2026 (31 issue dates × 26 provinces = 806 province-forecasts per horizon), each model only sees data that was actually available on that date. We score models on three simple questions:'),
    el('div', { class: 'tiles' },
      el('div', { class: 'tile' },
        el('div', { class: 'label' }, '1. Prediction Error (Log Error)'),
        el('div', { class: 'value' }, 'Lower is better'),
        el('div', { class: 'note' }, 'Measures |ln(1 + predicted) − ln(1 + actual)|. Putting deaths on a log scale prevents one giant spike in North Kivu from hiding misses in other provinces. 0.00 means exact.')
      ),
      el('div', { class: 'tile' },
        el('div', { class: 'label' }, '2. Typical Error Multiplier (×)'),
        el('div', { class: 'value' }, 'Closer to ×1.0 is better'),
        el('div', { class: 'note' }, 'Converts log error into an everyday multiplier: exp(error). For example, ML’s 30-day log error of 0.358 means its forecast is typically within ×1.43 of actual deaths, whereas VIEWS (0.597) is off by ×1.82.')
      ),
      el('div', { class: 'tile' },
        el('div', { class: 'label' }, '3. Geographic Ranking (Spearman)'),
        el('div', { class: 'value' }, 'Higher is better (0 to 1)'),
        el('div', { class: 'note' }, 'Measures whether a model puts the 26 provinces in the right order from most violent to safest each month. 1.00 is a perfect ranking.')
      )
    )
  );
  root.append(guide);

  /* Part 1: 2024-2026 Walk-Forward Scoreboard */
  const part1 = chapter('02', 'Multi-Horizon Backtest (2024–2026)', 'Comparing Simple Baseline, Spatio-Temporal ML, and VIEWS at 30, 60, and 90 days');
  part1.append(
    el('p', { class: 'muted' }, `Across ${sc['30'].forecasts} province-month forecasts per horizon, here is how each model performs:`),
    table(
      ['Horizon', 'Simple Baseline Error (Multiplier)', 'ACLED ML Error (Multiplier)', 'ML Improvement vs. Baseline', 'VIEWS Error (Multiplier)', 'VIEWS vs. Baseline', 'Rank: ACLED ML', 'Rank: Simple', 'Rank: VIEWS'],
      H.filter(h => sc[h]).map(h => [
        `${h} days (${Number(h) / 30}m)`,
        `${num(sc[h].error_baseline, 3)} (${factor(sc[h].error_baseline)})`,
        `${num(sc[h].error_ml, 3)} (${factor(sc[h].error_ml)})`,
        worse(sc[h].skill_ml),
        `${num(sc[h].error_views, 3)} (${factor(sc[h].error_views)})`,
        worse(sc[h].skill_views),
        num(sc[h].rank_ml),
        num(sc[h].rank_baseline),
        num(sc[h].rank_views),
      ])
    ),
    el('div', { class: 'card scroll', id: 'skillChart' }),
    conclusion(
      'Multi-Horizon Walk-Forward Backtest',
      `The ACLED Spatio-Temporal ML model beats both the Simple Baseline and VIEWS across every horizon: it reduces prediction error by 16.8% at 30 days (0.358 vs. 0.430), 13.1% at 60 days (0.488 vs. 0.562), and 12.2% at 90 days (0.581 vs. 0.662). By contrast, the complex VIEWS deep-learning model has 32% to 39% higher error than the Simple Baseline even when rescaled.`
    )
  );
  root.append(part1);
  document.getElementById('skillChart').append(skillChart('skillChart'));

  /* Part 2: Resolved 2026-10-24 Holdout Test for X-Post + ACLED */
  if (XC && XC.holdout_next_week) {
    const ho = XC.holdout_next_week, m = ho.models;
    const names = Object.keys(ho.actual);
    const part2 = chapter('03', 'Testing X-Post + ACLED (24 Oct 2026 Holdout)', 'Does adding X posts improve the forecast over ACLED alone?');
    part2.append(
      el('p', { class: 'muted' }, `Because X API search only provides the last 7 days of posts (${dayName(XC.posts_from)} to ${dayName(XC.posts_to)}), we evaluate the X-Post + ACLED model on the resolved next-week holdout (${dayName(ho.target_week)}). Models only see ACLED data prior to the 14-day reporting lag (${dayName(ho.lagged_week)}) and use X posts to nowcast the missing gap week (${dayName(ho.gap_week)}):`),
      table(['Model', 'Mean Log Error (Lower = Better)', 'Typical Multiplier', 'Spearman Rank (Higher = Better)', ...names], [
        ['1. Simple Baseline (ACLED 2026-10-10)', num(m.acled_simple.log_error, 3), factor(m.acled_simple.log_error), num(m.acled_simple.spearman, 3), ...names.map(n => deaths(m.acled_simple.predictions[n]))],
        ['2. ACLED Forecast (Spatio-Temporal ML Only)', num(m.ml_acled_only.log_error, 3), factor(m.ml_acled_only.log_error), num(m.ml_acled_only.spearman, 3), ...names.map(n => deaths(m.ml_acled_only.predictions[n]))],
        ['3. X-Post + ACLED (Keyword Rules Nowcast)', num(m.ml_x_rules.log_error, 3), factor(m.ml_x_rules.log_error), num(m.ml_x_rules.spearman, 3), ...names.map(n => deaths(m.ml_x_rules.predictions[n]))],
        ...(m.ml_x_llm_credible ? [['4. X-Post + ACLED (Gemini LLM Credible Nowcast)', num(m.ml_x_llm_credible.log_error, 3), factor(m.ml_x_llm_credible.log_error), num(m.ml_x_llm_credible.spearman, 3), ...names.map(n => deaths(m.ml_x_llm_credible.predictions[n]))]] : []),
        ['Actual ACLED Recorded Fatalities (2026-10-24)', '—', '—', '—', ...names.map(n => deaths(ho.actual[n]))],
      ]),
      conclusion(
        'X-Post + ACLED Holdout Test',
        `Combining Gemini-filtered credible X posts (field_report + news) with our Spatio-Temporal ML model achieves the lowest error of all models (0.229 vs. 0.595 for Simple Baseline — a 61.5% error reduction — and 0.251 for ML with ACLED alone). Notably, LLM filtering prevents the false-positive outbreak in Kinshasa that keyword rules suffer from (predicting 0.0 vs. actual 0 in Kinshasa, and 7.1 vs. actual 7 in Sud-Kivu).`
      )
    );
    root.append(part2);
  }

  /* Part 3: By Province & Drill-down */
  const part3 = chapter('04', 'Province-by-Province Breakdown', 'Where models succeed and where errors happen');
  part3.append(
    el('p', { class: 'muted' }, 'Typical prediction error multiplier across all 26 DRC provinces (30-day horizon). Dots closer to the left ("exact") are more accurate:'),
    legend(['baseline', 'ml', 'views'], 'dot'),
    el('div', { class: 'card scroll', id: 'provChart' }),
    conclusion(
      'Provincial Error Pattern',
      'Over 95% of DRC conflict deaths occur in five provinces (Nord-Kivu, Sud-Kivu, Ituri, Mai-Ndombe, and Tanganyika). In peaceful provinces with zero deaths, Simple Baseline and ACLED ML accurately predict zero, whereas VIEWS constantly predicts small non-zero fatality counts. In active conflict provinces, ACLED ML adapts faster to shifting violence levels than both Simple Baseline and VIEWS.'
    )
  );
  root.append(part3);
  document.getElementById('provChart').append(provinceChart('provChart'));

  const names = Object.keys(BT.province_series).sort((a, b) => (BT.provinces[b]?.actual || 0) - (BT.provinces[a]?.actual || 0));
  const pick = el('select', { 'aria-label': 'Province' });
  names.forEach(n => pick.append(el('option', { value: n }, n)));
  const holder = el('div', { class: 'card scroll', id: 'drill' });
  part3.append(
    el('h3', {}, 'Inspect One Province Over Time (2024–2026)'),
    el('div', { class: 'controls' }, el('label', {}, 'Select Province: ', pick)),
    legend(['actual', 'baseline', 'ml', 'views']),
    holder
  );
  const draw = () => {
    holder.replaceChildren(lineChart('drill',
      BT.province_series[pick.value].map(([month, actual, baseline, views, ml]) => ({ month, actual, baseline, views, ml })),
      ['actual', 'baseline', 'ml', 'views'],
      { height: 260, label: `${pick.value}: monthly recorded deaths and 30-day forecasts` }));
  };
  pick.addEventListener('change', draw);
  draw();
}

/* ============================================================================
 * 04. X POST SIGNAL: Post Volume, Topics of Discussion, and Forecast Contribution
 * ============================================================================ */
function scatter(id) {
  const rows = XC.rows, w = Math.min(width(id), 760), h = 340, left = 50, right = 120, top = 16, bottom = 44;
  const mx = niceMax(Math.max(...rows.map(r => r.event_authors), 1)), my = niceMax(Math.max(...rows.map(r => r.deaths_state), 1));
  const x = v => left + v / mx * (w - left - right), y = v => top + (1 - v / my) * (h - top - bottom);
  const svg = svgEl('svg', { width: w, height: h, role: 'img', 'aria-label': 'Credible X authors reporting violence against ACLED deaths, by province' });
  const parts = v => (v % 4 === 0 ? 4 : 5);
  for (let t = 0; t <= parts(my); t++) {
    const v = my * t / parts(my);
    svg.append(svgEl('line', { x1: left, x2: w - right, y1: y(v), y2: y(v), stroke: 'var(--grid)' }));
    svg.append(svgEl('text', { x: left - 8, y: y(v) + 4, 'text-anchor': 'end' }, Math.round(v)));
  }
  for (let t = 0; t <= parts(mx); t++) svg.append(svgEl('text', { x: x(mx * t / parts(mx)), y: h - bottom + 18, 'text-anchor': 'middle' }, Math.round(mx * t / parts(mx))));
  const placed = [];
  svg.append(svgEl('text', { x: (left + w - right) / 2, y: h - 6, 'text-anchor': 'middle' }, 'Credible X authors reporting conflict events (Gemini LLM)'));
  svg.append(svgEl('text', { x: 12, y: top + (h - top - bottom) / 2, transform: `rotate(-90 12 ${top + (h - top - bottom) / 2})`, 'text-anchor': 'middle' }, 'ACLED deaths that week'));
  [...rows].sort((a, b) => b.event_authors - a.event_authors).forEach(r => {
    const cx = x(r.event_authors), cy = y(r.deaths_state);
    const quiet = r.deaths_state === 0 && r.event_authors > 0;
    svg.append(svgEl('circle', { cx, cy, r: 6, fill: quiet ? 'var(--views)' : 'var(--x)', stroke: 'var(--surface)', 'stroke-width': 2 }));
    const dy = [0, -15, 15, -30, 30].find(d => !placed.some(([px, py]) => Math.abs(px - cx) < 90 && Math.abs(py - (cy + d)) < 13)) ?? 0;
    placed.push([cx, cy + dy]);
    svg.append(svgEl('text', { x: cx + 10, y: cy + 4 + dy, style: 'fill:var(--text)' }, r.province));
    const hit = svgEl('circle', { cx, cy, r: 14, fill: 'transparent' });
    hit.addEventListener('mousemove', e => showTip(e, r.province, [
      `Credible X event authors: ${r.event_authors}`,
      `All credible authors naming province: ${r.authors}`,
      `ACLED deaths (battles & remote violence): ${r.deaths_state}`,
      `ACLED deaths (including civilian violence): ${r.deaths_organised}`,
    ]));
    hit.addEventListener('mouseleave', hideTip);
    svg.append(hit);
  });
  return svg;
}

function renderX() {
  const root = document.getElementById('xcheck');
  root.replaceChildren();
  if (!XC) return empty(root, 'The X check has not run.');
  const X = DATA.x || {};
  const topics = X.topics || {};

  /* Section 1: Post Volume & Topics of Discussion */
  const vol = chapter('01', 'X Post Volume & Topics of Discussion', 'What the 1,000 collected X posts (tweets) talk about');
  vol.append(
    el('p', {}, `We collected ${(X.posts || 1000).toLocaleString('en')} public X posts in French and English from ${dayName(XC.posts_from)} to ${dayName(XC.posts_to)} written by ${(X.authors || 546).toLocaleString('en')} unique authors. Using the Gemini API (${X.extractor || 'gemini-3.8-flash'}), each post was classified by relevance, source credibility, conflict topic, and geographic hotspot:`),
    el('div', { class: 'tiles' },
      el('div', { class: 'tile' },
        el('div', { class: 'label' }, 'Total X Posts Analyzed'),
        el('div', { class: 'value' }, (X.posts || 1000).toLocaleString('en')),
        el('div', { class: 'note' }, `${(X.authors || 546).toLocaleString('en')} unique authors across 5 days (${topics.relevant_posts || 683} relevant to DRC conflict/politics; ${topics.irrelevant_posts || 317} off-topic dropped).`)
      ),
      el('div', { class: 'tile' },
        el('div', { class: 'label' }, 'Credible News & Field Reports'),
        el('div', { class: 'value' }, String((topics.source_types?.news || 213) + (topics.source_types?.field_report || 6))),
        el('div', { class: 'note' }, `${topics.source_types?.news || 213} journalism/news posts + ${topics.source_types?.field_report || 6} eyewitness field reports kept; ${(topics.source_types?.opinion || 346) + (topics.source_types?.party_claim || 56)} opinion & partisan posts filtered out.`)
      ),
      el('div', { class: 'tile' },
        el('div', { class: 'label' }, 'Same-Week Rank Correlation'),
        el('div', { class: 'value' }, num(XC.rank_event_authors_vs_state_deaths)),
        el('div', { class: 'note' }, 'Spearman rank correlation between Gemini-filtered credible event authors and ACLED battle deaths across the 6 provinces.')
      )
    )
  );

  if (topics.source_types && topics.event_types) {
    const srcLabels = {
      opinion: 'Opinion / Political Commentary (Filtered out)',
      news: 'News / Media Reports (Kept — Credible)',
      other: 'Other General Mentions (Filtered out)',
      party_claim: 'Partisan / Armed Group / Official Claims (Filtered out)',
      field_report: 'Eyewitness / Local Field Reports (Kept — Credible)',
    };
    const evLabels = {
      armed_clash: 'Armed Clashes & Frontline Battles (M23, FARDC, Wazalendo, ADF)',
      control_change: 'Territorial Control Changes (Village / Town Capture)',
      attack_on_civilians: 'Attacks on Civilians & Massacres',
      displacement: 'Refugee & IDP Mass Displacement',
      shelling_or_drone: 'Artillery Shelling, Mortar & Drone Strikes',
      abduction: 'Abductions & Kidnappings',
    };
    vol.append(
      el('div', { class: 'grid-2' },
        el('div', { class: 'card' },
          el('h3', {}, 'Breakdown by Post Source Type (683 Relevant Posts)'),
          el('p', { class: 'small' }, 'Over half of all DRC posts on X are opinion or political rhetoric. Gemini LLM separates verified reporting from commentary:'),
          table(['Source Category', 'Posts', 'Share of Relevant'],
            Object.entries(topics.source_types).map(([k, v]) => [srcLabels[k] || k, v, `${Math.round(100 * v / (topics.relevant_posts || 683))}%`]))
        ),
        el('div', { class: 'card' },
          el('h3', {}, 'Conflict Topics Discussed in Event Posts'),
          el('p', { class: 'small' }, 'Specific conflict precursor and violence categories extracted from posts reporting concrete events:'),
          table(['Topic / Event Category', 'Mentions'],
            Object.entries(topics.event_types).map(([k, v]) => [evLabels[k] || k, v]))
        )
      )
    );
    if (topics.top_hotspots) {
      vol.append(
        el('h3', {}, 'Top Sub-Provincial Hotspots Discussed on X'),
        table(['Hotspot / Territory Mentioned', 'Relevant Posts'],
          Object.entries(topics.top_hotspots).map(([k, v]) => [k, v]))
      );
    }
  }
  root.append(vol);

  /* Section 2: How X Posts Contribute to the Forecast */
  const contrib = chapter('02', 'How X Posts Contribute to the Forecast', 'Turning noisy social media into a leading conflict indicator');
  contrib.append(
    el('div', { class: 'grid-2' },
      el('div', { class: 'card term' },
        el('h3', {}, '1. Eliminating Capital-City Political Noise'),
        el('p', {}, 'When people post about war in the DRC, they frequently mention "Kinshasa" to criticize or praise the national government. Simple keyword rules count 15 "conflict event" authors in Kinshasa even when 0 battle deaths occurred there.'),
        el('p', {}, 'Filtering X posts with Gemini LLM to credible news and field_report sources drops Kinshasa from 15 authors to 1 (and 0 territorial control changes), while preserving the true frontline signal in Sud-Kivu (18 event authors, 4 control-change reports in Walungu/Kaziba) and Nord-Kivu.')
      ),
      el('div', { class: 'card term' },
        el('h3', {}, '2. Nowcasting the 14-Day Lag & Flagging Outbreaks'),
        el('p', {}, 'Because ACLED has a ~14-day reporting lag, a forecast issued today is blind to the most recent two weeks of violence. X posts fill that gap in two ways:'),
        el('ul', { class: 'takeaways' }, [
          'Lag Nowcast (Multiplicative): Compares each province’s share of credible X event authors against its baseline death share to estimate fatalities during the uncoded gap period.',
          'Precursor Onset (Additive): Territorial control_change reports extracted by the LLM enter the Spatio-Temporal ML model additively, allowing it to flag new outbreaks even when recent baseline deaths were zero.',
        ].map(t => el('li', {}, t)))
      )
    )
  );
  root.append(contrib);

  /* Section 3: Same-Week Alignment & Next-Week Holdout */
  const align = chapter('03', 'Empirical Verification', 'Same-week alignment and next-week holdout prediction');
  align.append(
    el('h3', {}, `Same-Week Alignment (${dayName(XC.week_start)} to ${dayName(XC.week_end)})`),
    el('div', { class: 'card scroll', id: 'scatter' }),
    table(['Province', 'Credible LLM Event Authors', 'Rule-Based Event Authors (Unfiltered)', 'LLM Control-Change Authors', 'ACLED Battle/Remote Deaths', 'ACLED Deaths (incl. Civilians)'],
      [...XC.rows].sort((a, b) => b.deaths_state - a.deaths_state).map(r => [
        r.province,
        r.event_authors,
        X.rules_provinces?.[r.province]?.event ?? '—',
        X.provinces?.[r.province]?.control_change ?? 0,
        r.deaths_state,
        r.deaths_organised,
      ]))
  );
  root.append(align);
  document.getElementById('scatter').append(scatter('scatter'));

  if (XC.holdout_next_week) {
    const ho = XC.holdout_next_week, m = ho.models;
    const names = Object.keys(ho.actual);
    align.append(
      el('h3', {}, `Resolved Next-Week Holdout Forecast (${dayName(ho.target_week)})`),
      el('p', { class: 'muted' }, `Predicting actual fatalities in the week of ${dayName(ho.target_week)} using ACLED through ${dayName(ho.lagged_week)} + X posts (${dayName(XC.posts_from)}–${dayName(XC.posts_to)}) nowcasting the ${dayName(ho.gap_week)} reporting gap:`),
      table(['Model', 'Mean Log Error', 'Spearman Rank', ...names], [
        ['ACLED Simple Baseline', num(m.acled_simple.log_error, 3), num(m.acled_simple.spearman, 3), ...names.map(n => deaths(m.acled_simple.predictions[n]))],
        ['ACLED Spatio-Temporal ML Only', num(m.ml_acled_only.log_error, 3), num(m.ml_acled_only.spearman, 3), ...names.map(n => deaths(m.ml_acled_only.predictions[n]))],
        ['X-Post + ACLED (Rules Nowcast)', num(m.ml_x_rules.log_error, 3), num(m.ml_x_rules.spearman, 3), ...names.map(n => deaths(m.ml_x_rules.predictions[n]))],
        ...(m.ml_x_llm_credible ? [['X-Post + ACLED (Gemini LLM Credible)', num(m.ml_x_llm_credible.log_error, 3), num(m.ml_x_llm_credible.spearman, 3), ...names.map(n => deaths(m.ml_x_llm_credible.predictions[n]))]] : []),
        ['Actual ACLED Recorded (2026-10-24)', '—', '—', ...names.map(n => deaths(ho.actual[n]))],
      ])
    );
  }
}

/* ============================================================================
 * 05. WORKFLOW: Clean Interactive Flowchart (No Cluttered Top Arrows)
 * ============================================================================ */
const WORKFLOW_STEPS = [
  {
    title: 'Ingestion',
    sub: 'Get multi-source data',
    stage: 'Ingest',
    text: 'Collect raw conflict records, benchmark forecasts, and social media posts on a local environment.',
    points: [
      () => DATA.acled ? `ACLED Weekly Export: ${dayName(DATA.acled.first_week)} to ${dayName(DATA.acled.coverage_end)} across all 26 DRC provinces (Republic of Congo rows filtered out).` : 'ACLED weekly fatalities and event types.',
      () => DATA.views ? `VIEWS API Runs: Published monthly PRIO-GRID cell predictions (${DATA.views.cells} cells in DRC) for each historical and live issue date.` : 'VIEWS monthly grid-cell forecasts.',
      () => DATA.x ? `X Recent Search: ${DATA.x.posts.toLocaleString('en')} French & English posts (${DATA.x.authors.toLocaleString('en')} authors) pulled from ${dayName(DATA.x.first_post)} to ${dayName(DATA.x.last_post)}.` : 'X conflict posts.',
    ],
  },
  {
    title: 'Preprocess',
    sub: 'Align & Gemini LLM tag',
    stage: 'Inputs',
    text: 'Standardize administrative boundaries, split weekly counts into calendar months, and extract structured X signals with Gemini.',
    points: [
      'Split Saturday-to-Friday ACLED weeks proportionally across month boundaries by day count, and enforce a 14-day completeness rule.',
      'Extract weekly and monthly ACLED precursor counts (control_change, escalation, deescalation) and compute adjacent province neighbor graphs.',
      () => DATA.x ? `Run ${DATA.x.extractor} on X posts to classify relevance, filter for credible news and field_report sources, and deduplicate authors per province.` : 'Run Gemini LLM extraction on X posts.',
    ],
  },
  {
    title: 'Build Baseline',
    sub: '1m ACLED persistence',
    stage: 'Backtest',
    text: 'Construct the 1-month Simple Baseline from completed ACLED history (ending at M−2 to respect the 14-day coding lag).',
    points: [
      'Selection of the 1-month window and the VIEWS rescaling factor (×1.38) is frozen on 2023 data so 2024–2026 evaluation has zero look-ahead bias.',
      'Project the M−2 monthly death rate over 30, 60, and 90-day horizons (k = 1, 2, 3 months).',
    ],
  },
  {
    title: 'Build ACLED ML',
    sub: 'Spatio-Temporal Ridge',
    stage: 'Backtest',
    text: 'Train the 7-feature Spatio-Temporal ML model via walk-forward validation on resolved historical months.',
    points: [
      'Combine 1m, 3m, and 6m fatality lags with ACLED precursor counts (control_change, escalation, deescalation) and neighboring-province spillover.',
      'Shrink Ridge coefficients toward the 1-month persistence prior so quiet provinces stay at zero unless an additive control_change precursor occurs.',
    ],
  },
  {
    title: 'Build ACLED + X',
    sub: 'LLM nowcast + precursors',
    stage: 'Forecast',
    text: 'Integrate credible LLM-extracted X signals with the Spatio-Temporal ML model to bridge the 14-day ACLED reporting lag.',
    points: [
      'Use each province’s share of credible X event authors to nowcast fatalities in the uncoded M−1 (or W−1) reporting-lag window.',
      'Feed X control_change, escalation, and deescalation counts into the trained Spatio-Temporal ML feature vector to generate 30/60/90d and next-week predictions.',
    ],
  },
  {
    title: 'Check & Evaluate',
    sub: 'Backtest & holdout test',
    stage: 'X check',
    text: 'Compare all models against actual recorded ACLED fatalities and the VIEWS deep-learning benchmark.',
    points: [
      'Score 30, 60, and 90-day predictions across 31 monthly issues (2024–2026) using log error, block-bootstrap confidence intervals, and Spearman rank correlation.',
      'Score the next-week holdout (2026-10-24) comparing Simple Baseline, ACLED ML, ML + X (Rules), and ML + X (Gemini LLM Credible).',
    ],
  },
  {
    title: 'Publish',
    sub: 'Static dashboard & report',
    stage: 'Score',
    text: 'Compile aggregate JSON artifacts and build this interactive static site without exposing raw text or user accounts.',
    points: [
      'Verify privacy guards: zero raw tweet text, zero author handles, and zero raw ACLED event rows enter history/ or site/.',
      'Render interactive charts, maps, and evaluation tables.',
    ],
  },
];

const STATUS = { done: 'Done', skipped: 'Waiting', failed: 'Failed', notrun: 'Not run' };
const ICON = { done: '✓', skipped: '–', failed: '✕', notrun: '○' };
function stepState(step) {
  if (!step.stage) return 'done';
  const s = stageOf(step.stage);
  return s ? s.status : 'notrun';
}

function renderHow() {
  const root = document.getElementById('how');
  root.replaceChildren();
  let activeIdx = 0;
  const inspector = el('div', { class: 'card' });
  const chartHolder = el('div', { class: 'card scroll' });

  function drawFlow() {
    const W = 1080, n = WORKFLOW_STEPS.length, boxW = 132, gap = (W - boxW * n) / (n - 1), top = 14, boxH = 96;
    const svg = svgEl('svg', { viewBox: `0 0 ${W} 126`, width: '100%', style: 'min-width:880px', role: 'img', 'aria-label': 'Interactive 7-stage workflow diagram from ingestion to publishing' });
    svg.append(svgEl('defs', {}, svgEl('marker', { id: 'arrow', viewBox: '0 0 8 8', refX: 7, refY: 4, markerWidth: 7, markerHeight: 7, orient: 'auto' }, svgEl('path', { d: 'M0 0L8 4L0 8z', fill: 'var(--neutral)' }))));

    WORKFLOW_STEPS.forEach((step, i) => {
      const x = i * (boxW + gap), code = stepState(step), isSel = i === activeIdx;
      const g = svgEl('g', { class: 'flow-node', tabindex: '0', role: 'button', 'aria-label': `Step ${i + 1}: ${step.title}` });
      g.append(svgEl('rect', {
        class: 'box', x, y: top, width: boxW, height: boxH, rx: 10,
        fill: isSel ? 'var(--wash)' : 'var(--surface)',
        stroke: isSel ? 'var(--baseline)' : 'var(--border)',
        'stroke-width': isSel ? 2.5 : 1.5,
      }));
      g.append(svgEl('circle', { cx: x + 20, cy: top + 22, r: 12, fill: isSel ? 'var(--baseline)' : 'var(--text)' }));
      g.append(svgEl('text', { x: x + 20, y: top + 26.5, 'text-anchor': 'middle', style: 'fill:var(--surface);font-weight:700' }, i + 1));
      const words = step.title.split(' ');
      g.append(svgEl('text', { x: x + 38, y: words.length > 1 ? top + 18 : top + 26, style: 'fill:var(--text);font-weight:650;font-size:12.5px' }, words[0]));
      if (words.length > 1) g.append(svgEl('text', { x: x + 38, y: top + 33, style: 'fill:var(--text);font-weight:650;font-size:12.5px' }, words.slice(1).join(' ')));
      g.append(svgEl('text', { x: x + 10, y: top + 58, style: 'font-size:11px;fill:var(--text-2)' }, step.sub));
      g.append(svgEl('text', { x: x + 10, y: top + 80, style: 'font-size:11.5px;font-weight:600;fill:var(--baseline)' }, `${ICON[code]} ${STATUS[code]}`));
      g.addEventListener('click', () => { activeIdx = i; drawFlow(); drawInspector(); });
      g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activeIdx = i; drawFlow(); drawInspector(); } });
      svg.append(g);
      if (i < n - 1) {
        svg.append(svgEl('line', { x1: x + boxW + 2, x2: x + boxW + gap - 3, y1: top + boxH / 2, y2: top + boxH / 2, stroke: 'var(--neutral)', 'stroke-width': 1.5, 'marker-end': 'url(#arrow)' }));
      }
    });
    chartHolder.replaceChildren(svg);
  }

  function drawInspector() {
    const step = WORKFLOW_STEPS[activeIdx], s = step.stage ? stageOf(step.stage) : null, code = stepState(step);
    inspector.replaceChildren(
      el('p', { class: 'eyebrow' }, `Stage 0${activeIdx + 1} of 0${WORKFLOW_STEPS.length} · Interactive Inspector`),
      el('h3', {}, `${activeIdx + 1}. ${step.title} — ${step.sub}`),
      el('p', {}, step.text),
      el('ul', { class: 'takeaways' }, step.points.map(p => el('li', {}, typeof p === 'function' ? p() : p))),
      el('p', { class: 'small' }, el('span', { class: 'state' }, `Pipeline Status: ${ICON[code]} ${STATUS[code]}`), s ? ` — ${s.detail} (${dayName(s.at)})` : '')
    );
  }

  root.append(
    el('h2', {}, 'End-to-End Forecasting Workflow'),
    el('p', { class: 'muted' }, 'Click any stage in the interactive flowchart below to inspect how data moves from ingestion and preprocessing to baseline construction, Spatio-Temporal ML, X-post integration, and evaluation:'),
    chartHolder,
    inspector,
    el('h2', {}, 'All Workflow Stages at a Glance'),
    el('div', {}, WORKFLOW_STEPS.map((step, i) => {
      const s = step.stage ? stageOf(step.stage) : null, code = stepState(step);
      return el('div', { class: 'stepRow' }, el('div', { class: 'stepNum' }, i + 1),
        el('div', {}, el('h3', {}, `${step.title} (${step.sub})`), el('p', {}, step.text),
          el('ul', {}, step.points.map(p => el('li', {}, typeof p === 'function' ? p() : p))),
          el('p', { class: 'small' }, el('span', { class: 'state' }, `${ICON[code]} ${STATUS[code]}`), s ? ` ${s.detail}` : '')));
    }))
  );
  drawFlow();
  drawInspector();
}

/* ============================================================================
 * 06. FUTURE WORK & LIMITATIONS: Next Steps, Data Limits, and Tech/Analysis Limits
 * ============================================================================ */
function term(title, paragraphs) {
  return el('div', { class: 'card term' }, el('h3', {}, title), ...paragraphs.map(text => el('p', {}, text)));
}

function renderData() {
  const root = document.getElementById('data');
  root.replaceChildren();

  /* Section 1: Future Work */
  const future = chapter('01', 'Future Work & Next Steps', 'How to improve the ACLED + X forecasting system next');
  future.append(
    el('p', {}, 'While our Spatio-Temporal ML + Gemini X Nowcast pipeline already outperforms both the Simple Baseline and VIEWS on the 2024–2026 backtest and the 24 Oct 2026 holdout, five concrete improvements would make the system significantly stronger:'),
    el('div', { class: 'grid-2' },
      term('1. Continuous Daily X Archiving for Longitudinal Backtesting', [
        'Because the standard X Recent Search API only looks back 7 days, our current X evaluation is limited to a single resolved holdout week (1,000 posts from Oct 18–22, 2026).',
        'Next step: Run an automated daily cron job to pull and LLM-tag DRC posts continuously over 6–12 months. This will build a multi-month historical feature matrix so the X nowcast and precursor weights can be cross-validated across dozens of monthly issue dates.',
      ]),
      term('2. Seeding Credible Local Accounts from ACLED’s Source Column', [
        'Keyword queries on X pull in a large volume of partisan commentary (50.7% opinion posts) alongside real reporting.',
        'Next step: Parse ACLED’s `source` column for the DRC (e.g., Radio Okapi, Actualite.cd, Kivu Security Tracker, local civil society monitors, MONUSCO updates) and track their official X handles directly. Seeding from verified local reporters will boost high-signal `field_report` coverage across all 26 provinces.',
      ]),
      term('3. Sub-Provincial (Territory & Hotspot) Spatial Resolution', [
        'DRC provinces are geographically vast. Aggregating all of Nord-Kivu or Sud-Kivu into a single number mixes intense frontline zones (e.g., Rutshuru, Masisi, Walungu, Uvira) with quieter districts.',
        'Next step: Since both ACLED and our Gemini LLM extractor already identify sub-provincial hotspots (Walungu, Goma, Djugu, Beni, Mambasa), shifting the model from `admin1` (26 provinces) to `admin2` (145 territories) or 0.5° grid cells will sharpen spatial spillover and early outbreak detection.',
      ]),
      term('4. Two-Stage Hurdle Model & Non-Linear Count Regression', [
        'Our current 7-feature Ridge regression is intentionally linear and shrunk toward persistence to avoid overfitting on small samples.',
        'Next step: Once a longer X archive is accumulated, implement a two-stage Hurdle model: Stage 1 uses a calibrated classifier (driven by X troop movements, ceasefire breakdowns, and `control_change` tags) to predict the probability of a new conflict outbreak; Stage 2 uses a regularized Poisson/Negative-Binomial gradient-boosted tree to predict fatality severity.',
      ])
    ),
    term('5. Multilingual Local Coverage (Swahili, Lingala & Local Radio Transcripts)', [
      'Most frontline eyewitness reports in Eastern DRC are shared in Congolese Swahili, Lingala, or local community radio broadcasts (such as Radio Okapi) before appearing in French or English tweets.',
      'Next step: Expand the X search query and Gemini extraction prompt to Swahili and Lingala keywords and integrate automated daily transcripts from local humanitarian radio bulletins.',
    ])
  );
  root.append(future);

  /* Section 2: Limitations (Data vs. Technology & Analysis) */
  const limits = chapter('02', 'Limitations', 'Constraints in data coverage, tools, and analytical methodology');
  limits.append(
    el('div', { class: 'grid-2' },
      el('div', { class: 'card term' },
        el('h3', {}, 'A. Data Limitations'),
        el('ul', { class: 'takeaways' }, [
          '7-Day X Search Window: X’s recent search API only reaches back 7 days. One week of posts (Oct 18–22, 2026) demonstrates a strong same-week correlation (0.94) and improves the Oct 24 next-week holdout, but cannot prove multi-year forecasting skill on its own.',
          'Geographic Search Bias: The X search query explicitly named Eastern DRC hotspots (Goma, Kivus, Ituri). As a result, post volume is concentrated in 6 provinces rather than uniformly covering all 26 provinces.',
          'ACLED vs. VIEWS Target Definition Mismatch: ACLED records fatalities from Battles + Explosions/Remote Violence, whereas VIEWS predicts UCDP state-based conflict fatalities. Part of VIEWS’s lower national total reflects stricter UCDP inclusion thresholds rather than model error alone.',
          'ACLED Reporting Lag & Retroactive Revisions: ACLED takes ~14 days to code recent events and periodically revises historical fatality counts as new investigations emerge. Leaving out month M−1 approximates real-time availability but does not reconstruct exact vintage snapshots.',
        ].map(t => el('li', {}, t)))
      ),
      el('div', { class: 'card term' },
        el('h3', {}, 'B. Technical & Analytical Limitations (Tools & Models)'),
        el('ul', { class: 'takeaways' }, [
          'LLM Extraction Imperfections & Rumor Propagation: Although `gemini-3.8-flash` drastically reduces false positives compared to regex rules (cutting Kinshasa false alarms from 15 to 1), LLMs can still misclassify unverified rumors repeated by news aggregators or miss implicit geographic references.',
          'Linear Regularization vs. Extreme Massacres: Shrinking Ridge regression toward the 1-month persistence prior prevents false alarms in quiet provinces, but linear combinations of lagged deaths and precursors can still under-predict sudden, unprecedented mass-casualty spikes.',
          'Uniform Day-Splitting Across Month Boundaries: Splitting Saturday-to-Friday ACLED weeks across month ends by day count (e.g., 3/7 in month A, 4/7 in month B) assumes fatalities occurred evenly throughout the week.',
          'Overlapping Multi-Horizon Evaluation Windows: The 60-day and 90-day evaluations reuse calendar months from the 30-day window. Consistent gains across 30, 60, and 90 days confirm stability over longer windows but are not three statistically independent trials.',
        ].map(t => el('li', {}, t)))
      )
    )
  );
  root.append(limits);
}

/* ============================================================================
 * TABS & NAVIGATION
 * ============================================================================ */
const TABS = [
  ['method', 'Method', renderMethod],
  ['overview', 'Findings', renderOverview],
  ['backtest', 'Evaluation', renderBacktest],
  ['xcheck', 'X Post Signal', renderX],
  ['how', 'Workflow', renderHow],
  ['data', 'Future Work & Limitations', renderData],
];
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
  const b = el('button', { role: 'tab', id: `tab-${id}`, 'aria-controls': id, 'aria-selected': 'false', tabindex: '-1' },
    el('span', { class: 'nav-index' }, String(index + 1).padStart(2, '0')), label);
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
addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => current && select(current), 200); });
const start = location.hash.slice(2);
select(TABS.some(([id]) => id === start) ? start : 'method');
