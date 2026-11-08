# DRC fatalities: Can machine learning on ACLED, plus X posts, forecast DRC conflict deaths better than [VIEWS](https://viewsforecasting.org/)?

**Live dashboard: [mhadeli.github.io/drc-escalation-forecasting](https://mhadeli.github.io/drc-escalation-forecasting/)**

Forecasts cumulative conflict deaths over one, two and three calendar months in the 26 provinces of the Democratic Republic of the Congo, and compares three forecasts: a machine-learning model on ACLED data, the same model with X posts added, and VIEWS. The horizons are labelled 30, 60 and 90 days in the outputs. For a November issue, they cover November, November–December and November–January respectively.

## Workflow

1. **Ingest three sources.** ACLED weekly deaths by province from 2020, VIEWS forecasts from the run public on each date, and 1,000 X posts from 18 to 22 October 2026.
2. **Prepare.** Put all three on the same provinces, calendar months and definition of deaths. An LLM labels each X post and keeps credible news and field reports.
3. **Build the ACLED model.** Random Forest and XGBoost learn monthly deaths from seven ACLED features. The one with lower error on 2023 is frozen before 2024 is scored. Random Forest won, 0.605 against 0.607.
4. **Add X posts.** ACLED is about two weeks behind. X estimates deaths in the month ACLED has not coded, and the same model makes the forecast.
5. **Compare and evaluate.** Every month from January 2024, each model forecasts using only data available then, and is scored against what ACLED recorded.

## Results

| Question | Answer |
|---|---|
| Does the ACLED model beat VIEWS? | Yes, at every horizon. At 30 days, mean log error is 26% lower; the corresponding typical-error multipliers are ×1.55 against ×1.82 for VIEWS. |
| Does it beat repeating the latest coded month's deaths (M−2)? | No. It approximately ties at 30 days (×1.55 against ×1.54) and loses at 60 and 90 days. |
| Does X point to where people die? | Credible X authors rank the six provinces almost as ACLED does, rank correlation 0.94 in the week of 17 October. |
| Does X improve the forecast? | In a one-week test, ranking improves from 0.77 to 0.94, while mean log error increases slightly from 0.298 to 0.316. One week cannot settle it. |

The November 2026 forecast was rebuilt on 8 November with the selected model, so it is retrospective, not prospective. It can be scored from about 14 December 2026.

VIEWS predicts UCDP state-based deaths and the outcome here is ACLED's count, so part of the gap to VIEWS is a difference in definitions.

## How it runs

```
ingest (local) ──▶ prepare ──▶ ACLED model (Random Forest / XGBoost) ──▶ ACLED + X ──▶ compare with VIEWS ──▶ dashboard
```

Ingest is the only step that reads raw data, so it runs on your machine. It writes post-free aggregates to `history/inputs/`. Pushing them starts the `pipeline` workflow on GitHub Actions, which runs the tests and every later stage, commits the results to `history/` and deploys the dashboard.

| Workflow | Trigger | Does |
|---|---|---|
| `pipeline.yml` | A push to `history/inputs/`, or by hand | Tests, every stage after ingest, commits `history/`, deploys the dashboard |
| `pages.yml` | A push to `history/` or the dashboard files | Rebuilds and deploys the dashboard |
| `tests.yml` | Every push and pull request | Runs the unit tests |

No secrets are needed on GitHub.

## Run it locally

After installing the dependencies, a fresh clone can run `pipeline.py orchestrate` directly from the committed aggregates in `history/inputs/`, without raw files or API access. The ingest commands below are for refreshing those aggregates and require your own raw ACLED and X files.

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

`orchestrate` also rebuilds `site/index.html` and writes the standalone summary `reports/backtest.md`. The following offline ingest example requires the named private raw files and previously downloaded VIEWS catalog, run caches and province boundaries. These files are gitignored and are not included in a fresh clone:

```bash
.venv/bin/python pipeline.py ingest \
  --acled raw_data/acled_weekly_2020-01-01_2026-11-07.csv \
  --posts data/raw/drc_posts_20261022T190717Z.jsonl --offline
.venv/bin/python pipeline.py orchestrate
```

To score the November example, import a newer ACLED weekly file after 14 December 2026 and rerun the pipeline. A month must be complete in the file **and** its reporting-lag date must have passed. Scoring records the source hash and retrieval time. The example remains retrospective even after it is scored.

For a new issue month, pass the same month to `ingest --issue YYYY-MM` and `forecast --issue YYYY-MM`. Earlier forecasts are retained in `history/forecasts/`. A forecast is labelled prospective only when saved before its target period starts. `--force` archives the previous file before replacing it; avoid using it for prospective evaluation.

## Optional X and LLM work

Copy `.env.example` to `.env` and supply your keys locally. Fetching posts and calling an LLM consume your API credits; neither happens during ordinary orchestration or CI.

```bash
.venv/bin/python pipeline.py fetch-posts --limit 1000
.venv/bin/pip install -r requirements-llm.txt
# Ingest a saved pull with --posts PATH --extractor MODEL_ID to use Gemini.
# Rules remain the default and need no LLM dependencies.
```

LLM outputs stay in `data/extractions/` and `data/llm_cache/`; only aggregate counters are published. More than 2% extraction failures stops ingestion. New cache identities include the prompt, schema and post content. The extractor also accepts legacy `v1` caches identified by post ID, so those older records can still be reused after inputs change.

For extraction evaluation, run `python -m drc_watch.evaluate --posts PATH sample -n 200`, manually label every boolean field, save `data/labels/gold.csv`, then run `python -m drc_watch.evaluate --posts PATH score --gold data/labels/gold.csv --model MODEL_ID`. Existing labelling sheets are never overwritten. **No extraction accuracy claim is available until those human labels exist.** The X check accepts posts spanning multiple ACLED weeks; it uses author-week counts and reports that ACLED covers full weeks, not the exact partial post window.

## Rules that protect the result

- **Information cutoff:** a forecast issued on the first of month M uses ACLED only through M-2, because ACLED is still coding M-1. VIEWS runs must be released before the issue date, with inputs at least two months old.
- **Chosen once:** the original one-month reference and VIEWS scale stay frozen in `history/reference.json`. A stricter selection-boundary check is reported separately and never automatically replaces this reference. New references use the stricter rule.
- **Frozen forecast:** the November example is written once. Rerunning the pipeline leaves it unchanged.
- **Missing is missing:** months ACLED has not finished are never counted as zero.

## Data rules

Raw ACLED files stay in `raw_data/`, and raw posts, the VIEWS cache and boundaries stay in `data/`. Both are gitignored. `history/` holds only aggregates: monthly and recent weekly deaths per province, VIEWS province totals, X author counts per province, forecasts and scores. No post text or account names are published. Do not add raw data to Actions artifacts or caches, because this repo is public.

ACLED and VIEWS results are published with attribution. Confirm with each provider that this use is allowed: access@acleddata.com for ACLED, and views@pcr.uu.se for VIEWS, whose wiki states no licence. Province boundaries are from geoBoundaries, built from OpenStreetMap under the ODbL.

## Known limits

- VIEWS predicts UCDP state-based deaths. ACLED codes events differently, so even the closest definition does not match, and the comparison is not entirely fair to VIEWS.
- The `state` key is an event-type proxy (battles and explosions/remote violence), not verified state participation. The weekly export has no actor identities.
- The log error rewards forecasting zero in quiet provinces, which favours carrying recent history forward.
- ACLED is a revised snapshot. The one-month gap approximates what was known on each date but cannot reconstruct it.
- One week of X data and one retrospective example cannot show whether X improves forecasts. That needs many weekly issues.
- The X search named the eastern provinces, and X volume tracks attention as much as violence.

## Folders

The page is plain HTML, CSS and JavaScript, with no frontend build step. Edit `drc_watch/dashboard.html` for the page structure, `dashboard.css` for appearance, and `dashboard.js` for chart rendering and wording. Run `python pipeline.py dashboard` to copy the assets and embed the published results into `site/`. Forecast values and the frozen reference are independent of these presentation files.

```
drc_escalation_watch/
├── pipeline.py          command line: fetch-posts, ingest, backtest, xcheck, forecast, score, orchestrate, dashboard
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
│   ├── dashboard.html   page structure and data placeholder
│   ├── dashboard.css    presentation styles
│   └── dashboard.js     charts, interactions and reader explanations
├── .github/workflows/   tests, pipeline, dashboard
├── reports/prereg.md    the evaluation specification
├── tests/
├── history/             aggregates, results and status, tracked in git
├── raw_data/            gitignored: ACLED exports
└── data/                gitignored: raw posts, VIEWS cache, boundaries, LLM cache
```
