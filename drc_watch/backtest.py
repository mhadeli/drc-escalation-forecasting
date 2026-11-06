"""Build hotspot-month panels, fit the 2022 mapping, and score paired baselines."""

import csv
import json
import math
from datetime import date, timedelta

import numpy as np

from drc_watch import acled, views
from drc_watch import forecast as fc
from drc_watch.fetch_recent import DATA, HISTORY, ROOT, checksum, now, write_csv, write_json

REPORTS = ROOT / 'reports'
LAGS = (7, 14, 28)


def resolved_through(meta):
    """Leave two weeks for reporting before declaring a negative outcome resolved."""
    return min(date.fromisoformat(meta['coverage_end']), date.fromisoformat(meta['retrieved_at'][:10]) - timedelta(days=14))


def mapping(expected, coefficient):
    return -math.expm1(-coefficient * expected)


def fit_scale(rows):
    """One shared coefficient; use only targets that resolved in 2022."""
    pairs = [(row[f'e_{h}'], row[f'major_{h}']) for row in rows for h in fc.HORIZONS
             if row['issue'].startswith('2022-') and
             date.fromisoformat(row['issue']) + timedelta(days=h) < date(2023, 1, 1) and
             row[f'e_{h}'] is not None and row[f'major_{h}'] is not None]
    if not pairs or not any(e > 0 for e, _ in pairs):
        raise ValueError('No usable 2022 calibration data. Import earlier ACLED coverage and cache the historical VIEWS runs.')
    expected, truth = np.array(pairs).T
    loss = lambda c: np.mean((-np.expm1(-c * expected) - truth) ** 2)
    grid = np.r_[0, np.logspace(-8, 4, 241) / expected[expected > 0].mean()]
    index = min(range(len(grid)), key=lambda i: loss(grid[i]))
    low, high = grid[max(index - 1, 0)], grid[min(index + 1, len(grid) - 1)]
    for _ in range(60):
        a, b = low + (high - low) / 3, high - (high - low) / 3
        if loss(a) < loss(b):
            high = b
        else:
            low = a
    coefficient = min((0, (low + high) / 2, grid[index]), key=loss)
    return {'coefficient': float(coefficient), 'n': len(pairs), 'positives': int(truth.sum()),
            'training_year': 2022, 'last_target': '2022-12-31', 'objective': 'pooled Brier',
            'fitted_at': now().isoformat()}


def build():
    events, meta = acled.load_snapshot()
    boxes, runs = acled.load_boxes(), views.catalog()
    dates, notes = acled.major_dates(events, boxes), acled.note_history(events, boxes)
    coverage_end = resolved_through(meta)
    coverage_start = date.fromisoformat(meta['coverage_start'])
    panels, cache = {}, {}
    for lag in LAGS:
        rows, issue = [], date(2022, 1, 1)
        while issue + timedelta(days=90) <= coverage_end:
            cutoff = issue - timedelta(days=lag)
            if acled.month_shift(cutoff, -12) < coverage_start:
                issue = acled.month_shift(issue, 1)
                continue
            run = views.select_run(runs, issue)
            if run and run['run'] not in cache:
                cache[run['run']] = views.load_monthly(run['run'], boxes)
            monthly = cache.get(run['run'], {}) if run else {}
            for name, province_aliases in fc.HOTSPOTS.items():
                history = dates[name]
                rate = acled.recent_rate(history, issue, lag)
                signal = fc.note_multiplier(acled.note_counts(notes[name], issue, lag))
                baseline = {h: -math.expm1(-rate * h / 30) for h in fc.HORIZONS}
                text = fc.adjust(baseline, signal)
                row = {'hotspot': name, 'province': province_aliases[0], 'issue': issue.isoformat(),
                       'lag': lag, 'cutoff': cutoff.isoformat(),
                       'onset': int(not any(acled.month_shift(cutoff, -6) < d <= cutoff for d in history)),
                       'views_run': run['run'] if run else '', 'views_released': run['released'] if run else ''}
                for h in fc.HORIZONS:
                    row[f'major_{h}'] = acled.target(history, issue, h, coverage_end)
                    row[f'p_recent_{h}'] = baseline[h]
                    row[f'p_text_{h}'] = text[h]
                    row[f'e_{h}'] = views.expected(monthly.get(name, {}), issue, h)
                rows.append(row)
            issue = acled.month_shift(issue, 1)
        if not rows:
            raise ValueError('Not enough ACLED coverage for a 2022 calibration and a resolved backtest.')
        panels[lag] = rows
    calibration = fit_scale(panels[14])
    for lag, rows in panels.items():
        for row in rows:
            for h in fc.HORIZONS:
                row[f'p_views_{h}'] = None if row[f'e_{h}'] is None else mapping(row[f'e_{h}'], calibration['coefficient'])
        write_csv(DATA / ('panel.csv' if lag == 14 else f'panel_lag{lag}.csv'), rows)
    write_json(DATA / 'calibration.json', calibration)
    # A box too small to hold a 0.5-degree cell centre has no VIEWS forecast; it drops out of the paired scores.
    no_cells = sorted(name for name in boxes if not any(monthly.get(name) for monthly in cache.values()))
    write_json(DATA / 'panel.meta.json', {'built_at': now().isoformat(), 'acled': meta,
               'hotspots_sha256': acled.boxes_hash(), 'catalog_sha256': checksum(views.OUT / 'catalog.json'),
               'resolved_through': coverage_end.isoformat(), 'rows': len(panels[14]), 'hotspots_without_views': no_cells})
    return panels[14]


