"""Static dashboard from history/: no API calls, no post text, no raw ACLED rows."""

import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name('dashboard.html')


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def collect(history):
    inputs = history / 'inputs'
    acled = read(inputs / 'acled.json')
    views = read(inputs / 'views.json')
    x = read(inputs / 'x_counts.json')
    return {
        'backtest': read(history / 'backtest.json'), 'xcheck': read(history / 'x_check.json'),
        'forecast': read(history / 'forecast.json'), 'status': read(history / 'pipeline_status.json'),
        'ingest': read(history / 'ingest_status.json'),
        'acled': None if not acled else {k: acled.get(k) for k in ('first_week', 'coverage_end')},
        # National monthly totals only, for the time chart; never per-event rows.
        'national_actual': None if not acled else {
            m: round(sum(series[m] for series in acled['monthly'][acled['primary']].values()), 1)
            for m in sorted(next(iter(acled['monthly'][acled['primary']].values())))},
        'views': None if not views else {'cells': views.get('cells')},
        'x': None if not x else {k: x.get(k) for k in ('first_post', 'last_post', 'posts', 'authors',
                                                   'extractor', 'topics', 'rules_provinces')},
    }


def build(history, site):
    history, site = Path(history), Path(site)
    payload = json.dumps(collect(history), ensure_ascii=False, allow_nan=False).replace('</', '<\\/')
    site.mkdir(parents=True, exist_ok=True)
    (site / 'index.html').write_text(TEMPLATE.read_text().replace('/*DATA*/null', payload))
    for name in ('dashboard.css', 'dashboard.js'):
        (site / name).write_text(TEMPLATE.with_name(name).read_text())
    return site / 'index.html'
