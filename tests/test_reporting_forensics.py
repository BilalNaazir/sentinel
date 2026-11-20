import json
from pathlib import Path

from sentinel.reporting.forensics import latest_calibration


def test_latest_calibration_reads_the_newest_report(tmp_path: Path) -> None:
    for stamp, within in (("20261113_100000", 0.8), ("20261114_100000", 1.0)):
        report = {"judge_model": "claude-sonnet-5-5", "agreement": {"within_one": within}}
        (tmp_path / f"judge_calibration_{stamp}.json").write_text(json.dumps(report), encoding="utf-8")
    newest = latest_calibration(tmp_path)
    assert newest is not None
    assert newest["agreement"]["within_one"] == 1.0
    assert newest["file"] == "judge_calibration_20261114_100000.json"


def test_no_calibration_report_is_none(tmp_path: Path) -> None:
    assert latest_calibration(tmp_path) is None
