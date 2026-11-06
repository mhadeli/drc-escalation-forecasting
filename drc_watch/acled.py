"""Import an ACLED export, assert its coverage, and derive reviewable boxes."""

import argparse
import calendar
import csv
import hashlib
import json
import math
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from drc_watch import forecast as fc
from drc_watch.fetch_recent import DATA, ROOT, checksum, load_env, now, write_csv, write_json

BOXES = ROOT / 'config' / 'hotspots.csv'
TOKEN_URL = 'https://acleddata.com/oauth/token'
READ_URL = 'https://acleddata.com/api/acled/read'
PAGE_SIZE = 5000
COUNTRY = 'Democratic Republic of Congo'
NOTE_WINDOW_DAYS = 30


def month_shift(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def parse_date(text):
    for fmt in ('%Y-%m-%d', '%d %B %Y', '%d %b %Y', '%d-%b-%y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Unsupported event date: {text!r}; export ISO dates if possible.')


def read_events(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        required = {'event_date', 'latitude', 'longitude', 'fatalities', 'sub_event_type'}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f'ACLED CSV needs columns: {sorted(required)}')
        events, seen = [], set()
        for line, row in enumerate(reader, 2):
            try:
                if row.get('iso') and int(row['iso']) != 180:
                    raise ValueError('Export must contain DRC (ISO numeric 180) only.')
                event_id = row.get('event_id_cnty') or str(line)
                if event_id in seen:
                    raise ValueError(f'Duplicate event ID {event_id}')
                seen.add(event_id)
                lat, lon, deaths = float(row['latitude']), float(row['longitude']), float(row['fatalities'])
                if not all(math.isfinite(v) for v in (lat, lon, deaths)) or not (-90 <= lat <= 90 and -180 <= lon <= 180 and deaths >= 0):
                    raise ValueError('Invalid coordinates or fatalities')
                events.append({'date': parse_date(row['event_date']), 'lat': lat, 'lon': lon,
                               'major': deaths >= 10 or 'territory' in row['sub_event_type'].lower(),
                               'admin1': row.get('admin1', ''), 'admin2': row.get('admin2', ''),
                               'has_note': bool((row.get('notes') or '').strip()), **fc.note_cues(row.get('notes') or '')})
            except (ValueError, TypeError) as error:
                raise ValueError(f'ACLED line {line}: {error}') from None
    if not events:
        raise ValueError('ACLED export is empty.')
    return sorted(events, key=lambda e: e['date'])


def import_file(path, coverage_start, coverage_end):
    """Coverage is asserted by the operator, not guessed from event dates."""
    path = Path(path).resolve()
    events = read_events(path)
    start, end = date.fromisoformat(coverage_start), date.fromisoformat(coverage_end)
    if start > end or end > now().date():
        raise ValueError('Coverage must be ordered and cannot extend into the future.')
    if events[0]['date'] < start or events[-1]['date'] > end:
        raise ValueError('Export contains events outside the asserted coverage interval.')
    digest = checksum(path)
    target = DATA / 'acled' / f'{digest[:16]}.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(path, target)
    meta = {'file': target.name, 'sha256': digest, 'retrieved_at': now().isoformat(),
            'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(), 'events': len(events),
            'notes': sum(e['has_note'] for e in events),
            'availability': 'revised snapshot; event-date lag is not a publication vintage'}
    write_json(DATA / 'acled' / 'snapshot.json', meta)
    derive_boxes(events, digest)
    return meta


def download(path, start, end):
    """myACLED login, then every page of DRC events. Returns False when no credentials are set."""
    env = load_env()
    if not (env.get('ACLED_EMAIL') and env.get('ACLED_PASSWORD')):
        return False
    login = requests.post(TOKEN_URL, timeout=60, data={'username': env['ACLED_EMAIL'], 'password': env['ACLED_PASSWORD'],
                          'grant_type': 'password', 'client_id': 'acled', 'scope': 'authenticated'})
    if b'_cf_chl' in login.content or b'Just a moment' in login.content:
        raise ValueError('Cloudflare blocked the ACLED login from this network. Use a myACLED export locally and publish the results.')
    if login.status_code != 200:
        raise ValueError(f'ACLED login failed with HTTP {login.status_code}; check the credentials and access level.')
    headers, rows, page = {'Authorization': f"Bearer {login.json()['access_token']}"}, [], 1
    while True:
        response = requests.get(READ_URL, headers=headers, timeout=120, params={
            '_format': 'json', 'country': COUNTRY, 'event_date': f'{start}|{end}', 'event_date_where': 'BETWEEN',
            'limit': PAGE_SIZE, 'page': page})
        if response.status_code != 200:
            raise ValueError(f'ACLED read failed with HTTP {response.status_code}.')
        batch = response.json().get('data') or []
        rows += batch
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    if not rows:
        raise ValueError('ACLED returned no rows; the account access level may not include event-level data.')
    fields = sorted({key for row in rows for key in row})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return True


def derive_boxes(events, source):
    """Draft boxes from administrative event extents; never auto-approve them."""
    existing = {}
    if BOXES.exists():
        with BOXES.open(newline='') as handle:
            existing = {r['hotspot']: r for r in csv.DictReader(handle)}
    rows = []
    for name, (province, aliases) in fc.HOTSPOTS.items():
        old = existing.get(name, {})
        if old.get('reviewed') == '1':
            rows.append(old)
            continue
        matches = [e for e in events if fc.normalize(e['admin2']) in aliases or
                   (name in ('Tanganyika', 'Kinshasa (political violence)') and fc.normalize(e['admin1']) == fc.normalize(province))]
        row = {'hotspot': name, 'province': province, 'south': '', 'west': '', 'north': '', 'east': '',
               'reviewed': '0', 'source': source}
        if matches:
            row.update(south=min(e['lat'] for e in matches) - .000001,
                       west=min(e['lon'] for e in matches) - .000001,
                       north=max(e['lat'] for e in matches) + .000001,
                       east=max(e['lon'] for e in matches) + .000001)
        rows.append(row)
    write_csv(BOXES, rows)


def load_boxes(path=None):
    path = path or BOXES
    if not path.exists():
        raise ValueError('Import ACLED first, then review config/hotspots.csv.')
    with path.open(newline='') as handle:
        rows = list(csv.DictReader(handle))
    if {r['hotspot'] for r in rows} != set(fc.HOTSPOTS) or len(rows) != len(fc.HOTSPOTS):
        raise ValueError('config/hotspots.csv must contain each of the 20 hotspots exactly once.')
    result = {}
    for row in rows:
        if row['reviewed'] != '1':
            raise ValueError(f"Review the box for {row['hotspot']} and set reviewed=1 in config/hotspots.csv.")
        bounds = [float(row[k]) for k in ('south', 'west', 'north', 'east')]
        south, west, north, east = bounds
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise ValueError(f"Invalid box for {row['hotspot']}")
        result[row['hotspot']] = bounds
    return result


def boxes_hash(path=None):
    """Hash the boundaries, not the file, so re-saving it with other line endings changes nothing."""
    with (path or BOXES).open(newline='') as handle:
        rows = sorted((r['hotspot'], *(float(r[k] or 'nan') for k in ('south', 'west', 'north', 'east'))) for r in csv.DictReader(handle))
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def inside(lat, lon, bounds):
    south, west, north, east = bounds
    return south <= lat < north and west <= lon < east


def load_snapshot():
    meta_path = DATA / 'acled' / 'snapshot.json'
    if not meta_path.exists():
        raise ValueError('No ACLED snapshot. Use fetch --acled FILE --coverage-start DATE --coverage-end DATE.')
    meta = json.loads(meta_path.read_text())
    path = meta_path.parent / meta['file']
    if checksum(path) != meta['sha256']:
        raise ValueError('ACLED snapshot checksum changed; re-import the export.')
    return read_events(path), meta


def major_dates(events, boxes):
    return {name: sorted({e['date'] for e in events if e['major'] and inside(e['lat'], e['lon'], bounds)})
            for name, bounds in boxes.items()}


def note_history(events, boxes):
    """Per hotspot, each dated note cue. Separate from major events: any event's note can carry a cue."""
    keys = ('control_change', 'escalation', 'deescalation')
    return {name: [(e['date'], *(e[k] for k in keys)) for e in events if inside(e['lat'], e['lon'], bounds)]
            for name, bounds in boxes.items()}


def note_counts(history, issue, lag=14, window=NOTE_WINDOW_DAYS):
    """Notes with each cue in the window ending at the reporting-lag cutoff."""
    cutoff = issue - timedelta(days=lag)
    rows = [row for row in history if cutoff - timedelta(days=window) < row[0] <= cutoff]
    return {key: sum(row[i + 1] for row in rows) for i, key in enumerate(('control_change', 'escalation', 'deescalation'))}


def recent_rate(dates, issue, lag=14):
    cutoff = issue - timedelta(days=lag)
    occupied = sum(any(month_shift(cutoff, -i - 1) < d <= month_shift(cutoff, -i) for d in dates)
                   for i in range(12))
    return (occupied + .5) / 12.5


def target(dates, issue, horizon, coverage_end):
    end = issue + timedelta(days=horizon)
    if end > coverage_end:
        return None
    return int(any(issue < day <= end for day in dates))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', required=True, type=Path)
    parser.add_argument('--coverage-start', required=True)
    parser.add_argument('--coverage-end', required=True)
    args = parser.parse_args()
    print(json.dumps(import_file(args.file, args.coverage_start, args.coverage_end), indent=2))