def read_panel(path):
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key, value in row.items():
            if key.startswith(('p_', 'e_', 'major_')):
                row[key] = float(value) if value != '' else None
        row['onset'], row['lag'] = int(row['onset']), int(row['lag'])
    return rows


def auc(truth, scores):
    truth, scores = np.asarray(truth), np.asarray(scores)
    positive = truth.sum()
    if positive == 0 or positive == len(truth):
        return None
    order, ranks = np.argsort(scores), np.empty(len(scores), dtype=float)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        ranks[order[i:j]] = (i + 1 + j) / 2
        i = j
    return float((ranks[truth == 1].sum() - positive * (positive + 1) / 2) / (positive * (len(truth) - positive)))


def metrics(truth, recent, external, ranking, text):
    truth, recent, external, text = map(np.asarray, (truth, recent, external, text))
    a, b, c = (float(np.mean((p - truth) ** 2)) for p in (recent, external, text))
    return {'n': len(truth), 'positives': int(truth.sum()), 'brier_recent': a, 'brier_views': b, 'brier_text': c,
            'skill_views': 1 - b / a if a > 0 else None, 'skill_text': 1 - c / a if a > 0 else None,
            'auc_recent': auc(truth, recent), 'auc_views': auc(truth, ranking), 'auc_text': auc(truth, text)}


def intervals(rows, horizon, draws=1000):
    """Paired three-month moving blocks; keep all locations in each month."""
    if draws < 1:
        raise ValueError('Bootstrap draws must be positive.')
    months = sorted({r['issue'] for r in rows})
    indices = {month: [i for i, row in enumerate(rows) if row['issue'] == month] for month in months}
    arrays = [np.array([r[key] for r in rows]) for key in
              (f'major_{horizon}', f'p_recent_{horizon}', f'p_views_{horizon}', f'e_{horizon}', f'p_text_{horizon}')]
    rng, values = np.random.default_rng(7), {}
    for _ in range(draws):
        sampled = []
        while len(sampled) < len(months):
            start = int(rng.integers(len(months)))
            sampled.extend(months[(start + i) % len(months)] for i in range(min(3, len(months))))
        take = [i for month in sampled[:len(months)] for i in indices[month]]
        result = metrics(*(a[take] for a in arrays))
        for key, value in result.items():
            if key not in ('n', 'positives') and value is not None:
                values.setdefault(key, []).append(value)
    return {key: np.quantile(value, [.05, .95]).tolist() for key, value in values.items()}


def scoreboard(rows, draws):
    scores = []
    splits = {'all': rows, 'onsets': [r for r in rows if r['onset']]}
    splits.update({province: [r for r in rows if r['province'] == province] for province in sorted({r['province'] for r in rows})})
    for split, subset in splits.items():
        for h in fc.HORIZONS:
            paired = [r for r in subset if all(r[k] is not None for k in (f'major_{h}', f'p_recent_{h}', f'p_views_{h}', f'e_{h}'))]
            if not paired:
                continue
            result = metrics(*([r[key] for r in paired] for key in
                               (f'major_{h}', f'p_recent_{h}', f'p_views_{h}', f'e_{h}', f'p_text_{h}')))
            scores.append({'split': split, 'horizon': h, **result, 'excluded': len(subset) - len(paired),
                           'intervals': intervals(paired, h, draws)})
    return scores


