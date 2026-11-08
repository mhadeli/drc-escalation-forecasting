"""Monthly backtest of province fatalities: an ACLED-history baseline against VIEWS, at 30, 60 and 90 days.

An issue is the first day of month M. Horizons cover M, M..M+1 and M..M+2. ACLED's latest weeks are still being
coded on the issue day, so the baseline sees only months through M-2, the same information cutoff VIEWS has.
"""

import math

import numpy as np

from drc_watch.acled import shift
from drc_watch.forecast import HORIZONS

WINDOWS = (1, 3, 6, 12)   # Candidate baseline windows in months; one is chosen on 2023 only.
GAP = 1                    # Months between the newest month the baseline uses and the issue month, minus one.
SELECTION = ('2023-01', '2023-12')
DRAWS = 1000
SELECTION_VERSION = 4  # All selection outcomes must also finish inside the selection year.
FEATURE_NAMES = ('log_b1', 'trend_3m', 'trend_6m', 'control_change', 'escalation', 'deescalation', 'neighbor_deaths')
FEATURE_LABELS = {'log_b1': 'Deaths in the latest coded month', 'trend_3m': '3-month trend', 'trend_6m': '6-month trend',
                  'control_change': 'Territory changing hands', 'escalation': 'Escalation events',
                  'deescalation': 'De-escalation events', 'neighbor_deaths': 'Deaths in neighbouring provinces'}
ML_MODELS = ('random_forest', 'xgboost')
ML_LABELS = {'random_forest': 'Random Forest', 'xgboost': 'XGBoost'}
MIN_TRAINING_ROWS = 50


def new_model(name):
    """Both models predict log(1 + monthly deaths) from the same seven features. Settings are fixed, not tuned."""
    if name == 'random_forest':
        from sklearn.ensemble import RandomForestRegressor
        return RandomForestRegressor(n_estimators=200, min_samples_leaf=5, max_features=.6, random_state=7, n_jobs=-1)
    if name == 'xgboost':
        from xgboost import XGBRegressor
        return XGBRegressor(n_estimators=300, max_depth=3, learning_rate=.05, subsample=.8, colsample_bytree=.8,
                            min_child_weight=5, random_state=7, n_jobs=4, verbosity=0)
    raise ValueError(f'Unknown model: {name}')


def fit_model(name, X, y):
    if len(X) < MIN_TRAINING_ROWS:
        return None
    model = new_model(name)
    model.fit(np.asarray(X, dtype=float), np.asarray(y, dtype=float))
    return model


def predict_deaths(model, features, months=1):
    """Back from log(1 + monthly deaths) to deaths over the horizon. Never negative."""
    if model is None or not features:
        return [None] * len(features)
    raw = model.predict(np.asarray(features, dtype=float))
    return [max(0.0, math.expm1(max(0.0, float(v)))) * months for v in raw]


def baseline_rate(series, issue, window, gap=GAP):
    """Mean monthly deaths over the `window` months ending `gap + 1` months before the issue month."""
    months = [shift(issue, -gap - 1 - i) for i in range(window)]
    if any(m not in series for m in months):
        return None
    return sum(series[m] for m in months) / window


def outcome(series, issue, months):
    keys = [shift(issue, i) for i in range(months)]
    if any(k not in series for k in keys):
        return None  # Not complete yet: never treated as zero.
    return sum(series[k] for k in keys)


def views_total(views_series, issue, months):
    if views_series is None:
        return None
    keys = [shift(issue, i) for i in range(months)]
    if any(k not in views_series for k in keys):
        return None
    return sum(views_series[k] for k in keys)


def issues_between(first, last):
    out, key = [], first
    while key <= last:
        out.append(key)
        key = shift(key, 1)
    return out


def panel(monthly, views_by_issue, issues, gap=GAP):
    """One row per issue, province and horizon, with every candidate baseline and VIEWS. Missing stays None."""
    rows = []
    for issue in issues:
        for name, series in monthly.items():
            for days, months in HORIZONS.items():
                actual = outcome(series, issue, months)
                if actual is None:
                    continue
                rates = {w: baseline_rate(series, issue, w, gap) for w in WINDOWS}
                rows.append({'issue': issue, 'province': name, 'horizon': days, 'actual': actual,
                             **{f'base_{w}': None if r is None else r * months for w, r in rates.items()},
                             'views': views_total((views_by_issue.get(issue) or {}).get(name), issue, months)})
    return rows


def log_error(forecast, actual):
    return abs(math.log1p(forecast) - math.log1p(actual))


