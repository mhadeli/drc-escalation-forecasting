# DRC conflict deaths: can a simple forecast match VIEWS, and does X help?

**Live dashboard: [mhadeli.github.io/drc-escalation-forecasting](https://mhadeli.github.io/drc-escalation-forecasting/)**

Estimates conflict deaths in each of the 26 provinces of the Democratic Republic of the Congo for one, two and three calendar months. The `30/60/90` keys are approximate legacy labels, not exact day windows. One workflow runs from ingestion to evaluation with three sources:

- **ACLED:** weekly deaths by province, from 2020. It supplies the baseline and the outcomes.
- **VIEWS:** the published forecast to compare with, taken from the run that was public on each date.
- **X posts:** one week of posts, checked against ACLED's same week and used to adjust one frozen November forecast example.

## Results so far

January 2024 through July 2026, using the original frozen one-month persistence baseline. The `30` horizon is one calendar month.

| Question | Number | Interpretation |
|---|---|---|
| Does the simple forecast match the national death toll? | **6,759 predicted; 6,811 recorded** | One-month persistence closely matches the national level. This does not establish accurate forecasts for each province or month. |
| Does VIEWS match that toll? | **4,305 predicted** | Its total is lower, but VIEWS predicts UCDP state-based deaths while these outcomes are ACLED battles and explosions. The national-level gap is not a like-for-like loss. |
| Where are the deaths? | **Mean province rank correlation: 0.59 baseline; 0.50 VIEWS** | The simple forecast is slightly better at ordering provinces on this sample. |
| What is the province-month log error? | **0.43 baseline; 0.60 VIEWS; VIEWS skill interval entirely below zero** | Persistence is lower on the specified score. Every quiet province-month has the same weight as Nord-Kivu. |

VIEWS's mean log error is about **39% higher** than persistence, and it is worse in both Nord-Kivu and Sud-Kivu, where most deaths occur, while doing better in Ituri (**1,235** recorded deaths) and Mai-Ndombe (**193**). Weighting each province's mean error by its total deaths narrows the relative gap to about **31%**, without reversing it; this is a descriptive sensitivity check, not the primary score. Rescaling VIEWS by **1.38**, fitted on 2023, increases its log error to **0.66**. Its lower national total is therefore not a sufficient explanation for the log-score gap.

The two- and three-month rows repeat the pattern. They share outcome months with the one-month score, so they are one overlapping check, not three independent results.

### Does X help?

**Open.** The 0.82 rank correlation is same-week attention against same-week deaths, on six provinces the search was aimed at. Kinshasa has **15 event authors and zero primary-definition deaths** in that week. This does not test whether X improves a forecast.

The frozen adjustment multiplies the baseline. Kinshasa's November baseline is **zero**, so its 15 authors still produce **zero** forecast deaths. The un-decayed multipliers are about **0.58 in Ituri**, **1.08 in Nord-Kivu**, and **1.03 in Sud-Kivu**. Escalation, control-change and de-escalation are extracted but are **not used in this adjustment**. This formula can test a small reallocation among provinces with existing predicted deaths; it cannot detect a new outbreak where the baseline is zero. Another formula would be another untested rule, and one week of posts is not enough to fit one.

The November example's first outcome window can be scored from **14 December 2026**, if ACLED coverage is complete. It was saved on **7 November**, after its November 1 target period began, so it is a **retrospective reconstruction**, not a prospective test. Its values remain frozen. One resolved example will still not establish that X improves forecasts.

### Window choice and method

The original selection was a practical near tie: one month scored **0.579**, three months **0.580**. The frozen primary choice stays **one month**: each province's most recent available monthly death rate, ending at M-2. This numerical near tie is not a statistical equivalence test.

A separate boundary check restricts both issue dates and final outcome months to 2023; it selects three months (**0.545**, versus **0.566** for one month). Original late-2023 selection issues included early-2024 outcomes at longer horizons. That limitation is disclosed, not silently fixed by retuning the frozen reference after seeing evaluation results. The stricter check remains in `history/backtest.json` as `selection_sensitivity`. Results are exploratory. The information cutoff, frozen reference and missing-data rules remain the method; details and their timing are in [reports/prereg.md](reports/prereg.md).

**Presentation sentence:** A one-month ACLED forecast matches the national toll and is a bit better at ranking provinces; VIEWS uses a different death definition and is worse on this log score, particularly in the Kivus; X has not been tested as a forecasting improvement.

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

`orchestrate` also rebuilds `site/index.html` and writes the standalone summary `reports/backtest.md`. To use the provided data entirely offline:

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
# Ingest a saved pull with --posts PATH --extractor MODEL_ID to use Gemini or Claude.
# Rules remain the default and need no LLM dependencies.
```

LLM outputs stay in `data/extractions/`; only aggregate counters are published. More than 2% extraction failures stops ingestion. Caches include prompt, schema and post content, so edited inputs cannot reuse stale outputs.

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
│   ├── dashboard.html   page structure and data placeholder
│   ├── dashboard.css    presentation styles
│   └── dashboard.js     charts, interactions and reader explanations
├── .github/workflows/   tests, pipeline, dashboard
├── reports/prereg.md    the evaluation specification
├── tests/
├── history/             aggregates, results and status, tracked in git
├── raw_data/            gitignored: ACLED exports
└── data/                gitignored: raw posts, VIEWS cache, boundaries, old project archive
```
