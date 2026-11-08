"""Dated VIEWS production runs, complete local caches, and PRIO-GRID mapping."""

import csv
import json
import math
import re
import time
from datetime import date
from urllib.parse import urlparse

import requests

from drc_watch.acled import shift
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
        raise ValueError('No VIEWS catalog. Run pipeline.py ingest first.')
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


def cell_center(gid):
    if not 1 <= gid <= 259200:
        raise ValueError('Invalid PRIO-GRID ID.')
    row, column = divmod(gid - 1, 720)
    return -90 + row * .5 + .25, -180 + column * .5 + .25


def month_index(key):
    """VIEWS month_id for 'YYYY-MM': January 1980 is 1."""
    year, month = map(int, key.split('-'))
    return (year - 1980) * 12 + month


def select_run(runs, issue):
    """Newest run released strictly before the issue date, with input data at least two calendar months old."""
    cutoff = shift(f'{issue.year}-{issue.month:02d}', -2) + '-01'
    candidates = [r for r in runs if r['released'] < issue.isoformat() and r['last_input'] <= cutoff]
    return max(candidates, key=lambda r: (r['last_input'], r['released'], r['run']), default=None)


def read_run(run):
    path, meta_path = OUT / f'{run}.csv', OUT / f'{run}.meta.json'
    if not path.exists() or not meta_path.exists():
        raise ValueError(f'VIEWS run {run} is not cached. Run pipeline.py ingest with network access.')
    if checksum(path) != json.loads(meta_path.read_text())['sha256']:
        raise ValueError(f'VIEWS cache checksum mismatch: {run}')
    with path.open(newline='') as handle:
        return [(int(r['pg_id']), int(r['month_id']), float(r['main_mean'])) for r in csv.DictReader(handle)]


def province_totals(run, cell_province, months):
    """{province: {'YYYY-MM': expected deaths}} for the given months. A month absent from the run stays absent."""
    wanted = {month_index(key): key for key in months}
    totals, seen = {}, {}
    for gid, month, value in read_run(run):
        if month not in wanted:
            continue
        if gid not in cell_province:
            raise ValueError(f'VIEWS cell {gid} in {run} has no province; rebuild the cell map.')
        name, key = cell_province[gid], wanted[month]
        totals.setdefault(name, {}).setdefault(key, 0.0)
        totals[name][key] += value
        seen.setdefault(key, set()).add(gid)
    for key, cells in seen.items():
        if cells != set(cell_province):
            raise ValueError(f'{run} covers {len(cells)} of {len(cell_province)} cells in {key}; incomplete runs are not summed.')
    return {name: {key: round(value, 4) for key, value in series.items()} for name, series in totals.items()}
