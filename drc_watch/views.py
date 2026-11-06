"""Dated VIEWS production runs, complete local caches, and PRIO-GRID mapping."""

import calendar
import csv
import json
import math
import re
import time
from datetime import date, timedelta
from urllib.parse import urlparse

import requests

from drc_watch.acled import inside, month_shift
from drc_watch.fetch_recent import DATA, checksum, now, write_csv, write_json

OUT = DATA / 'views'
CATALOG_URL = 'https://raw.githubusercontent.com/wiki/prio-data/views_api/Available-datasets.md'
API = 'https://api.viewsforecasting.org'
NEGATIVE_TOLERANCE = .01  # Fatalities per cell-month.


def parse_catalog(text):
    runs = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) < 6 or not re.fullmatch(r'fatalities\d{3}_\d{4}_\d{2}_t\d{2}', cells[0]):
            continue
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', cells[5]):
            continue  # Planned releases and imprecise dates cannot establish availability.
        runs.append({'run': cells[0], 'last_input': cells[3] + '-01', 'released': cells[5]})
    if not runs:
        raise ValueError('No dated VIEWS production runs found; check the catalog format.')
    return runs


def fetch_catalog():
    response = requests.get(CATALOG_URL, timeout=60)
    response.raise_for_status()
    runs = parse_catalog(response.text)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'catalog.md').write_text(response.text)
    write_json(OUT / 'catalog.json', {'source': CATALOG_URL, 'retrieved_at': now().isoformat(), 'runs': runs})
    return runs


def catalog():
    path = OUT / 'catalog.json'
    if not path.exists():
        raise ValueError('No VIEWS catalog. Run pipeline.py fetch first.')
    return json.loads(path.read_text())['runs']


def read_forecast(row):
    """Return (fatalities, probability). fatalities001 publishes ln(fatalities + 1)."""
    if 'main_mean' in row:
        return float(row['main_mean']), float(row['main_dich'])
    if 'sc_pgm_sb_main' in row:
        return math.expm1(float(row['sc_pgm_sb_main'])), float(row['sc_pgm_sb_dich_main'])
    raise ValueError(f'Unknown VIEWS forecast fields: {sorted(row)}')


def fetch_run(run, pause=3):
    path = OUT / f'{run}.csv'
    meta_path = OUT / f'{run}.meta.json'
    if path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if checksum(path) != meta['sha256']:
            raise ValueError(f'Changed VIEWS cache: {path}; restore it before continuing.')
        return meta
    url = f'{API}/{run}/pgm/sb?iso=COD&pagesize=50000'
    first_url, rows, expected, pages = url, [], None, 0
    while url:
        if urlparse(url).netloc != urlparse(API).netloc:
            raise ValueError('Unexpected VIEWS pagination host.')
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        payload = response.json()
        metadata = payload.get('metadata', payload)
        if expected is None:
            expected = int(metadata['row_count'])
        rows.extend(payload['data'])
        pages += 1
        url = metadata.get('next_page')
        if pages > 100 or len(rows) > expected:
            raise ValueError(f'Invalid VIEWS pagination for {run}')
        if url:
            time.sleep(pause)
    if not rows or len(rows) != expected:
        raise ValueError(f'Incomplete VIEWS response for {run}: {len(rows)} / {expected} rows')
    clean, clipped = [], 0
    for row in rows:
        value, probability = read_forecast(row)
        if -NEGATIVE_TOLERANCE <= value < 0:  # Ensemble rounding noise; larger negatives still fail below.
            value, clipped = 0.0, clipped + 1
        if not math.isfinite(value) or value < 0 or not 0 <= probability <= 1:
            raise ValueError(f'Invalid VIEWS forecast in {run}')
        clean.append({'pg_id': int(row['pg_id']), 'month_id': int(row['month_id']),
                      'main_mean': value, 'main_dich': probability})
    keys = {(r['pg_id'], r['month_id']) for r in clean}
    if len(keys) != len(clean):
        raise ValueError(f'Duplicate cell-months in {run}')
    write_csv(path, clean)
    meta = {'run': run, 'url': first_url, 'retrieved_at': now().isoformat(),
            'rows': len(clean), 'pages': pages, 'clipped_negative': clipped, 'sha256': checksum(path)}
    write_json(meta_path, meta)
    return meta


