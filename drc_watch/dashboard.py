"""Static dashboard from history/: no API calls, no post text, no raw ACLED rows."""

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name('dashboard.html')


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def acled_eda(acled):
    if not acled:
        return None
    state = acled['monthly'][acled['primary']]
    organised = acled['monthly'].get('organised', state)
    pre = acled.get('precursors') or {}
    months = sorted(next(iter(state.values())))
    years = sorted({m[:4] for m in months})
    by_year = []
    for y in years:
        ym = [m for m in months if m.startswith(y)]
        s = round(sum(state[p][m] for p in state for m in ym), 1)
        o = round(sum(organised[p][m] for p in organised for m in ym), 1)
        by_year.append({'year': y, 'months': len(ym), 'state': s, 'organised': o, 'civilians': round(max(0.0, o - s), 1)})
    by_province = []
    for p in sorted(state):
        s = round(sum(state[p].values()), 1)
        o = round(sum(organised[p].values()), 1)
        by_province.append({
            'province': p, 'state': s, 'organised': o, 'civilians': round(max(0.0, o - s), 1),
            'active_months': sum(1 for v in state[p].values() if v >= 1),
            'zero_months': sum(1 for v in state[p].values() if v == 0),
            'control_change': round(sum((pre.get('control_change', {}).get(p) or {}).values()), 1),
            'escalation': round(sum((pre.get('escalation', {}).get(p) or {}).values()), 1),
            'deescalation': round(sum((pre.get('deescalation', {}).get(p) or {}).values()), 1),
        })
    return {
        **{k: acled.get(k) for k in ('source', 'rows', 'first_week', 'last_week', 'coverage_end',
                                     'first_month', 'complete_through', 'definitions', 'primary')},
        'months_n': len(months),
        'by_year': by_year,
        'by_province': sorted(by_province, key=lambda r: -r['state']),
    }


def views_eda(views, acled):
    if not views:
        return None
    issues = views.get('issues') or {}
    cells_map = views.get('cells_per_province') or {}
    state = acled['monthly'][acled['primary']] if acled else {}
    months = set(next(iter(state.values()))) if state else set()
    resolved = [i for i in sorted(issues) if i in months]
    runs = {}
    for issue, info in sorted(issues.items()):
        r = runs.setdefault(info['run'], {'run': info['run'], 'released': info['released'],
                                          'last_input': info['last_input'], 'first_issue': issue, 'last_issue': issue, 'issues': 0})
        r['last_issue'] = issue
        r['issues'] += 1
    by_province = []
    for p in sorted(set(state) | set(cells_map)):
        v_vals = [issues[i]['provinces'].get(p, {}).get(i, 0.0) for i in resolved]
        a_vals = [state.get(p, {}).get(i, 0.0) for i in resolved]
        n = len(resolved) or 1
        by_province.append({
            'province': p, 'cells': cells_map.get(p, 0),
            'acled_mean': round(sum(a_vals) / n, 1), 'views_mean': round(sum(v_vals) / n, 1),
            'acled_total': round(sum(a_vals), 1), 'views_total': round(sum(v_vals), 1),
            'zero_months': sum(1 for v in a_vals if v == 0),
        })
    return {
        'source': views.get('source'), 'boundaries': views.get('boundaries'),
        'cells': views.get('cells'), 'border_cells': views.get('border_cells'),
        'issues_n': len(issues), 'resolved_issues_n': len(resolved),
        'first_issue': min(issues) if issues else None, 'last_issue': max(issues) if issues else None,
        'runs_n': len(runs), 'runs': list(runs.values()),
        'by_province': sorted(by_province, key=lambda r: (-r['acled_total'], -r['cells'])),
    }


def collect(history):
    inputs = history / 'inputs'
    acled = read(inputs / 'acled.json')
    views = read(inputs / 'views.json')
    x = read(inputs / 'x_counts.json')
    return {
        'backtest': read(history / 'backtest.json'), 'xcheck': read(history / 'x_check.json'),
        'forecast': read(history / 'forecast.json'), 'status': read(history / 'pipeline_status.json'),
        'ingest': read(history / 'ingest_status.json'),
        'acled': acled_eda(acled),
        # National monthly totals only, for the time chart; never per-event rows.
        'national_actual': None if not acled else {
            m: round(sum(series[m] for series in acled['monthly'][acled['primary']].values()), 1)
            for m in sorted(next(iter(acled['monthly'][acled['primary']].values())))},
        'views': views_eda(views, acled),
        'x': None if not x else {k: x.get(k) for k in ('source', 'first_post', 'last_post', 'posts', 'usable_posts',
                                                   'authors', 'posts_per_day', 'languages', 'extractor', 'extraction',
                                                   'topics', 'provinces', 'rules_provinces', 'llm_credible_provinces')},
    }


def build(history, site):
    history, site = Path(history), Path(site)
    payload = json.dumps(collect(history), ensure_ascii=False, allow_nan=False).replace('</', '<\\/')
    site.mkdir(parents=True, exist_ok=True)
    (site / 'index.html').write_text(TEMPLATE.read_text().replace('/*DATA*/null', payload))
    for name in ('dashboard.css', 'dashboard.js'):
        (site / name).write_text(TEMPLATE.with_name(name).read_text())
    return site / 'index.html'
