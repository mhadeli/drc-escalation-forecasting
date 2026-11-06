"""Static dashboard from history/ and the hotspot boxes: no API calls and no post text."""

import base64
import csv
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name('dashboard.html')

# Approximate territory centres for map labels; not exact event locations and not used in any score.
CENTRES = {
    'Walungu (Kaziba, Muhungu, Kamanyola)': (-2.75, 28.75), 'Uvira (Ruzizi plain)': (-3.10, 29.10),
    'Fizi (Minembwe, Hauts Plateaux)': (-3.85, 28.90), 'Mwenga (Itombwe, Kamituga)': (-3.10, 28.35),
    'Kalehe (Minova, Numbi)': (-1.95, 28.95), 'Kabare / Bukavu': (-2.50, 28.86), 'Shabunda': (-2.69, 27.34),
    'Goma / Nyiragongo': (-1.68, 29.22), 'Masisi': (-1.40, 28.82), 'Rutshuru': (-1.19, 29.45),
    'Walikale': (-1.42, 28.05), 'Lubero / Butembo': (0.00, 29.15), 'Beni': (0.49, 29.47), 'Djugu': (1.92, 30.50),
    'Irumu / Bunia': (1.56, 30.25), 'Mambasa': (1.36, 29.05), 'Mahagi / Aru': (2.60, 30.90),
    'Tanganyika': (-5.94, 29.19), 'Kwamouth (Mobondo)': (-3.18, 16.22), 'Kinshasa (political violence)': (-4.32, 15.31),
}


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def image(path):
    return f'data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}' if path.exists() else None


def boxes(path):
    if not path or not Path(path).exists():
        return {}
    with Path(path).open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    return {r['hotspot']: {'bounds': [float(r[k]) for k in ('south', 'west', 'north', 'east')], 'reviewed': r['reviewed'] == '1'}
            for r in rows if r['south']}


def collect(history, boxes_path):
    runs = []
    for path in sorted(history.glob('runs/*/forecast.json')):
        saved = read(path)
        saved['health'] = read(path.with_name('health.json'))
        saved['scores'] = read(path.with_name('scores.json'))
        runs.append(saved)
    return {'runs': runs, 'live': read(history / 'live_scores.json'), 'backtest': read(history / 'backtest' / 'scores.json'),
            'hotspots': read(history / 'backtest' / 'hotspots.json'), 'views': read(history / 'views_map.json'),
            'status': read(history / 'pipeline_status.json'), 'reference': read(history / 'reference.json'),
            'centres': CENTRES, 'boxes': boxes(boxes_path),
            'plots': {'recent': image(history / 'backtest' / 'reliability_recent.png'),
                      'text': image(history / 'backtest' / 'reliability_text.png'),
                      'views': image(history / 'backtest' / 'reliability_views.png')}}


def build(history, site, boxes_path=None):
    history, site = Path(history), Path(site)
    payload = json.dumps(collect(history, boxes_path), ensure_ascii=False, allow_nan=False).replace('</', '<\\/')
    site.mkdir(parents=True, exist_ok=True)
    (site / 'index.html').write_text(TEMPLATE.read_text().replace('/*DATA*/null', payload))
    return site / 'index.html'
