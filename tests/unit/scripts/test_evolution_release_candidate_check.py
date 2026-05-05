import json
import sqlite3
from pathlib import Path

from scripts.evaluation import evolution_release_candidate_check as rc_check


def _write_sample_db(
    run_dir: Path,
    *,
    novel_id: str = "novel-rc-test",
    include_status_columns: bool = False,
    needs_review: int = 0,
    constraint_gate_status: str = "passed",
) -> None:
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "aitext.db"
    content = "照影山外门账房里，林照夜查账，照影镜血字仍在，谢无咎和沈青蘅追查安神丹。" * 80
    with sqlite3.connect(db_path) as conn:
        if include_status_columns:
            conn.execute(
                "CREATE TABLE novels (id TEXT PRIMARY KEY, needs_review INTEGER, constraint_gate_status TEXT)"
            )
            conn.execute(
                "INSERT INTO novels VALUES (?, ?, ?)",
                (novel_id, needs_review, constraint_gate_status),
            )
        else:
            conn.execute("CREATE TABLE novels (id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO novels VALUES (?)", (novel_id,))
        conn.execute("CREATE TABLE chapters (novel_id TEXT, number INTEGER, title TEXT, content TEXT)")
        for chapter_number in range(1, 11):
            conn.execute(
                "INSERT INTO chapters VALUES (?, ?, ?, ?)",
                (novel_id, chapter_number, f"第{chapter_number}章", content),
            )
        conn.commit()


def test_scan_gate_warning_outputs_flags_gate_status_warning(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    checked = source_root / "status.py"
    checked.write_text(
        'payload = {"constraint_gate_status": "' + "warning" + '"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(rc_check, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rc_check, "SEARCH_ROOTS", ("source",))

    result = rc_check.scan_gate_warning_outputs()

    assert result["ok"] is False
    assert result["offender_count"] == 1
    assert "constraint_gate_status" in result["offenders"][0]


def test_validate_sample_default_tolerates_old_artifact_without_status_columns(tmp_path):
    run_dir = tmp_path / "run"
    _write_sample_db(run_dir, include_status_columns=False)

    result = rc_check.validate_sample(run_dir, "novel-rc-test")

    assert result["sample_status"] == "passed"
    assert result["ok"] is True
    assert result["acceptance"]["completed_chapters"] == 10
    assert result["acceptance"]["continuity_blocking_count"] == 0
    assert result["status_columns_present"] == {
        "needs_review": False,
        "constraint_gate_status": False,
    }


def test_validate_sample_strict_status_requires_status_columns(tmp_path):
    run_dir = tmp_path / "run"
    _write_sample_db(run_dir, include_status_columns=False)

    result = rc_check.validate_sample(run_dir, "novel-rc-test", strict_status=True)

    assert result["sample_status"] == "failed"
    assert result["blocking"] is True
    assert "strict_sample_status requires needs_review column" in result["failures"]
    assert "strict_sample_status requires constraint_gate_status column" in result["failures"]


def test_validate_sample_strict_status_accepts_fresh_passed_sample(tmp_path):
    run_dir = tmp_path / "run"
    _write_sample_db(run_dir, include_status_columns=True, needs_review=0, constraint_gate_status="passed")

    result = rc_check.validate_sample(run_dir, "novel-rc-test", strict_status=True)

    assert result["sample_status"] == "passed"
    assert result["blocking"] is False
    assert result["status_columns_present"] == {
        "needs_review": True,
        "constraint_gate_status": True,
    }


def test_write_latest_index_points_to_report_paths(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    output_dir = artifact_root / "evolution-release-candidate-test"
    output_dir.mkdir(parents=True)
    monkeypatch.setattr(rc_check, "ARTIFACT_ROOT", artifact_root)
    report = {
        "generated_at": "2026-05-04T00:00:00+00:00",
        "status": "passed",
        "blocking_failures": [],
        "checks": {"sample_acceptance": {"sample_status": "passed"}},
    }

    rc_check.write_latest_index(output_dir, report)

    index = json.loads((artifact_root / "evolution-release-candidate-latest.json").read_text(encoding="utf-8"))
    assert index["status"] == "passed"
    assert index["sample_status"] == "passed"
    assert index["json"] == str(output_dir / "release_candidate_report.json")
    assert index["markdown"] == str(output_dir / "release_candidate_report.md")


def test_scan_gate_warning_outputs_ignores_status_severity_warning_words(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    checked = source_root / "issue.py"
    checked.write_text(
        'payload = {"severity": "warning", "message": "allowed severity"}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(rc_check, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rc_check, "SEARCH_ROOTS", ("source",))

    result = rc_check.scan_gate_warning_outputs()

    assert result["ok"] is True
    assert result["offender_count"] == 0