def selection_rows(rows):
    return [r for r in rows if SELECTION[0] <= r['issue'] <= SELECTION[1]
            and shift(r['issue'], HORIZONS[r['horizon']] - 1) <= SELECTION[1]]


def choose_window(rows):
    """The window with the lowest mean log error over the selection year, all horizons pooled."""
    picked = selection_rows(rows)
    errors = {}
    for w in WINDOWS:
        usable = [r for r in picked if r[f'base_{w}'] is not None]
        if len(usable) < len(picked) or not usable:
            continue
        errors[w] = float(np.mean([log_error(r[f'base_{w}'], r['actual']) for r in usable]))
    if not errors:
        raise ValueError('No baseline window has complete data in the selection year.')
    return min(errors, key=errors.get), errors


def average_ranks(values):
    values = np.asarray(values, dtype=float)
    ranks = np.empty(len(values))
    ranks[np.argsort(values, kind='stable')] = np.arange(len(values), dtype=float)
    for value in np.unique(values):
        tied = values == value
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(a, b):
    """Rank correlation with average ranks for ties; None when either side is constant."""
    if len(a) < 3 or len(set(a)) < 2 or len(set(b)) < 2:
        return None
    return float(np.corrcoef(average_ranks(a), average_ranks(b))[0, 1])


def block_interval(rows, model, reference, block, draws=DRAWS, seed=7):
    """Resample runs of `block` consecutive issues, keeping every province in an issue together."""
    if draws < 1:
        raise ValueError('Bootstrap draws must be positive.')
    issues = sorted({r['issue'] for r in rows})
    if len(issues) < 2 * block:
        return None  # Every resample would hold nearly the same issues: an interval that only looks certain.
    by_issue = {i: [r for r in rows if r['issue'] == i] for i in issues}
    rng, values = np.random.default_rng(seed), []
    for _ in range(draws):
        picked = []
        while len(picked) < len(issues):
            start = int(rng.integers(len(issues)))
            picked.extend(issues[(start + k) % len(issues)] for k in range(block))
        sample = [r for i in picked[:len(issues)] for r in by_issue[i]]
        ref = np.mean([log_error(r[reference], r['actual']) for r in sample])
        if ref > 0:
            values.append(1 - np.mean([log_error(r[model], r['actual']) for r in sample]) / ref)
    return [float(v) for v in np.quantile(values, [.05, .95])] if values else None


def scores(rows, base_key, draws=DRAWS):
    """Per horizon: mean log error, VIEWS skill relative to the baseline, and province rank correlation."""
    result = {}
    for days, months in HORIZONS.items():
        cases = [{**r, 'baseline': r[base_key]} for r in rows
                 if r['horizon'] == days and r[base_key] is not None and r['views'] is not None]
        if not cases:
            continue
        base = float(np.mean([log_error(r['baseline'], r['actual']) for r in cases]))
        views = float(np.mean([log_error(r['views'], r['actual']) for r in cases]))
        scaled = [r for r in cases if r.get('views_scaled') is not None]
        views_scaled = float(np.mean([log_error(r['views_scaled'], r['actual']) for r in scaled])) if len(scaled) == len(cases) else None
        ml_cases = [r for r in cases if r.get('ml') is not None]
        ml_err = float(np.mean([log_error(r['ml'], r['actual']) for r in ml_cases])) if len(ml_cases) == len(cases) else None
        # Descriptive sensitivity: weight each province's mean error by its total deaths.
        province_cases = [[r for r in cases if r['province'] == name] for name in sorted({r['province'] for r in cases})]
        toll = sum(r['actual'] for r in cases)
        weighted = {key: sum(sum(r['actual'] for r in group) * np.mean([log_error(r[key], r['actual']) for r in group])
                             for group in province_cases) / toll if toll else None for key in ('baseline', 'views')}
        rho = {'baseline': [], 'views': [], 'ml': []}
        for issue in sorted({r['issue'] for r in cases}):
            group = [r for r in cases if r['issue'] == issue]
            for key in ('baseline', 'views'):
                value = spearman([r[key] for r in group], [r['actual'] for r in group])
                if value is not None:
                    rho[key].append(value)
            if ml_err is not None:
                value = spearman([r['ml'] for r in group], [r['actual'] for r in group])
                if value is not None:
                    rho['ml'].append(value)
        result[str(days)] = {
            'issues': len({r['issue'] for r in cases}), 'forecasts': len(cases),
            'excluded': sum(r['horizon'] == days for r in rows) - len(cases),
            'error_baseline': base, 'error_views': views, 'error_ml': ml_err,
            'skill_views': 1 - views / base if base > 0 else None,
            'skill_views_90': block_interval(cases, 'views', 'baseline', max(3, months), draws),
            'skill_ml': None if ml_err is None or base <= 0 else 1 - ml_err / base,
            'skill_ml_90': None if ml_err is None else block_interval(cases, 'ml', 'baseline', max(3, months), draws),
            'error_views_scaled': views_scaled,
            'death_weighted_error_baseline': weighted['baseline'], 'death_weighted_error_views': weighted['views'],
            'skill_views_scaled': None if views_scaled is None or base <= 0 else 1 - views_scaled / base,
            'skill_views_scaled_90': None if views_scaled is None else block_interval(cases, 'views_scaled', 'baseline', max(3, months), draws),
            'rank_baseline': float(np.mean(rho['baseline'])) if rho['baseline'] else None,
            'rank_views': float(np.mean(rho['views'])) if rho['views'] else None,
            'rank_ml': float(np.mean(rho['ml'])) if rho['ml'] else None,
            'total_actual': float(sum(r['actual'] for r in cases)),
            'total_baseline': float(sum(r['baseline'] for r in cases)),
            'total_views': float(sum(r['views'] for r in cases)),
            'total_ml': float(sum(r['ml'] for r in cases)) if ml_err is not None else None,
        }
    return result


