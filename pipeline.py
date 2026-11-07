"""DRC fatalities forecasting: 30, 60 and 90 days per province, from ACLED history and X posts, compared with VIEWS.

  python pipeline.py ingest --acled raw_data/acled_weekly.csv --posts data/raw/drc_posts.jsonl   # local: raw data
  python pipeline.py backtest     # ACLED-history baseline against VIEWS, 2023 onward
  python pipeline.py xcheck       # X authors per province against ACLED deaths in the same week
  python pipeline.py forecast     # the one live forecast, frozen once written
  python pipeline.py score        # scores the live forecast once ACLED has coded its months
  python pipeline.py orchestrate  # every stage after ingest, as GitHub Actions runs it
  python pipeline.py dashboard

Ingest is the only step that reads raw data. It writes post-free aggregates to history/inputs/, so every later
stage, locally or on GitHub Actions, runs from committed files.
"""

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from drc_watch import acled, backtest, dashboard, provinces, views
from drc_watch import fetch_recent as x
from drc_watch import forecast as fc

HISTORY = x.HISTORY
INPUTS = HISTORY / 'inputs'
BOUNDARIES = x.DATA / 'boundaries' / 'COD_ADM1_simplified.geojson'
BOUNDARIES_URL = ('https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/COD/ADM1/'
                  'geoBoundaries-COD-ADM1_simplified.geojson')
ISSUE = '2026-11'  # The live forecast is issued on 2026-11-01.
FIRST_ISSUE = '2023-01'
EVALUATION_START = '2024-01'  # 2023 chooses the baseline window, so it is not scored.
CODE = ('drc_watch/acled.py', 'drc_watch/views.py', 'drc_watch/provinces.py', 'drc_watch/forecast.py',
        'drc_watch/backtest.py', 'pipeline.py')


def code_hash():
    digest = hashlib.sha256()
    for name in CODE:
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:12]


def read(name):
    path = INPUTS / name if not str(name).startswith('/') else Path(name)
    if not path.exists():
        raise ValueError(f'{path.name} is missing. Run pipeline.py ingest first.')
    return json.loads(path.read_text())


def issue_date(key):
    return date.fromisoformat(f'{key}-01')


def usable(row):
    return row['on_topic'] and not row['historical'] and not row.get('extraction_error')


# 1. Ingest ---------------------------------------------------------------------------------------------------

def ingest_acled(path):
    rows = acled.read_weekly(path)
    cover = acled.coverage(rows)
    weekly = {d: acled.weekly_totals(rows, d) for d in acled.DEFINITIONS}
    monthly = {d: acled.to_monthly(weekly[d], cover) for d in acled.DEFINITIONS}
    last = date.fromisoformat(cover['last_week'])
    recent = [last - timedelta(weeks=i) for i in range(12, -1, -1)]
    x.write_json(INPUTS / 'acled.json', {
        'source': 'ACLED weekly aggregated data, Democratic Republic of Congo', 'file_sha256': x.checksum(path),
        'rows': len(rows), **cover, 'definitions': {d: list(t) for d, t in acled.DEFINITIONS.items()}, 'primary': acled.PRIMARY,
        'monthly': monthly,
        'weekly': {d: {w.isoformat(): acled.week_totals(weekly[d], w) for w in recent} for d in acled.DEFINITIONS}})
    return cover


