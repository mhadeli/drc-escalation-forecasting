"""DRC fatalities forecasting: 1, 2 and 3 calendar months per province, compared with VIEWS.

  python pipeline.py ingest --acled raw_data/acled_weekly.csv --posts data/raw/drc_posts.jsonl   # local: raw data
  python pipeline.py backtest     # ACLED-history baseline against VIEWS, 2023 onward
  python pipeline.py xcheck       # X authors per province against ACLED deaths in the same week
  python pipeline.py forecast     # preserves the saved example; --issue YYYY-MM creates a new issue
  python pipeline.py score        # scores the saved issue once the calendar and ACLED coverage resolve
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
        'drc_watch/backtest.py', 'drc_watch/extract_llm.py', 'drc_watch/fetch_recent.py', 'pipeline.py')


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
    pre_weekly = acled.weekly_precursors(rows)
    pre_monthly = {k: acled.to_monthly(pre_weekly[k], cover) for k in pre_weekly}
    last = date.fromisoformat(cover['last_week'])
    recent = [last - timedelta(weeks=i) for i in range(52, -1, -1)]
    x.write_json(INPUTS / 'acled.json', {
        'source': 'ACLED weekly aggregated data, Democratic Republic of Congo', 'file_sha256': x.checksum(path),
        'rows': len(rows), 'retrieved_at': x.now().isoformat(), **cover,
        'definitions': {d: list(t) for d, t in acled.DEFINITIONS.items()}, 'primary': acled.PRIMARY,
        'monthly': monthly, 'precursors': pre_monthly,
        'weekly': {d: {w.isoformat(): acled.week_totals(weekly[d], w) for w in recent} for d in acled.DEFINITIONS},
        'weekly_precursors': {k: {w.isoformat(): acled.week_totals(pre_weekly[k], w) for w in recent} for k in pre_weekly}})
    return cover


def ingest_views(cover, fetch=True, issue=ISSUE):
    """Province totals for each issue's three months, from the run that was public on that day."""
    if fetch:
        views.fetch_catalog()
    runs = views.catalog()
    issues = sorted(set(backtest.issues_between(FIRST_ISSUE, acled.shift(cover['complete_through'], -2)) + [issue]))
    if not BOUNDARIES.exists():
        if not fetch:
            raise ValueError('Province boundaries are not cached; run ingest once without --offline.')
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
    cells = {gid for gid, _, _ in views.read_run(chosen[issue]['run'])}
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