def views_scale(rows):
    """One factor putting VIEWS on ACLED's level, fitted on the selection year at 30 days only."""
    picked = [r for r in selection_rows(rows) if r['horizon'] == 30 and r['views'] is not None]
    predicted = sum(r['views'] for r in picked)
    if not picked or predicted <= 0:
        return None
    return sum(r['actual'] for r in picked) / predicted


def add_scaled(rows, factor):
    for r in rows:
        r['views_scaled'] = None if factor is None or r['views'] is None else r['views'] * factor
    return rows


def national_series(rows, base_key, horizon=30):
    """DRC totals per issue at a given horizon (default 30 days): actual, baseline, ML and VIEWS."""
    out = []
    for issue in sorted({r['issue'] for r in rows}):
        group = [r for r in rows if r['issue'] == issue and r['horizon'] == horizon]
        if not group or any(r[base_key] is None for r in group) or any(r['actual'] is None for r in group):
            continue
        views = None if any(r['views'] is None for r in group) else round(sum(r['views'] for r in group), 1)
        scaled = None if views is None or any(r.get('views_scaled') is None for r in group) else round(sum(r['views_scaled'] for r in group), 1)
        ml = None if any(r.get('ml') is None for r in group) else round(sum(r['ml'] for r in group), 1)
        out.append({'month': issue, 'actual': round(sum(r['actual'] for r in group), 1),
                    'baseline': round(sum(r[base_key] for r in group), 1), 'ml': ml, 'views': views, 'views_scaled': scaled})
    return out


def national_horizons(rows, base_key):
    """DRC totals per issue across 30, 60 and 90-day horizons."""
    return {str(days): national_series(rows, base_key, days) for days in HORIZONS}


def by_province(rows, base_key):
    out = {}
    for name in sorted({r['province'] for r in rows}):
        cases = [r for r in rows if r['province'] == name and r['horizon'] == 30 and r[base_key] is not None and r['views'] is not None]
        if not cases:
            continue
        ml_cases = [r for r in cases if r.get('ml') is not None]
        out[name] = {'months': len(cases), 'actual': round(sum(r['actual'] for r in cases), 1),
                     'error_baseline': round(float(np.mean([log_error(r[base_key], r['actual']) for r in cases])), 3),
                     'error_ml': round(float(np.mean([log_error(r['ml'], r['actual']) for r in ml_cases])), 3) if ml_cases else None,
                     'error_views': round(float(np.mean([log_error(r['views'], r['actual']) for r in cases])), 3)}
    return out


def province_series(rows, base_key):
    """Per province at 30 days: month, actual, baseline, VIEWS, and ML for the drill-down chart."""
    out = {}
    for r in sorted(rows, key=lambda r: r['issue']):
        if r['horizon'] == 30 and r[base_key] is not None:
            out.setdefault(r['province'], []).append(
                [r['issue'], round(r['actual'], 1), round(r[base_key], 1),
                 None if r['views'] is None else round(r['views'], 1),
                 None if r.get('ml') is None else round(r['ml'], 1)])
    return out


# Spatio-Temporal ML Model (ACLED Baselines + Precursors + Spatial Spillover + X Nowcast) ---------------------------