def ingest_views(cover, fetch=True):
    """Province totals for each issue's three months, from the run that was public on that day."""
    if fetch:
        views.fetch_catalog()
    runs = views.catalog()
    issues = backtest.issues_between(FIRST_ISSUE, acled.shift(cover['complete_through'], -2)) + [ISSUE]
    if not BOUNDARIES.exists():
        BOUNDARIES.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(BOUNDARIES_URL, timeout=120)
        response.raise_for_status()
        BOUNDARIES.write_bytes(response.content)
    shapes = provinces.load_shapes(BOUNDARIES)
    chosen = {issue: views.select_run(runs, issue_date(issue)) for issue in issues}
    missing = [i for i, run in chosen.items() if run is None]
    if missing:
        raise ValueError(f'No VIEWS run was public for issues {missing}.')
    for run in {r['run'] for r in chosen.values()}:
        views.fetch_run(run) if fetch else views.read_run(run)
    cells = {gid for gid, _, _ in views.read_run(chosen[ISSUE]['run'])}
    cell_province, border = provinces.assign_cells(cells, shapes, views.cell_center)
    out = {}
    for issue, run in chosen.items():
        months = [acled.shift(issue, i) for i in range(3)]
        out[issue] = {**run, 'provinces': views.province_totals(run['run'], cell_province, months)}
    per_province = Counter(cell_province.values())
    x.write_json(INPUTS / 'views.json', {
        'source': 'VIEWS fatalities, state-based, PRIO-GRID month, api.viewsforecasting.org',
        'boundaries': 'geoBoundaries gbOpen COD ADM1 (OpenStreetMap, ODbL 1.0)', 'cells': len(cell_province),
        'border_cells': len(border), 'cells_per_province': dict(sorted(per_province.items())), 'issues': out})
    x.write_json(INPUTS / 'provinces.geojson', provinces.map_geojson(shapes))
    return len(chosen), len(cell_province), len(border)


def ingest_posts(path):
    posts = x.read_jsonl(path)
    if not posts:
        raise ValueError(f'No posts in {path}')
    times = sorted(datetime.fromisoformat(p['created_at'].replace('Z', '+00:00')) for p in posts)
    tagged = [fc.classify(p) for p in posts]
    signals = fc.province_signals(tagged, times[-1])
    x.write_json(INPUTS / 'x_counts.json', {
        'source': 'X recent search, saved pull', 'posts_sha256': x.checksum(path), 'extractor': 'rules',
        'first_post': times[0].isoformat(), 'last_post': times[-1].isoformat(), 'posts': len(posts),
        'usable_posts': sum(map(usable, tagged)), 'authors': len({p.get('author_id') for p in posts}),
        'posts_per_day': dict(sorted(Counter(p['created_at'][:10] for p in posts).items())),
        'languages': dict(Counter(p.get('lang', 'und') for p in posts).most_common()),
        'provinces': signals})
    return signals


def ingest(args):
    cover = ingest_acled(args.acled)
    print(f"ACLED: {cover['first_week']} to {cover['coverage_end']}, complete through {cover['complete_through']}.")
    issues, cells, border = ingest_views(cover, fetch=not args.offline)
    print(f'VIEWS: {issues} issues, {cells} cells mapped to provinces, {border} border cells assigned to the nearest.')
    if args.posts:
        signals = ingest_posts(args.posts)
        print('X event authors:', {k: v['event'] for k, v in signals.items()})
    stamp('Ingest', 'done', f"ACLED through {cover['coverage_end']}; {issues} VIEWS issues; X {'loaded' if args.posts else 'unchanged'}.")


def stamp(stage, status, detail):
    """Ingest runs locally, so it leaves its own status for the dashboard."""
    path = HISTORY / 'ingest_status.json'
    x.write_json(path, {'stage': stage, 'status': status, 'detail': detail, 'at': x.now().isoformat(timespec='seconds')})


# 2. Backtest ---------------------------------------------------------------------------------------------------

def run_backtest(draws=backtest.DRAWS):
    data, views_in = read('acled.json'), read('views.json')
    monthly = data['monthly'][data['primary']]
    last_issue = acled.shift(data['complete_through'], -2)
    issues = backtest.issues_between(FIRST_ISSUE, last_issue)
    views_by_issue = {i: v['provinces'] for i, v in views_in['issues'].items()}
    rows = backtest.panel(monthly, views_by_issue, issues)
    window, errors = backtest.choose_window(rows)
    reference = freeze_reference(window, errors, backtest.views_scale(rows), data)
    key = f"base_{reference['window']}"
    backtest.add_scaled(rows, reference['views_scale'])
    evaluation = [r for r in rows if r['issue'] >= EVALUATION_START]
    no_gap = backtest.add_scaled(backtest.panel(monthly, views_by_issue, [i for i in issues if i >= EVALUATION_START], gap=0),
                                 reference['views_scale'])
    result = {
        'definition': data['primary'], 'window': reference['window'], 'gap_months': backtest.GAP,
        'views_scale': reference['views_scale'],
        'selection': {'period': list(backtest.SELECTION), 'errors': {str(w): e for w, e in errors.items()}},
        'evaluation': {'first_issue': EVALUATION_START, 'last_issue': last_issue, 'scores': backtest.scores(evaluation, key, draws)},
        'selection_year_scores': backtest.scores([r for r in rows if r['issue'] < EVALUATION_START], key, draws),
        'sensitivity_no_gap': backtest.scores(no_gap, key, draws),
        'national': backtest.national_series(rows, key),
        'provinces': backtest.by_province(evaluation, key),
        'province_series': backtest.province_series(rows, key),
        'views_runs': {i: v['run'] for i, v in views_in['issues'].items() if i in issues},
        'acled_complete_through': data['complete_through'], 'acled_sha256': data['file_sha256'], 'code_hash': code_hash()}
    x.write_json(HISTORY / 'backtest.json', result)
    return result


