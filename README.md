# DRC escalation forecasting

**Live dashboard: [mhadeli.github.io/drc-escalation-forecasting](https://mhadeli.github.io/drc-escalation-forecasting/)**

Do text signals beat a forecasting baseline built from ACLED history alone?

For 20 DRC hotspots, this forecasts the probability of a major escalation within 30, 60 and 90 days. A major escalation is at least one ACLED event with 10+ fatalities or a territory change. The evaluation rules were fixed before the first score in [reports/prereg.md](reports/prereg.md).

One workflow runs from ingestion to evaluation with two text sources, and compares the result with VIEWS:

- **Baseline:** each hotspot's own ACLED history over the trailing 12 months, with no text. It is fixed in advance.
- **ACLED notes:** a text signal backtested from 2023 onward, because notes have history.
- **X posts:** a text signal scored live, because X recent search reaches back only 7 days.
- **VIEWS:** an outside comparison, using forecasts as they were published at the time, mapped to this target with one coefficient fitted on 2022.

## Dashboard and automation

The dashboard shows results, maps, the forecast against VIEWS, how each number is calculated, and the status of every stage. GitHub Actions builds it from committed files in `history/`.

| Workflow | Trigger | Does |
|---|---|---|
| `pipeline.yml` | Mondays, or by hand from the Actions tab | Tests, then all stages, then commits `history/` and deploys the dashboard |
| `pages.yml` | A push that changes `history/`, `config/` or the dashboard | Rebuilds and deploys the dashboard |
| `tests.yml` | Every push and pull request | Runs the unit tests |

```
tests ──▶ VIEWS ──▶ ACLED ──▶ Backtest ──▶ Forecast ──▶ Score ──▶ Publish ──▶ dashboard
```

A stage that lacks its inputs skips itself and says why on the dashboard, so the workflow can run before everything is set up.

### One-time setup

1. **Pages:** Settings → Pages → Source: GitHub Actions.
2. **Secrets:** Settings → Secrets and variables → Actions. Add `ACLED_EMAIL` and `ACLED_PASSWORD`, plus `X_BEARER_TOKEN` and `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` when you want live forecasts.
3. **Hotspot boxes:** the first ACLED import commits drafts to `config/hotspots.csv`. Review each box, set `reviewed=1`, and push.
4. **Weekly X pull, optional:** it costs credits, so it is off. Set the repository variable `ENABLE_X_PULL` to `true` to schedule it, and `X_EXTRACTOR` to choose a model.

ACLED's Cloudflare protection may block logins from GitHub's servers. If the ACLED stage reports that, run the backtest locally from an export and push `history/` and `config/`. The dashboard needs nothing else.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests

# 1. Export DRC events from the myACLED Data Export Tool, 2018 onward. Give the dates you filtered to.
.venv/bin/python pipeline.py fetch --acled export.csv --coverage-start 2018-01-01 --coverage-end 2026-10-31

# 2. Open config/hotspots.csv, check each drafted box, and set reviewed=1. The build refuses unreviewed boxes.

# 3. Backtest the baseline, ACLED notes and VIEWS. The first report freezes the VIEWS mapping in history/reference.json.
.venv/bin/python pipeline.py build
.venv/bin/python pipeline.py backtest
.venv/bin/python pipeline.py publish

# 4. Live forecast: a new X pull, or a saved posts file. The default extractor is rules.
.venv/bin/python pipeline.py run --fetch --extractor gemini-3.8-flash
.venv/bin/python pipeline.py run --posts saved_posts.jsonl

# 5. Import a newer ACLED export, then score whatever has resolved.
.venv/bin/python pipeline.py score

# 6. Build the dashboard and open it.
.venv/bin/python pipeline.py dashboard
open site/index.html
```

`pipeline.py orchestrate` runs the stages in order, as GitHub Actions does.

LLM extraction needs `pip install -r requirements-llm.txt` and a key in `.env`. To test an extractor, label a sample blind and score it:

```bash
.venv/bin/python -m drc_watch.evaluate --posts saved_posts.jsonl sample   # save the labelled copy as data/labels/gold.csv
.venv/bin/python -m drc_watch.evaluate --posts saved_posts.jsonl score --model gemini-3.8-flash
```

## Rules that protect the result

- **As-of:** a forecast at t uses ACLED events dated at least 14 days before t, with sensitivity checks at 7 and 28 days. ACLED is a revised snapshot, so this approximates what was known then and does not reconstruct it.
- **VIEWS vintage:** only runs released before t, with input data at least two months old.
- **VIEWS scale:** `fatalities001` publishes `ln(fatalities+1)` under different field names. `read_forecast` in `views.py` converts it to fatalities, and later versions already publish fatalities.
- **Notes signal:** rule patterns only, because an LLM reading old text already knows what followed.
- **Calibration:** the coefficient sees only outcomes that resolved during 2022.
- **Missing is missing:** absent cells, months and unresolved outcomes are never treated as zero.
- **Live scoring:** only resolved horizons count, and only the first run of each ISO week. Conclusions wait for 26 weekly issues across six months.

## Data rules

Raw posts, raw ACLED events, extractions, the LLM cache and briefs stay in `data/`, which is gitignored, or on the temporary GitHub runner. `history/` holds only forecasts, author counts per hotspot, health checks, scores and the aggregated VIEWS map. Do not add raw data to Actions artifacts or caches, because this repo is public. The code keeps no per-author reliability record, and nothing trains or fine-tunes a model on X content. Check that an LLM tier does not train on inputs before sending posts to it.

ACLED and VIEWS results are published with attribution. Confirm with each provider that this use is allowed: access@acleddata.com for ACLED and views@pcr.uu.se for VIEWS, whose wiki states no licence.

## Known limits

- The 20 hotspots were chosen in 2026 knowing where fighting happened, so scores are optimistic compared with a prospective list.
- VIEWS predicts state-based UCDP deaths, while the target covers all ACLED violence, so VIEWS is at a disadvantage and the mapping is our own.
- The coefficient is fitted on `fatalities001` and applied to later VIEWS models. VIEWS has no usable run before July 2022, so its 2022 sample is small.
- A box too small to contain a 0.5° cell centre has no VIEWS forecast. `build` warns about these, and they drop out of the paired scores.
- The X multiplier weights are unlearned hypotheses, and X volume tracks attention as much as violence.

## Folders

```
drc_escalation_watch/
├── pipeline.py          command line, live run, health checks, brief, scoring, publishing
├── drc_watch/           the code
│   ├── fetch_recent.py  X pull with daily quotas, file helpers
│   ├── acled.py         ACLED import, hotspot boxes, targets, recent rate
│   ├── views.py         VIEWS catalog, run cache, grid mapping, expected fatalities
│   ├── forecast.py      gazetteer, rule extractor, X multiplier
│   ├── extract_llm.py   LLM extraction, prompt version, cache
│   ├── backtest.py      panel, calibration, scoreboard, report
│   ├── evaluate.py      blind labelling sheet, field-level extractor scores
│   ├── dashboard.py     builds the static page from history/
│   └── dashboard.html   the page template, charts and map
├── .github/workflows/   tests, pipeline, dashboard
├── config/              hotspots.csv, created by the first ACLED import
├── reports/             prereg.md is tracked; backtest reports are gitignored
├── tests/
├── history/             post-free results and pipeline status, tracked in git
├── site/                the built dashboard, gitignored
└── data/                gitignored: raw posts, labels, VIEWS cache, ACLED, runs, LLM cache
    └── x_post_archive/  old project's runs, forecasts, notebook and README, kept for reference
```

The old project's forecasts stay in the archive because they use hand-set tier priors and cannot be scored by the new `score` command.