def feature_vector(name, issue, monthly, precursors=None, neighbors=None, gap=GAP, nowcast=None):
    """Build the 7-feature spatio-temporal vector for (province, issue).

    When `nowcast` is provided (from X posts covering the M-1 reporting-lag month), the model uses the
    nowcasted M-1 deaths for that province in place of the month ACLED has not finished coding.
    """
    series = dict(monthly[name])
    use_gap = gap
    if nowcast and name in nowcast:
        series[shift(issue, -1)] = nowcast[name]['deaths']
        use_gap = 0
    b1 = baseline_rate(series, issue, 1, use_gap)
    b3 = baseline_rate(series, issue, 3, use_gap)
    b6 = baseline_rate(series, issue, 6, use_gap)
    if any(v is None for v in (b1, b3, b6)):
        return None
    # X supplies only the deaths of the uncoded month. Its author counts are not on the scale of ACLED event counts,
    # so the event features always come from ACLED's latest coded month.
    if precursors:
        cc = baseline_rate(precursors['control_change'].get(name, {}), issue, 1, gap) or 0.0
        es = baseline_rate(precursors['escalation'].get(name, {}), issue, 1, gap) or 0.0
        de = baseline_rate(precursors['deescalation'].get(name, {}), issue, 1, gap) or 0.0
    else:
        cc = es = de = 0.0
    nb_list = (neighbors or {}).get(name, ())
    nb_vals = [baseline_rate(monthly.get(nb, {}), issue, 1, gap) for nb in nb_list]
    nb_vals = [v for v in nb_vals if v is not None]
    nb1 = float(np.mean(nb_vals)) if nb_vals else 0.0
    active = 1.0 if (b1 > 0 or cc > 0) else 0.0
    return np.array([
        math.log1p(b1),
        active * (math.log1p(b3) - math.log1p(b1)),
        active * (math.log1p(b6) - math.log1p(b1)),
        math.log1p(cc),
        active * math.log1p(es),
        active * math.log1p(de),
        active * math.log1p(nb1),
    ], dtype=float)


def training_set(issues, monthly, feats, months):
    X, y = [], []
    for ti in issues:
        for p in monthly:
            f = feats.get((p, ti))
            act = outcome(monthly[p], ti, months)
            if f is not None and act is not None:
                X.append(f)
                y.append(math.log1p(act / months))
    return X, y


def add_ml(rows, monthly, precursors=None, neighbors=None, gap=GAP, models=ML_MODELS):
    """Walk-forward predictions `ml_<model>` for each row. Each issue's models see only outcomes known by then."""
    all_months = sorted({m for series in monthly.values() for m in series})
    first_train = all_months[min(6 + gap, len(all_months) - 1)]
    last_issue = max((r['issue'] for r in rows), default=first_train)
    candidates = issues_between(first_train, last_issue)
    feats = {(p, i): feature_vector(p, i, monthly, precursors, neighbors, gap) for i in candidates for p in monthly}
    predictions = {}
    for issue in sorted({r['issue'] for r in rows}):
        cutoff = shift(issue, -gap - 1)
        names = [p for p in monthly if feats.get((p, issue)) is not None]
        for days, months in HORIZONS.items():
            X, y = training_set([ti for ti in candidates if shift(ti, months - 1) <= cutoff], monthly, feats, months)
            for name in models:
                values = predict_deaths(fit_model(name, X, y), [feats[(p, issue)] for p in names], months)
                predictions.update({(name, p, issue, days): v for p, v in zip(names, values)})
    for r in rows:
        for name in models:
            r[f'ml_{name}'] = predictions.get((name, r['province'], r['issue'], r['horizon']))
    return rows


def choose_ml(rows, models=ML_MODELS):
    """The model with the lowest mean log error on the selection year, where every outcome finished inside 2023."""
    picked = selection_rows(rows)
    errors = {}
    for name in models:
        usable = [r for r in picked if r.get(f'ml_{name}') is not None]
        if usable and len(usable) == len(picked):
            errors[name] = float(np.mean([log_error(r[f'ml_{name}'], r['actual']) for r in usable]))
    if not errors:
        raise ValueError('No ML model has complete predictions in the selection year.')
    return min(errors, key=errors.get), errors


def use_ml(rows, name):
    for r in rows:
        r['ml'] = r.get(f'ml_{name}')
    return rows


