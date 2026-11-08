"""Evaluation, provenance, and privacy checks. No network or paid inference."""

import importlib.util
import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import TestCase, mock, skipUnless

import pipeline
from drc_watch import acled, backtest, provinces, views
from drc_watch import evaluate
from drc_watch import fetch_recent as x
from test_pipeline import Sandbox, SECRET


class Selection(TestCase):
    def test_selection_excludes_targets_extending_into_the_test_year(self):
        def row(issue, horizon, actual, a, b):
            return {'issue': issue, 'horizon': horizon, 'actual': actual,
                    'base_1': a, 'base_3': b, 'base_6': 99, 'base_12': 99, 'views': 1}
        inside = row('2023-01', 30, 0, 0, 10)
        leaked = row('2023-12', 90, 1000, 999, 0)
        self.assertEqual(backtest.selection_rows([inside, leaked]), [inside])
        self.assertEqual(backtest.choose_window([inside, leaked])[0], 1)
        self.assertEqual(backtest.views_scale([inside, leaked]), 0)

    def test_bootstrap_rejects_zero_draws(self):
        with self.assertRaises(ValueError):
            backtest.block_interval([], 'a', 'b', 3, draws=0)

    def test_error_score_reports_excluded_cases(self):
        rows = [{'issue': '2024-01', 'province': 'Ituri', 'horizon': 30, 'actual': 1, 'base': 1, 'views': v}
                for v in (2, None)]
        scores = backtest.scores(rows, 'base', draws=2)['30']
        self.assertEqual((scores['forecasts'], scores['excluded']), (1, 1))


