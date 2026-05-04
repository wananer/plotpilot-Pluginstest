import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOTS = [
    "application",
    "interfaces",
    "plugins",
    "scripts/evaluation",
    "tests",
]
GATE_FIELDS = (
    "constraint_gate_status",
    "constraint_status",
    "route_gate_status",
    "boundary_gate_status",
    "chapter_draft_status",
)


def test_gate_status_outputs_never_use_warning():
    offenders: list[str] = []
    field_pattern = "|".join(re.escape(field) for field in GATE_FIELDS)
    patterns = [
        re.compile(rf"({field_pattern})\s*[:=]\s*[\"']warning[\"']"),
        re.compile(rf"[\"']({field_pattern})[\"']\s*:\s*[\"']warning[\"']"),
    ]

    for root in SEARCH_ROOTS:
        for path in (PROJECT_ROOT / root).rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".vue", ".js"} or not path.is_file():
                continue
            rel = path.relative_to(PROJECT_ROOT)
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if any(pattern.search(line) for pattern in patterns):
                    offenders.append(f"{rel}:{line_number}:{line.strip()}")

    assert offenders == []