def predict_issue_ml(issue, monthly, precursors=None, neighbors=None, gap=GAP, nowcast=None, model='random_forest'):
    """Train on every outcome resolved by the issue's cutoff and predict 30/60/90 days, with and without the X nowcast."""
    all_months = sorted({m for series in monthly.values() for m in series})
    first_train = all_months[min(6 + gap, len(all_months) - 1)]
    cutoff = shift(issue, -gap - 1)
    candidates = issues_between(first_train, cutoff) if first_train <= cutoff else []
    feats = {(p, ti): feature_vector(p, ti, monthly, precursors, neighbors, gap) for ti in candidates for p in monthly}
    predictions, importance = {name: {} for name in monthly}, {}
    for days, months in HORIZONS.items():
        X, y = training_set([ti for ti in candidates if shift(ti, months - 1) <= cutoff], monthly, feats, months)
        fitted = fit_model(model, X, y)
        if fitted is not None and days == 30:
            importance = {k: round(float(v), 4) for k, v in zip(FEATURE_NAMES, fitted.feature_importances_)}
        for name in monthly:
            base = feature_vector(name, issue, monthly, precursors, neighbors, gap)
            with_x = feature_vector(name, issue, monthly, precursors, neighbors, gap, nowcast=nowcast) if nowcast and name in nowcast else None
            ml = predict_deaths(fitted, [base], months)[0] if base is not None else None
            xv = predict_deaths(fitted, [with_x], months)[0] if with_x is not None else None
            predictions[name][f'ml_{days}'] = None if ml is None else round(ml, 1)
            predictions[name][f'x_{days}'] = None if xv is None else round(xv, 1)
    return predictions, {'model': model, 'trained_through': cutoff, 'importance_30': importance}


def predict_weekly_holdout(weekly_deaths, weekly_precursors, neighbors, target_week, nowcasts_by_name, model='random_forest'):
    """Train a weekly version of the selected model up to `target_week - 2w` and predict `target_week`.

    `weekly_deaths` maps `week_iso -> {province: deaths}`. `nowcasts_by_name` maps a label to an X nowcast of the
    gap week `target_week - 1w`, which ACLED had not finished coding.
    """
    from datetime import date, timedelta
    target = date.fromisoformat(target_week) if isinstance(target_week, str) else target_week
    provinces_list = sorted(neighbors) if neighbors else sorted({p for w in weekly_deaths.values() for p in w})

    def get_val(table, prov, wk):
        return float((table.get(wk.isoformat()) or {}).get(prov, 0.0))

    def wk_feat(prov, wk, nowcast=None):
        w2 = wk - timedelta(weeks=2)
        b4 = float(np.mean([get_val(weekly_deaths, prov, wk - timedelta(weeks=i)) for i in range(2, 6)]))
        b12 = float(np.mean([get_val(weekly_deaths, prov, wk - timedelta(weeks=i)) for i in range(2, 14)]))
        b1 = float(nowcast[prov]['deaths']) if nowcast and prov in nowcast else get_val(weekly_deaths, prov, w2)
        cc, es, de = (get_val((weekly_precursors or {}).get(k, {}), prov, w2) for k in ('control_change', 'escalation', 'deescalation'))
        nbs = (neighbors or {}).get(prov, ())
        nb_val = float(np.mean([get_val(weekly_deaths, nb, w2) for nb in nbs])) if nbs else 0.0
        active = 1.0 if (b1 > 0 or cc > 0) else 0.0
        return [math.log1p(b1), active * (math.log1p(b4) - math.log1p(b1)), active * (math.log1p(b12) - math.log1p(b1)),
                math.log1p(cc), active * math.log1p(es), active * math.log1p(de), active * math.log1p(nb_val)]

    all_weeks = sorted(date.fromisoformat(w) for w in weekly_deaths)
    train_weeks = [w for w in all_weeks if w <= target - timedelta(weeks=2) and (w - timedelta(weeks=13)) >= min(all_weeks)]
    X = [wk_feat(p, w) for w in train_weeks for p in provinces_list]
    y = [math.log1p(get_val(weekly_deaths, p, w)) for w in train_weeks for p in provinces_list]
    fitted = fit_model(model, X, y)
    lagged_week = target - timedelta(weeks=2)
    predict = lambda nowcast=None: {p: round(v, 2) for p, v in zip(provinces_list, predict_deaths(fitted, [wk_feat(p, target, nowcast) for p in provinces_list]))}
    models = {'acled_simple': {p: get_val(weekly_deaths, p, lagged_week) for p in provinces_list}, 'ml_acled_only': predict()}
    for label, nowcast in (nowcasts_by_name or {}).items():
        models[label] = predict(nowcast)
    return {'target_week': target.isoformat(), 'lagged_week': lagged_week.isoformat(), 'model': model,
            'training_rows': len(X), 'actual': {p: get_val(weekly_deaths, p, target) for p in provinces_list}, 'models': models}