class DataValidation(TestCase):
    def test_missing_and_negative_counts_are_not_zeros(self):
        for value in ('', None, '-1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                acled.number(value)
        self.assertEqual(acled.number('0'), 0)

    def test_coverage_cannot_extend_to_future_days_of_a_week(self):
        cover = acled.coverage([{'week': date(2026, 10, 24)}], as_of=date(2026, 10, 25))
        self.assertEqual(cover['coverage_end'], '2026-10-25')

    def test_evaluation_cannot_silently_drop_missing_predictions(self):
        with self.assertRaises(ValueError):
            evaluate.score([{'post_id': 'absent'}], {})


class Provenance(Sandbox):
    def test_new_issue_preserves_the_old_forecast_and_uses_the_actual_creation_time(self):
        self.write_inputs(complete='2026-10')
        pipeline.run_backtest(draws=2)
        original, _ = pipeline.run_forecast()
        path = pipeline.INPUTS / 'views.json'
        data = json.loads(path.read_text())
        data['issues']['2026-12'] = {
            'run': 'december', 'released': '2026-11-25', 'last_input': '2026-10-01',
            'provinces': {n: {'2026-12': 12, '2027-01': 12, '2027-02': 12} for n in provinces.PROVINCES}}
        x.write_json(path, data)
        with mock.patch.object(x, 'now', return_value=datetime(2026, 11, 30, tzinfo=timezone.utc)):
            current, new = pipeline.run_forecast(issue_key='2026-12')
        self.assertTrue(new)
        self.assertEqual(current['evaluation_kind'], 'prospective')
        self.assertEqual(current['months']['90'], ['2026-12', '2027-01', '2027-02'])
        archived = json.loads((self.history / 'forecasts' / '2026-11.json').read_text())
        self.assertEqual(archived['rows'], original['rows'])

    def test_missing_views_horizon_blocks_forecast_creation(self):
        self.write_inputs()
        pipeline.run_backtest(draws=2)
        path = pipeline.INPUTS / 'views.json'
        data = json.loads(path.read_text())
        del data['issues'][pipeline.ISSUE]['provinces']['Ituri']['2027-01']
        x.write_json(path, data)
        with self.assertRaises(ValueError):
            pipeline.run_forecast()
        self.assertFalse((self.history / 'forecast.json').exists())

    def test_incomplete_views_download_is_not_cached(self):
        response = mock.Mock()
        response.json.return_value = {'row_count': 2, 'next_page': '', 'data': [
            {'pg_id': 5, 'month_id': 562, 'main_mean': 1, 'main_dich': .1}]}
        with mock.patch.object(views.requests, 'get', return_value=response):
            with self.assertRaises(ValueError):
                views.fetch_run('incomplete', pause=0)
        self.assertFalse((views.OUT / 'incomplete.csv').exists())

    def test_x_daily_quotas_cover_seven_contiguous_windows(self):
        calls = []
        def get(url, params, **kwargs):
            calls.append(params)
            response = mock.Mock(status_code=200)
            response.json.return_value = {'data': [{'id': str(len(calls))}], 'meta': {}}
            return response
        current = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
        with mock.patch.object(x.requests, 'get', side_effect=get), mock.patch.object(x.time, 'sleep'):
            posts, summary = x.collect('not-a-real-token', target=7, current=current, pause=0)
        self.assertEqual(len(posts), 7)
        self.assertEqual(len(summary['daily_quotas']), 7)
        self.assertTrue(all(a['end_time'] == b['start_time'] for a, b in zip(calls, calls[1:])))
        self.assertTrue(all(c['start_time'].endswith('Z') for c in calls))

    def test_seven_day_post_window_can_overlap_two_acled_weeks(self):
        self.write_inputs()
        posts = self.root / 'posts.jsonl'
        x.write_jsonl(posts, [{'id': str(i), 'author_id': 'a', 'created_at': day, 'text': 'attaque a Beni'}
                             for i, day in enumerate(('2026-10-22T10:00:00Z', '2026-10-25T10:00:00Z'))])
        pipeline.ingest_posts(posts)
        path = pipeline.INPUTS / 'acled.json'
        data = json.loads(path.read_text())
        for definition in data['weekly']:
            data['weekly'][definition]['2026-10-24'] = {n: 1 for n in provinces.PROVINCES}
        x.write_json(path, data)
        result = pipeline.run_xcheck()
        self.assertEqual(result['weeks_n'], 2)
        self.assertEqual(result['week_end'], '2026-10-30')
        self.assertEqual(result['x_measure'], 'author-week counts')

    def test_legacy_reference_stays_frozen_when_selection_sensitivity_differs(self):
        self.write_inputs()
        old = {'window': 12, 'views_scale': 10 / 12, 'frozen_at': '2026-11-07', 'errors': {'12': .1}}
        x.write_json(self.history / 'reference.json', old)
        result = pipeline.run_backtest(draws=2)
        reference = json.loads((self.history / 'reference.json').read_text())
        self.assertEqual({k: reference[k] for k in old}, old)  # The frozen choices never change.
        self.assertIn(reference['ml_model'], backtest.ML_MODELS)  # The model choice is frozen alongside them.
        self.assertEqual(result['window'], 12)
        self.assertEqual(result['selection_sensitivity']['outcomes_through'], '2023-12')

    def test_kind_uses_actual_creation_time_not_a_backdated_issue(self):
        self.assertEqual(pipeline.forecast_kind({'issued': '2026-11-01', 'written_at': '2026-11-07T10:00:00+00:00'}), 'retrospective')
        self.assertEqual(pipeline.forecast_kind({'issued': '2026-12-01', 'written_at': '2026-11-30T10:00:00+00:00'}), 'prospective')

    def test_existing_forecast_only_gets_a_provenance_annotation(self):
        saved = {'issued': '2026-11-01', 'written_at': '2026-11-07T10:00:00+00:00', 'rows': [{'baseline_30': 123}]}
        x.write_json(self.history / 'forecast.json', saved)
        loaded, new = pipeline.run_forecast()
        self.assertFalse(new)
        self.assertEqual(loaded['rows'], saved['rows'])
        self.assertEqual(loaded['evaluation_kind'], 'retrospective')

    def test_failed_backtest_does_not_use_stale_reference_for_a_new_forecast(self):
        self.write_inputs()
        with mock.patch.object(pipeline, 'run_backtest', side_effect=ValueError('bad selection')):
            stages = pipeline.orchestrate(draws=2)
        self.assertEqual([s['status'] for s in stages], ['done', 'failed', 'done', 'skipped', 'skipped'])
        self.assertFalse((self.history / 'forecast.json').exists())

    def test_calendar_and_data_must_both_resolve_before_scoring(self):
        self.write_inputs()
        pipeline.run_backtest(draws=2)
        pipeline.run_forecast()
        self.write_inputs(complete='2026-11')
        with mock.patch.object(x, 'now', return_value=datetime(2026, 11, 7, tzinfo=timezone.utc)):
            self.assertEqual(pipeline.run_score()['horizons']['30']['status'], 'pending')
        with mock.patch.object(x, 'now', return_value=datetime(2026, 12, 15, tzinfo=timezone.utc)):
            result = pipeline.run_score()
        self.assertEqual(result['horizons']['30']['status'], 'scored')
        self.assertEqual(result['acled_sha256'], 'abc')
        self.assertEqual(result['evaluation_kind'], 'retrospective')

    def test_missing_outcome_month_is_not_scored_as_zero(self):
        self.write_inputs()
        pipeline.run_backtest(draws=2)
        pipeline.run_forecast()
        self.write_inputs(complete='2026-11')
        path = pipeline.INPUTS / 'acled.json'
        data = json.loads(path.read_text())
        del data['monthly']['state']['Ituri']['2026-11']
        x.write_json(path, data)
        with mock.patch.object(x, 'now', return_value=datetime(2026, 12, 15, tzinfo=timezone.utc)):
            self.assertEqual(pipeline.run_score()['horizons']['30']['status'], 'missing')

    def test_offline_ingestion_never_downloads_missing_boundaries(self):
        with mock.patch.object(pipeline, 'BOUNDARIES', self.root / 'missing.geojson'), \
             mock.patch.object(pipeline.views, 'catalog', return_value=[]), \
             mock.patch.object(pipeline.requests, 'get') as get:
            with self.assertRaises(ValueError):
                pipeline.ingest_views({'complete_through': '2026-09'}, fetch=False)
            get.assert_not_called()


@skipUnless(importlib.util.find_spec('pydantic'), 'Optional LLM dependency not installed')
class LLMPrivacy(Sandbox):
    def setUp(self):
        super().setUp()
        from drc_watch import extract_llm
        self.extractor = extract_llm

    def test_llm_geography_replaces_rules_instead_of_inheriting_it(self):
        post = {'id': '1', 'author_id': 'a', 'created_at': '2026-10-20T00:00:00Z', 'text': 'attaque a Beni'}
        record = {'extraction': {'relevant': True, 'historical': False, 'hotspots': ['Uvira (Ruzizi plain)'],
                  'actors': [], 'event_types': ['armed_clash'], 'escalation': False, 'deescalation': False,
                  'fatalities': None, 'displaced': None, 'other_places': [], 'source_type': 'news'}}
        row = self.extractor.to_tagged(post, record)
        self.assertEqual(row['provinces'], ['Sud-Kivu'])
        failed = self.extractor.to_tagged(post, {'extraction': None, 'error': 'refusal'})
        self.assertEqual(failed['provinces'], [])

    def test_cache_identity_includes_the_post_text(self):
        post = {'id': '1', 'created_at': '2026-10-20T00:00:00Z', 'text': 'attaque a Beni'}
        response = {'extraction': {'relevant': True}, 'error': None}
        with mock.patch.object(self.extractor, 'CACHE', self.root / 'cache'), \
             mock.patch.object(self.extractor, '_call_gemini', return_value=response) as call:
            self.extractor.extract_one(None, post, 'gemini-test')
            self.extractor.extract_one(None, post, 'gemini-test')
            self.extractor.extract_one(None, {**post, 'text': 'different report'}, 'gemini-test')
        self.assertEqual(call.call_count, 2)

    def test_error_messages_cannot_leak_post_text_to_published_inputs(self):
        from drc_watch import forecast as fc
        post = {'id': '1', 'author_id': 'a', 'created_at': '2026-10-20T00:00:00Z', 'text': 'attaque a Beni'}
        path = self.root / 'posts.jsonl'
        x.write_jsonl(path, [post])
        stats = {'model': 'gemini-test', 'prompt_version': 'v2', 'posts': 1, 'errors': 0,
                 'tokens': {}, 'error_examples': [SECRET]}
        with mock.patch.object(self.extractor, 'tag_posts', return_value=([fc.classify(post)], stats)):
            pipeline.ingest_posts(path, 'gemini-test')
        self.assertNotIn(SECRET, (pipeline.INPUTS / 'x_counts.json').read_text())


class ModelChoice(TestCase):
    def test_a_near_tie_keeps_the_frozen_model_but_a_real_change_fails(self):
        reference = {'ml_model': 'random_forest'}
        with mock.patch.object(pipeline, 'HISTORY', Path(tempfile.mkdtemp())):
            self.assertEqual(pipeline.freeze_ml(reference, 'xgboost', {'random_forest': .6070, 'xgboost': .6052})['ml_model'], 'random_forest')
            with self.assertRaises(ValueError):
                pipeline.freeze_ml(reference, 'xgboost', {'random_forest': .70, 'xgboost': .60})
