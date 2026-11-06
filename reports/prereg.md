# Evaluation specification

Written before this implementation's first score. Changes require a new version.

- Question: do live X signals improve on the selected historical baseline?
- Target: at least one ACLED event with 10+ reported fatalities or a sub-event
  type containing `territory`, in a reviewed hotspot box in `(issue, issue+h]`.
  Displacement is excluded from both historical and live targets.
- Monthly historical issues: 2023-01 onward; require a resolved 90-day outcome.
- Event history: trailing 12 calendar months ending 14 days before issue;
  sensitivity checks at 7 and 28 days. Never use ACLED's modification timestamp
  to simulate when a record first became available.
- Recent rate: count distinct months containing a major event, then
  lambda = (k + 0.5) / 12.5; p(h) = 1 - exp(-lambda*h/30).
  This is the plan's smoothed Poisson approximation to monthly occupancy.
- VIEWS: only runs published strictly before issue and with last input month
  at least two calendar months earlier. Choose the newest input month, then
  release, then model version. Cache complete paginated responses.
- VIEWS expected fatalities are summed over cell centres inside each box.
  Prorate monthly means by the exact days in `(issue, issue+h]`; this assumes
  a constant within-month rate. Do not treat missing cells/months as zero.
- Mapping p = 1 - exp(-c*expected_fatalities): one nonnegative c minimizing
  pooled Brier loss on 2022 issues whose outcomes also resolve during 2022.
  Freeze c before evaluating 2023 onward.
- Primary comparison: paired Brier scores at 30, 60 and 90 days for lag 14.
  Report AUC, reliability, onset cases (no major event in six prior months),
  province splits, and 7/28-day lag sensitivity. Use the same complete cases
  for both baselines. Show counts and all missing coverage.
- Intervals: 1,000 paired bootstrap draws of three consecutive issue months,
  retaining all hotspots within each month; report 5th/95th percentiles.
- Choose the lower mean Brier baseline over all three primary horizons.
  This is retrospective model selection, not independent evidence about the
  selected baseline. Freeze its identity and coefficient for subsequent live runs.
- Live: retain the original multiplier weights and 21-day decay as unlearned
  hypotheses. Adjust the reference baseline's cumulative hazards, preserving
  monotonic 30/60/90-day probabilities. Evaluate only resolved horizons with
  confirmed source coverage and record the ACLED snapshot hash/retrieval date.
- Interim at three months; conclusions only after at least 26 weekly issues
  spanning six calendar months. Overlapping forecasts are not independent.

## Limits that must accompany every report

The 20 hotspots were selected with hindsight. Bounding boxes approximate
administrative areas and can overlap. ACLED is a revised snapshot: an event-date
lag is a sensitivity assumption, not a reconstruction of historical publication
availability. VIEWS predicts state-based UCDP fatalities; our ACLED target also
includes other violence. Its mapping is our derived benchmark, not VIEWS's own
probability of major escalation. Historical 2022 release delays reduce the
available calibration sample. Source coverage dates are operator assertions,
not inferred from the first/last recorded event. Check data terms before sharing.