def freeze_reference(window, errors, scale, data):
    """The baseline window and the VIEWS scale are chosen once, on 2023, and kept. A run that would differ fails."""
    path = HISTORY / 'reference.json'
    if path.exists():
        saved = json.loads(path.read_text())
        if saved['window'] != window or abs(saved['views_scale'] - scale) > 1e-9:
            raise ValueError(f"2023 now picks window {window} and scale {scale:.4f}, not the frozen {saved['window']} and "
                             f"{saved['views_scale']:.4f}; the inputs changed. Investigate before deleting history/reference.json.")
        return saved
    saved = {'window': window, 'views_scale': scale, 'gap_months': backtest.GAP, 'definition': data['primary'],
             'chosen_on': list(backtest.SELECTION),
             'errors': {str(w): e for w, e in errors.items()}, 'frozen_at': x.now().isoformat(timespec='seconds'),
             'acled_sha256': data['file_sha256']}
    x.write_json(path, saved)
    return saved


# 3. X check ------------------------------------------------------------------------------------------------------

def run_xcheck():
    data, counts = read('acled.json'), read('x_counts.json')
    first = date.fromisoformat(counts['first_post'][:10])
    last = date.fromisoformat(counts['last_post'][:10])
    week = first - timedelta(days=(first.weekday() - 5) % 7)  # The ACLED week, starting Saturday, holding the first post.
    if last > week + timedelta(days=6):
        raise ValueError(f'The X posts span {first} to {last}, more than the ACLED week starting {week}.')
    deaths = {d: data['weekly'][d].get(week.isoformat()) for d in data['definitions']}
    if deaths[data['primary']] is None:
        raise ValueError(f'ACLED has no weekly totals for the week starting {week}.')
    rows = [{'province': name, 'event_authors': spot['event'], 'authors': spot['authors'],
             **{f'deaths_{d}': deaths[d][name] for d in deaths}} for name, spot in counts['provinces'].items()]
    result = {'week_start': week.isoformat(), 'week_end': (week + timedelta(days=6)).isoformat(),
              'posts_from': counts['first_post'], 'posts_to': counts['last_post'], 'rows': rows,
              'rank_event_authors_vs_state_deaths': backtest.spearman([r['event_authors'] for r in rows], [r['deaths_state'] for r in rows]),
              'rank_event_authors_vs_organised_deaths': backtest.spearman([r['event_authors'] for r in rows], [r['deaths_organised'] for r in rows]),
              'rank_all_authors_vs_state_deaths': backtest.spearman([r['authors'] for r in rows], [r['deaths_state'] for r in rows]),
              'provinces_n': len(rows)}
    x.write_json(HISTORY / 'x_check.json', result)
    return result


# 4. Live forecast ------------------------------------------------------------------------------------------------

