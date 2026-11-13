"""The LLM judge: a second model scores a summary against the rubric in config/rubrics.yaml.

python -m sentinel.evals.judge reports/golden_run_XXXX.json            # judge 5 summaries from a run
python -m sentinel.evals.judge reports/golden_run_XXXX.json GT-021     # ...or chosen tickets
"""

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from sentinel.gateway.client import send_request
from sentinel.pipeline.models import StepOutputError, parse_output
from sentinel.tracing.core import last_span

RUBRICS_PATH = Path("config") / "rubrics.yaml"
ROUTING_PATH = Path("config") / "routing.yaml"
SUMMARY_RUBRIC = "summary_quality_v1"
JUDGE_SYSTEM = "You are a strict, fair grader of customer support summaries. Reply with JSON only."


class Rubric(BaseModel):
    name: str
    prompt: str

    def fill(self, **values: str) -> str:
        """Put the values into the rubric's {placeholders}. Other braces (like the JSON example) stay."""
        text = self.prompt
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        return text


class JudgeScore(BaseModel):
    score: int = Field(ge=1, le=5)
    reason: str


@cache
def load_rubric(name: str) -> Rubric:
    rubrics = yaml.safe_load(RUBRICS_PATH.read_text(encoding="utf-8"))
    return Rubric(name=name, prompt=rubrics[name]["prompt"])


@cache
def judge_model() -> str:
    """The judge is fixed in routing.yaml, so scores stay comparable from run to run."""
    model: str = yaml.safe_load(ROUTING_PATH.read_text(encoding="utf-8"))["judge_model"]
    return model


def judge_summary(ticket_text: str, reference_summary: str, candidate_summary: str) -> JudgeScore:
    rubric = load_rubric(SUMMARY_RUBRIC)
    prompt = rubric.fill(
        ticket_text=ticket_text,
        reference_summary=reference_summary,
        candidate_summary=candidate_summary,
    )
    resp = send_request(prompt, judge_model(), system=JUDGE_SYSTEM, max_tokens=1000, step="judge")
    span = last_span()
    try:
        return parse_output(resp.text, JudgeScore)
    except StepOutputError as exc:
        if span is not None:
            span.error = f"StepOutputError: {exc.reason}"
        raise


if __name__ == "__main__":
    import json
    import sys

    from sentinel.settings import get_settings

    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    chosen = sys.argv[2:]
    golden = {
        c["case_id"]: c for c in json.loads(get_settings().golden_path.read_text(encoding="utf-8"))["cases"]
    }
    results = [r for r in report["results"] if r["summary"] and (not chosen or r["ticket_id"] in chosen)]

    for r in results[: len(chosen) or 5]:
        case = golden[r["ticket_id"]]
        verdict = judge_summary(case["input"]["ticket_text"], case["expected"]["summary"], r["summary"])
        print(f"{r['ticket_id']}  score {verdict.score}  {verdict.reason}")
        print(f"         summary: {r['summary']}\n")