def ingest_posts(path, extractor='rules'):
    posts = x.read_jsonl(path)
    if not posts:
        raise ValueError(f'No posts in {path}')
    times = sorted(datetime.fromisoformat(p['created_at'].replace('Z', '+00:00')) for p in posts)
    if times[-1] > x.now():
        raise ValueError('A post is dated in the future.')
    if len({p['id'] for p in posts}) != len(posts):
        raise ValueError('Duplicate post IDs in the snapshot.')
    extraction = {}
    rule_tagged = [fc.classify(p) for p in posts]
    rule_signals = fc.province_signals(rule_tagged, times[-1])
    if extractor == 'rules':
        tagged = rule_tagged
    else:
        from drc_watch import extract_llm
        tagged, extraction = extract_llm.tag_posts(posts, extractor)
        x.write_jsonl(x.DATA / 'extractions' / f'{x.checksum(path)[:16]}.jsonl', tagged)
        # Provider errors can echo submitted text; only aggregate counters may leave data/.
        extraction = {k: extraction[k] for k in ('model', 'prompt_version', 'posts', 'errors', 'tokens')}
    if extraction.get('errors', 0) > .02 * len(posts):
        raise ValueError('More than 2% of extractions failed; review the private extraction records before ingestion.')
    signals = fc.province_signals(tagged, times[-1], credible_only=(extractor != 'rules'))
    # If cached gemini-3.8-flash LLM extractions exist locally, include them alongside rules for side-by-side comparison.
    llm_credible = signals if extractor != 'rules' else None
    llm_rows_for_topics = tagged if extractor != 'rules' else None
    if extractor == 'rules':
        from drc_watch import extract_llm
        if all(any(p.exists() for p in extract_llm._cache_paths(post, 'gemini-3.8-flash')) for post in posts):
            llm_rows, _ = extract_llm.tag_posts(posts, 'gemini-3.8-flash')
            llm_credible = fc.province_signals(llm_rows, times[-1], credible_only=True)
            llm_rows_for_topics = llm_rows
    topics = None
    if llm_rows_for_topics:
        # Rows from extract_llm.to_tagged: on_topic is the model's relevance judgement.
        rel_rows = [r for r in llm_rows_for_topics if r.get('on_topic') and not r.get('extraction_error')]
        if not rel_rows:
            raise ValueError('The LLM marked no post as relevant; check the extraction before publishing a topic summary.')
        src_counts = Counter(r.get('source_type') or 'other' for r in rel_rows)
        ev_counts = Counter(e for r in rel_rows for e in (r.get('event_types') or []))
        hs_counts = Counter(h for r in rel_rows for h in (r.get('hotspots') or []))
        topics = {
            'relevant_posts': len(rel_rows),
            'irrelevant_posts': len(llm_rows_for_topics) - len(rel_rows),
            'source_types': dict(src_counts.most_common()),
            'event_types': dict(ev_counts.most_common()),
            'top_hotspots': dict(hs_counts.most_common(8)),
            'credible_posts': sum(1 for r in rel_rows if r.get('source_type') in ('news', 'field_report')),
            'historical_posts': sum(1 for r in rel_rows if r.get('historical')),
        }
    by_week = {}
    for row in tagged:
        day = date.fromisoformat(row['created_at'][:10])
        week = (day - timedelta(days=(day.weekday() - 5) % 7)).isoformat()
        by_week.setdefault(week, []).append(row)
    weekly = {week: fc.province_signals(rows, times[-1], credible_only=(extractor != 'rules')) for week, rows in sorted(by_week.items())}
    x.write_json(INPUTS / 'x_counts.json', {
        'source': 'X recent search, saved pull', 'posts_sha256': x.checksum(path), 'extractor': extractor,
        'ingested_at': x.now().isoformat(), 'extraction': extraction,
        'first_post': times[0].isoformat(), 'last_post': times[-1].isoformat(), 'posts': len(posts),
        'usable_posts': sum(map(usable, tagged)), 'authors': len({p.get('author_id') for p in posts}),
        'posts_per_day': dict(sorted(Counter(p['created_at'][:10] for p in posts).items())),
        'languages': dict(Counter(p.get('lang', 'und') for p in posts).most_common()),
        'topics': topics,
        'provinces': signals, 'rules_provinces': rule_signals, 'llm_credible_provinces': llm_credible,
        'weeks': weekly})
    return signals


def ingest(args):
    cover = ingest_acled(args.acled)
    print(f"ACLED: {cover['first_week']} to {cover['coverage_end']}, complete through {cover['complete_through']}.")
    issues, cells, border = ingest_views(cover, fetch=not args.offline, issue=args.issue)
    print(f'VIEWS: {issues} issues, {cells} cells mapped to provinces, {border} border cells assigned to the nearest.')
    if args.posts:
        signals = ingest_posts(args.posts, args.extractor)
        print('X event authors:', {k: v['event'] for k, v in signals.items()})
    stamp('Ingest', 'done', f"ACLED through {cover['coverage_end']}; {issues} VIEWS issues; X {'loaded' if args.posts else 'unchanged'}.")


def stamp(stage, status, detail):
    """Ingest runs locally, so it leaves its own status for the dashboard."""
    path = HISTORY / 'ingest_status.json'
    x.write_json(path, {'stage': stage, 'status': status, 'detail': detail, 'at': x.now().isoformat(timespec='seconds')})


# 2. Backtest ---------------------------------------------------------------------------------------------------

