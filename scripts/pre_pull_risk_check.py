#!/usr/bin/env python3
"""Pre-pull risk check for local PlotPilot worktrees.

This script is intentionally read-only. It classifies local git changes so a
developer can decide whether it is safe to fetch/rebase/merge remote updates.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


CODE_PREFIXES = (
    "application/",
    "interfaces/",
    "plugins/",
    "frontend/src/",
    "frontend/public/",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "scripts/start_daemon.py",
)
TEST_PREFIXES = ("tests/", "frontend/tests/")
DOC_PREFIXES = ("docs/",)
SCRIPT_PREFIXES = ("scripts/evaluation/", "scripts/prototypes/", "scripts/utils/", "scripts/pre_pull_risk_check.py")
GENERATED_PREFIXES = ("data/", ".omx/")
GENERATED_MARKERS = ("__pycache__/",)
GENERATED_SUFFIXES = (".pyc", ".pyo", ".db", ".db-shm", ".db-wal", ".sqlite", ".sqlite3", ".log")
HIGH_RISK_STATUSES = {"MM", "AM", "AD", "MD", "DM", "RM", "CM"}


@dataclass
class Entry:
    status: str
    path: str


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True)


def parse_status(raw: str) -> list[Entry]:
    entries: list[Entry] = []
    for line in raw.splitlines():
        if not line:
            continue
        status = line[:2]
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append(Entry(status=status, path=path))
    return entries


def category(path: str) -> str:
    if path.startswith(GENERATED_PREFIXES) or any(marker in path for marker in GENERATED_MARKERS) or path.endswith(GENERATED_SUFFIXES):
        return "generated"
    if path.startswith(TEST_PREFIXES):
        return "test"
    if path.startswith(DOC_PREFIXES):
        return "doc"
    if path.startswith(SCRIPT_PREFIXES):
        return "script"
    if path.startswith(CODE_PREFIXES):
        return "code"
    if path in {".gitignore", "pyproject.toml", "pytest.ini"}:
        return "config"
    return "other"


def group(entries: list[Entry]) -> dict[str, list[Entry]]:
    grouped: dict[str, list[Entry]] = {"high_risk": [], "code": [], "test": [], "doc": [], "script": [], "generated": [], "config": [], "other": []}
    for entry in entries:
        normalized = entry.status.replace(" ", "")
        if normalized in HIGH_RISK_STATUSES or (entry.status[0] != " " and entry.status[1] != " " and entry.status != "??"):
            grouped["high_risk"].append(entry)
        grouped[category(entry.path)].append(entry)
    return grouped


def print_section(title: str, entries: list[Entry]) -> None:
    if not entries:
        return
    print(f"\n## {title} ({len(entries)})")
    for entry in entries:
        print(f"{entry.status} {entry.path}")


def main() -> int:
    root = Path(run_git(["rev-parse", "--show-toplevel"]).strip())
    branch = run_git(["branch", "--show-current"]).strip() or "(detached)"
    entries = parse_status(run_git(["status", "--porcelain=v1"]))
    grouped = group(entries)

    print(f"PlotPilot pre-pull risk check")
    print(f"root: {root}")
    print(f"branch: {branch}")
    print(f"local changes: {len(entries)}")

    print_section("HIGH RISK staged+unstaged or mixed-state paths", grouped["high_risk"])
    print_section("Code paths that may affect runtime", grouped["code"])
    print_section("Tests", grouped["test"])
    print_section("Docs", grouped["doc"])
    print_section("Scripts/evaluation utilities", grouped["script"])
    print_section("Generated/local runtime artifacts", grouped["generated"])
    print_section("Config", grouped["config"])
    print_section("Other", grouped["other"])

    if grouped["high_risk"]:
        print("\nDecision: BLOCK git pull/rebase until high-risk mixed-state paths are committed, unstaged, or otherwise resolved.")
        return 2
    if grouped["code"]:
        print("\nDecision: CAUTION. Runtime code changes exist; create a safety branch or WIP commit before pulling.")
        return 1
    if grouped["generated"] and not any(entry for entry in entries if category(entry.path) != "generated"):
        print("\nDecision: OK after cleaning/ignoring generated artifacts.")
        return 0
    print("\nDecision: OK. No high-risk local changes detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
