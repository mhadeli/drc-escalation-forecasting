"""Offline tests: leakage rules, scales, boundaries, and one synthetic run through every command."""

import csv
import json
import math
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline
from drc_watch import acled, backtest, dashboard, evaluate, views
from drc_watch import fetch_recent as x
from drc_watch import forecast as fc

SECRET = 'SECRETPOSTTEXT'
PROVINCE_BOX = {name: [-12 + i * .5 - .1, 20.0, -12 + i * .5 + .6, 21.0] for i, name in enumerate(fc.HOTSPOTS)}


class Sandbox(unittest.TestCase):
    """Point every module's paths at a temporary folder."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        data = self.root / 'data'
        (self.root / 'reports').mkdir()
        shutil.copyfile(x.ROOT / 'reports' / 'prereg.md', self.root / 'reports' / 'prereg.md')
        patches = [(x, 'DATA', data), (x, 'ROOT', self.root), (acled, 'DATA', data), (acled, 'ROOT', self.root),
                   (acled, 'BOXES', self.root / 'hotspots.csv'), (views, 'DATA', data), (views, 'OUT', data / 'views'),
                   (backtest, 'DATA', data), (backtest, 'ROOT', self.root), (backtest, 'REPORTS', self.root / 'reports'),
                   (pipeline, 'RUNS', data / 'runs'), (pipeline, 'HISTORY', self.root / 'history'),
                   (backtest, 'HISTORY', self.root / 'history')]
        for module, name, value in patches:
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.data = data

    def write_boxes(self):
        rows = [{'hotspot': name, 'province': fc.HOTSPOTS[name][0], 'south': b[0], 'west': b[1], 'north': b[2], 'east': b[3],
                 'reviewed': 1, 'source': 'test'} for name, b in PROVINCE_BOX.items()]
        x.write_csv(acled.BOXES, rows)

    def write_acled(self, path, start=date(2020, 1, 1), end=date(2026, 9, 30), quiet_after=None):
        rows, day, i = [], start, 0
        names = list(PROVINCE_BOX)
        while day <= end:
            for index, name in enumerate(names):
                # Busy hotspots have a major event most months; quiet ones rarely.
                if (i + index) % (2 + index % 5) == 0 and not (quiet_after and day > quiet_after):
                    south, west, north, east = PROVINCE_BOX[name]
                    rows.append({'event_id_cnty': f'COD{len(rows)}', 'iso': 180, 'event_date': day.isoformat(),
                                 'latitude': (south + north) / 2, 'longitude': (west + east) / 2,
                                 'fatalities': 12, 'sub_event_type': 'Armed clash', 'admin1': '', 'admin2': name})
            day, i = acled.month_shift(day, 1), i + 1
        x.write_csv(path, rows)

    def write_views(self):
        """Cache synthetic runs and a catalog, with released dates that follow input months."""
        runs, day = [], date(2022, 1, 1)
        views.OUT.mkdir(parents=True, exist_ok=True)
        while day <= date(2026, 8, 1):
            run = f'fatalities002_{day.year}_{day.month:02d}_t01'
            released = acled.month_shift(day, 1).replace(day=20)
            runs.append({'run': run, 'last_input': day.isoformat(), 'released': released.isoformat()})
            first = views.month_id(day) + 1
            rows = [{'pg_id': views.cell_id((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), 'month_id': first + m,
                     'main_mean': .3 + (i % 5) * .8, 'main_dich': .1} for i, b in enumerate(PROVINCE_BOX.values()) for m in range(36)]
            path = views.OUT / f'{run}.csv'
            x.write_csv(path, rows)
            x.write_json(views.OUT / f'{run}.meta.json', {'sha256': x.checksum(path)})
            day = acled.month_shift(day, 1)
        x.write_json(views.OUT / 'catalog.json', {'runs': runs})
        return runs

    def prepare(self, **kwargs):
        export = self.root / 'export.csv'
        self.write_acled(export, **kwargs)
        self.write_views()
        fixed = datetime(2026, 11, 1, tzinfo=timezone.utc)
        with mock.patch.object(acled, 'now', lambda: fixed):
            acled.import_file(export, '2018-01-01', '2026-10-31')
        self.write_boxes()


class Grid(unittest.TestCase):
    def test_round_trip_and_a_published_drc_cell(self):
        self.assertEqual(views.cell_id(*views.cell_center(110579)), 110579)
        self.assertEqual(views.cell_center(110579), (-13.25, 29.25))

    def test_rejects_invalid_cells(self):
        with self.assertRaises(ValueError):
            views.cell_center(0)


class ViewsData(Sandbox):
    def fake_get(self, pages):
        pages = iter(pages)
        response = lambda payload: mock.Mock(status_code=200, json=lambda: payload, raise_for_status=lambda: None)
        return lambda url, timeout=None: response(next(pages))

    def test_fatalities001_log_scale_is_converted(self):
        page = {'row_count': 1, 'next_page': '', 'data': [
            {'pg_id': 5, 'month_id': 508, 'sc_pgm_sb_main': math.log(11), 'sc_pgm_sb_dich_main': .2}]}
        with mock.patch.object(views.requests, 'get', self.fake_get([page])):
            views.fetch_run('fatalities001_2022_03_t01', pause=0)
        with (views.OUT / 'fatalities001_2022_03_t01.csv').open() as handle:
            row = next(csv.DictReader(handle))
        self.assertAlmostEqual(float(row['main_mean']), 10)

    def test_tiny_negatives_are_clipped_and_counted_but_large_ones_fail(self):
        row = lambda value: {'pg_id': 5, 'month_id': 508, 'sc_pgm_sb_main': value, 'sc_pgm_sb_dich_main': .2}
        with mock.patch.object(views.requests, 'get', self.fake_get([{'row_count': 1, 'next_page': '', 'data': [row(-.0011)]}])):
            meta = views.fetch_run('fatalities001_2022_01_t01', pause=0)
        self.assertEqual(meta['clipped_negative'], 1)
        with mock.patch.object(views.requests, 'get', self.fake_get([{'row_count': 1, 'next_page': '', 'data': [row(-.5)]}])):
            with self.assertRaises(ValueError):
                views.fetch_run('fatalities001_2022_02_t01', pause=0)

    def test_later_runs_keep_their_scale(self):
        self.assertEqual(views.read_forecast({'main_mean': 10, 'main_dich': .5}), (10.0, .5))

    def test_unknown_fields_fail(self):
        with self.assertRaises(ValueError):
            views.read_forecast({'pg_id': 1})

    def test_incomplete_pages_are_not_cached(self):
        page = {'row_count': 2, 'next_page': '', 'data': [{'pg_id': 5, 'month_id': 1, 'main_mean': 1, 'main_dich': .1}]}
        with mock.patch.object(views.requests, 'get', self.fake_get([page])):
            with self.assertRaises(ValueError):
                views.fetch_run('fatalities002_2024_01_t01', pause=0)
        self.assertFalse((views.OUT / 'fatalities002_2024_01_t01.csv').exists())

    def test_catalog_skips_undated_runs(self):
        text = ('| fatalities002_2024_01_t01 | fatalities | 002 | 2024-01 | 2024-02 to 2027-01 | 2024-03-01 | x |\n'
                '| fatalities003_2026_10_t01 | fatalities | 003 | 2026-10 | 2026-11 to 2029-10 | End of Nov | x |\n')
        self.assertEqual([r['run'] for r in views.parse_catalog(text)], ['fatalities002_2024_01_t01'])

    def test_run_selection_respects_release_and_input_lag(self):
        runs = [{'run': 'a', 'last_input': '2023-12-01', 'released': '2024-02-20'},
                {'run': 'b', 'last_input': '2024-01-01', 'released': '2024-03-01'},  # Released on the issue day.
                {'run': 'c', 'last_input': '2024-02-01', 'released': '2024-02-25'}]  # Input too recent.
        self.assertEqual(views.select_run(runs, date(2024, 3, 1))['run'], 'a')
        self.assertIsNone(views.select_run(runs, date(2024, 2, 1)))

    def test_expected_prorates_and_keeps_missing_as_missing(self):
        monthly = {views.month_id(date(2024, 1, 1)): 31.0, views.month_id(date(2024, 2, 1)): 29.0}
        self.assertAlmostEqual(views.expected(monthly, date(2024, 1, 21), 30), 10 + 20)
        self.assertIsNone(views.expected(monthly, date(2024, 2, 15), 30))


class Rules(unittest.TestCase):
    def test_recent_rate_formula_and_cutoff(self):
        issue = date(2024, 3, 1)
        self.assertAlmostEqual(acled.recent_rate([], issue), .5 / 12.5)
        self.assertAlmostEqual(acled.recent_rate([date(2024, 2, 1), date(2024, 2, 2)], issue), 1.5 / 12.5)
        # Inside the 14-day reporting lag, so unknown at issue.
        self.assertAlmostEqual(acled.recent_rate([date(2024, 2, 20)], issue), .5 / 12.5)
        self.assertAlmostEqual(acled.recent_rate([date(2024, 2, 20)], issue, lag=7), 1.5 / 12.5)

    def test_target_boundaries(self):
        issue, end = date(2024, 1, 1), date(2025, 1, 1)
        self.assertEqual(acled.target([issue], issue, 30, end), 0)
        self.assertEqual(acled.target([issue + timedelta(days=30)], issue, 30, end), 1)
        self.assertEqual(acled.target([issue + timedelta(days=31)], issue, 30, end), 0)
        self.assertIsNone(acled.target([], issue, 30, issue + timedelta(days=29)))

    def test_major_definition(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'a.csv'
            base = {'iso': 180, 'event_date': '2024-01-01', 'latitude': 1, 'longitude': 2}
            x.write_csv(path, [{**base, 'event_id_cnty': 'a', 'fatalities': 9, 'sub_event_type': 'Armed clash'},
                               {**base, 'event_id_cnty': 'b', 'fatalities': 10, 'sub_event_type': 'Armed clash'},
                               {**base, 'event_id_cnty': 'c', 'fatalities': 0, 'sub_event_type': 'Government regains territory'}])
            self.assertEqual([e['major'] for e in acled.read_events(path)], [False, True, True])

    def test_adjust_is_identity_at_one_and_monotonic(self):
        base = {30: .2, 60: .3, 90: .4}
        for h, p in fc.adjust(base, 1).items():
            self.assertAlmostEqual(p, base[h])
        up = fc.adjust(base, 6)
        self.assertTrue(up[30] <= up[60] <= up[90] <= 1 and up[30] > .2)
        self.assertLess(fc.adjust(base, .5)[30], .2)

    def test_auc(self):
        self.assertEqual(backtest.auc([0, 0, 1, 1], [.1, .2, .3, .4]), 1)
        self.assertEqual(backtest.auc([0, 1], [.5, .5]), .5)
        self.assertIsNone(backtest.auc([1, 1], [.1, .2]))

    def test_calibration_ignores_anything_resolving_after_2022(self):
        rows = [{'issue': '2022-06-01', 'e_30': 1.0, 'major_30': 1, 'e_60': 1.0, 'major_60': 0, 'e_90': 1.0, 'major_90': 1},
                {'issue': '2022-12-15', 'e_30': 1000.0, 'major_30': 0, 'e_60': None, 'major_60': None, 'e_90': None, 'major_90': None},
                {'issue': '2024-01-01', 'e_30': 1000.0, 'major_30': 0, 'e_60': None, 'major_60': None, 'e_90': None, 'major_90': None}]
        fitted = backtest.fit_scale(rows)
        self.assertEqual(fitted['n'], 3)
        self.assertEqual(fitted['coefficient'], backtest.fit_scale(rows[:1])['coefficient'])

    def test_rules_flag_old_posts_relative_to_post_date(self):
        post = {'id': '1', 'created_at': '2026-10-01T00:00:00Z', 'text': 'En 2019 une attaque a Beni'}
        self.assertTrue(fc.classify(post)['historical'])


class XPull(Sandbox):
    def test_seven_daily_windows_and_format(self):
        seen = []

        def fake(url, params, headers, timeout):
            seen.append(params)
            post = {'id': str(len(seen)), 'text': 't', 'created_at': params['start_time']}
            return mock.Mock(status_code=200, json=lambda: {'data': [post], 'meta': {}})
        with mock.patch.object(x.requests, 'get', fake), mock.patch.object(x.time, 'sleep'):
            posts, details = x.collect('token', target=70)
        self.assertEqual(len(details['daily_quotas']), 7)
        self.assertEqual(len(posts), 7)
        for params in seen:
            datetime.strptime(params['start_time'], '%Y-%m-%dT%H:%M:%SZ')

    def test_out_of_credits_keeps_partial_pull(self):
        responses = iter([mock.Mock(status_code=200, json=lambda: {'data': [{'id': '1', 'text': 't', 'created_at': '2026-01-01T00:00:00Z'}], 'meta': {}}),
                          mock.Mock(status_code=402)])
        with mock.patch.object(x.requests, 'get', lambda *a, **k: next(responses)), mock.patch.object(x.time, 'sleep'):
            posts, details = x.collect('token', target=7)
        self.assertEqual(len(posts), 1)
        self.assertIn('402', details['stopped_early'])


class EndToEnd(Sandbox):
    def posts_file(self, day):
        names = ['Beni', 'Masisi', 'Kinshasa']
        posts = [{'id': str(i), 'author_id': str(i), 'lang': 'fr', 'created_at': f'{day}T{i % 20:02d}:00:00Z',
                  'text': f'{SECRET} {names[i % 3]} attaque et combats, offensive en cours'} for i in range(30)]
        path = self.root / 'posts.jsonl'
        x.write_jsonl(path, posts)
        return path

    def test_every_command(self):
        self.prepare()
        backtest.build()
        report = backtest.report(draws=20)
        text = report.read_text()
        self.assertIn('Lower mean primary Brier', text)
        self.assertIn('fatalities001', text)
        reference = json.loads((self.root / 'history' / 'reference.json').read_text())
        self.assertIn(reference['baseline'], ('recent', 'views'))

        out = pipeline.run(posts_file=self.posts_file('2026-06-15'))
        saved = json.loads((out / 'forecast.json').read_text())
        self.assertEqual(saved['issued'], '2026-06-15')
        self.assertEqual(len(saved['rows']), 20)
        for row in saved['rows']:
            self.assertTrue(row['p30'] <= row['p60'] <= row['p90'])
            self.assertIsNotNone(row['p30_views'])
        beni = next(r for r in saved['rows'] if r['hotspot'] == 'Beni')
        self.assertGreater(beni['p30'], beni['p30_baseline'])
        shabunda = next(r for r in saved['rows'] if r['hotspot'] == 'Shabunda')
        self.assertAlmostEqual(shabunda['p30'], shabunda['p30_baseline'])

        result = pipeline.score(draws=20)
        # Snapshot resolved through 2026-10-18: all three horizons from 2026-06-15 are resolved.
        self.assertEqual(sorted(result['horizons']), ['30', '60', '90'])
        self.assertFalse(result['horizons']['30']['conclusive'])

        pipeline.publish()
        page = dashboard.build(self.root / 'history', self.root / 'site', acled.BOXES)
        self.assertIn('Beni', page.read_text())
        self.assertIn(SECRET, (out / 'tagged.jsonl').read_text())  # Sanity: the marker exists locally.
        for path in list((self.root / 'history').rglob('*')) + [page]:
            if path.is_file() and path.suffix != '.png':
                self.assertNotIn(SECRET, path.read_text(), path)
        for path in (self.root / 'history').rglob('*.json'):
            self.assertNotIn('author_id', path.read_text(), path)

    def test_future_events_change_targets_but_never_forecast_inputs(self):
        self.prepare()
        backtest.build()
        before = {(r['hotspot'], r['issue']): r for r in backtest.read_panel(self.data / 'panel.csv')}
        coefficient = json.loads((self.data / 'calibration.json').read_text())['coefficient']
        shutil.rmtree(self.data / 'acled')
        acled.BOXES.unlink()
        self.prepare(quiet_after=date(2024, 6, 1))
        backtest.build()
        after = {(r['hotspot'], r['issue']): r for r in backtest.read_panel(self.data / 'panel.csv')}
        self.assertEqual(json.loads((self.data / 'calibration.json').read_text())['coefficient'], coefficient)
        changed_target = False
        for key, row in after.items():
            if key[1] <= '2024-06-14':  # Lag 14: nothing after 2024-06-01 is visible yet.
                self.assertEqual(row['p_recent_30'], before[key]['p_recent_30'], key)
                self.assertEqual(row['onset'], before[key]['onset'], key)
            self.assertEqual(row['e_30'], before[key]['e_30'], key)
            changed_target |= row['major_90'] != before[key]['major_90']
        self.assertTrue(changed_target)

    def test_a_later_first_event_is_an_onset_with_no_history(self):
        self.prepare()
        export = self.root / 'late.csv'
        with (self.root / 'export.csv').open(newline='') as handle:
            rows = [r for r in csv.DictReader(handle) if r['admin2'] != 'Shabunda' or r['event_date'] >= '2024-06-01']
        x.write_csv(export, rows)
        fixed = datetime(2026, 11, 1, tzinfo=timezone.utc)
        with mock.patch.object(acled, 'now', lambda: fixed):
            acled.import_file(export, '2018-01-01', '2026-10-31')
        backtest.build()
        row = next(r for r in backtest.read_panel(self.data / 'panel.csv') if r['hotspot'] == 'Shabunda' and r['issue'] == '2024-03-01')
        self.assertEqual(row['onset'], 1)
        self.assertAlmostEqual(row['p_recent_30'], -math.expm1(-.5 / 12.5))

    def test_unresolved_horizons_are_not_scored(self):
        self.prepare()
        backtest.build()
        backtest.report(draws=5)
        out = pipeline.run(posts_file=self.posts_file('2026-09-10'))
        pipeline.score(draws=5)
        scores = json.loads((self.root / 'history' / 'runs' / out.name / 'scores.json').read_text())['scores']
        self.assertEqual(sorted(scores), ['p30'])  # Resolved through 2026-10-18.

    def test_only_the_first_run_of_a_week_counts(self):
        self.prepare()
        backtest.build()
        backtest.report(draws=5)
        pipeline.run(posts_file=self.posts_file('2026-06-15'))
        pipeline.run(posts_file=self.posts_file('2026-06-16'))
        self.assertEqual(pipeline.score(draws=5)['horizons']['30']['issues'], 1)

    def test_live_run_refuses_a_stale_acled_snapshot(self):
        self.prepare()
        backtest.build()
        backtest.report(draws=5)
        with self.assertRaisesRegex(ValueError, 'Import a newer export'):
            pipeline.run(posts_file=self.posts_file('2026-12-15'))

    def test_live_run_needs_a_reference(self):
        with self.assertRaisesRegex(ValueError, 'reference'):
            pipeline.preflight(date(2026, 6, 15))

    def test_fetch_checks_prerequisites_before_spending_credits(self):
        with mock.patch.object(x, 'collect') as collect, self.assertRaises(ValueError):
            pipeline.run(fetch_posts=True)
        collect.assert_not_called()

    def test_unreviewed_boxes_stop_the_build(self):
        export = self.root / 'export.csv'
        self.write_acled(export)
        self.write_views()
        acled.import_file(export, '2018-01-01', '2026-10-31')
        with self.assertRaisesRegex(ValueError, 'reviewed=1'):
            backtest.build()

    def test_gazetteer_alias_is_not_a_gap(self):
        tagged = [{'on_topic': True, 'historical': False, 'text': 'village de Kaziba puis village de Nowhere', 'other_places': ['Kaziba']}]
        summary = {'returned': 1, 'requested': 1, 'languages': {}, 'posts_per_day': {}}
        checks = pipeline.health(summary, tagged, [{'hotspot': 'Beni', 'p30_views': .1}])
        self.assertEqual(checks['gazetteer_gaps'], [('nowhere', 1)])


class Orchestration(Sandbox):
    def test_with_nothing_set_up_every_stage_explains_itself(self):
        self.write_views()
        with mock.patch.object(views, 'fetch_all'), mock.patch.object(x, 'load_env', lambda: {}), \
                mock.patch.object(acled, 'load_env', lambda: {}):
            stages = {s['stage']: s['status'] for s in pipeline.orchestrate()}
        self.assertEqual(stages, {'VIEWS': 'done', 'ACLED': 'skipped', 'Backtest': 'skipped', 'Forecast': 'skipped',
                                  'Score': 'skipped', 'Publish': 'done'})
        self.assertTrue((self.root / 'history' / 'views_map.json').exists())
        self.assertTrue((self.root / 'history' / 'pipeline_status.json').exists())

    def test_a_failed_stage_is_recorded_and_later_stages_still_run(self):
        self.write_views()
        with mock.patch.object(views, 'fetch_all', side_effect=ValueError('boom')), mock.patch.object(acled, 'load_env', lambda: {}):
            stages = pipeline.orchestrate()
        self.assertEqual((stages[0]['status'], stages[0]['detail']), ('failed', 'boom'))
        self.assertEqual(stages[-1]['status'], 'done')

    def test_too_few_issues_give_no_interval_rather_than_a_false_certain_one(self):
        cases = [{'issue': f'2026-01-{d:02d}', 'p': .4, 'baseline': .3, 'outcome': d % 2} for d in range(1, 20, 7)]
        self.assertIsNone(pipeline.skill_interval(cases, 30, draws=20))
        many = [{'issue': (date(2026, 1, 1) + timedelta(days=7 * i)).isoformat(), 'p': .4, 'baseline': .3, 'outcome': i % 3 == 0} for i in range(12)]
        low, high = pipeline.skill_interval(many, 30, draws=50)
        self.assertLess(low, high)

    def test_box_hash_ignores_line_endings(self):
        self.write_boxes()
        first = acled.boxes_hash()
        acled.BOXES.write_bytes(acled.BOXES.read_bytes().replace(b'\r\n', b'\n'))
        self.assertEqual(acled.boxes_hash(), first)

    def test_acled_download_needs_credentials_and_reports_cloudflare(self):
        with mock.patch.object(acled, 'load_env', lambda: {}):
            self.assertFalse(acled.download(self.root / 'a.csv', '2018-01-01', '2026-01-01'))
        blocked = mock.Mock(status_code=403, content=b'Just a moment')
        with mock.patch.object(acled, 'load_env', lambda: {'ACLED_EMAIL': 'a', 'ACLED_PASSWORD': 'b'}), \
                mock.patch.object(acled.requests, 'post', lambda *a, **k: blocked):
            with self.assertRaisesRegex(ValueError, 'Cloudflare'):
                acled.download(self.root / 'a.csv', '2018-01-01', '2026-01-01')

    def test_acled_download_pages_until_short_page(self):
        login = mock.Mock(status_code=200, content=b'{}', json=lambda: {'access_token': 't'})
        row = {'event_id_cnty': 'a', 'event_date': '2024-01-01'}
        pages = iter([{'data': [row] * acled.PAGE_SIZE}, {'data': [row]}])
        response = lambda *a, **k: mock.Mock(status_code=200, json=lambda p=next(pages): p)
        path = self.root / 'a.csv'
        with mock.patch.object(acled, 'load_env', lambda: {'ACLED_EMAIL': 'a', 'ACLED_PASSWORD': 'b'}), \
                mock.patch.object(acled.requests, 'post', lambda *a, **k: login), mock.patch.object(acled.requests, 'get', response):
            self.assertTrue(acled.download(path, '2018-01-01', '2026-01-01'))
        self.assertEqual(len(path.read_text().splitlines()), acled.PAGE_SIZE + 2)

    def test_views_map_uses_only_a_released_run(self):
        self.write_views()
        result = views.latest_map(date(2026, 10, 15))
        self.assertEqual(result['run'], 'fatalities002_2026_08_t01')
        self.assertEqual(len(result['cells']), 20)


class Extraction(unittest.TestCase):
    def test_evaluator_scores_rules_against_gold(self):
        post = {'id': '1', 'created_at': '2026-10-01T00:00:00Z', 'text': 'attaque a Beni'}
        gold = [{'post_id': '1', 'lang': 'fr', 'relevant': True, 'historical': False, 'event': True, 'control_change': False,
                 'escalation': False, 'deescalation': False, 'hotspots': {'Beni'}, 'actors': set(), 'fatalities': None}]
        report = evaluate.score(gold, {'1': fc.classify(post)})
        self.assertEqual(report['fields']['event']['f1'], 1.0)
        self.assertEqual(report['fields']['hotspots']['f1'], 1.0)

    def test_llm_rows_match_rule_rows_and_errors_are_excluded(self):
        try:
            from drc_watch import extract_llm
        except ImportError:
            self.skipTest('requirements-llm.txt not installed')
        post = {'id': '1', 'created_at': '2026-10-01T00:00:00Z', 'text': 'Goma était tombée', 'author_id': '9'}
        found = {'relevant': True, 'historical': True, 'event_types': [], 'escalation': False, 'deescalation': False,
                 'hotspots': ['Goma / Nyiragongo'], 'other_places': [], 'actors': [], 'fatalities': None, 'displaced': None, 'source_type': 'news'}
        row = extract_llm.to_tagged(post, {'extraction': found})
        self.assertEqual(set(fc.classify(post)) - set(row), set())
        self.assertTrue(row['historical'] and not row['event'])
        failed = extract_llm.to_tagged(post, {'extraction': None, 'error': 'refusal'})
        self.assertFalse(pipeline.usable(failed))
        self.assertIn('Judge all timing relative', extract_llm.SYSTEM)
        self.assertNotIn('before 2026', extract_llm.SYSTEM + json.dumps(extract_llm.PostExtraction.model_json_schema()))


if __name__ == '__main__':
    unittest.main()
