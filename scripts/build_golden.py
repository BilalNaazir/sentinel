"""Validate the hand-written golden tickets and write data/eval_dataset.v1.json."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from golden_source import TICKETS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENTITY_KEYS = ["names", "emails", "products", "reference_ids", "amounts", "dates"]
CATEGORIES = {"billing", "technical", "account", "general"}
DIFFICULTIES = {"easy", "medium", "hard"}


def build():
    cases, errors, seen = [], [], set()
    for row in TICKETS:
        cid, cat, diff, lang, tags, text, summary, action, ents, notes = row
        if cid in seen:
            errors.append(f"{cid}: duplicate id")
        seen.add(cid)
        if cat not in CATEGORIES:
            errors.append(f"{cid}: bad category {cat}")
        if diff not in DIFFICULTIES:
            errors.append(f"{cid}: bad difficulty {diff}")
        for k in ents:
            if k not in ENTITY_KEYS:
                errors.append(f"{cid}: unknown entity key {k}")
        full = {k: list(ents.get(k, [])) for k in ENTITY_KEYS}
        for k, vals in full.items():
            for v in vals:
                if v not in text:
                    errors.append(f"{cid}: entity {k}={v!r} not found verbatim in text")
        if len(summary.split()) > 40:
            errors.append(f"{cid}: summary longer than 40 words")
        cases.append({
            "case_id": cid,
            "dataset_version": 1,
            "source": "golden",
            "input": {"ticket_text": text, "language": lang},
            "expected": {
                "category": cat,
                "entities": full,
                "summary": summary,
                "next_action": action,
            },
            "difficulty": diff,
            "tags": tags,
            "failure_type": None,
            "notes": notes,
        })
    if errors:
        print("\n".join(errors))
        sys.exit(1)

    out = {
        "dataset": "sentinel-support-tickets",
        "version": 1,
        "created": "2026-11-08",
        "description": "Golden eval set for the 4-step support ticket pipeline. Hand-written; review every label before relying on it.",
        "labeling_rules_ref": "docs/labeling_guide.md",
        "cases": cases,
    }
    path = ROOT / "data" / "eval_dataset.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {len(cases)} cases -> {path}")
    print("category:", dict(Counter(c['expected']['category'] for c in cases)))
    print("difficulty:", dict(Counter(c['difficulty'] for c in cases)))
    print("language:", dict(Counter(c['input']['language'] for c in cases)))
    tags = Counter(t for c in cases for t in c["tags"])
    print("edge tags:", {k: tags[k] for k in ["ambiguous", "very_short", "typos", "sarcasm", "non_english", "multi_issue", "prompt_injection", "mixed_currency", "missing_info"]})


if __name__ == "__main__":
    build()
