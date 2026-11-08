# Sentinel: data and config pack

This pack holds every dataset and config file the Sentinel build plan needs. Drop the folders into the repo root; the layout matches the plan's repository structure.

The workload is support tickets for **Brightdesk**, a fictional project management SaaS with Starter, Pro and Business plans.

## What's here

| File | What it is | Used in |
| --- | --- | --- |
| `data/eval_dataset.v1.json` | **Golden set: 100 hand-written tickets.** Each has a category, exact entities, an ideal summary, a next action, a difficulty, tags and notes. 28 billing, 28 technical, 24 account, 20 general. Includes stress cases: 7 ambiguous, 7 non-English, 6 very short, 4 sarcastic, 4 with typos, 3 multi-issue and 1 prompt injection. | Phases 2, 3, 7 |
| `data/load_test_tickets.jsonl` | **600 template-generated tickets** with known categories and entities, including 60 stress tickets (10%). | Phase 9, and router training |
| `data/complexity_labels.csv` | **260 step requests labelled tier 1/2/3** with features, built from load-test tickets only (no golden overlap). Labels come from explicit rules (`label_reason`). | Phase 4 |
| `data/forensics_planted_failures.json` | **10 failed traces** with simulated span outputs, the true root-cause step and the failure type, covering all 6 failure types. | Phase 5 |
| `data/judge_calibration.json` | **30 candidate summaries** with human scores 1–5 and reasons, across 10 golden tickets. | Phase 3 (judge calibration) |
| `config/models.yaml` | Model registry with prices as of 2026-11-08 | Phase 1 |
| `config/routing.yaml` | Tier-to-model map per step, escalation rule, judge and reference models | Phase 4 |
| `config/thresholds.yaml` | Regression, drift, pass criteria, verification sample rate, alerts | Phases 3, 4, 7 |
| `config/rubrics.yaml` | Judge rubrics: summary quality, step faithfulness, answer agreement | Phases 3–5 |
| `prompts/*.yaml` | Version 1 prompts for intake, extraction, classification and summary | Phase 2 |
| `db/schema.sql` | Postgres schema for the 7 tables in the plan | Phase 1 |
| `docs/labeling_guide.md` | Category definitions, tie-breaks and entity rules behind every label | Always |
| `scripts/` | The generators. Run them to rebuild everything. | — |

## Rebuild

```bash
python scripts/build_golden.py               # validates every entity appears verbatim, writes the golden set
python scripts/build_load_and_complexity.py  # seeded, so output is reproducible
python scripts/build_forensics_and_judge.py
```

## Before you use it

1. **Review the golden set yourself.** The plan's interview story is that the golden set is human-verified. I wrote every case carefully, but you should read all 100, fix anything you disagree with, and note your changes.
2. **Hand-review at least 50 rows of `complexity_labels.csv`.** The tiers are rule-based weak labels. Set `human_reviewed=1` on the rows you check, and say in your write-up which labels were rule-based and which were reviewed.
3. **Check model prices** against the provider pricing pages before you report savings. Your headline number comes from `models.yaml`.
4. **Fill `avg_latency_ms`** in `models.yaml` from your Phase 1 baseline run.
5. **Calibrate the judge before trusting it.** Run the judge on `judge_calibration.json` and report its agreement with the human scores.

## Ways to score

- **Category:** exact match.
- **Entities:** per-field F1 on exact strings, averaged across the six fields. A pass needs F1 ≥ 0.8.
- **Summary:** judge score with `summary_quality_v1`. A pass needs ≥ 4.
- **Forensics:** the analyzer is correct when both `root_cause_step` and `failure_type` match.