def run_backtest(draws=backtest.DRAWS):
    data, views_in = read('acled.json'), read('views.json')
    geo = read('provinces.geojson') if (INPUTS / 'provinces.geojson').exists() else {'features': []}
    neighbors = provinces.neighbors(geo)
    precursors = data.get('precursors')
    monthly = data['monthly'][data['primary']]
    last_issue = acled.shift(data['complete_through'], -2)
    issues = backtest.issues_between(FIRST_ISSUE, last_issue)
    views_by_issue = {i: v['provinces'] for i, v in views_in['issues'].items()}
    rows = backtest.panel(monthly, views_by_issue, issues)
    window, errors = backtest.choose_window(rows)
    reference = freeze_reference(window, errors, backtest.views_scale(rows), data)
    key = f"base_{reference['window']}"
    backtest.add_scaled(rows, reference['views_scale'])
    backtest.add_ml(rows, monthly, precursors, neighbors)
    chosen, ml_errors = backtest.choose_ml(rows)
    reference = freeze_ml(reference, chosen, ml_errors)
    backtest.use_ml(rows, reference['ml_model'])
    evaluation = [r for r in rows if r['issue'] >= EVALUATION_START]
    if not evaluation:
        raise ValueError('No resolved evaluation outcomes from 2024 onward.')
    no_gap = backtest.add_scaled(backtest.panel(monthly, views_by_issue, [i for i in issues if i >= EVALUATION_START], gap=0),
                                 reference['views_scale'])
    backtest.use_ml(backtest.add_ml(no_gap, monthly, precursors, neighbors, gap=0, models=(reference['ml_model'],)), reference['ml_model'])
    ml_compare = {}
    for name in backtest.ML_MODELS:
        per = {}
        for days in fc.HORIZONS:
            cases = [r for r in evaluation if r['horizon'] == days and r.get(f'ml_{name}') is not None]
            per[str(days)] = None if not cases else sum(backtest.log_error(r[f'ml_{name}'], r['actual']) for r in cases) / len(cases)
        ml_compare[name] = per
    result = {
        'definition': data['primary'], 'window': reference['window'], 'gap_months': backtest.GAP,
        'views_scale': reference['views_scale'],
        'ml': {'selected': reference['ml_model'], 'label': backtest.ML_LABELS[reference['ml_model']],
               'selection_errors': reference['ml_selection_errors'], 'selection_errors_this_run': ml_errors, 'evaluation_errors': ml_compare,
               'features': [backtest.FEATURE_LABELS[f] for f in backtest.FEATURE_NAMES]},
        'selection': {'period': list(backtest.SELECTION), 'version': reference.get('selection_version', 3),
                      'errors': reference.get('errors', {str(w): e for w, e in errors.items()})},
        'selection_sensitivity': {'window': window, 'outcomes_through': backtest.SELECTION[1],
                                 'errors': {str(w): e for w, e in errors.items()},
                                 'note': 'Restricts selection outcomes to 2023; does not replace the frozen reference.'},
        'evaluation': {'first_issue': EVALUATION_START, 'last_issue': last_issue, 'scores': backtest.scores(evaluation, key, draws)},
        'selection_year_scores': backtest.scores(
            [r for r in rows if r['issue'] < EVALUATION_START] if reference.get('selection_version', 3) == 3
            else backtest.selection_rows(rows), key, draws),
        'sensitivity_no_gap': backtest.scores(no_gap, key, draws),
        'national': backtest.national_series(rows, key),
        'views_runs': {i: v['run'] for i, v in views_in['issues'].items() if i in issues},
        'acled_complete_through': data['complete_through'], 'acled_sha256': data['file_sha256'], 'code_hash': code_hash()}
    x.write_json(HISTORY / 'backtest.json', result)
    write_report(result)
    return result