def run_forecast(force=False):
    path = HISTORY / 'forecast.json'
    if path.exists() and not force:
        return json.loads(path.read_text()), False
    data, views_in, counts = read('acled.json'), read('views.json'), read('x_counts.json')
    reference = read(HISTORY / 'reference.json')
    issue = issue_date(ISSUE)
    if datetime.fromisoformat(counts['last_post']) >= datetime(issue.year, issue.month, 1, tzinfo=timezone.utc):
        raise ValueError('A post is dated on or after the issue date.')
    monthly = data['monthly'][data['primary']]
    run = views_in['issues'][ISSUE]
    rates = {}
    for name, series in monthly.items():
        rate = backtest.baseline_rate(series, ISSUE, reference['window'])
        if rate is None:
            raise ValueError(f'ACLED does not cover the baseline window for {name}; it needs months through {acled.shift(ISSUE, -2)}.')
        rates[name] = rate
    events = {name: counts['provinces'][name]['event'] for name in fc.X_PROVINCES}
    multipliers = fc.x_multipliers(events, {name: rates[name] for name in fc.X_PROVINCES})
    rows = []
    for name in sorted(monthly):
        row = {'province': name, 'baseline_rate': round(rates[name], 2), 'x_multiplier': None, 'x_event_authors': events.get(name)}
        if name in multipliers:
            row['x_multiplier'] = round(multipliers[name], 3)
        for days, months in fc.HORIZONS.items():
            base = rates[name] * months
            row[f'baseline_{days}'] = round(base, 1)
            row[f'x_{days}'] = round(base * fc.decayed(multipliers[name], days), 1) if name in multipliers else None
            v = backtest.views_total(run['provinces'].get(name), ISSUE, months)
            row[f'views_{days}'] = None if v is None else round(v, 1)
            row[f'views_scaled_{days}'] = None if v is None else round(v * reference['views_scale'], 1)
        rows.append(row)
    saved = {'issued': issue.isoformat(), 'definition': data['primary'], 'window_months': reference['window'],
             'views_scale': reference['views_scale'],
             'acled_through': acled.shift(ISSUE, -2), 'x_posts': [counts['first_post'], counts['last_post']],
             'views_run': {k: run[k] for k in ('run', 'released', 'last_input')},
             'months': {str(d): [acled.shift(ISSUE, i) for i in range(m)] for d, m in fc.HORIZONS.items()},
             'resolves': {str(d): (acled.month_end(acled.shift(ISSUE, m - 1)) + timedelta(days=acled.REPORTING_LAG_DAYS)).isoformat()
                          for d, m in fc.HORIZONS.items()},
             'written_at': x.now().isoformat(timespec='seconds'), 'code_hash': code_hash(), 'rows': rows}
    x.write_json(path, saved)
    return saved, True


# 5. Score ------------------------------------------------------------------------------------------------------

def run_score():
    saved, data = read(HISTORY / 'forecast.json'), read('acled.json')
    monthly = data['monthly'][saved['definition']]
    result = {'acled_complete_through': data['complete_through'], 'horizons': {}}
    for days, months in fc.HORIZONS.items():
        needed = saved['months'][str(days)]
        if needed[-1] > data['complete_through']:
            result['horizons'][str(days)] = {'status': 'pending', 'resolves': saved['resolves'][str(days)]}
            continue
        cases = [(r, sum(monthly[r['province']][m] for m in needed)) for r in saved['rows']]
        err = lambda key, subset: sum(backtest.log_error(r[f'{key}_{days}'], a) for r, a in subset) / len(subset)
        with_x = [(r, a) for r, a in cases if r[f'x_{days}'] is not None]
        result['horizons'][str(days)] = {
            'status': 'scored', 'provinces': len(cases), 'error_baseline': err('baseline', cases), 'error_views': err('views', cases),
            'error_views_scaled': err('views_scaled', cases),
            'x_provinces': len(with_x), 'error_x': err('x', with_x), 'error_baseline_x_provinces': err('baseline', with_x),
            'actual': {r['province']: round(a, 1) for r, a in cases}}
    x.write_json(HISTORY / 'live_scores.json', result)
    return result


# Orchestration ---------------------------------------------------------------------------------------------------

class Skip(Exception):
    """A stage that cannot or need not run; not a failure."""