def reliability(rows, destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for baseline in ('recent', 'text', 'views'):
        figure, axes = plt.subplots(1, 3, figsize=(10, 3), sharex=True, sharey=True)
        for axis, h in zip(axes, fc.HORIZONS):
            points = [r for r in rows if r[f'p_views_{h}'] is not None and r[f'major_{h}'] is not None]
            for low in np.arange(0, 1, .1):
                bucket = [r for r in points if low <= r[f'p_{baseline}_{h}'] <= low + .1
                          and (low == 0 or r[f'p_{baseline}_{h}'] > low)]
                if bucket:
                    axis.scatter(np.mean([r[f'p_{baseline}_{h}'] for r in bucket]),
                                 np.mean([r[f'major_{h}'] for r in bucket]), s=15 + len(bucket))
            axis.plot([0, 1], [0, 1], '--', color='gray')
            axis.set(title=f'{h} days', xlabel='Predicted probability', xlim=(0, 1), ylim=(0, 1))
        axes[0].set_ylabel('Observed frequency')
        figure.suptitle(f'{baseline}: paired historical cases; marker area reflects count')
        figure.tight_layout()
        figure.savefig(destination / f'reliability_{baseline}.png', dpi=160)
        plt.close(figure)


def hotspot_detail(rows):
    """Per-hotspot paired Brier scores and monthly series for the comparison chart."""
    detail = {}
    for name in sorted({r['hotspot'] for r in rows}):
        subset, entry = [r for r in rows if r['hotspot'] == name], {}
        for h in fc.HORIZONS:
            paired = [r for r in subset if r[f'p_views_{h}'] is not None and r[f'major_{h}'] is not None]
            entry[str(h)] = {'n': len(paired), 'positives': int(sum(r[f'major_{h}'] for r in paired)),
                             'brier_recent': float(np.mean([(r[f'p_recent_{h}'] - r[f'major_{h}']) ** 2 for r in paired])) if paired else None,
                             'brier_views': float(np.mean([(r[f'p_views_{h}'] - r[f'major_{h}']) ** 2 for r in paired])) if paired else None,
                             'brier_text': float(np.mean([(r[f'p_text_{h}'] - r[f'major_{h}']) ** 2 for r in paired])) if paired else None,
                             'series': [[r['issue'], round(r[f'p_recent_{h}'], 4), round(r[f'p_views_{h}'], 4), int(r[f'major_{h}']), round(r[f'p_text_{h}'], 4)] for r in paired]}
        detail[name] = entry
    return detail


def report(draws=1000):
    prereg = REPORTS / 'prereg.md'
    if not prereg.exists():
        raise ValueError('Write reports/prereg.md before the first score.')
    panels = {lag: [r for r in read_panel(DATA / ('panel.csv' if lag == 14 else f'panel_lag{lag}.csv'))
                    if r['issue'] >= '2023-01-01'] for lag in LAGS}
    if not panels[14]:
        raise ValueError('No resolved 2023-onward cases. Extend ACLED coverage before scoring.')
    meta = json.loads((DATA / 'panel.meta.json').read_text())
    if meta['hotspots_sha256'] != acled.boxes_hash():
        raise ValueError('Hotspot boxes changed; rebuild the panel before scoring.')
    scores = {str(lag): scoreboard(rows, draws) for lag, rows in panels.items()}
    primary = [s for s in scores['14'] if s['split'] == 'all']
    if len(primary) != 3:
        raise ValueError('No complete paired baseline comparison at all three horizons; cache missing VIEWS runs/cells.')
    means = {name: sum(s[f'brier_{name}'] for s in primary) / 3 for name in ('recent', 'text', 'views')}
    answer = 'yes' if all(s['intervals'].get('skill_text', [0])[0] > 0 for s in primary) else 'not shown'
    stamp = now().strftime('%Y%m%dT%H%M%S%fZ')
    destination = REPORTS / f'backtest_{stamp}'
    destination.mkdir(parents=True)
    reliability(panels[14], destination)
    calibration = json.loads((DATA / 'calibration.json').read_text())
    payload = {'created_at': now().isoformat(), 'prereg_sha256': checksum(prereg), 'panel': meta,
               'calibration': calibration, 'bootstrap_draws': draws, 'baseline': 'recent', 'mean_brier': means, 'text_beats_baseline': answer, 'scores': scores}
    write_json(destination / 'scores.json', payload)
    write_json(destination / 'hotspots.json', hotspot_detail(panels[14]))
    lines = ['# Historical baseline comparison', '', f"Resolved through {meta['resolved_through']}; X is not backtested.", '',
             '| Lag | Split | Days | N | Excluded | Brier baseline | Brier notes | Brier VIEWS | Notes skill (90% interval) | VIEWS skill (90% interval) | AUC baseline / notes / VIEWS |',
             '|---|---|---:|---:|---:|---:|---:|---:|---|---|---|']
    fmt = lambda v: 'undefined' if v is None else f'{v:.3f}'
    for lag, table in scores.items():
        for s in table:
            band = lambda key: f"[{s['intervals'][key][0]:.3f}, {s['intervals'][key][1]:.3f}]" if key in s['intervals'] else 'undefined'
            lines.append(f"| {lag} | {s['split']} | {s['horizon']} | {s['n']} | {s['excluded']} | "
                         f"{s['brier_recent']:.3f} | {s['brier_text']:.3f} | {s['brier_views']:.3f} | "
                         f"{fmt(s['skill_text'])} {band('skill_text')} | {fmt(s['skill_views'])} {band('skill_views')} | "
                         f"{fmt(s['auc_recent'])} / {fmt(s['auc_text'])} / {fmt(s['auc_views'])} |")
    lines += ['', f"Do ACLED-note text signals beat the ACLED-history baseline at all three horizons, with intervals above zero? **{answer}**.", '',
              f"Notes in this export: {meta['acled'].get('notes', 'unknown')} of {meta['acled']['events']} events. Without notes, the text signal equals the baseline.", '',
              f"2022 mapping: c={calibration['coefficient']:.6g}, {calibration['n']} horizon-labelled cases. "
              'It is fitted on fatalities001 forecasts and applied to later VIEWS model versions, so it may not transfer.', '',
              f"Hotspots with no VIEWS cell centre in their box, so excluded from paired scores: {', '.join(meta['hotspots_without_views']) or 'none'}.", '',
              '![Baseline reliability](reliability_recent.png)', '![Notes-adjusted reliability](reliability_text.png)', '![VIEWS reliability](reliability_views.png)', '',
              '## Cases (30 days; alert threshold 0.5, descriptive)', '']
    paired = [r for r in panels[14] if r['p_views_30'] is not None]
    for title, label, alert in [('Onset caught', 1, True), ('Onset missed', 1, False), ('False alarm', 0, True)]:
        cases = [r for r in paired if r['major_30'] == label and (r['p_text_30'] >= .5) == alert
                 and (not label or r['onset'])]
        case = max(cases, key=lambda r: r['p_text_30']) if cases else None
        lines.append(f"- {title}: {case['hotspot']}, {case['issue']}, p={case['p_text_30']:.3f}." if case else f'- {title}: no qualifying case.')
    lines += ['', '## Limitations', '', prereg.read_text().split('## Limits that must accompany every report')[1].strip(), '',
              f"ACLED retrieved: {meta['acled']['retrieved_at']}; SHA-256: `{meta['acled']['sha256']}`.",
              'Brier and AUC intervals for every split are in scores.json. Insufficient classes yield undefined AUC.']
    report_path = destination / 'report.md'
    report_path.write_text('\n'.join(lines) + '\n')
    reference = HISTORY / 'reference.json'
    if not reference.exists():
        write_json(reference, {'baseline': 'recent', 'coefficient': calibration['coefficient'], 'frozen_at': now().isoformat(),
                              'report': str(report_path.relative_to(ROOT)), 'prereg_sha256': checksum(prereg),
                              'hotspots_sha256': meta['hotspots_sha256'], 'mean_brier': means})
    return report_path


def live_baselines(issue, dates, boxes, reference, notes):
    run = views.select_run(views.catalog(), issue)
    monthly = views.load_monthly(run['run'], boxes) if run else {}
    result = {}
    for name in boxes:
        rate = acled.recent_rate(dates[name], issue)
        counts = acled.note_counts(notes[name], issue)
        note_signal = fc.note_multiplier(counts)
        baseline = {h: -math.expm1(-rate * h / 30) for h in fc.HORIZONS}
        text = fc.adjust(baseline, note_signal)
        row = {'hotspot': name, 'province': fc.HOTSPOTS[name][0], 'views_run': run['run'] if run else None, 'recent_rate': rate,
               'note_multiplier': round(note_signal, 3), 'note_counts': counts}
        for h in fc.HORIZONS:
            expected = views.expected(monthly.get(name, {}), issue, h)
            row[f'e{h}'] = expected
            row[f'p{h}_recent'] = baseline[h]
            row[f'p{h}_notes'] = text[h]
            row[f'p{h}_views'] = None if expected is None else mapping(expected, reference['coefficient'])
            row[f'p{h}_baseline'] = baseline[h]
        result[name] = row
    return result
