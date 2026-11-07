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
        # Descriptive sensitivity: weight each province's mean error by its total deaths.
        province_cases = [[r for r in cases if r['province'] == name] for name in sorted({r['province'] for r in cases})]
        toll = sum(r['actual'] for r in cases)
        weighted = {key: sum(sum(r['actual'] for r in group) * np.mean([log_error(r[key], r['actual']) for r in group])
                             for group in province_cases) / toll if toll else None for key in ('baseline', 'views')}
        rho = {'baseline': [], 'views': []}
        for issue in sorted({r['issue'] for r in cases}):
            group = [r for r in cases if r['issue'] == issue]
            for key in rho:
                value = spearman([r[key] for r in group], [r['actual'] for r in group])
                if value is not None:
                    rho[key].append(value)
        result[str(days)] = {
            'issues': len({r['issue'] for r in cases}), 'forecasts': len(cases),
            'excluded': sum(r['horizon'] == days for r in rows) - len(cases),
            'error_baseline': base, 'error_views': views,
            'skill_views': 1 - views / base if base > 0 else None,
            'skill_views_90': block_interval(cases, 'views', 'baseline', max(3, months), draws),
            'error_views_scaled': views_scaled,
            'death_weighted_error_baseline': weighted['baseline'], 'death_weighted_error_views': weighted['views'],
            'skill_views_scaled': None if views_scaled is None or base <= 0 else 1 - views_scaled / base,
            'skill_views_scaled_90': None if views_scaled is None else block_interval(cases, 'views_scaled', 'baseline', max(3, months), draws),
            'rank_baseline': float(np.mean(rho['baseline'])) if rho['baseline'] else None,
            'rank_views': float(np.mean(rho['views'])) if rho['views'] else None,
            'total_actual': float(sum(r['actual'] for r in cases)),
            'total_baseline': float(sum(r['baseline'] for r in cases)),
            'total_views': float(sum(r['views'] for r in cases)),
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
