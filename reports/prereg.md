# Evaluation specification, version 5

Version 5, 2026-11-07. The question changed from the probability of a major escalation in 20 hotspots to
fatalities per province, because the ACLED data available is the weekly aggregated product, by province.
Earlier specifications remain in git history. Version 4 added a stricter selection-boundary check. Version 5 keeps the original frozen reference as primary and reports that check separately; both reference versions are preserved under history/archive/.

## What was fixed when

Be clear about the order, because it decides what each result can claim.

- The backtest rules below were written together with the first backtest run on 2026-11-07. The baseline
  window was originally chosen on 2023 issue dates: one month scored 0.579, three months 0.580, a practical near tie. Some late-2023 issues use early-2024 outcomes at longer horizons. The separate version-4 check restricts outcomes to 2023 and selects three months. Version 5 retains the original one-month reference rather than retuning it after seeing scores. Treat the analysis as exploratory, not a preregistered independent test.
- The rescaled VIEWS comparison was added after the first run showed that VIEWS predicts fewer deaths than
  ACLED records. It is a secondary check and does not change the decision rule.
- The X adjustment was fixed before any live outcome existed. The X check on the week of 17 October was run
  on the same day, and the adjustment was not changed after seeing it.

## Question

Forecast conflict deaths per DRC province for the next one, two and three calendar months. The 30/60/90 keys are approximate legacy labels, not exact day windows. Test a baseline built from ACLED
history alone against VIEWS, and test whether X posts add information.

## Definitions

- Deaths: ACLED fatalities in Battles plus Explosions/Remote violence, the closest ACLED match to VIEWS's
  state-based UCDP deaths. These event types do not establish state participation: the weekly file does not contain actor identities. Adding Violence against civilians is reported as a second view.
- Units: the 26 DRC provinces as ACLED names them. Rows for the Republic of Congo are dropped by country.
- Months: ACLED weeks start on Saturday. A week crossing a month end is split by days.
- Complete months: a month counts only when the data run at least 14 days past its end. Incomplete months
  are missing, never zero.
- Issue: the first day of month M. Horizons of 30, 60 and 90 days cover M, M..M+1 and M..M+2.

## Forecasts

- Baseline: mean monthly deaths over a window of w months ending at M-2, times the horizon's months. The
  month M-1 is left out because ACLED is still coding it on the issue date.
- Window: w in {1, 3, 6, 12}. The original choice used the lowest mean log error on 2023 issue dates, all horizons pooled. Keep that one-month reference in history/reference.json. Report the stricter outcome-contained choice separately in selection_sensitivity; it must not overwrite the reference. New references use the stricter rule.
- VIEWS: the newest run released strictly before the issue date, with its last input month at least two
  months earlier. Each grid cell goes to the province containing its centre, or the nearest province for
  border cells. Sum the cells and the horizon's months. Cells missing from a run make it unusable.
- VIEWS rescaled: VIEWS times one factor equal to ACLED's 2023 total over VIEWS's 2023 total at 30 days.
- X adjustment: across the six provinces the X gazetteer covers, compare each province's share of X authors
  posting about violence with its share of baseline 30-day deaths, adding one to every count.
  Escalation, control-change and de-escalation tags are extracted but not used in this adjustment. A zero baseline stays zero regardless of author count.
  m = (x_share / baseline_share) ^ 0.5, limited to 0.5-2. It fades with a 21-day time constant:
  a(h) = 1 + (m - 1)(21/h)(1 - exp(-h/21)). Baseline + X = baseline(h) x a(h).

## Scoring

Reporting separates national totals, province ranking and province-month log error. A national-level match does not establish province-month accuracy. Death-weighted error is a descriptive sensitivity: weight each province's mean error by its total recorded deaths, then normalize by the national toll. This was added after seeing the primary scores.

- Error: mean |ln(1 + forecast) - ln(1 + actual)| over province-issues. Skill of VIEWS against the baseline is
  1 - error_VIEWS / error_baseline.
- Rank correlation: Spearman across provinces within each issue, averaged over issues.
- Intervals: 1,000 resamples of blocks of consecutive issues, at least three months long, keeping all
  provinces of an issue together. Report the 5th and 95th percentiles.
- Backtest period: issues from 2024-01 to the last issue whose 90-day outcome is complete. 2023 is reported
  separately as the selection year.
- Sensitivity: the baseline using M-1 as well, as if ACLED had no reporting lag.
- Decision: the baseline beats VIEWS if the 90% skill interval is below zero at all three horizons.
  VIEWS beats the baseline if it is above zero at all three. These statements concern this score and ACLED outcome only; the overlapping horizons are not independent tests and do not establish general superiority over VIEWS.

## X

- X check: X authors posting about violence per province, from posts in one ACLED week, against ACLED deaths
  in that week. Spearman across the six provinces. With six provinces this is a sanity check, not a test.
- Retrospective example: one reconstruction targeting 2026-11-01, saved on 2026-11-07, using ACLED through 2026-09, X posts from 18 to 22 October 2026 and
  VIEWS run fatalities003_2026_09_t01. Its original numeric values are kept, including its original one-month baseline, matching the retained primary reference. It cannot count as prospective evidence. New explicit issue months preserve old forecasts under history/forecasts/. Each horizon is scored when
  ACLED has completed its months. One issue cannot show whether X helps.

## Limits that must accompany every report

VIEWS predicts UCDP state-based deaths and ACLED codes events differently, so no ACLED definition matches
exactly, and this comparison is not entirely fair to VIEWS. ACLED is a revised snapshot, so leaving out the
latest month approximates what was known at each date and cannot reconstruct it. Splitting weeks by days
assumes deaths are even within a week. The log error rewards forecasting zero in quiet provinces, which
favours persistence. The X search named the eastern provinces, so X attention there is partly by design, and
X volume tracks attention as much as violence. Check data terms before sharing.

## Provenance and scoring

A forecast is prospective only if saved before its target period starts. The calendar date and ACLED coverage must both resolve before scoring. Score artifacts record the ACLED hash and retrieval time. Current aggregates contain a revised ACLED snapshot; the reporting gap does not recreate historical publication vintages.
