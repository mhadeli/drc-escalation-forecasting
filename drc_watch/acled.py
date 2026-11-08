"""ACLED weekly aggregated data: DRC province-week fatalities, split into calendar months."""

import calendar
import csv
from collections import defaultdict
from datetime import date, timedelta

from drc_watch.provinces import PROVINCES

COUNTRY = 'Democratic Republic of Congo'
COLUMNS = ('week', 'country', 'admin1', 'event_type', 'events', 'fatalities')
# The primary definition is the closest ACLED match to VIEWS's state-based UCDP deaths.
DEFINITIONS = {
    'state': ('Battles', 'Explosions/Remote violence'),
    'organised': ('Battles', 'Explosions/Remote violence', 'Violence against civilians'),
}
PRIMARY = 'state'
REPORTING_LAG_DAYS = 14  # A month counts as complete only this long after it ends.
PRECURSORS = ('event', 'control_change', 'escalation', 'deescalation')
CONTROL_CHANGE_SUBTYPES = ('Non-state actor overtakes territory', 'Government regains territory')
ESCALATION_SUBTYPES = ('Change to group/activity', 'Looting/property destruction')
ESCALATION_TYPES = ('Riots', 'Protests')
DEESCALATION_SUBTYPES = ('Agreement',)


def number(text):
    if text is None or not text.strip():
        raise ValueError('Missing counts must not be interpreted as zero.')
    value = int(text.replace(',', ''))
    if value < 0:
        raise ValueError('Counts cannot be negative.')
    return value


def month_key(day):
    return f'{day.year}-{day.month:02d}'


def shift(key, months):
    year, month = map(int, key.split('-'))
    index = year * 12 + month - 1 + months
    return f'{index // 12}-{index % 12 + 1:02d}'


def month_end(key):
    year, month = map(int, key.split('-'))
    return date(year, month, calendar.monthrange(year, month)[1])


def read_weekly(path):
    """DRC rows only. The export may include the Republic of Congo, whose provinces are dropped by country."""
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing = set(COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'Not an ACLED weekly aggregated file; missing columns {sorted(missing)}')
        rows = [r for r in reader if r['country'] == COUNTRY]
    if not rows:
        raise ValueError(f'No rows for {COUNTRY}.')
    seen = set()
    for row in rows:
        week = date.fromisoformat(row['week'])
        if week.weekday() != 5:
            raise ValueError(f'ACLED weeks start on Saturday; {week} does not.')
        if row['admin1'] not in PROVINCES:
            raise ValueError(f"Unknown DRC province: {row['admin1']}")
        key = tuple(row.get(k) for k in ('week', 'admin1', 'event_type', 'sub_event_type'))
        if key in seen:
            raise ValueError(f'Duplicate province/week/event-type row: {key}')
        seen.add(key)
        row['week'], row['fatalities'], row['events'] = week, number(row['fatalities']), number(row['events'])
    return rows


def weekly_totals(rows, definition=PRIMARY):
    """{(province, week start): fatalities}; weeks with no qualifying events are absent and mean zero."""
    types = DEFINITIONS[definition]
    totals = defaultdict(int)
    for row in rows:
        if row['event_type'] in types:
            totals[row['admin1'], row['week']] += row['fatalities']
    return dict(totals)


def weekly_precursors(rows):
    """Extract weekly precursor event counts matching the X signal schema (event, control_change, escalation, deescalation)."""
    counts = {name: defaultdict(int) for name in PRECURSORS}
    for row in rows:
        key = (row['admin1'], row['week'])
        etype, subtype, events = row['event_type'], row.get('sub_event_type', ''), row['events']
        if etype in DEFINITIONS[PRIMARY]:
            counts['event'][key] += events
        if subtype in CONTROL_CHANGE_SUBTYPES:
            counts['control_change'][key] += events
        if subtype in ESCALATION_SUBTYPES or etype in ESCALATION_TYPES:
            counts['escalation'][key] += events
        if subtype in DEESCALATION_SUBTYPES:
            counts['deescalation'][key] += events
    return {name: dict(series) for name, series in counts.items()}


def coverage(rows, as_of=None):
    first, last = min(r['week'] for r in rows), max(r['week'] for r in rows)
    end = min(last + timedelta(days=6), as_of or date.today())
    complete = month_key(end - timedelta(days=REPORTING_LAG_DAYS))
    if month_end(complete) > end - timedelta(days=REPORTING_LAG_DAYS):
        complete = shift(complete, -1)
    start = month_key(first) if first.day == 1 else shift(month_key(first), 1)
    return {'first_week': first.isoformat(), 'last_week': last.isoformat(), 'coverage_end': end.isoformat(),
            'first_month': start, 'complete_through': complete}


def to_monthly(weekly, cover):
    """Split each week across calendar months by days, the best a weekly total allows. Totals are conserved."""
    months = []
    key = cover['first_month']
    while key <= cover['complete_through']:
        months.append(key)
        key = shift(key, 1)
    monthly = {name: {key: 0.0 for key in months} for name in PROVINCES}
    for (name, week), deaths in weekly.items():
        for offset in range(7):
            key = month_key(week + timedelta(days=offset))
            if key in monthly[name]:
                monthly[name][key] += deaths / 7
    return {name: {key: round(value, 4) for key, value in series.items()} for name, series in monthly.items()}


def week_totals(weekly, week):
    return {name: weekly.get((name, week), 0) for name in PROVINCES}