def fetch_all(pause=3, selected=None):
    runs = fetch_catalog()
    eligible = [r for r in runs if r['released'] <= now().date().isoformat()]
    if selected:
        eligible = [r for r in eligible if r['run'] in selected]
        if {r['run'] for r in eligible} != set(selected):
            raise ValueError('A requested VIEWS run is absent or has not been released.')
    for run in sorted(eligible, key=lambda r: (r['last_input'], r['released'])):
        missing = not (OUT / f"{run['run']}.meta.json").exists()
        fetch_run(run['run'], pause)
        print(f"cached {run['run']}")
        if missing:
            time.sleep(pause)


def cell_id(lat, lon):
    if not (-90 <= lat < 90 and -180 <= lon < 180):
        raise ValueError('Coordinates outside the PRIO-GRID.')
    return math.floor((lat + 90) * 2) * 720 + math.floor((lon + 180) * 2) + 1


def cell_center(gid):
    if not 1 <= gid <= 259200:
        raise ValueError('Invalid PRIO-GRID ID.')
    row, column = divmod(gid - 1, 720)
    return -90 + row * .5 + .25, -180 + column * .5 + .25


def month_id(day):
    return (day.year - 1980) * 12 + day.month


def select_run(runs, issue):
    cutoff = month_shift(issue.replace(day=1), -2).isoformat()
    candidates = [r for r in runs if r['released'] < issue.isoformat() and r['last_input'] <= cutoff]
    return max(candidates, key=lambda r: (r['last_input'], r['released'], r['run']), default=None)


def load_monthly(run, boxes):
    """Sum cell means; preserve missing cell-months as missing, not zero."""
    path = OUT / f'{run}.csv'
    meta_path = OUT / f'{run}.meta.json'
    if not path.exists() or not meta_path.exists():
        return {}
    if checksum(path) != json.loads(meta_path.read_text())['sha256']:
        raise ValueError(f'VIEWS cache checksum mismatch: {run}')
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    by_month, all_cells = {}, set()
    for row in rows:
        gid, month = int(row['pg_id']), int(row['month_id'])
        by_month.setdefault(month, {})[gid] = float(row['main_mean'])
        all_cells.add(gid)
    result = {}
    for name, bounds in boxes.items():
        cells = {gid for gid in all_cells if inside(*cell_center(gid), bounds)}
        result[name] = {month: sum(values[gid] for gid in cells)
                        for month, values in by_month.items() if cells and cells <= values.keys()}
    return result


def latest_map(issue):
    """Expected fatalities per grid cell over the next three calendar months, from the newest eligible run."""
    run = select_run(catalog(), issue)
    if not run:
        raise ValueError(f'No VIEWS run was available on {issue}.')
    months = [month_id(issue) + i for i in (1, 2, 3)]
    totals = {}
    with (OUT / f"{run['run']}.csv").open(newline='') as handle:
        for row in csv.DictReader(handle):
            if int(row['month_id']) in months:
                totals[int(row['pg_id'])] = totals.get(int(row['pg_id']), 0) + float(row['main_mean'])
    if not totals:
        raise ValueError(f"{run['run']} does not cover the next three months.")
    return {**run, 'issue': issue.isoformat(), 'months': months, 'total': round(sum(totals.values()), 1),
            'cells': [[*cell_center(gid), round(value, 3)] for gid, value in sorted(totals.items())]}


def expected(monthly, issue, horizon):
    """Exact day windows; monthly means are assumed uniform within a month."""
    start, end, total = issue + timedelta(days=1), issue + timedelta(days=horizon), 0.0
    while start <= end:
        key = month_id(start)
        if key not in monthly:
            return None
        days = calendar.monthrange(start.year, start.month)[1]
        stop = min(end, start.replace(day=days))
        total += monthly[key] * ((stop - start).days + 1) / days
        start = stop + timedelta(days=1)
    return total
