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
RIDGE_PRIOR = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=float)
RIDGE_ALPHA = 25.0
WEEKLY_ALPHA = 10.0


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


def national_series(rows, base_key):
    """DRC totals per issue at 30 days: what happened, the baseline and VIEWS."""
    out = []
    for issue in sorted({r['issue'] for r in rows}):
        group = [r for r in rows if r['issue'] == issue and r['horizon'] == 30]
        if not group or any(r[base_key] is None for r in group):
            continue
        views = None if any(r['views'] is None for r in group) else round(sum(r['views'] for r in group), 1)
        scaled = None if views is None or any(r.get('views_scaled') is None for r in group) else round(sum(r['views_scaled'] for r in group), 1)
        out.append({'month': issue, 'actual': round(sum(r['actual'] for r in group), 1),
                    'baseline': round(sum(r[base_key] for r in group), 1), 'views': views, 'views_scaled': scaled})
    return out


def by_province(rows, base_key):
    out = {}
    for name in sorted({r['province'] for r in rows}):
        cases = [r for r in rows if r['province'] == name and r['horizon'] == 30 and r[base_key] is not None and r['views'] is not None]
        if not cases:
            continue
        out[name] = {'months': len(cases), 'actual': round(sum(r['actual'] for r in cases), 1),
                     'error_baseline': round(float(np.mean([log_error(r[base_key], r['actual']) for r in cases])), 3),
                     'error_views': round(float(np.mean([log_error(r['views'], r['actual']) for r in cases])), 3)}
    return out


def province_series(rows, base_key):
    """Per province at 30 days: month, what happened, the baseline and VIEWS, for the drill-down chart."""
    out = {}
    for r in sorted(rows, key=lambda r: r['issue']):
        if r['horizon'] == 30 and r[base_key] is not None:
            out.setdefault(r['province'], []).append(
                [r['issue'], round(r['actual'], 1), round(r[base_key], 1), None if r['views'] is None else round(r['views'], 1)])
    return out


# Spatio-Temporal ML Model (ACLED Baselines + Precursors + Spatial Spillover + X Nowcast) ---------------------------