def orchestrate(draws=backtest.DRAWS):
    def stage_inputs():
        names = ('acled.json', 'views.json', 'x_counts.json', 'provinces.geojson')
        missing = [n for n in names if not (INPUTS / n).exists()]
        if missing:
            raise Skip(f"Missing {', '.join(missing)}. Run pipeline.py ingest locally and push history/inputs.")
        data = read('acled.json')
        return f"ACLED through {data['coverage_end']} (complete through {data['complete_through']}), VIEWS and X aggregates present."

    def stage_backtest():
        if not (INPUTS / 'views.json').exists():
            raise Skip('Needs the ingested inputs.')
        s = run_backtest(draws)['evaluation']['scores']['30']
        if s['skill_views'] is None:
            return f"{s['issues']} monthly issues. The baseline made no errors, so VIEWS's relative error is undefined."
        change = -s['skill_views']
        return (f"{s['issues']} monthly issues. At 30 days VIEWS's error is {abs(change):.0%} "
                f"{'higher' if change > 0 else 'lower'} than the baseline's.")

    def stage_xcheck():
        if not (INPUTS / 'x_counts.json').exists():
            raise Skip('Needs the X counts.')
        r = run_xcheck()
        rho = r['rank_event_authors_vs_state_deaths']
        return f"{r['provinces_n']} provinces, week of {r['week_start']}; rank correlation {'n/a' if rho is None else f'{rho:.2f}'}."

    def stage_forecast():
        if not (HISTORY / 'reference.json').exists():
            raise Skip('Needs the backtest.')
        saved, new = run_forecast()
        return f"{'Written' if new else 'Frozen since'} {saved['written_at'][:10]}, issued {saved['issued']}."

    def stage_score():
        if not (HISTORY / 'forecast.json').exists():
            raise Skip('Needs the forecast.')
        h = run_score()['horizons']
        pending = [d for d, v in h.items() if v['status'] == 'pending']
        if len(pending) == len(h):
            raise Skip(f"Nothing has resolved yet; 30 days resolves about {h['30']['resolves']}.")
        return f"Scored {len(h) - len(pending)} of {len(h)} horizons."

    stages = []
    for name, action in (('Inputs', stage_inputs), ('Backtest', stage_backtest), ('X check', stage_xcheck),
                         ('Forecast', stage_forecast), ('Score', stage_score)):
        try:
            status, detail = 'done', action()
        except Skip as reason:
            status, detail = 'skipped', str(reason)
        except (ValueError, OSError, KeyError) as error:
            status, detail = 'failed', f'{type(error).__name__}: {error}'[:300]
        stages.append({'stage': name, 'status': status, 'detail': detail, 'at': x.now().isoformat(timespec='seconds')})
        print(f'{name}: {status}. {detail}')
    x.write_json(HISTORY / 'pipeline_status.json', {'finished_at': x.now().isoformat(timespec='seconds'), 'stages': stages})
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a') as handle:
            handle.write('| Stage | Status | Detail |\n|---|---|---|\n')
            handle.writelines(f"| {s['stage']} | {s['status']} | {s['detail']} |\n" for s in stages)
    return stages


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    get = sub.add_parser('ingest', help='raw ACLED, VIEWS and X data to post-free aggregates in history/inputs')
    get.add_argument('--acled', type=Path, required=True, help='ACLED weekly aggregated CSV')
    get.add_argument('--posts', type=Path, help='saved X posts, JSON lines')
    get.add_argument('--offline', action='store_true', help='use the cached VIEWS catalog and runs only')
    test = sub.add_parser('backtest')
    test.add_argument('--draws', type=int, default=backtest.DRAWS)
    sub.add_parser('xcheck')
    go = sub.add_parser('forecast')
    go.add_argument('--force', action='store_true', help='overwrite the frozen forecast')
    sub.add_parser('score')
    plan = sub.add_parser('orchestrate')
    plan.add_argument('--draws', type=int, default=backtest.DRAWS)
    sub.add_parser('dashboard')
    args = parser.parse_args()
    try:
        if args.command == 'ingest':
            ingest(args)
        elif args.command == 'backtest':
            print(json.dumps(run_backtest(args.draws)['evaluation']['scores'], indent=2))
        elif args.command == 'xcheck':
            print(json.dumps(run_xcheck(), indent=2))
        elif args.command == 'forecast':
            saved, new = run_forecast(args.force)
            print(('Written' if new else 'Already frozen; pass --force to overwrite') + f": issued {saved['issued']}")
        elif args.command == 'score':
            print(json.dumps(run_score(), indent=2))
        elif args.command == 'orchestrate':
            if any(s['status'] == 'failed' for s in orchestrate(args.draws)):
                sys.exit(1)
        else:
            print(dashboard.build(HISTORY, x.ROOT / 'site'))
    except ValueError as error:
        sys.exit(f'Error: {error}')


if __name__ == '__main__':
    main()
