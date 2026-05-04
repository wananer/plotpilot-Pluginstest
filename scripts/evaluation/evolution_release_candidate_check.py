#!/usr/bin/env python3
"""Run the Evolution unified-constraint release-candidate checks.

This script is intentionally verification-only. It never generates chapters or
calls an LLM; sample validation only reads an existing pressure-test database.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = PROJECT_ROOT / ".omx" / "artifacts"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ALLOWED_GATE_STATUSES = {"passed", "auto_revised", "needs_review", "skipped"}
GATE_FIELDS = (
    "constraint_gate_status",
    "constraint_status",
    "route_gate_status",
    "boundary_gate_status",
    "chapter_draft_status",
)
SEARCH_ROOTS = ("application", "interfaces", "plugins", "scripts/evaluation", "tests")

PYTEST_TARGETS = [
    "tests/unit/scripts/test_evolution_release_candidate_check.py",
    "tests/unit/test_gate_status_semantics.py",
    "tests/unit/plugins/world_evolution_core/test_chapter_execution_draft.py",
    "tests/unit/application/services/test_voice_drift_service.py",
    "tests/test_autopilot_routes.py",
    "tests/integration/interfaces/api/v1/test_voice_api.py",
    "tests/test_evolution_pressure_test.py::test_article_issue_report_separates_continuity_blocking_from_style_warning",
    "tests/test_evolution_pressure_test.py::test_article_issue_report_writes_json_and_markdown",
    "tests/test_evolution_world_service.py::test_diagnostics_summary_separates_continuity_and_style_counts",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def run_command(command: list[str], *, cwd: Path = PROJECT_ROOT, timeout: int = 300) -> dict[str, Any]:
    started = utc_now()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        output = proc.stdout or ""
        return {
            "command": command,
            "started_at": started,
            "returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "output_tail": output[-6000:],
        }
    except Exception as exc:
        return {
            "command": command,
            "started_at": started,
            "returncode": 124,
            "ok": False,
            "output_tail": str(exc),
        }


def scan_gate_warning_outputs() -> dict[str, Any]:
    field_pattern = "|".join(re.escape(field) for field in GATE_FIELDS)
    patterns = [
        re.compile(rf"({field_pattern})\s*[:=]\s*[\"']warning[\"']"),
        re.compile(rf"[\"']({field_pattern})[\"']\s*:\s*[\"']warning[\"']"),
    ]
    offenders: list[str] = []
    for root in SEARCH_ROOTS:
        root_path = PROJECT_ROOT / root
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".vue", ".js"} or not path.is_file():
                continue
            rel = path.relative_to(PROJECT_ROOT)
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if any(pattern.search(line) for pattern in patterns):
                    offenders.append(f"{rel}:{line_number}:{line.strip()}")
    return {
        "ok": not offenders,
        "allowed_statuses": sorted(ALLOWED_GATE_STATUSES),
        "offender_count": len(offenders),
        "offenders": offenders,
    }


def discover_sample() -> dict[str, Any] | None:
    candidates: list[tuple[float, Path, str, int]] = []
    if not ARTIFACT_ROOT.exists():
        return None
    for db_path in ARTIFACT_ROOT.glob("*/data/aitext.db"):
        try:
            for novel_id, chapter_count in _chapter_counts(db_path).items():
                if chapter_count >= 10:
                    candidates.append((db_path.stat().st_mtime, db_path.parent.parent, novel_id, chapter_count))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    mtime, run_dir, novel_id, chapter_count = candidates[0]
    return {
        "run_dir": run_dir,
        "novel_id": novel_id,
        "chapter_count": chapter_count,
        "selection": "latest_mtime_with_at_least_10_completed_chapters",
        "database_mtime": mtime,
        "candidate_count": len(candidates),
    }


def _chapter_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT novel_id, count(*) FROM chapters WHERE length(coalesce(content, '')) > 0 GROUP BY novel_id"
        ).fetchall()
    return {str(novel_id): int(count or 0) for novel_id, count in rows if novel_id}


def _novel_status(db_path: Path, novel_id: str) -> dict[str, Any]:
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cols = {row[1] for row in conn.execute("PRAGMA table_info(novels)").fetchall()}
            if "id" not in cols:
                return {}
            wanted = [
                col
                for col in ("id", "current_stage", "autopilot_status", "constraint_gate_status", "needs_review")
                if col in cols
            ]
            if not wanted:
                return {}
            row = conn.execute(
                f"SELECT {', '.join(wanted)} FROM novels WHERE id = ? LIMIT 1",
                (novel_id,),
            ).fetchone()
            return dict(row) if row else {}
    except Exception:
        return {}


def validate_sample(run_dir: Path | None, novel_id: str | None, *, strict_status: bool = False) -> dict[str, Any]:
    sample_source = "explicit" if run_dir and novel_id else "auto_discovered"
    discovery: dict[str, Any] = {}
    if not (run_dir and novel_id):
        discovered = discover_sample()
        if discovered:
            discovery = {
                key: (str(value) if isinstance(value, Path) else value)
                for key, value in discovered.items()
                if key not in {"run_dir", "novel_id"}
            }
            run_dir = discovered["run_dir"]
            novel_id = discovered["novel_id"]
    if not (run_dir and novel_id):
        return {
            "sample_status": "missing",
            "ok": True,
            "blocking": False,
            "message": "No existing 10-chapter sample found. Run the frontend pressure test and pass --sample-run-dir/--sample-novel-id.",
        }

    run_dir = run_dir.resolve()
    db_path = run_dir / "data" / "aitext.db"
    if not db_path.exists():
        return {
            "sample_status": "missing",
            "ok": True,
            "blocking": False,
            "run_dir": str(run_dir),
            "novel_id": novel_id,
            "message": "Sample database not found.",
        }

    from scripts.evaluation.evolution_article_issue_report import build_report

    try:
        article_report = build_report(run_dir, novel_id, no_llm=True)
    except Exception as exc:
        return {
            "sample_status": "failed",
            "ok": False,
            "blocking": True,
            "run_dir": str(run_dir),
            "novel_id": novel_id,
            "message": str(exc),
        }

    status = _novel_status(db_path, novel_id)
    summary = article_report.get("summary") or {}
    acceptance = {
        "completed_chapters": article_report.get("chapter_count"),
        "needs_review": status.get("needs_review"),
        "constraint_gate_status": status.get("constraint_gate_status"),
        "continuity_blocking_count": summary.get("continuity_blocking_count"),
        "style_warning_count": summary.get("style_warning_count"),
        "style_needs_review_count": summary.get("style_needs_review_count"),
    }
    # Older artifact DBs may not have the latest status columns. They are
    # informative but should not fail RC validation when chapter/report checks pass.
    status_gate_known = bool(status.get("constraint_gate_status"))
    needs_review_known = status.get("needs_review") is not None
    failures = []
    if acceptance["completed_chapters"] != 10:
        failures.append("completed_chapters != 10")
    if acceptance["continuity_blocking_count"] != 0:
        failures.append("continuity_blocking_count != 0")
    if needs_review_known and bool(acceptance["needs_review"]):
        failures.append("needs_review is true")
    if status_gate_known and acceptance["constraint_gate_status"] != "passed":
        failures.append("constraint_gate_status is not passed")
    if strict_status and not needs_review_known:
        failures.append("strict_sample_status requires needs_review column")
    if strict_status and not status_gate_known:
        failures.append("strict_sample_status requires constraint_gate_status column")
    if strict_status and needs_review_known and bool(acceptance["needs_review"]):
        failures.append("strict_sample_status requires needs_review=false")
    if strict_status and status_gate_known and acceptance["constraint_gate_status"] != "passed":
        failures.append("strict_sample_status requires constraint_gate_status=passed")
    return {
        "sample_status": "passed" if not failures else "failed",
        "ok": not failures,
        "blocking": bool(failures),
        "source": sample_source,
        "discovery": discovery,
        "strict_status": strict_status,
        "run_dir": str(run_dir),
        "novel_id": novel_id,
        "acceptance": acceptance,
        "status_columns_present": {
            "needs_review": needs_review_known,
            "constraint_gate_status": status_gate_known,
        },
        "failures": failures,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    suffix = "-strict" if args.strict_sample_status else ""
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else ARTIFACT_ROOT / f"evolution-release-candidate-{now_slug()}{suffix}"
    output_dir.mkdir(parents=True, exist_ok=True)
    pytest_result = run_command([sys.executable, "-m", "pytest", *PYTEST_TARGETS, "-q"], timeout=args.pytest_timeout)
    frontend_result = {"ok": True, "skipped": True, "reason": "skip_frontend_build"}
    if not args.skip_frontend_build:
        frontend_result = run_command(["npm", "run", "build"], cwd=PROJECT_ROOT / "frontend", timeout=args.frontend_timeout)
    gate_scan = scan_gate_warning_outputs()
    sample = validate_sample(
        Path(args.sample_run_dir).expanduser().resolve() if args.sample_run_dir else None,
        args.sample_novel_id,
        strict_status=args.strict_sample_status,
    )
    blocking_failures = []
    if not pytest_result.get("ok"):
        blocking_failures.append("pytest_failed")
    if not frontend_result.get("ok"):
        blocking_failures.append("frontend_build_failed")
    if not gate_scan.get("ok"):
        blocking_failures.append("gate_warning_output_found")
    if sample.get("blocking"):
        blocking_failures.append("sample_acceptance_failed")
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "output_dir": str(output_dir),
        "status": "passed" if not blocking_failures else "failed",
        "blocking_failures": blocking_failures,
        "checks": {
            "pytest": pytest_result,
            "frontend_build": frontend_result,
            "gate_warning_scan": gate_scan,
            "sample_acceptance": sample,
        },
        "public_contract": {
            "allowed_gate_statuses": sorted(ALLOWED_GATE_STATUSES),
            "report_summary_fields": [
                "continuity_blocking_count",
                "continuity_issue_count",
                "style_warning_count",
                "style_needs_review_count",
                "story_quality_issue_count",
            ],
            "compatibility_fields": [
                "boundary_gate_status",
                "route_gate_status",
                "chapter_draft_status",
                "constraint_gate_status",
                "last_constraint_issue",
                "last_chapter_audit.issues",
            ],
        },
    }
    write_report(output_dir, report)
    write_latest_index(output_dir, report)
    return report


def write_report(output_dir: Path, report: dict[str, Any]) -> None:
    json_path = output_dir / "release_candidate_report.json"
    md_path = output_dir / "release_candidate_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")


def write_latest_index(output_dir: Path, report: dict[str, Any]) -> None:
    index_path = ARTIFACT_ROOT / "evolution-release-candidate-latest.json"
    index = {
        "generated_at": report.get("generated_at"),
        "status": report.get("status"),
        "output_dir": str(output_dir),
        "json": str(output_dir / "release_candidate_report.json"),
        "markdown": str(output_dir / "release_candidate_report.md"),
        "blocking_failures": report.get("blocking_failures") or [],
        "sample_status": ((report.get("checks") or {}).get("sample_acceptance") or {}).get("sample_status"),
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks") or {}
    sample = checks.get("sample_acceptance") or {}
    lines = [
        "# Evolution Unified Constraint RC Report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Blocking failures: `{', '.join(report.get('blocking_failures') or []) or 'none'}`",
        f"- Pytest: `{'passed' if (checks.get('pytest') or {}).get('ok') else 'failed'}`",
        f"- Frontend build: `{'passed' if (checks.get('frontend_build') or {}).get('ok') else 'failed'}`",
        f"- Gate warning scan: `{'passed' if (checks.get('gate_warning_scan') or {}).get('ok') else 'failed'}`",
        f"- Sample status: `{sample.get('sample_status')}`",
        "",
        "## Sample Acceptance",
        "",
        f"- Run dir: `{sample.get('run_dir', '')}`",
        f"- Novel id: `{sample.get('novel_id', '')}`",
        f"- Source: `{sample.get('source', '')}`",
        f"- Discovery: `{json.dumps(sample.get('discovery') or {}, ensure_ascii=False)}`",
        f"- Strict status: `{sample.get('strict_status', False)}`",
        f"- Acceptance: `{json.dumps(sample.get('acceptance') or {}, ensure_ascii=False)}`",
        f"- Failures: `{', '.join(sample.get('failures') or []) or 'none'}`",
        "",
        "## Public Contract",
        "",
        f"- Allowed Gate statuses: `{', '.join((report.get('public_contract') or {}).get('allowed_gate_statuses') or [])}`",
        "- `warning` is an issue severity only; it must not be written as a Gate status.",
        "- Style drift is audited separately from continuity blocking.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Evolution unified-constraint release-candidate checks.")
    parser.add_argument("--output-dir", help="Where to write release_candidate_report.json/md.")
    parser.add_argument("--sample-run-dir", help="Existing pressure-run directory containing data/aitext.db.")
    parser.add_argument("--sample-novel-id", help="Novel id inside the sample run database.")
    parser.add_argument("--strict-sample-status", action="store_true", help="Require sample DB status columns and values: needs_review=false and constraint_gate_status=passed.")
    parser.add_argument("--skip-frontend-build", action="store_true")
    parser.add_argument("--pytest-timeout", type=int, default=300)
    parser.add_argument("--frontend-timeout", type=int, default=180)
    args = parser.parse_args(argv)
    report = build_report(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output_dir": report["output_dir"],
                "blocking_failures": report["blocking_failures"],
                "sample_status": ((report.get("checks") or {}).get("sample_acceptance") or {}).get("sample_status"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
