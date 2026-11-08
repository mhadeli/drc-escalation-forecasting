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
        'forecast': read(history / 'forecast.json'), 'live': read(history / 'live_scores.json'),
        'reference': read(history / 'reference.json'), 'status': read(history / 'pipeline_status.json'),
        'ingest': read(history / 'ingest_status.json'), 'geo': read(inputs / 'provinces.geojson'),
        # Metadata only: the dashboard does not need the full monthly tables.
        'acled': None if not acled else {k: acled.get(k) for k in ('source', 'rows', 'first_week', 'last_week', 'coverage_end',
                                                               'complete_through', 'definitions', 'primary')},
        'views': None if not views else {'cells': views.get('cells'), 'border_cells': views.get('border_cells'),
                                         'cells_per_province': views.get('cells_per_province'), 'boundaries': views.get('boundaries')},
        'x': None if not x else {k: x.get(k) for k in ('first_post', 'last_post', 'posts', 'usable_posts', 'authors',
                                                   'posts_per_day', 'languages', 'extractor')},
    }


def build(history, site):
    history, site = Path(history), Path(site)
    payload = json.dumps(collect(history), ensure_ascii=False, allow_nan=False).replace('</', '<\\/')
    site.mkdir(parents=True, exist_ok=True)
    (site / 'index.html').write_text(TEMPLATE.read_text().replace('/*DATA*/null', payload))
    for name in ('dashboard.css', 'dashboard.js'):
        (site / name).write_text(TEMPLATE.with_name(name).read_text())
    return site / 'index.html'
