# DRC conflict deaths: forecasts against VIEWS

**Live dashboard: [mhadeli.github.io/drc-escalation-forecasting](https://mhadeli.github.io/drc-escalation-forecasting/)**

Forecasts conflict deaths in each of the 26 provinces of the Democratic Republic of the Congo for the next 30, 60 and 90 days. One workflow runs from ingestion to evaluation with three sources:

- **ACLED:** weekly deaths by province, from 2020. It supplies the baseline and the outcomes.
- **VIEWS:** the published forecast to compare with, taken from the run that was public on each date.
- **X posts:** one week of posts, checked against ACLED's same week and used to adjust one live forecast.

## Results so far

| Question | Answer |
|---|---|
| Does a baseline from ACLED history alone beat VIEWS? | Yes, at all three horizons, from January 2024 to July 2026. VIEWS's error is about 39% higher at 30 days. |
| Does X track where people are dying? | Broadly. Rank correlation 0.82 across six provinces in the week of 17 October 2026, which is too few to prove anything. |
| Does X improve the forecast? | Open. The live forecast issued 1 November 2026 resolves from mid-December. |

The evaluation rules, and when each was fixed, are in [reports/prereg.md](reports/prereg.md).

## How it runs

```
ingest (local) ──▶ align ──▶ baseline ──▶ backtest ──▶ X check ──▶ live forecast ──▶ score ──▶ dashboard
```

Ingest is the only step that reads raw data, so it runs on your machine. It writes post-free aggregates to `history/inputs/`. Pushing them starts the `pipeline` workflow on GitHub Actions, which runs the tests and every later stage, commits the results to `history/` and deploys the dashboard.

| Workflow | Trigger | Does |
|---|---|---|
| `pipeline.yml` | A push to `history/inputs/`, or by hand | Tests, every stage after ingest, commits `history/`, deploys the dashboard |
| `pages.yml` | A push to `history/` or the dashboard files | Rebuilds and deploys the dashboard |
| `tests.yml` | Every push and pull request | Runs the unit tests |

No secrets are needed on GitHub.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests

# 1. Ingest. The ACLED file is the weekly aggregated export from the ACLED Data Export Tool, filtered to the DRC.
#    VIEWS runs and province boundaries download on first use; --offline uses the cache.
.venv/bin/python pipeline.py ingest --acled raw_data/acled_weekly.csv --posts data/raw/drc_posts.jsonl

# 2. Every stage after ingest, as GitHub Actions runs them. Each also runs alone:
#    backtest, xcheck, forecast, score.
.venv/bin/python pipeline.py orchestrate

# 3. The dashboard.
.venv/bin/python pipeline.py dashboard && open site/index.html
```

To score the live forecast, download a newer ACLED weekly file after mid-December 2026, run ingest again and push `history/inputs/`. The forecast itself never changes.

## Rules that protect the result

- **Information cutoff:** a forecast issued on the first of month M uses ACLED only through M-2, because ACLED is still coding M-1. VIEWS runs must be released before the issue date, with inputs at least two months old.
- **Chosen once:** the baseline window and the VIEWS rescaling factor are fitted on 2023 and frozen in `history/reference.json`. A later run that would pick differently stops with an error.
- **Frozen forecast:** the live forecast is written once. Rerunning the pipeline leaves it unchanged.
- **Missing is missing:** months ACLED has not finished are never counted as zero.

## Data rules

Raw ACLED files stay in `raw_data/`, and raw posts, the VIEWS cache and boundaries stay in `data/`. Both are gitignored. `history/` holds only aggregates: monthly and recent weekly deaths per province, VIEWS province totals, X author counts per province, forecasts and scores. No post text or account names are published. Do not add raw data to Actions artifacts or caches, because this repo is public.

ACLED and VIEWS results are published with attribution. Confirm with each provider that this use is allowed: access@acleddata.com for ACLED, and views@pcr.uu.se for VIEWS, whose wiki states no licence. Province boundaries are from geoBoundaries, built from OpenStreetMap under the ODbL.

## Known limits

- VIEWS predicts UCDP state-based deaths. ACLED codes events differently, so even the closest definition does not match, and the comparison is not entirely fair to VIEWS.
- The log error rewards forecasting zero in quiet provinces, which favours carrying recent history forward.
- ACLED is a revised snapshot. The one-month gap approximates what was known on each date but cannot reconstruct it.
- One week of X data and one live issue cannot show whether X improves forecasts. That needs many weekly issues.
- The X search named the eastern provinces, and X volume tracks attention as much as violence.

## Folders

```
drc_escalation_watch/
├── pipeline.py          command line: ingest, backtest, xcheck, forecast, score, orchestrate, dashboard
├── drc_watch/
│   ├── acled.py         weekly file, death definitions, weeks to months, coverage
│   ├── provinces.py     province names, boundaries, grid cells to provinces
│   ├── views.py         VIEWS catalog, run cache, run selection, province totals
│   ├── forecast.py      gazetteer, rule extractor, X counts and adjustment
│   ├── backtest.py      baseline, panel, window choice, scores, intervals
│   ├── fetch_recent.py  X pull with daily quotas, file helpers
│   ├── extract_llm.py   optional LLM extraction
│   ├── evaluate.py      blind labelling and extractor scores
│   ├── dashboard.py     builds the static page from history/
│   └── dashboard.html   the page template
├── .github/workflows/   tests, pipeline, dashboard
├── reports/prereg.md    the evaluation specification
├── tests/
├── history/             aggregates, results and status, tracked in git
├── raw_data/            gitignored: ACLED exports
└── data/                gitignored: raw posts, VIEWS cache, boundaries, old project archive
```
