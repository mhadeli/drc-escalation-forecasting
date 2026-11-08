"""Tests for the fatalities pipeline. Synthetic data only; nothing here needs the network."""

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
from drc_watch import acled, backtest, dashboard, evaluate, provinces, views
from drc_watch import fetch_recent as x
from drc_watch import forecast as fc

SECRET = 'SECRETPOSTTEXT'
HEADER = ['week', 'region', 'country', 'admin1', 'event_type', 'sub_event_type', 'events', 'fatalities',
          'population_exposure', 'disorder_type', 'centroid_latitude', 'centroid_longitude']


def weekly_file(path, rows):
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for week, country, admin1, event_type, deaths in rows:
            writer.writerow([week, 'Middle Africa', country, admin1, event_type, 'x', 1, f'{deaths:,}', '1,000', 'Political violence', 0, 0])


def square(lon, lat, size=1.0):
    return [[[lon, lat], [lon + size, lat], [lon + size, lat + size], [lon, lat + size], [lon, lat]]]


class Sandbox(unittest.TestCase):
    """Point every module's paths at a temporary folder."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        history = self.root / 'history'
        for module, name, value in [(x, 'DATA', self.root / 'data'), (x, 'ROOT', self.root), (views, 'OUT', self.root / 'data' / 'views'),
                                    (pipeline, 'HISTORY', history), (pipeline, 'INPUTS', history / 'inputs')]:
            patcher = mock.patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.history = history

    def write_inputs(self, deaths=lambda name, month: 10.0, x_events=None, complete='2026-09'):
        """Aggregates as ingest would write them: monthly ACLED, weekly ACLED, VIEWS per issue, X counts."""
        months = backtest.issues_between('2020-01', complete)
        monthly = {name: {m: deaths(name, m) for m in months} for name in provinces.PROVINCES}
        week = {name: 0 for name in provinces.PROVINCES}
        week.update({'Sud-Kivu': 24, 'Nord-Kivu': 6, 'Ituri': 3})
        x.write_json(pipeline.INPUTS / 'acled.json', {
            'file_sha256': 'abc', 'coverage_end': '2026-10-30', 'complete_through': complete, 'first_week': '2019-12-28',
            'last_week': '2026-10-24', 'primary': 'state', 'definitions': {d: list(t) for d, t in acled.DEFINITIONS.items()},
            'monthly': {'state': monthly, 'organised': monthly}, 'weekly': {'state': {'2026-10-17': week}, 'organised': {'2026-10-17': week}}})
        issues = {}
        for issue in backtest.issues_between(pipeline.FIRST_ISSUE, acled.shift(complete, -2)) + [pipeline.ISSUE]:
            issues[issue] = {'run': f'run_{issue}', 'released': f'{issue}-01', 'last_input': f'{issue}-01',
                             'provinces': {n: {acled.shift(issue, i): 12.0 for i in range(3)} for n in provinces.PROVINCES}}
        x.write_json(pipeline.INPUTS / 'views.json', {'cells': 763, 'border_cells': 0, 'cells_per_province': {}, 'boundaries': 'test', 'issues': issues})
        events = x_events or {'Nord-Kivu': 21, 'Sud-Kivu': 48, 'Ituri': 10, 'Tanganyika': 1, 'Mai-Ndombe': 0, 'Kinshasa': 15}
        x.write_json(pipeline.INPUTS / 'x_counts.json', {
            'first_post': '2026-10-18T20:00:00+00:00', 'last_post': '2026-10-22T19:00:00+00:00', 'posts': 10, 'usable_posts': 8,
            'authors': 7, 'posts_per_day': {}, 'languages': {}, 'extractor': 'rules',
            'provinces': {n: {'authors': v + 2, 'event': v, **{s: 0 for s in fc.SIGNALS if s != 'event'}} for n, v in events.items()}})
        x.write_json(pipeline.INPUTS / 'provinces.geojson', {'type': 'FeatureCollection', 'features': []})


class AcledWeekly(unittest.TestCase):
    def test_drops_the_republic_of_congo_and_reads_thousands(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'w.csv'
            weekly_file(path, [('2026-10-17', 'Democratic Republic of Congo', 'Sud-Kivu', 'Battles', 1200),
                               ('2026-10-17', 'Republic of Congo', 'Pool', 'Battles', 5)])
            rows = acled.read_weekly(path)
        self.assertEqual([(r['admin1'], r['fatalities']) for r in rows], [('Sud-Kivu', 1200)])

    def test_rejects_weeks_not_starting_on_saturday_and_unknown_provinces(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'w.csv'
            weekly_file(path, [('2026-10-18', 'Democratic Republic of Congo', 'Sud-Kivu', 'Battles', 1)])
            with self.assertRaises(ValueError):
                acled.read_weekly(path)
            weekly_file(path, [('2026-10-17', 'Democratic Republic of Congo', 'Atlantis', 'Battles', 1)])
            with self.assertRaises(ValueError):
                acled.read_weekly(path)

    def test_definitions_select_event_types(self):
        rows = [{'admin1': 'Ituri', 'week': date(2026, 10, 17), 'event_type': t, 'fatalities': 5}
                for t in ('Battles', 'Explosions/Remote violence', 'Violence against civilians', 'Riots')]
        self.assertEqual(acled.weekly_totals(rows, 'state')[('Ituri', date(2026, 10, 17))], 10)
        self.assertEqual(acled.weekly_totals(rows, 'organised')[('Ituri', date(2026, 10, 17))], 15)

    def test_weeks_split_across_months_by_days_and_totals_are_conserved(self):
        weekly = {('Ituri', date(2024, 1, 27)): 70, ('Ituri', date(2024, 2, 3)): 14}  # 27 Jan to 2 Feb: 5 days in January.
        cover = {'first_month': '2024-01', 'complete_through': '2024-02'}
        monthly = acled.to_monthly(weekly, cover)['Ituri']
        self.assertAlmostEqual(monthly['2024-01'], 50)
        self.assertAlmostEqual(monthly['2024-02'], 34)
        self.assertAlmostEqual(sum(monthly.values()), 84)

    def test_coverage_leaves_out_months_still_being_coded(self):
        rows = [{'week': date(2019, 12, 28)}, {'week': date(2026, 10, 24)}]
        cover = acled.coverage(rows)
        self.assertEqual(cover['coverage_end'], '2026-10-30')
        self.assertEqual(cover['complete_through'], '2026-09')
        self.assertEqual(cover['first_month'], '2020-01')
        rows[-1]['week'] = date(2026, 10, 3)  # Ends 9 October: September ended only 9 days earlier, still being coded.
        self.assertEqual(acled.coverage(rows)['complete_through'], '2026-08')


class Provinces(unittest.TestCase):
    def test_geoboundaries_names_map_to_acled(self):
        self.assertEqual(provinces.acled_name('North Kivu'), 'Nord-Kivu')
        self.assertEqual(provinces.acled_name('Équateur'), 'Equateur')
        with self.assertRaises(ValueError):
            provinces.acled_name('Pool')

    def test_cells_go_to_the_containing_province_holes_excluded_and_border_cells_to_the_nearest(self):
        shapes = {'A': [square(0, 0, 2) + [[[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5], [0.5, 0.5]]]], 'B': [square(0.6, 0.6, 0.8)]}
        centres = {1: (1.75, 1.75), 2: (1.0, 1.0), 3: (5.0, 1.0)}
        assigned, border = provinces.assign_cells(centres, shapes, lambda gid: centres[gid])
        self.assertEqual(assigned, {1: 'A', 2: 'B', 3: 'A'})
        self.assertEqual(border, [3])

    def test_simplify_keeps_the_ends(self):
        ring = [[0, 0], [1, 0.001], [2, 0], [2, 2], [0, 0]]
        out = provinces.simplify(ring, .01)
        self.assertEqual(out[0], ring[0])
        self.assertEqual(out[-1], ring[-1])
        self.assertNotIn([1, 0.001], out)


class Views(Sandbox):
    def fake_get(self, pages):
        pages = iter(pages)
        return lambda url, timeout=None: mock.Mock(status_code=200, json=lambda p=next(pages): p, raise_for_status=lambda: None)

    def test_fatalities001_log_scale_is_converted(self):
        page = {'row_count': 1, 'next_page': '', 'data': [{'pg_id': 5, 'month_id': 508, 'sc_pgm_sb_main': math.log(11), 'sc_pgm_sb_dich_main': .2}]}
        with mock.patch.object(views.requests, 'get', self.fake_get([page])):
            views.fetch_run('fatalities001_2022_03_t01', pause=0)
        self.assertAlmostEqual(views.read_run('fatalities001_2022_03_t01')[0][2], 10)

    def test_tiny_negatives_are_clipped_but_large_ones_fail(self):
        row = lambda v: {'pg_id': 5, 'month_id': 508, 'sc_pgm_sb_main': v, 'sc_pgm_sb_dich_main': .2}
        with mock.patch.object(views.requests, 'get', self.fake_get([{'row_count': 1, 'next_page': '', 'data': [row(-.0011)]}])):
            self.assertEqual(views.fetch_run('fatalities001_2022_01_t01', pause=0)['clipped_negative'], 1)
        with mock.patch.object(views.requests, 'get', self.fake_get([{'row_count': 1, 'next_page': '', 'data': [row(-.5)]}])):
            with self.assertRaises(ValueError):
                views.fetch_run('fatalities001_2022_02_t01', pause=0)

    def test_run_selection_respects_release_and_input_lag(self):
        runs = [{'run': 'a', 'last_input': '2026-08-01', 'released': '2026-09-25'},
                {'run': 'b', 'last_input': '2026-09-01', 'released': '2026-11-01'},  # Released on the issue day.
                {'run': 'c', 'last_input': '2026-10-01', 'released': '2026-10-20'}]  # Input too recent.
        self.assertEqual(views.select_run(runs, date(2026, 11, 1))['run'], 'a')
        runs[1]['released'] = '2026-10-28'
        self.assertEqual(views.select_run(runs, date(2026, 11, 1))['run'], 'b')
        self.assertIsNone(views.select_run(runs, date(2026, 9, 1)))

    def test_province_totals_sum_cells_and_refuse_incomplete_months(self):
        path = views.OUT / 'r.csv'
        x.write_csv(path, [{'pg_id': g, 'month_id': views.month_index('2026-11'), 'main_mean': v, 'main_dich': 0} for g, v in ((1, 2.0), (2, 3.0), (3, 4.0))])
        x.write_json(views.OUT / 'r.meta.json', {'sha256': x.checksum(path)})
        self.assertEqual(views.province_totals('r', {1: 'Ituri', 2: 'Ituri', 3: 'Sud-Kivu'}, ['2026-11']),
                         {'Ituri': {'2026-11': 5.0}, 'Sud-Kivu': {'2026-11': 4.0}})
        with self.assertRaises(ValueError):
            views.province_totals('r', {1: 'Ituri', 2: 'Ituri', 3: 'Sud-Kivu', 4: 'Kasai'}, ['2026-11'])

    def test_month_index(self):
        self.assertEqual(views.month_index('2026-10'), 562)
        self.assertEqual(views.month_index('2029-09'), 597)


class Backtest(unittest.TestCase):
    def test_baseline_skips_the_month_still_being_coded(self):
        series = {'2026-08': 10, '2026-09': 20, '2026-10': 1000}
        self.assertEqual(backtest.baseline_rate(series, '2026-11', 1), 20)
        self.assertEqual(backtest.baseline_rate(series, '2026-11', 2), 15)
        self.assertIsNone(backtest.baseline_rate(series, '2026-11', 3))

    def test_later_data_never_changes_a_forecast(self):
        series = {backtest.issues_between('2022-01', '2024-12')[i]: float(i) for i in range(36)}
        before = {w: backtest.baseline_rate(series, '2024-03', w) for w in backtest.WINDOWS}
        for month in ('2024-02', '2024-03', '2024-06'):
            series[month] = 99999.0
        self.assertEqual(before, {w: backtest.baseline_rate(series, '2024-03', w) for w in backtest.WINDOWS})

    def test_incomplete_outcomes_are_missing_not_zero(self):
        self.assertIsNone(backtest.outcome({'2026-11': 5}, '2026-11', 2))
        self.assertEqual(backtest.outcome({'2026-11': 5, '2026-12': 1}, '2026-11', 2), 6)

    def test_window_choice_uses_2023_only(self):
        months = backtest.issues_between('2020-01', '2026-09')
        flat = {'P': {m: 10.0 for m in months}}
        rows = backtest.panel(flat, {}, backtest.issues_between('2023-01', '2026-07'))
        chosen = backtest.choose_window(rows)[0]
        for m in months:
            if m >= '2024-01':
                flat['P'][m] = 1000.0 if m.endswith(('01', '04', '07', '10')) else 0.0
        self.assertEqual(backtest.choose_window(backtest.panel(flat, {}, backtest.issues_between('2023-01', '2026-07')))[0], chosen)

    def test_spearman_handles_ties_and_constants(self):
        self.assertAlmostEqual(backtest.spearman([1, 2, 3], [10, 20, 30]), 1)
        self.assertAlmostEqual(backtest.spearman([1, 2, 3], [3, 2, 1]), -1)
        self.assertIsNone(backtest.spearman([1, 1, 1], [1, 2, 3]))
        self.assertGreater(backtest.spearman([0, 0, 1, 5], [0, 1, 2, 9]), .9)

    def test_too_few_issues_give_no_interval_rather_than_a_false_certain_one(self):
        rows = [{'issue': i, 'actual': 1, 'views': 2, 'baseline': 1} for i in ('2024-01', '2024-02', '2024-03')]
        self.assertIsNone(backtest.block_interval(rows, 'views', 'baseline', 3))

    def test_views_scale_is_fitted_on_2023_only(self):
        rows = [{'issue': '2023-05', 'horizon': 30, 'views': 10, 'actual': 20}, {'issue': '2024-05', 'horizon': 30, 'views': 10, 'actual': 900}]
        self.assertEqual(backtest.views_scale(rows), 2)


class XSignal(unittest.TestCase):
    def test_provinces_from_hotspots_and_province_names(self):
        row = fc.classify({'id': '1', 'created_at': '2026-10-20T00:00:00Z', 'text': 'Attaque à Uvira, combats au Nord-Kivu'})
        self.assertEqual(row['provinces'], ['Nord-Kivu', 'Sud-Kivu'])

    def test_authors_count_once_and_copies_once(self):
        post = lambda i, author, text: fc.classify({'id': i, 'author_id': author, 'created_at': '2026-10-20T00:00:00Z', 'text': text})
        tagged = [post('1', 'a', 'attaque à Beni'), post('2', 'a', 'combats à Beni hier'), post('3', 'b', 'attaque à Beni')]
        counts = fc.province_signals(tagged, datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertEqual(counts['Nord-Kivu']['authors'], 1)  # b posted a copy of a's text.

    def test_posts_after_the_issue_time_fail(self):
        row = fc.classify({'id': '1', 'created_at': '2026-11-02T00:00:00Z', 'text': 'attaque à Beni'})
        with self.assertRaises(ValueError):
            fc.province_signals([row], datetime(2026, 11, 1, tzinfo=timezone.utc))

    def test_multiplier_is_one_when_shares_match_and_is_limited(self):
        m = fc.x_multipliers({'A': 9, 'B': 9}, {'A': 4, 'B': 4})
        self.assertAlmostEqual(m['A'], 1)
        m = fc.x_multipliers({'A': 999, 'B': 0}, {'A': 0, 'B': 999})
        self.assertEqual((m['A'], m['B']), fc.X_LIMITS[::-1])

    def test_decay_is_identity_at_one_and_fades_with_horizon(self):
        self.assertEqual(fc.decayed(1, 30), 1)
        self.assertGreater(fc.decayed(2, 30), fc.decayed(2, 90))
        self.assertGreater(fc.decayed(2, 90), 1)

    def test_rules_flag_old_posts_relative_to_post_date(self):
        self.assertTrue(fc.classify({'id': '1', 'created_at': '2026-10-01T00:00:00Z', 'text': 'En 2019 une attaque a Beni'})['historical'])


class EndToEnd(Sandbox):
    def test_every_stage_and_the_dashboard(self):
        self.write_inputs()
        stages = pipeline.orchestrate(draws=50)
        self.assertEqual([s['status'] for s in stages], ['done', 'done', 'done', 'done', 'skipped'])
        saved = json.loads((self.history / 'forecast.json').read_text())
        self.assertEqual(saved['acled_through'], '2026-09')
        self.assertEqual(saved['resolves']['30'], '2026-12-14')
        ituri = next(r for r in saved['rows'] if r['province'] == 'Ituri')
        self.assertEqual((ituri['baseline_30'], ituri['baseline_90'], ituri['views_90']), (10.0, 30.0, 36.0))
        self.assertIsNone(next(r for r in saved['rows'] if r['province'] == 'Kasai')['x_30'])
        page = dashboard.build(self.history, self.root / 'site').read_text()
        self.assertNotIn(SECRET, page)
        self.assertIn('"issued": "2026-11-01"', page)

    def test_the_forecast_is_frozen_once_written(self):
        self.write_inputs()
        pipeline.run_backtest(draws=20)
        first, new = pipeline.run_forecast()
        self.assertTrue(new)
        self.write_inputs(deaths=lambda name, month: 500.0 if month == '2026-09' else 10.0)
        again, new = pipeline.run_forecast()
        self.assertFalse(new)
        self.assertEqual(first['rows'], again['rows'])

    def test_a_changed_selection_year_stops_the_backtest(self):
        self.write_inputs()
        pipeline.run_backtest(draws=20)
        self.write_inputs(deaths=lambda name, month: (50.0 if month.endswith(('01', '04', '07', '10')) else 0.0) if month < '2024-01' else 10.0)
        with self.assertRaises(ValueError):
            pipeline.run_backtest(draws=20)

    def test_score_waits_for_acled_then_scores(self):
        self.write_inputs()
        pipeline.run_backtest(draws=20)
        pipeline.run_forecast()
        self.assertEqual(pipeline.run_score()['horizons']['30']['status'], 'pending')
        self.write_inputs(complete='2026-11')
        with mock.patch.object(x, 'now', return_value=datetime(2026, 12, 15, tzinfo=timezone.utc)):
            scored = pipeline.run_score()['horizons']
        self.assertEqual(scored['30']['status'], 'scored')
        self.assertEqual(scored['60']['status'], 'pending')
        self.assertAlmostEqual(scored['30']['error_baseline'], 0)

    def test_x_check_pairs_the_posts_with_their_acled_week(self):
        self.write_inputs()
        result = pipeline.run_xcheck()
        self.assertEqual((result['week_start'], result['week_end']), ('2026-10-17', '2026-10-23'))
        self.assertEqual(result['provinces_n'], 6)

    def test_with_no_inputs_every_stage_explains_itself(self):
        stages = pipeline.orchestrate(draws=10)
        self.assertTrue(all(s['status'] == 'skipped' for s in stages), stages)

    def test_ingested_posts_publish_counts_but_never_text(self):
        posts = self.root / 'posts.jsonl'
        x.write_jsonl(posts, [{'id': '1', 'author_id': 'acct', 'created_at': '2026-10-20T00:00:00.000Z', 'lang': 'fr',
                               'text': f'attaque à Beni {SECRET}'}])
        pipeline.ingest_posts(posts)
        saved = (pipeline.INPUTS / 'x_counts.json').read_text()
        self.assertNotIn(SECRET, saved)
        self.assertNotIn('acct', saved)
        self.assertEqual(json.loads(saved)['provinces']['Nord-Kivu']['event'], 1)


class Extraction(unittest.TestCase):
    def test_evaluator_scores_rules_against_gold(self):
        post = {'id': '1', 'created_at': '2026-10-01T00:00:00Z', 'text': 'attaque a Beni'}
        gold = [{'post_id': '1', 'lang': 'fr', 'relevant': True, 'historical': False, 'event': True, 'control_change': False,
                 'escalation': False, 'deescalation': False, 'hotspots': {'Beni'}, 'actors': set(), 'fatalities': None}]
        report = evaluate.score(gold, {'1': fc.classify(post)})
        self.assertEqual(report['fields']['event']['f1'], 1.0)


class SpatioTemporalML(unittest.TestCase):
    def test_weekly_precursors_and_neighbors(self):
        rows = [
            {'admin1': 'Nord-Kivu', 'week': date(2026, 10, 17), 'event_type': 'Battles',
             'sub_event_type': 'Non-state actor overtakes territory', 'events': 3, 'fatalities': 12},
            {'admin1': 'Sud-Kivu', 'week': date(2026, 10, 17), 'event_type': 'Strategic developments',
             'sub_event_type': 'Agreement', 'events': 1, 'fatalities': 0},
        ]
        pre = acled.weekly_precursors(rows)
        self.assertEqual(pre['event'][('Nord-Kivu', date(2026, 10, 17))], 3)
        self.assertEqual(pre['control_change'][('Nord-Kivu', date(2026, 10, 17))], 3)
        self.assertEqual(pre['deescalation'][('Sud-Kivu', date(2026, 10, 17))], 1)
        shapes = {'Ituri': [square(0, 0, 1)], 'Nord-Kivu': [square(1, 0, 1)], 'Kasai': [square(10, 10, 1)]}
        nbs = provinces.neighbors(shapes)
        self.assertIn('Nord-Kivu', nbs['Ituri'])
        self.assertNotIn('Kasai', nbs['Ituri'])

    def test_additive_control_change_lifts_zero_baseline_province(self):
        months = backtest.issues_between('2020-01', '2026-09')
        monthly = {'Ituri': {m: 0.0 for m in months}}
        precursors = {k: {'Ituri': {m: 0.0 for m in months}} for k in acled.PRECURSORS}
        quiet_vec = backtest.feature_vector('Ituri', '2026-11', monthly, precursors, {'Ituri': []})
        self.assertEqual(float(quiet_vec.sum()), 0.0)
        nowcast = fc.nowcast_from_x({'Ituri': {'event': 5, 'control_change': 2, 'escalation': 1, 'deescalation': 0}}, {'Ituri': 0.0})
        onset_vec = backtest.feature_vector('Ituri', '2026-11', monthly, precursors, {'Ituri': []}, nowcast=nowcast)
        self.assertGreater(onset_vec[3], 0.0)  # Additive control_change feature is positive even when baseline is 0.


if __name__ == '__main__':
    unittest.main()
