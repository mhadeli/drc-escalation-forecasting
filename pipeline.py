"""DRC escalation watch: do X posts add forecasting skill over a baseline that already exists?

  python pipeline.py fetch --acled export.csv --coverage-start 2018-01-01 --coverage-end 2026-10-31
  (review config/hotspots.csv and set reviewed=1 for every hotspot)
  python pipeline.py build
  python pipeline.py backtest
  python pipeline.py run --fetch --extractor gemini-3.8-flash    # or: run --posts saved.jsonl
  python pipeline.py score
  python pipeline.py publish && python pipeline.py dashboard
  python pipeline.py orchestrate --pull-x                        # every stage in order, as CI runs it

Raw posts, extractions and briefs stay in data/ (gitignored). Only post-free files reach history/.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from drc_watch import acled, backtest, dashboard, views
from drc_watch import fetch_recent as x
from drc_watch import forecast as fc

RUNS = x.DATA / 'runs'
HISTORY = x.HISTORY
PUBLISHED = ('manifest.json', 'forecast.json', 'health.json')
CODE = ('drc_watch/fetch_recent.py', 'drc_watch/forecast.py', 'drc_watch/extract_llm.py', 'drc_watch/acled.py',
        'drc_watch/views.py', 'drc_watch/backtest.py', 'pipeline.py')
PLACE_CUES = (r"(?:village|localite|groupement|chefferie|territoire|cite|secteur|axe|near|village of|town of|localities? of)"
              r" (?:de |d'|du |of )?([a-z][a-z\-]{3,})")


def code_hash():
    digest = hashlib.sha256()
    for name in CODE:
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:12]


def fetch(args):
    if not args.acled and args.no_views:
        raise ValueError('Nothing to fetch.')
    if args.acled:
        if not (args.coverage_start and args.coverage_end):
            raise ValueError('--acled needs --coverage-start and --coverage-end, the dates the export was filtered to.')
        meta = acled.import_file(args.acled, args.coverage_start, args.coverage_end)
        print(f"ACLED: {meta['events']} events. Review config/hotspots.csv and set reviewed=1 before build.")
    if not args.no_views:
        views.fetch_all(args.pause)


def preflight(issue):
    """Everything a live forecast needs, checked before any paid API call."""
    path = HISTORY / 'reference.json'
    if not path.exists():
        raise ValueError('No frozen reference from the backtest. Run build and backtest first.')
    reference = json.loads(path.read_text())
    if reference['hotspots_sha256'] != acled.boxes_hash():
        raise ValueError('config/hotspots.csv changed after the reference baseline was frozen.')
    events, meta = acled.load_snapshot()
    cutoff = issue - timedelta(days=14)
    if date.fromisoformat(meta['coverage_end']) < cutoff or date.fromisoformat(meta['coverage_start']) > acled.month_shift(cutoff, -12):
        raise ValueError(f"ACLED covers {meta['coverage_start']} to {meta['coverage_end']}; "
                         f'issue {issue} needs {acled.month_shift(cutoff, -12)} to {cutoff}. Import a newer export.')
    boxes = acled.load_boxes()
    return reference, meta, boxes, acled.major_dates(events, boxes), acled.note_history(events, boxes)


def post_time(post):
    return datetime.fromisoformat(post['created_at'].replace('Z', '+00:00'))


def snapshot_from_file(path):
    """A saved posts file: the forecast is issued at its last post, so nothing later can leak in."""
    posts = x.read_jsonl(path)
    if not posts:
        raise ValueError(f'No posts in {path}')
    last = max(post_time(p) for p in posts)
    return posts, {'posts_file': str(path), 'sha256': x.checksum(path), 'retrieved_at': last.isoformat(),
                   'requested': len(posts), 'returned': len(posts), 'query': None,
                   'posts_per_day': dict(sorted(Counter(p['created_at'][:10] for p in posts).items())),
                   'languages': dict(Counter(p.get('lang', 'und') for p in posts))}


def extract(posts, extractor):
    if extractor == 'rules':
        return [fc.classify(post) for post in posts], {'extractor': 'rules'}
    from drc_watch import extract_llm
    tagged, stats = extract_llm.tag_posts(posts, extractor)
    return tagged, {'extractor': 'llm', **stats}


def usable(row):
    return row['on_topic'] and not row['historical'] and not row.get('extraction_error')


def health(summary, tagged, rows):
    known = {alias for _, aliases in fc.HOTSPOTS.values() for alias in aliases}
    gaps = Counter()
    for row in filter(usable, tagged):
        found = re.findall(PLACE_CUES, fc.normalize(row['text'])) + [fc.normalize(p) for p in row.get('other_places') or []]
        gaps.update(place for place in found if place not in known)
    share = sum(map(usable, tagged)) / max(len(tagged), 1)
    errors = sum(1 for row in tagged if row.get('extraction_error'))
    warnings = []
    if summary.get('stopped_early'):
        warnings.append(f"X pull stopped early: {summary['stopped_early']}")
    if summary['returned'] < .8 * summary['requested']:
        warnings.append(f"Only {summary['returned']} of {summary['requested']} posts returned.")
    if share < .5:
        warnings.append(f'Usable share {share:.0%} is below 50%.')
    if errors > .02 * len(tagged):
        warnings.append(f'{errors} extraction errors.')
    if any(row['p30_views'] is None for row in rows):
        warnings.append('No VIEWS forecast for: ' + ', '.join(r['hotspot'] for r in rows if r['p30_views'] is None))
    return {'posts_returned': summary['returned'], 'usable_share': round(share, 3), 'languages': summary['languages'],
            'posts_per_day': summary['posts_per_day'], 'extraction_errors': errors,
            'gazetteer_gaps': gaps.most_common(10), 'warnings': warnings}


def brief(saved, checks, tagged):
    lines = [f"# DRC escalation watch, issued {saved['issued']}", '',
             f"Target: {fc.TARGET}. Baseline: ACLED history. The X adjustment is an untested hypothesis, "
             'so compare each number with its baseline.', '',
             '| Hotspot | 30 days | Baseline | 60 days | 90 days | Signal: events, control, escalation, de-escalation |',
             '|---|---:|---:|---:|---:|---|']
    for r in saved['rows'][:8]:
        lines.append(f"| {r['hotspot']} | {r['p30']:.0%} | {r['p30_baseline']:.0%} | {r['p60']:.0%} | {r['p90']:.0%} | "
                     f"{r['event']}, {r['control_change']}, {r['escalation']}, {r['deescalation']} |")
    lines += ['', '## Evidence for the largest changes', '']
    for r in sorted(saved['rows'], key=lambda r: r['p30'] - r['p30_baseline'], reverse=True)[:3]:
        posts = [p for p in tagged if usable(p) and r['hotspot'] in p['hotspots'] and p['event']]
        lines.append(f"**{r['hotspot']}**")
        lines += [f"- {p['created_at'][:10]}: {' '.join(p['text'].split())[:240]}" for p in posts[:2]] or ['- No event posts.']
        lines.append('')
    lines += ['## Caveats', ''] + [f'- {w}' for w in checks['warnings'] or ['No health warnings this run.']]
    lines.append('- X volume reflects attention as much as violence, and partisan accounts on every side post victory claims.')
    return '\n'.join(lines) + '\n'


def run(fetch_posts=False, posts_file=None, extractor='rules', target=1000):
    if fetch_posts and posts_file:
        raise ValueError('Use --fetch or --posts, not both.')
    if posts_file:
        posts, summary = snapshot_from_file(posts_file)
    elif fetch_posts:
        posts, summary = None, None
    else:
        summary = x.latest_summary()
        posts = x.read_jsonl(summary['posts_file'])
    issue = x.now().date() if fetch_posts else date.fromisoformat(summary['retrieved_at'][:10])
    reference, meta, boxes, dates, notes = preflight(issue)
    baselines = backtest.live_baselines(issue, dates, boxes, reference, notes)  # Fails here, before X credits are spent.
    if fetch_posts:
        token = x.load_env().get('X_BEARER_TOKEN')
        if not token:
            raise ValueError('Set X_BEARER_TOKEN in .env.')
        posts, details = x.collect(token, target)
        summary = x.save_snapshot(posts, target, details)
    issued_at = datetime.fromisoformat(summary['retrieved_at'])
    tagged, stats = extract(posts, extractor)
    signals = fc.hotspot_signals(tagged, issued_at)
    rows = []
    for name, base in baselines.items():
        multiplier = fc.signal_multiplier(signals[name])
        adjusted = fc.adjust({h: base[f'p{h}_baseline'] for h in fc.HORIZONS}, multiplier)
        rows.append({**base, **signals[name], 'signal_multiplier': round(multiplier, 3), **{f'p{h}': adjusted[h] for h in fc.HORIZONS}})
    rows.sort(key=lambda r: r['p30'], reverse=True)
    run_id = x.now().strftime('%Y%m%dT%H%M%S%fZ')
    out = RUNS / run_id
    saved = {'run_id': run_id, 'issued': issue.isoformat(), 'target': fc.TARGET, 'reference': reference['baseline'],
             'hotspots_sha256': reference['hotspots_sha256'], 'extractor': stats,
             'resolve_by': {f'p{h}': (issue + timedelta(days=h)).isoformat() for h in fc.HORIZONS}, 'rows': rows}
    checks = health(summary, tagged, rows)
    x.write_jsonl(out / 'tagged.jsonl', tagged)
    x.write_json(out / 'forecast.json', saved)
    x.write_json(out / 'health.json', checks)
    x.write_json(out / 'manifest.json', {
        'run_id': run_id, 'issued': issue.isoformat(), 'code_hash': code_hash(), 'extractor': stats, 'reference': reference,
        'inputs': {'posts_file': Path(summary['posts_file']).name, 'posts_sha256': summary['sha256'], 'query': summary['query'],
                   'acled_sha256': meta['sha256'], 'acled_retrieved_at': meta['retrieved_at']},
        'counts': {'posts': len(posts), 'usable': sum(map(usable, tagged))}, 'warnings': checks['warnings']})
    (out / 'brief.md').write_text(brief(saved, checks, tagged))
    for name in PUBLISHED:  # tagged.jsonl and brief.md contain post text and stay local.
        (HISTORY / 'runs' / run_id).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out / name, HISTORY / 'runs' / run_id / name)
    return out


def skill_interval(cases, horizon, draws=1000):
    """Forecasts overlap, so resample runs of consecutive issues about one horizon long."""
    issues = sorted({c['issue'] for c in cases})
    by_issue = {issue: [c for c in cases if c['issue'] == issue] for issue in issues}
    block, rng, values = max(1, math.ceil(horizon / 7)), np.random.default_rng(7), []
    if len(issues) < 2 * block:  # Every resample would hold the same issues, a zero-width interval that only looks certain.
        return None
    for _ in range(draws):
        picked = []
        while len(picked) < len(issues):
            start = int(rng.integers(len(issues)))
            picked.extend(issues[(start + i) % len(issues)] for i in range(block))
        sample = [c for issue in picked[:len(issues)] for c in by_issue[issue]]
        model = np.mean([(c['p'] - c['outcome']) ** 2 for c in sample])
        base = np.mean([(c['baseline'] - c['outcome']) ** 2 for c in sample])
        if base > 0:
            values.append(1 - model / base)
    return np.quantile(values, [.05, .95]).tolist() if values else None


def score(draws=1000):
    """Score resolved horizons only. One run per ISO week counts, the first, so reruns cannot be picked."""
    events, meta = acled.load_snapshot()
    boxes = acled.load_boxes()
    dates, resolved = acled.major_dates(events, boxes), backtest.resolved_through(meta)
    boxes_hash, counted, cases = acled.boxes_hash(), set(), {h: [] for h in fc.HORIZONS}
    for path in sorted((HISTORY / 'runs').glob('*/forecast.json')):
        saved = json.loads(path.read_text())
        issue = date.fromisoformat(saved['issued'])
        if saved['hotspots_sha256'] != boxes_hash:
            print(f"Skipped {saved['run_id']}: hotspot boxes changed after it was issued.")
            continue
        week = issue.isocalendar()[:2]
        first = week not in counted
        counted.add(week)
        scores = {}
        for h in fc.HORIZONS:
            resolved_rows = [(row, acled.target(dates[row['hotspot']], issue, h, resolved)) for row in saved['rows']]
            resolved_rows = [(row, outcome) for row, outcome in resolved_rows if outcome is not None]
            if not resolved_rows:
                continue
            brier = lambda key: float(np.mean([(row[key] - y) ** 2 for row, y in resolved_rows]))
            scores[f'p{h}'] = {'n': len(resolved_rows), 'positives': sum(y for _, y in resolved_rows),
                               'brier_model': brier(f'p{h}'), 'brier_baseline': brier(f'p{h}_baseline'), 'brier_notes': brier(f'p{h}_notes')}
            if first:
                cases[h] += [{'issue': saved['issued'], 'p': row[f'p{h}'], 'baseline': row[f'p{h}_baseline'], 'notes': row[f'p{h}_notes'],
                              'views': row[f'p{h}_views'], 'outcome': y} for row, y in resolved_rows]
        x.write_json(path.parent / 'scores.json', {'run_id': saved['run_id'], 'resolved_through': resolved.isoformat(),
                     'acled_sha256': meta['sha256'], 'acled_retrieved_at': meta['retrieved_at'], 'scores': scores})
    summary = {'resolved_through': resolved.isoformat(), 'acled_sha256': meta['sha256'], 'horizons': {}}
    for h, rows in cases.items():
        if not rows:
            continue
        issues = sorted({c['issue'] for c in rows})
        model = float(np.mean([(c['p'] - c['outcome']) ** 2 for c in rows]))
        base = float(np.mean([(c['baseline'] - c['outcome']) ** 2 for c in rows]))
        notes = float(np.mean([(c['notes'] - c['outcome']) ** 2 for c in rows]))
        paired = [c for c in rows if c['views'] is not None]
        paired_base = float(np.mean([(c['baseline'] - c['outcome']) ** 2 for c in paired])) if paired else None
        views_brier = float(np.mean([(c['views'] - c['outcome']) ** 2 for c in paired])) if paired else None
        spans_six = acled.month_shift(date.fromisoformat(issues[0]), 6) <= date.fromisoformat(issues[-1])
        summary['horizons'][str(h)] = {'issues': len(issues), 'forecasts': len(rows), 'brier_model': model, 'brier_baseline': base,
                                       'skill': 1 - model / base if base > 0 else None, 'skill_90': skill_interval(rows, h, draws),
                                       'brier_notes': notes, 'skill_notes': 1 - notes / base if base > 0 else None, 'brier_views': views_brier,
                                       'skill_views': 1 - views_brier / paired_base if paired_base else None,
                                       'conclusive': len(issues) >= 26 and spans_six}
    x.write_json(HISTORY / 'live_scores.json', summary)
    return summary


def publish():
    """Copy the latest backtest scores and plots and refresh the VIEWS map. Live runs publish themselves."""
    published = []
    reports = sorted(backtest.REPORTS.glob('backtest_*/scores.json'))
    if reports:
        target = HISTORY / 'backtest'
        target.mkdir(parents=True, exist_ok=True)
        for name in ('scores.json', 'hotspots.json', 'reliability_recent.png', 'reliability_text.png', 'reliability_views.png'):
            shutil.copyfile(reports[-1].parent / name, target / name)
        published.append(target)
    if (views.OUT / 'catalog.json').exists():
        x.write_json(HISTORY / 'views_map.json', views.latest_map(x.now().date()))
        published.append(HISTORY / 'views_map.json')
    return published


class Skip(Exception):
    """A stage that cannot or need not run; not a failure."""


def orchestrate(pull_x=False, extractor='rules', rebuild=False, pause=3, since='2018-01-01'):
    """Run every stage in order and record each outcome for the dashboard. Failures do not hide later stages."""
    snapshot, reference = x.DATA / 'acled' / 'snapshot.json', HISTORY / 'reference.json'

    def stage_views():
        views.fetch_all(pause)
        return f"{len(views.catalog())} dated runs cached."

    def stage_acled():
        today = x.now().date()
        path = x.DATA / 'acled' / 'download.csv'
        if not acled.download(path, since, today.isoformat()):
            if snapshot.exists():
                raise Skip('No ACLED credentials here; using the local snapshot.')
            raise Skip('No ACLED credentials or export yet, so nothing that needs ACLED can run.')
        meta = acled.import_file(path, since, today.isoformat())
        path.unlink()
        return f"{meta['events']} events through {meta['coverage_end']}."

    def stage_backtest():
        if not snapshot.exists():
            raise Skip('Needs ACLED.')
        if reference.exists() and not rebuild:
            raise Skip('The backtest report exists and the mapping is frozen; pass --rebuild to report on a newer snapshot.')
        try:
            acled.load_boxes()
        except ValueError as error:
            raise Skip(str(error))
        backtest.build()
        return f'Report: {backtest.report().parent.name}.'

    def stage_forecast():
        if not pull_x:
            raise Skip('The X pull is off, because it costs credits.')
        if not x.load_env().get('X_BEARER_TOKEN'):
            raise Skip('No X_BEARER_TOKEN set.')
        if not reference.exists():
            raise Skip('Needs the backtest first.')
        return f'Run {run(True, None, extractor).name} with {extractor}.'

    def stage_score():
        if not (snapshot.exists() and reference.exists()):
            raise Skip('Needs ACLED and the backtest.')
        horizons = score()['horizons']
        return f'{sum(h["forecasts"] for h in horizons.values())} resolved forecasts.' if horizons else 'No forecast has resolved yet.'

    def stage_publish():
        return 'Updated: ' + ', '.join(path.name for path in publish()) + '.'

    stages = []
    for name, action in (('VIEWS', stage_views), ('ACLED', stage_acled), ('Backtest', stage_backtest),
                         ('Forecast', stage_forecast), ('Score', stage_score), ('Publish', stage_publish)):
        try:
            status, detail = 'done', action()
        except Skip as reason:
            status, detail = 'skipped', str(reason)
        except (ValueError, OSError, requests.RequestException) as error:
            status, detail = 'failed', str(error)[:300]
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
    get = sub.add_parser('fetch', help='import an ACLED export and cache VIEWS runs')
    get.add_argument('--acled', type=Path)
    get.add_argument('--coverage-start')
    get.add_argument('--coverage-end')
    get.add_argument('--no-views', action='store_true')
    get.add_argument('--pause', type=float, default=3)
    sub.add_parser('build', help='hotspot-month targets and baselines')
    test = sub.add_parser('backtest', help='score baselines 2023 onward and freeze the reference')
    test.add_argument('--draws', type=int, default=1000)
    go = sub.add_parser('run', help='live forecast')
    go.add_argument('--fetch', action='store_true', help='pull new posts from X (costs credits)')
    go.add_argument('--posts', type=Path, help='saved posts file instead of the latest snapshot')
    go.add_argument('--extractor', default='rules', help='rules, or a gemini-* or claude-* model id')
    go.add_argument('--target', type=int, default=1000)
    sub.add_parser('score', help='score resolved live forecasts against ACLED')
    sub.add_parser('publish', help='copy the backtest results and VIEWS map to history/')
    plan = sub.add_parser('orchestrate', help='run every stage in order and record the outcome')
    plan.add_argument('--pull-x', action='store_true')
    plan.add_argument('--extractor', default='rules')
    plan.add_argument('--rebuild', action='store_true')
    sub.add_parser('dashboard', help='build site/index.html from history/')
    args = parser.parse_args()
    try:
        if args.command == 'fetch':
            fetch(args)
        elif args.command == 'build':
            rows = backtest.build()
            meta = json.loads((x.DATA / 'panel.meta.json').read_text())
            print(f"Panel: {len(rows)} rows, resolved through {meta['resolved_through']}.")
            print(f"Calibration: {json.loads((x.DATA / 'calibration.json').read_text())}")
            if meta['hotspots_without_views']:
                print('WARNING: no VIEWS cell centre inside these boxes:', ', '.join(meta['hotspots_without_views']))
        elif args.command == 'backtest':
            print(backtest.report(args.draws))
        elif args.command == 'run':
            out = run(args.fetch, args.posts, args.extractor, args.target)
            print(out)
            for warning in json.loads((out / 'manifest.json').read_text())['warnings']:
                print(f'WARNING: {warning}')
            print((out / 'brief.md').read_text().split('## Evidence')[0])
        elif args.command == 'score':
            result = score()
            print(json.dumps(result, indent=2))
            if not result['horizons']:
                print('No forecast has resolved yet.')
            elif not all(h['conclusive'] for h in result['horizons'].values()):
                print('Interim only: the specification needs 26 weekly issues across six months before any conclusion.')
        elif args.command == 'publish':
            for path in publish():
                print(path)
        elif args.command == 'orchestrate':
            if any(stage['status'] == 'failed' for stage in orchestrate(args.pull_x, args.extractor, args.rebuild)):
                sys.exit(1)
        else:
            print(dashboard.build(HISTORY, x.ROOT / 'site', acled.BOXES))
    except ValueError as error:
        sys.exit(f'Error: {error}')


if __name__ == '__main__':
    main()