def write_report(result):
    first = result['evaluation']['scores'].get('30')
    rank = lambda value: 'undefined' if value is None else f'{value:.2f}'
    lines = ['# DRC province fatalities: retrospective evaluation', '',
             f"Evaluation issues: {result['evaluation']['first_issue']} to {result['evaluation']['last_issue']}.",
             f"Selected baseline: {result['window']}-month average, ending at M-2.",
             'Primary results compare ACLED Simple Baseline, Spatio-Temporal ML (ACLED precursors + spillover), and VIEWS across 1/2/3 calendar months.', '',
             '| Months | Cases | Excluded | Log error: Simple | ML | VIEWS | Rescaled VIEWS | ML skill vs Simple (90% CI) | VIEWS skill vs Simple (90% CI) |',
             '|---|---:|---:|---:|---:|---:|---:|---|---|']
    if first:
        lines[6:6] = [f"National one-month total: simple baseline **{first['total_baseline']:,.0f}**, "
                      f"ML **{(first.get('total_ml') or 0):,.0f}**, "
                      f"ACLED **{first['total_actual']:,.0f}**, VIEWS **{first['total_views']:,.0f}**.",
                      f"Mean province rank correlation: ML **{rank(first.get('rank_ml'))}**, simple baseline **{rank(first['rank_baseline'])}**, "
                      f"VIEWS **{rank(first['rank_views'])}**. National level, ranking and log error answer different questions.", '']
    for days, s in result['evaluation']['scores'].items():
        ci, skill, scaled = s['skill_views_90'], s['skill_views'], s['error_views_scaled']
        ml_err, ml_skill, ml_ci = s.get('error_ml'), s.get('skill_ml'), s.get('skill_ml_90')
        interval = 'not estimable' if ci is None else f'[{ci[0]:.3f}, {ci[1]:.3f}]'
        ml_interval = 'not estimable' if ml_ci is None else f'[{ml_ci[0]:.3f}, {ml_ci[1]:.3f}]'
        skill_text = 'undefined' if skill is None else f'{skill:.3f}'
        ml_skill_text = 'undefined' if ml_skill is None else f'{ml_skill:.3f}'
        scaled_text = 'missing' if scaled is None else f'{scaled:.3f}'
        ml_text = 'missing' if ml_err is None else f'{ml_err:.3f}'
        lines.append(f"| {fc.HORIZONS[int(days)]} | {s['forecasts']} | {s['excluded']} | "
                     f"{s['error_baseline']:.3f} | {ml_text} | {s['error_views']:.3f} | {scaled_text} | "
                     f"{ml_skill_text}, {ml_interval} | {skill_text}, {interval} |")
    lines += ['', 'Positive skill means lower log error than the ACLED simple baseline.', '',
              'This is exploratory. Original selection used 2023 issue dates, including early-2024 outcomes for longer horizons.',
              'Restricting selection outcomes to 2023 selects three months; that check is recorded separately without changing the frozen reference.',
              'ACLED is a revised snapshot, and its event-type proxy is not UCDP state-based violence.',
              'Weeks are apportioned to calendar months by days; those monthly totals are approximate.',
              'The existing November example was saved after November 1 and is retrospective.',
              f"ACLED SHA-256: `{result['acled_sha256']}`.", f"Code hash: `{result['code_hash']}`."]
    path = x.ROOT / 'reports' / 'backtest.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n')


ML_TIE_TOLERANCE = .01  # Relative 2023 error gap below which the two models count as tied.


def freeze_ml(reference, chosen, errors):
    """The ML model is chosen once on 2023, like the baseline window, and kept. A run that would differ fails."""
    path = HISTORY / 'reference.json'
    if 'ml_model' in reference:
        frozen = reference['ml_model']
        if frozen != chosen:
            # A near tie can flip between machines (CPU, library build). Keep the frozen choice unless the gap is material.
            gap = (errors[frozen] - errors[chosen]) / errors[chosen]
            if gap > ML_TIE_TOLERANCE:
                raise ValueError(f"2023 now picks {chosen} by {gap:.1%}, not the frozen {frozen}; investigate before editing reference.json.")
        return reference
    reference = {**reference, 'ml_model': chosen, 'ml_selection_errors': errors,
                 'ml_frozen_at': x.now().isoformat(timespec='seconds')}
    x.write_json(path, reference)
    return reference


def ml_model():
    reference = read(HISTORY / 'reference.json')
    if 'ml_model' not in reference:
        raise ValueError('No ML model has been chosen yet. Run the backtest first.')
    return reference['ml_model']