def feature_vector(name, issue, monthly, precursors=None, neighbors=None, gap=GAP, nowcast=None):
    """Build the 7-feature spatio-temporal vector for (province, issue).

    When `nowcast` is provided (from X posts covering the M-1 reporting-lag month),
    the model uses the nowcasted M-1 deaths and precursor signals for that province.
    Quiet provinces (b1 == 0) stay at zero unless an additive `control_change > 0` triggers onset.
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
    if nowcast and name in nowcast:
        cc = float(nowcast[name].get('control_change', 0.0))
        es = float(nowcast[name].get('escalation', 0.0))
        de = float(nowcast[name].get('deescalation', 0.0))
    elif precursors:
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


def fit_ridge(X, y, alpha=RIDGE_ALPHA):
    """Fit Ridge regression shrunk toward the 1-month persistence prior w0 = [1, 0, ...]."""
    if len(X) == 0:
        return RIDGE_PRIOR.copy()
    X_mat = np.asarray(X, dtype=float)
    y_vec = np.asarray(y, dtype=float)
    dim = X_mat.shape[1]
    return np.linalg.solve(X_mat.T @ X_mat + alpha * np.eye(dim), X_mat.T @ y_vec + alpha * RIDGE_PRIOR)


def add_ml(rows, monthly, precursors=None, neighbors=None, gap=GAP, alpha=RIDGE_ALPHA):
    """Attach walk-forward Spatio-Temporal ML predictions (`ml`) to each backtest row without future leakage."""
    all_months = sorted({m for series in monthly.values() for m in series})
    if len(all_months) < 8:
        for r in rows:
            r['ml'] = r.get('base_1')
        return rows
    first_train = all_months[6 + gap]
    last_issue = max((r['issue'] for r in rows), default=first_train)
    candidate_issues = issues_between(first_train, last_issue)
    feats = {(p, i): feature_vector(p, i, monthly, precursors, neighbors, gap)
             for i in candidate_issues for p in monthly}
    by_issue_horizon = {}
    for issue in sorted({r['issue'] for r in rows}):
        cutoff = shift(issue, -gap - 1)
        for days, months in HORIZONS.items():
            train_issues = [ti for ti in candidate_issues if shift(ti, months - 1) <= cutoff]
            X_tr, y_tr = [], []
            for ti in train_issues:
                for p in monthly:
                    f = feats.get((p, ti))
                    act = outcome(monthly[p], ti, months)
                    if f is not None and act is not None:
                        X_tr.append(f)
                        y_tr.append(math.log1p(act / months))
            by_issue_horizon[issue, days] = fit_ridge(X_tr, y_tr, alpha)
    for r in rows:
        f = feats.get((r['province'], r['issue']))
        w = by_issue_horizon.get((r['issue'], r['horizon']))
        months = HORIZONS[r['horizon']]
        if f is None or w is None:
            r['ml'] = None
        else:
            r['ml'] = max(0.0, math.expm1(max(0.0, float(f @ w)))) * months
    return rows


def predict_issue_ml(issue, monthly, precursors=None, neighbors=None, gap=GAP, nowcast=None, alpha=RIDGE_ALPHA):
    """Fit the Spatio-Temporal ML model on all resolved months up to `issue - gap - 1` and predict 30/60/90d."""
    all_months = sorted({m for series in monthly.values() for m in series})
    first_train = all_months[min(6 + gap, len(all_months) - 1)]
    cutoff = shift(issue, -gap - 1)
    train_candidates = issues_between(first_train, cutoff) if first_train <= cutoff else []
    feats = {(p, ti): feature_vector(p, ti, monthly, precursors, neighbors, gap)
             for ti in train_candidates for p in monthly}
    weights = {}
    for days, months in HORIZONS.items():
        valid_issues = [ti for ti in train_candidates if shift(ti, months - 1) <= cutoff]
        X_tr, y_tr = [], []
        for ti in valid_issues:
            for p in monthly:
                f = feats.get((p, ti))
                act = outcome(monthly[p], ti, months)
                if f is not None and act is not None:
                    X_tr.append(f)
                    y_tr.append(math.log1p(act / months))
        weights[days] = fit_ridge(X_tr, y_tr, alpha)
    predictions = {name: {} for name in monthly}
    for name in monthly:
        f_base = feature_vector(name, issue, monthly, precursors, neighbors, gap, nowcast=None)
        f_x = feature_vector(name, issue, monthly, precursors, neighbors, gap, nowcast=nowcast) if nowcast and name in nowcast else None
        for days, months in HORIZONS.items():
            w = weights[days]
            predictions[name][f'ml_{days}'] = None if f_base is None else round(max(0.0, math.expm1(max(0.0, float(f_base @ w)))) * months, 1)
            predictions[name][f'x_{days}'] = None if f_x is None else round(max(0.0, math.expm1(max(0.0, float(f_x @ w)))) * months, 1)
    return predictions, {str(d): [round(float(v), 4) for v in w] for d, w in weights.items()}


def predict_weekly_holdout(weekly_deaths, weekly_precursors, neighbors, target_week, nowcasts_by_name, alpha=WEEKLY_ALPHA):
    """Train the weekly Spatio-Temporal Ridge model up to `target_week - 2w` and evaluate on `target_week` (e.g. 2026-10-24).

    `weekly_deaths` maps `week_iso -> {province: deaths}` (or `(province, date) -> deaths`).
    `nowcasts_by_name` maps label -> nowcast dict for the gap week `target_week - 1w`.
    """
    from datetime import date, timedelta
    target = date.fromisoformat(target_week) if isinstance(target_week, str) else target_week
    provinces_list = sorted(neighbors) if neighbors else sorted({p for w in weekly_deaths.values() for p in (w if isinstance(w, dict) else [])})

    def get_val(table, prov, wk):
        iso = wk.isoformat()
        if iso in table and isinstance(table[iso], dict):
            return float(table[iso].get(prov, 0.0))
        return float(table.get((prov, wk), 0.0))

    def wk_feat(prov, wk, nowcast=None):
        w2 = wk - timedelta(weeks=2)
        b4 = float(np.mean([get_val(weekly_deaths, prov, wk - timedelta(weeks=i)) for i in range(2, 6)]))
        b12 = float(np.mean([get_val(weekly_deaths, prov, wk - timedelta(weeks=i)) for i in range(2, 14)]))
        if nowcast and prov in nowcast:
            b1 = float(nowcast[prov]['deaths'])
            cc = float(nowcast[prov].get('control_change', 0.0))
            es = float(nowcast[prov].get('escalation', 0.0))
            de = float(nowcast[prov].get('deescalation', 0.0))
        else:
            b1 = get_val(weekly_deaths, prov, w2)
            cc = get_val((weekly_precursors or {}).get('control_change', {}), prov, w2)
            es = get_val((weekly_precursors or {}).get('escalation', {}), prov, w2)
            de = get_val((weekly_precursors or {}).get('deescalation', {}), prov, w2)
        nbs = (neighbors or {}).get(prov, ())
        nb_val = float(np.mean([get_val(weekly_deaths, nb, w2) for nb in nbs])) if nbs else 0.0
        active = 1.0 if (b1 > 0 or cc > 0) else 0.0
        return np.array([
            math.log1p(b1),
            active * (math.log1p(b4) - math.log1p(b1)),
            active * (math.log1p(b12) - math.log1p(b1)),
            math.log1p(cc),
            active * math.log1p(es),
            active * math.log1p(de),
            active * math.log1p(nb_val),
        ], dtype=float)

    all_weeks = sorted(date.fromisoformat(w) for w in weekly_deaths if isinstance(w, str))
    train_weeks = [w for w in all_weeks if w <= target - timedelta(weeks=2) and (w - timedelta(weeks=13)) >= min(all_weeks)]
    X_tr = [wk_feat(p, w) for w in train_weeks for p in provinces_list]
    y_tr = [math.log1p(get_val(weekly_deaths, p, w)) for w in train_weeks for p in provinces_list]
    weights = fit_ridge(X_tr, y_tr, alpha)

    lagged_week = target - timedelta(weeks=2)
    base_1w = {p: get_val(weekly_deaths, p, lagged_week) for p in provinces_list}
    actual = {p: get_val(weekly_deaths, p, target) for p in provinces_list}
    ml_only = {p: round(max(0.0, math.expm1(max(0.0, float(wk_feat(p, target) @ weights)))), 2) for p in provinces_list}
    models = {'acled_simple': base_1w, 'ml_acled_only': ml_only}
    for label, nowcast in (nowcasts_by_name or {}).items():
        models[label] = {p: round(max(0.0, math.expm1(max(0.0, float(wk_feat(p, target, nowcast) @ weights)))), 2) for p in provinces_list}
    return {'target_week': target.isoformat(), 'lagged_week': lagged_week.isoformat(),
            'weights': dict(zip(FEATURE_NAMES, [round(float(v), 4) for v in weights])),
            'actual': actual, 'models': models}