def freeze_reference(window, errors, scale, data):
    """Keep the original reference. New references use the stricter selection boundary."""
    path = HISTORY / 'reference.json'
    if scale is None:
        raise ValueError('No VIEWS observations in the selection year; cannot freeze a reference.')
    if path.exists():
        saved = json.loads(path.read_text())
        if saved.get('selection_version', 3) < backtest.SELECTION_VERSION:
            if abs(saved['views_scale'] - scale) > 1e-9:
                raise ValueError('The frozen VIEWS scale changed; investigate the source data before continuing.')
            return saved  # Historical selection corrections are sensitivity checks, never automatic retuning.
        elif saved['window'] != window or abs(saved['views_scale'] - scale) > 1e-9:
            raise ValueError(f"2023 now picks window {window} and scale {scale:.4f}, not the frozen {saved['window']} and "
                             f"{saved['views_scale']:.4f}; the inputs changed. Investigate before deleting history/reference.json.")
        else:
            return saved
    saved = {'window': window, 'views_scale': scale, 'gap_months': backtest.GAP, 'definition': data['primary'],
             'selection_version': backtest.SELECTION_VERSION, 'outcomes_through': backtest.SELECTION[1],
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
    weeks = counts.get('weeks') or {week.isoformat(): counts['provinces']}
    if last > week + timedelta(days=6) and 'weeks' not in counts:
        raise ValueError(f'The X posts span {first} to {last}, more than the ACLED week starting {week}.')
    if any(w not in data['weekly'][d] for w in weeks for d in data['definitions']):
        raise ValueError('ACLED has not supplied all weeks overlapping these posts. Refresh the ACLED export.')
    llm_provinces = counts.get('llm_credible_provinces')
    rules_provinces = counts.get('rules_provinces') or counts['provinces']
    rows = [{'province': name, 'event_authors': sum(spots[name]['event'] for spots in weeks.values()),
             'authors': sum(spots[name]['authors'] for spots in weeks.values()),
             'llm_event_authors': None if not llm_provinces else llm_provinces[name]['event'],
             **{f'deaths_{d}': sum(data['weekly'][d][w][name] for w in weeks) for d in data['definitions']}}
            for name in counts['provinces']]
    holdout = None
    next_week = (date.fromisoformat(max(weeks)) + timedelta(weeks=1)).isoformat()
    lagged_week = (date.fromisoformat(min(weeks)) - timedelta(weeks=1)).isoformat()
    primary_weekly = data['weekly'][data['primary']]
    if next_week in primary_weekly and lagged_week in primary_weekly and len(primary_weekly) >= 15:
        geo = read('provinces.geojson') if (INPUTS / 'provinces.geojson').exists() else {'features': []}
        neighbors = provinces.neighbors(geo)
        base_lagged = primary_weekly[lagged_week]
        nowcasts = {'ml_x_rules': fc.nowcast_from_x(rules_provinces, base_lagged)}
        if llm_provinces:
            nowcasts['ml_x_llm_credible'] = fc.nowcast_from_x(llm_provinces, base_lagged)
        raw_holdout = backtest.predict_weekly_holdout(primary_weekly, data.get('weekly_precursors'), neighbors, next_week, nowcasts,
                                                      model=ml_model())
        x_names = list(counts['provinces'])
        actual_x = [raw_holdout['actual'][n] for n in x_names]
        scores_x = {}
        for model_name, preds in raw_holdout['models'].items():
            pred_x = [preds[n] for n in x_names]
            scores_x[model_name] = {
                'log_error': round(sum(backtest.log_error(p, a) for p, a in zip(pred_x, actual_x)) / len(x_names), 3),
                'spearman': round(backtest.spearman(pred_x, actual_x) or 0.0, 3),
                'predictions': {n: preds[n] for n in x_names},
            }
        holdout = {
            'target_week': next_week, 'lagged_week': lagged_week, 'gap_week': min(weeks),
            'actual': {n: raw_holdout['actual'][n] for n in x_names},
            'model': raw_holdout['model'], 'training_rows': raw_holdout['training_rows'],
            'models': scores_x,
        }
    result = {'week_start': min(weeks), 'week_end': (date.fromisoformat(max(weeks)) + timedelta(days=6)).isoformat(),
              'weeks_n': len(weeks), 'x_measure': 'author-week counts' if len(weeks) > 1 else 'unique authors',
              'alignment_note': 'ACLED totals cover full weeks overlapping the posts; partial post weeks are not exact date matches.',
              'posts_from': counts['first_post'], 'posts_to': counts['last_post'], 'rows': rows,
              'rank_event_authors_vs_state_deaths': backtest.spearman([r['event_authors'] for r in rows], [r['deaths_state'] for r in rows]),
              'rank_llm_event_authors_vs_state_deaths': (
                  None if not llm_provinces else backtest.spearman([r['llm_event_authors'] for r in rows], [r['deaths_state'] for r in rows])),
              'rank_event_authors_vs_organised_deaths': backtest.spearman([r['event_authors'] for r in rows], [r['deaths_organised'] for r in rows]),
              'rank_all_authors_vs_state_deaths': backtest.spearman([r['authors'] for r in rows], [r['deaths_state'] for r in rows]),
              'holdout_next_week': holdout,
              'provinces_n': len(rows)}
    x.write_json(HISTORY / 'x_check.json', result)
    return result


# 4. Live forecast ------------------------------------------------------------------------------------------------

def forecast_kind(saved):
    written = datetime.fromisoformat(saved['written_at'].replace('Z', '+00:00'))
    issue = datetime.fromisoformat(saved['issued']).replace(tzinfo=timezone.utc)
    return 'prospective' if written < issue else 'retrospective'


def run_forecast(force=False, issue_key=None):
    path = HISTORY / 'forecast.json'
    if path.exists() and not force:
        saved = json.loads(path.read_text())
        if issue_key is None or saved['issued'][:7] == issue_key:
            if 'evaluation_kind' not in saved:
                saved['evaluation_kind'] = forecast_kind(saved)
                x.write_json(path, saved)  # Annotate provenance; do not change any forecast values.
            return saved, False
        archive = HISTORY / 'forecasts' / f"{saved['issued'][:7]}.json"
        if not archive.exists():
            x.write_json(archive, saved)
    elif path.exists():
        archive = HISTORY / 'archive' / f'forecast_{x.checksum(path)[:12]}.json'
        x.write_json(archive, json.loads(path.read_text()))
    data, views_in, counts = read('acled.json'), read('views.json'), read('x_counts.json')
    reference = read(HISTORY / 'reference.json')
    issue_key = issue_key or ISSUE
    issue = issue_date(issue_key)
    if (HISTORY / 'forecasts' / f'{issue_key}.json').exists():
        raise ValueError(f'Forecast {issue_key} is already frozen in history/forecasts; it cannot be recreated.')
    if datetime.fromisoformat(counts['last_post']) >= datetime(issue.year, issue.month, 1, tzinfo=timezone.utc):
        raise ValueError('A post is dated on or after the issue date.')
    monthly = data['monthly'][data['primary']]
    if issue_key not in views_in['issues']:
        raise ValueError(f'No VIEWS totals for {issue_key}. Run ingest --issue {issue_key} first.')
    run = views_in['issues'][issue_key]
    rates = {}
    for name, series in monthly.items():
        rate = backtest.baseline_rate(series, issue_key, reference['window'])
        if rate is None:
            raise ValueError(f'ACLED does not cover the baseline window for {name}; it needs months through {acled.shift(issue_key, -2)}.')
        rates[name] = rate
    x_signals = counts.get('llm_credible_provinces') or counts['provinces']
    events = {name: counts['provinces'][name]['event'] for name in fc.X_PROVINCES}
    multipliers = fc.x_multipliers(events, {name: rates[name] for name in fc.X_PROVINCES})
    geo = read('provinces.geojson') if (INPUTS / 'provinces.geojson').exists() else {'features': []}
    neighbors = provinces.neighbors(geo)
    nowcast = fc.nowcast_from_x(x_signals, {name: rates[name] for name in fc.X_PROVINCES})
    ml_preds, ml_info = backtest.predict_issue_ml(issue_key, monthly, data.get('precursors'), neighbors, nowcast=nowcast, model=ml_model())
    rows = []
    for name in sorted(monthly):
        row = {'province': name, 'baseline_rate': round(rates[name], 2), 'x_multiplier': None, 'x_event_authors': events.get(name),
               'x_llm_event_authors': x_signals[name]['event'] if name in x_signals else None}
        if name in multipliers:
            row['x_multiplier'] = round(multipliers[name], 3)
        for days, months in fc.HORIZONS.items():
            base = rates[name] * months
            row[f'baseline_{days}'] = round(base, 1)
            row[f'ml_{days}'] = ml_preds[name].get(f'ml_{days}')
            row[f'x_{days}'] = round(base * fc.decayed(multipliers[name], days), 1) if name in multipliers else None
            row[f'x_ml_{days}'] = ml_preds[name].get(f'x_{days}')
            v = backtest.views_total(run['provinces'].get(name), issue_key, months)
            if v is None:
                raise ValueError(f'Incomplete VIEWS totals for {name}, issue {issue_key}. Re-ingest the run.')
            row[f'views_{days}'] = None if v is None else round(v, 1)
            row[f'views_scaled_{days}'] = None if v is None else round(v * reference['views_scale'], 1)
        rows.append(row)
    saved = {'issued': issue.isoformat(), 'definition': data['primary'], 'window_months': reference['window'],
             'views_scale': reference['views_scale'], 'ml': ml_info,
             'acled_through': acled.shift(issue_key, -2), 'x_posts': [counts['first_post'], counts['last_post']],
             'views_run': {k: run[k] for k in ('run', 'released', 'last_input')},
             'months': {str(d): [acled.shift(issue_key, i) for i in range(m)] for d, m in fc.HORIZONS.items()},
             'resolves': {str(d): (acled.month_end(acled.shift(issue_key, m - 1)) + timedelta(days=acled.REPORTING_LAG_DAYS)).isoformat()
                          for d, m in fc.HORIZONS.items()},
             'written_at': x.now().isoformat(timespec='seconds'), 'code_hash': code_hash(), 'rows': rows}
    saved['evaluation_kind'] = forecast_kind(saved)
    saved['reference_sha256'] = x.checksum(HISTORY / 'reference.json')
    saved['acled_sha256'] = data['file_sha256']
    x.write_json(path, saved)
    return saved, True


# 5. Score ------------------------------------------------------------------------------------------------------

def run_score():
    saved, data = read(HISTORY / 'forecast.json'), read('acled.json')
    monthly = data['monthly'][saved['definition']]
    result = {'acled_complete_through': data['complete_through'], 'acled_sha256': data['file_sha256'],
              'acled_retrieved_at': data.get('retrieved_at'), 'scored_at': x.now().isoformat(),
              'evaluation_kind': forecast_kind(saved), 'horizons': {}}
    for days, months in fc.HORIZONS.items():
        needed = saved['months'][str(days)]
        if needed[-1] > data['complete_through'] or date.fromisoformat(saved['resolves'][str(days)]) > x.now().date():
            result['horizons'][str(days)] = {'status': 'pending', 'resolves': saved['resolves'][str(days)]}
            continue
        if any(m not in monthly.get(r['province'], {}) for r in saved['rows'] for m in needed):
            result['horizons'][str(days)] = {'status': 'missing', 'detail': 'Outcome months missing from a province.'}
            continue
        cases = [(r, sum(monthly[r['province']][m] for m in needed)) for r in saved['rows']]
        err = lambda key, subset: sum(backtest.log_error(r[f'{key}_{days}'], a) for r, a in subset) / len(subset)
        with_x = [(r, a) for r, a in cases if r[f'x_{days}'] is not None]
        result['horizons'][str(days)] = {
            'status': 'scored', 'provinces': len(cases), 'error_baseline': err('baseline', cases), 'error_views': err('views', cases),
            'error_views_scaled': err('views_scaled', cases),
            'x_provinces': len(with_x), 'error_x': err('x', with_x) if with_x else None,
            'error_baseline_x_provinces': err('baseline', with_x) if with_x else None,
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
        if not all((INPUTS / n).exists() for n in ('acled.json', 'views.json')):
            raise Skip('Needs the ingested inputs.')
        s = run_backtest(draws)['evaluation']['scores']['30']
        ml, views = s.get('error_ml'), s['error_views']
        if ml is None or not views:
            return f"{s['issues']} monthly issues scored."
        label = backtest.ML_LABELS[ml_model()]
        return f"{label} chosen on 2023. At 30 days its error is {abs(1 - ml / views):.0%} {'lower' if ml < views else 'higher'} than VIEWS's."

    def stage_xcheck():
        if not all((INPUTS / n).exists() for n in ('acled.json', 'x_counts.json')):
            raise Skip('Needs the X counts.')
        r = run_xcheck()
        rho = r['rank_event_authors_vs_state_deaths']
        return f"{r['provinces_n']} provinces, week of {r['week_start']}; rank correlation {'n/a' if rho is None else f'{rho:.2f}'}."

    def stage_forecast():
        if not (HISTORY / 'reference.json').exists():
            raise Skip('Needs the backtest.')
        saved, new = run_forecast()
        return f"{saved['evaluation_kind'].capitalize()} forecast: {'written' if new else 'frozen since'} {saved['written_at'][:10]}, target starts {saved['issued']}."

    def stage_score():
        if not (HISTORY / 'forecast.json').exists():
            raise Skip('Needs the forecast.')
        h = run_score()['horizons']
        pending = [d for d, v in h.items() if v['status'] != 'scored']
        if len(pending) == len(h):
            raise Skip(f"Nothing has resolved yet; 30 days resolves about {h['30']['resolves']}.")
        return f"Scored {len(h) - len(pending)} of {len(h)} horizons."

    stages = []
    for name, action in (('Inputs', stage_inputs), ('Backtest', stage_backtest), ('X check', stage_xcheck),
                         ('Forecast', stage_forecast), ('Score', stage_score)):
        try:
            if name in ('Forecast', 'Score') and any(s['status'] == 'failed' and s['stage'] in ('Inputs', 'Backtest', 'Forecast') for s in stages):
                raise Skip('An upstream stage failed; existing forecast artifacts were left unchanged.')
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
    pull = sub.add_parser('fetch-posts', help='manually fetch and save seven daily X quotas (uses API credits)')
    pull.add_argument('--limit', type=int, default=1000)
    get = sub.add_parser('ingest', help='raw ACLED, VIEWS and X data to post-free aggregates in history/inputs')
    get.add_argument('--acled', type=Path, required=True, help='ACLED weekly aggregated CSV')
    get.add_argument('--posts', type=Path, help='saved X posts, JSON lines')
    get.add_argument('--offline', action='store_true', help='use the cached VIEWS catalog and runs only')
    get.add_argument('--issue', default=ISSUE, help='forecast target month, YYYY-MM')
    get.add_argument('--extractor', default='rules', help='rules or a Gemini model ID; LLM calls require billing')
    test = sub.add_parser('backtest')
    test.add_argument('--draws', type=int, default=backtest.DRAWS)
    sub.add_parser('xcheck')
    go = sub.add_parser('forecast')
    go.add_argument('--force', action='store_true', help='overwrite the frozen forecast')
    go.add_argument('--issue', help='new target month, YYYY-MM; preserves the previous forecast')
    sub.add_parser('score')
    plan = sub.add_parser('orchestrate')
    plan.add_argument('--draws', type=int, default=backtest.DRAWS)
    sub.add_parser('dashboard')
    args = parser.parse_args()
    try:
        if args.command == 'fetch-posts':
            token = x.load_env().get('X_BEARER_TOKEN')
            if not token:
                raise ValueError('Set X_BEARER_TOKEN in .env or the environment before fetching posts.')
            posts, details = x.collect(token, args.limit)
            summary = x.save_snapshot(posts, args.limit, details)
            print(f"Saved {summary['returned']} posts to {summary['posts_file']}")
        elif args.command == 'ingest':
            ingest(args)
        elif args.command == 'backtest':
            print(json.dumps(run_backtest(args.draws)['evaluation']['scores'], indent=2))
        elif args.command == 'xcheck':
            print(json.dumps(run_xcheck(), indent=2))
        elif args.command == 'forecast':
            saved, new = run_forecast(args.force, args.issue)
            print(('Written' if new else 'Already frozen; pass --force to overwrite') + f": issued {saved['issued']}")
        elif args.command == 'score':
            print(json.dumps(run_score(), indent=2))
        elif args.command == 'orchestrate':
            stages = orchestrate(args.draws)
            print(dashboard.build(HISTORY, x.ROOT / 'site'))
            if any(s['status'] == 'failed' for s in stages):
                sys.exit(1)
        else:
            print(dashboard.build(HISTORY, x.ROOT / 'site'))
    except (ValueError, OSError, requests.RequestException) as error:
        if args.command == 'ingest':
            stamp('Ingest', 'failed', str(error)[:300])
        sys.exit(f'Error: {error}')


if __name__ == '__main__':
    main()
