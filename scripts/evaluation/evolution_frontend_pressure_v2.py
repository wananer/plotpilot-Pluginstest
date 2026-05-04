"""Frontend-driven Evolution A/B pressure test v2.

This runner is deliberately split into preparation, validation, and reporting.
It prepares an isolated data directory and identical PlotPilot-native seed data
for both arms, while chapter generation is still triggered from the real
workbench UI. The script never calls the old script-only chapter generator.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from application.ai.llm_audit import write_audit_inventory
from scripts.evaluation.evolution_pressure_test import EXPERIMENT_SPEC, _selected_chapter_outlines


ARTIFACT_ROOT = PROJECT_ROOT / ".omx" / "artifacts"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8005"
DEFAULT_FRONTEND_URL = "http://127.0.0.1:3010"
PLUGIN_NAME = "world_evolution_core"
HOME_UI_CREATION_METHOD = "plotpilot_home_ui"
BROWSER_USE_CREATION_METHOD = "browser_use_plotpilot_home_ui"
BROWSER_USE_BLOCKER = "browser_use_node_repl_unavailable"

MACRO_PROMPT_REQUIRED_TERMS = ("仙侠宗门悬疑群像", "照影山", "照影镜", "禁地灵脉")
THEME_TERMS = ("照影山", "照影镜", "禁地", "灵脉", "宗门", "戒律堂", "丹峰", "林照夜", "谢无咎", "沈青蘅")
CORE_CLUE_TERMS = ("照影镜", "血字", "账册", "灵石月例", "安神丹", "陆闻钟", "玄微真人", "禁地", "审心室", "镜阵")
REPETITIVE_PHRASES = (
    "没有说话",
    "没有回答",
    "沉默了几秒",
    "深吸一口气",
    "指节因为用力而泛白",
    "空气像是凝固了",
    "像是某种",
    "不是错觉",
)
ROUTE_MARKERS = ("回到", "再次", "重新", "又一次", "再一次", "仍在", "已经")
LOCATION_TERMS = ("账房", "丹峰", "戒律堂", "禁地", "审心室", "照影镜殿", "外门", "灵脉", "三峰", "山门")
EXPECTED_CHARACTER_NAMES = {"林照夜", "谢无咎", "沈青蘅", "玄微真人", "陆闻钟"}

ARM_CONTROL = "control_off"
ARM_EXPERIMENT = "experiment_on"
RUN_KINDS = ("calibration", "formal")
AUDITED_CHAPTER_GENERATION_PHASES = {"chapter_generation_stream", "chapter_generation_beat"}
AUDITED_CHAPTERLESS_PHASES = {"chapter_outline_suggestion", "evolution_agent_control_card"}
BOUNDARY_REVISION_PHASE = "hosted_write_boundary_revision"
BOUNDARY_REVISION_EVENTS = {
    "boundary_revision_start",
    "boundary_revision_applied",
    "boundary_revision_required",
    "boundary_revision_skipped",
}
BOUNDARY_REVISION_BASELINE_FAILED = 6
BOUNDARY_REVISION_BASELINE_TOTAL = 9
BOUNDARY_REVISION_TARGET_FAILED = 2
CHAPTERLESS_SUMMARY_MARKERS = (
    "为一幕（Act）生成简洁的摘要",
    "幕摘要",
    "请生成这一幕的摘要",
    "请生成这一卷的摘要",
    "请生成这一部的摘要",
    "请生成检查点摘要",
)
LEAKAGE_ACTIVE_ASSET_KEYS = {
    "agent_decisions",
    "capsules",
    "decisions",
    "events",
    "gene_candidates",
    "gene_versions",
    "reflections",
    "selections",
}


@dataclass(frozen=True)
class ArmPlan:
    run_kind: str
    arm: str
    novel_id: str
    chapter_count: int
    evolution_enabled: bool

    @property
    def workbench_url(self) -> str:
        return f"{DEFAULT_FRONTEND_URL}/book/{self.novel_id}/workbench"


def _now_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _hash_json(payload: Any) -> str:
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _run_repo_command(args: list[str], timeout: int = 20) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(PROJECT_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "command": args,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    return {
        "ok": proc.returncode == 0,
        "command": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }


def _git_snapshot() -> dict[str, Any]:
    head = _run_repo_command(["git", "rev-parse", "HEAD"])
    branch = _run_repo_command(["git", "branch", "--show-current"])
    status = _run_repo_command(["git", "status", "--short"])
    return {
        "head": head.get("stdout", "").strip(),
        "branch": branch.get("stdout", "").strip(),
        "dirty": bool(status.get("stdout", "").strip()),
        "status_short": status.get("stdout", ""),
    }


def build_arm_plan(run_id: str, *, calibration_chapters: int = 2, formal_chapters: int = 10) -> list[ArmPlan]:
    suffix = re.sub(r"[^0-9A-Za-z-]+", "-", run_id)[-18:].strip("-") or _now_slug()
    if calibration_chapters <= 0:
        return [
            ArmPlan("formal", ARM_EXPERIMENT, f"frontend-v2-experiment-on-{suffix}", formal_chapters, True),
        ]
    return [
        ArmPlan("calibration", ARM_CONTROL, f"frontend-v2-calib-control-off-{suffix}", calibration_chapters, False),
        ArmPlan("calibration", ARM_EXPERIMENT, f"frontend-v2-calib-experiment-on-{suffix}", calibration_chapters, True),
        ArmPlan("formal", ARM_CONTROL, f"frontend-v2-control-off-{suffix}", formal_chapters, False),
        ArmPlan("formal", ARM_EXPERIMENT, f"frontend-v2-experiment-on-{suffix}", formal_chapters, True),
    ]


def prepare_sandbox(run_dir: Path, *, source_data_dir: Path = PROJECT_ROOT / "data", overwrite: bool = False) -> dict[str, Any]:
    if run_dir.exists() and any(run_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    data_dir = run_dir / "data"
    if data_dir.exists() and overwrite:
        shutil.rmtree(data_dir)
    if source_data_dir.exists():
        shutil.copytree(source_data_dir, data_dir, dirs_exist_ok=True)
    else:
        data_dir.mkdir(parents=True, exist_ok=True)

    llm_calls_dir = run_dir / "llm_calls"
    llm_calls_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "exports").mkdir(parents=True, exist_ok=True)

    env = {
        "AITEXT_PROD_DATA_DIR": str(data_dir),
        "LLM_AUDIT_ENABLED": "true",
        "LLM_AUDIT_RUN_ID": run_dir.name,
        "LLM_AUDIT_OUTPUT_DIR": str(llm_calls_dir),
        "LOG_FILE": str(run_dir / "logs" / "aitext.log"),
    }
    env_lines = ["# Source this before starting the sandbox backend.", "export PYTHONPATH=\"$PWD${PYTHONPATH:+:$PYTHONPATH}\""]
    env_lines.extend(f"export {key}={json.dumps(value, ensure_ascii=False)}" for key, value in env.items())
    (run_dir / "env.sh").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": _utc_now(),
        "project_root": str(PROJECT_ROOT),
        "data_dir": str(data_dir),
        "llm_audit_output_dir": str(llm_calls_dir),
        "frontend_url": DEFAULT_FRONTEND_URL,
        "backend_url": DEFAULT_BACKEND_URL,
        "git": _git_snapshot(),
        "backend_start_command": (
            f"AITEXT_PROD_DATA_DIR={data_dir} "
            f"LLM_AUDIT_ENABLED=true LLM_AUDIT_RUN_ID={run_dir.name} "
            f"LLM_AUDIT_OUTPUT_DIR={llm_calls_dir} "
            "python -m uvicorn interfaces.main:app --host 127.0.0.1 --port 8005"
        ),
        "complete": False,
        "valid_experiment": False,
    }
    _write_json(run_dir / "run_manifest.json", manifest)
    return manifest


def start_backend(run_dir: Path, *, port: int = 8005) -> subprocess.Popen[str]:
    """Start a sandbox backend process; generation must still be triggered in the UI."""

    env = os.environ.copy()
    env.update(
        {
            "AITEXT_PROD_DATA_DIR": str(run_dir / "data"),
            "LLM_AUDIT_ENABLED": "true",
            "LLM_AUDIT_RUN_ID": run_dir.name,
            "LLM_AUDIT_OUTPUT_DIR": str(run_dir / "llm_calls"),
            "LOG_FILE": str(run_dir / "logs" / "aitext.log"),
            "PYTHONPATH": str(PROJECT_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
        }
    )
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    log = (run_dir / "logs" / "backend.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "interfaces.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    _write_json(run_dir / "backend_process.json", {"pid": proc.pid, "started_at": _utc_now(), "port": port})
    return proc


def start_frontend(run_dir: Path, *, port: int = 3010, backend_url: str = DEFAULT_BACKEND_URL) -> subprocess.Popen[str]:
    """Start the original PlotPilot frontend for UI-first pressure setup."""

    env = os.environ.copy()
    env.update({"VITE_API_BASE_URL": f"{backend_url.rstrip('/')}/api/v1"})
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    log = (run_dir / "logs" / "frontend.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT / "frontend"),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    _write_json(
        run_dir / "frontend_process.json",
        {
            "pid": proc.pid,
            "started_at": _utc_now(),
            "port": port,
            "frontend_url": f"http://127.0.0.1:{port}",
            "backend_url": backend_url,
        },
    )
    return proc


def wait_for_backend(base_url: str = DEFAULT_BACKEND_URL, *, timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            payload = http_json("GET", f"{base_url}/health", timeout=5)
            if payload.get("status") == "healthy":
                return True
        except Exception:
            time.sleep(1)
    return False


def wait_for_frontend(frontend_url: str = DEFAULT_FRONTEND_URL, *, timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            html = http_text("GET", frontend_url, timeout=5)
            if "<html" in html.lower() or "PlotPilot" in html or "墨枢" in html:
                return True
        except Exception:
            time.sleep(1)
    return False


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: int = 30) -> dict[str, Any]:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if not data:
                return {}
            return json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc


def http_text(method: str, url: str, *, timeout: int = 60) -> str:
    req = urllib.request.Request(url, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def set_evolution_enabled(base_url: str, enabled: bool) -> dict[str, Any]:
    return http_json("PUT", f"{base_url}/api/v1/plugins/{PLUGIN_NAME}/enabled", {"enabled": bool(enabled)})


def create_pressure_novel(base_url: str, plan: ArmPlan) -> dict[str, Any]:
    payload = {
        "novel_id": plan.novel_id,
        "title": f"{EXPERIMENT_SPEC['title']} · v2 · {plan.arm}",
        "author": "PlotPilot Pressure Harness",
        "target_chapters": plan.chapter_count,
        "premise": EXPERIMENT_SPEC["premise"],
        "genre": EXPERIMENT_SPEC["genre"],
        "world_preset": EXPERIMENT_SPEC["world_preset"],
        "length_tier": None,
        "target_words_per_chapter": 2500,
    }
    return http_json("POST", f"{base_url}/api/v1/novels/", payload)


def build_home_ui_creation_form(plan: ArmPlan) -> dict[str, Any]:
    return {
        "title": f"{EXPERIMENT_SPEC['title']} · v2 · {plan.arm}",
        "premise": EXPERIMENT_SPEC["premise"],
        "genre": "仙侠修真",
        "world_preset": "修仙风",
        "world_preset_label": "修仙风（宗门、境界、机缘）",
        "target_chapters": plan.chapter_count,
        "target_words_per_chapter": 2500,
        "use_advanced": True,
    }


def build_browser_use_creation_record(
    *,
    plan: ArmPlan,
    novel_id: str,
    novel_payload: dict[str, Any],
    screenshot_path: str = "",
    frontend_url: str = DEFAULT_FRONTEND_URL,
) -> dict[str, Any]:
    form = build_home_ui_creation_form(plan)
    actual_title = str(novel_payload.get("title") or "").strip()
    if actual_title:
        form["title"] = actual_title
    premise = str(novel_payload.get("premise") or "")
    premise_contains_core_theme = EXPERIMENT_SPEC["title"] in premise or "照影山" in premise
    validation = {
        "ok": (
            int(novel_payload.get("target_chapters") or 0) == plan.chapter_count
            and int(novel_payload.get("target_words_per_chapter") or 0) == int(form["target_words_per_chapter"])
            and premise_contains_core_theme
        ),
        "target_chapters": novel_payload.get("target_chapters"),
        "target_words_per_chapter": novel_payload.get("target_words_per_chapter"),
        "premise_contains_title": EXPERIMENT_SPEC["title"] in premise,
        "premise_contains_core_theme": premise_contains_core_theme,
    }
    return {
        "run_kind": plan.run_kind,
        "arm": plan.arm,
        "planned_novel_id": plan.novel_id,
        "novel_id": novel_id,
        "chapter_count": plan.chapter_count,
        "evolution_enabled": plan.evolution_enabled,
        "creation_method": BROWSER_USE_CREATION_METHOD,
        "ui_form": form,
        "browser_use": {
            "backend": "iab",
            "screenshot_path": screenshot_path,
            "workbench_url": f"{frontend_url.rstrip('/')}/book/{novel_id}/workbench",
        },
        "ui_validation": validation,
        "api_response": novel_payload,
        "workbench_url": f"{frontend_url.rstrip('/')}/book/{novel_id}/workbench",
    }


def record_browser_use_created_novel(
    run_dir: Path,
    *,
    novel_id: str,
    run_kind: str = "formal",
    arm: str = ARM_EXPERIMENT,
    chapter_count: int = 10,
    evolution_enabled: bool = True,
    screenshot_path: str = "",
    base_url: str = DEFAULT_BACKEND_URL,
    frontend_url: str = DEFAULT_FRONTEND_URL,
) -> dict[str, Any]:
    db_path = run_dir / "data" / "aitext.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Sandbox database does not exist: {db_path}")
    plan = ArmPlan(run_kind, arm, f"browser-use-{arm}-{run_dir.name}", chapter_count, evolution_enabled)
    set_evolution_enabled(base_url, evolution_enabled)
    novel_payload = http_json("GET", f"{base_url}/api/v1/novels/{novel_id}", timeout=30)
    record = build_browser_use_creation_record(
        plan=plan,
        novel_id=novel_id,
        novel_payload=novel_payload,
        screenshot_path=screenshot_path,
        frontend_url=frontend_url,
    )
    set_auto_approve(base_url, novel_id, False)
    seed = seed_native_context_in_app_db(db_path, novel_id, chapter_limit=chapter_count)
    seed.update(
        {
            "run_kind": run_kind,
            "arm": arm,
            "chapter_count": chapter_count,
            "evolution_enabled": evolution_enabled,
            "creation_method": BROWSER_USE_CREATION_METHOD,
        }
    )
    manifest = build_seed_manifest([seed])
    manifest["sandbox_foreign_key_check"] = check_sandbox_foreign_keys(db_path)
    manifest["created_novels"] = [record]
    manifest["creation_method"] = BROWSER_USE_CREATION_METHOD
    _write_json(run_dir / "seed_manifest.json", manifest)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update(
        {
            "novels": [record],
            "seed_manifest": str(run_dir / "seed_manifest.json"),
            "creation_method": BROWSER_USE_CREATION_METHOD,
            "frontend_url": frontend_url,
        }
    )
    run_manifest.pop("browser_use_blocker", None)
    if record["ui_validation"]["ok"] and manifest["sandbox_foreign_key_check"]["ok"]:
        run_manifest.pop("debug_only", None)
        run_manifest.pop("debug_only_reason", None)
    if not record["ui_validation"]["ok"]:
        run_manifest["debug_only"] = True
        run_manifest["debug_only_reason"] = "browser_use_ui_creation_validation_failed"
    if not manifest["sandbox_foreign_key_check"]["ok"]:
        run_manifest["debug_only"] = True
        run_manifest["debug_only_reason"] = "sandbox_foreign_key_violation"
    _write_json(run_dir / "run_manifest.json", run_manifest)
    return manifest


def record_browser_use_blocker(run_dir: Path, *, reason: str = BROWSER_USE_BLOCKER) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "creation_method": BROWSER_USE_CREATION_METHOD,
        "blocked": True,
        "blocker": reason,
        "recorded_at": _utc_now(),
    }
    _write_json(run_dir / "browser_use_blocker.json", payload)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update(
        {
            "creation_method": BROWSER_USE_CREATION_METHOD,
            "debug_only": True,
            "debug_only_reason": reason,
            "browser_use_blocker": str(run_dir / "browser_use_blocker.json"),
        }
    )
    _write_json(run_dir / "run_manifest.json", run_manifest)
    return payload


def create_novel_via_plotpilot_home_ui(
    frontend_url: str,
    form: dict[str, Any],
    *,
    screenshot_dir: Path,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Create a novel through the original PlotPilot Home UI.

    This intentionally fails if Playwright is unavailable; the pressure setup
    must not silently fall back to direct API creation when the goal is UI truth.
    """

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "create-via-ui requires Playwright for Python. Install it in the active environment "
            "and run `python -m playwright install chromium` before running UI-first pressure setup."
        ) from exc

    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / f"home-ui-create-{int(time.time())}.png"
    timeout_ms = timeout_seconds * 1000
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        try:
            page.goto(frontend_url, wait_until="networkidle", timeout=timeout_ms)
            page.get_by_placeholder(re.compile("用一段话写清主线")).fill(str(form["premise"]), timeout=timeout_ms)
            page.locator(".preset-row .n-select").nth(0).click(timeout=timeout_ms)
            page.get_by_text(str(form["genre"]), exact=True).click(timeout=timeout_ms)
            page.locator(".preset-row .n-select").nth(1).click(timeout=timeout_ms)
            page.get_by_text(str(form["world_preset_label"]), exact=True).click(timeout=timeout_ms)
            page.get_by_text(re.compile("高级")).click(timeout=timeout_ms)
            page.get_by_placeholder("留空则从梗概自动截取").fill(str(form["title"]), timeout=timeout_ms)
            number_inputs = page.locator(".advanced-settings .n-input-number input")
            number_inputs.nth(0).fill(str(form["target_chapters"]), timeout=timeout_ms)
            number_inputs.nth(1).fill(str(form["target_words_per_chapter"]), timeout=timeout_ms)
            with page.expect_response(
                lambda response: "/api/v1/novels" in response.url and response.request.method == "POST",
                timeout=timeout_ms,
            ) as response_info:
                page.get_by_role("button", name=re.compile("建档并进入工作台")).click(timeout=timeout_ms)
            response = response_info.value
            payload = response.json()
            page.screenshot(path=str(screenshot_path), full_page=True)
        except PlaywrightTimeoutError as exc:
            page.screenshot(path=str(screenshot_path), full_page=True)
            raise RuntimeError(f"Home UI novel creation timed out; screenshot: {screenshot_path}") from exc
        finally:
            browser.close()

    novel_id = str(payload.get("id") or payload.get("novel_id") or "")
    if not novel_id:
        raise RuntimeError(f"Home UI creation response did not include a novel id: {payload}")
    return {
        "novel_id": novel_id,
        "api_response": payload,
        "screenshot_path": str(screenshot_path),
        "final_url": f"{frontend_url.rstrip('/')}/book/{novel_id}/workbench",
    }


def set_auto_approve(base_url: str, novel_id: str, enabled: bool) -> dict[str, Any]:
    return http_json("PATCH", f"{base_url}/api/v1/novels/{novel_id}/auto-approve-mode", {"auto_approve_mode": bool(enabled)})


def create_seeded_novels_via_home_ui(
    run_dir: Path,
    plans: list[ArmPlan],
    *,
    base_url: str = DEFAULT_BACKEND_URL,
    frontend_url: str = DEFAULT_FRONTEND_URL,
    timeout_seconds: int = 120,
    ui_create_func: Any | None = None,
) -> dict[str, Any]:
    db_path = run_dir / "data" / "aitext.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Sandbox database does not exist: {db_path}")

    create_func = ui_create_func or create_novel_via_plotpilot_home_ui
    created: list[dict[str, Any]] = []
    seed_records: list[dict[str, Any]] = []
    screenshot_dir = run_dir / "screenshots"
    for plan in plans:
        set_evolution_enabled(base_url, plan.evolution_enabled)
        form = build_home_ui_creation_form(plan)
        ui_result = create_func(
            frontend_url,
            form,
            screenshot_dir=screenshot_dir,
            timeout_seconds=timeout_seconds,
        )
        novel_id = str(ui_result["novel_id"])
        novel_payload = http_json("GET", f"{base_url}/api/v1/novels/{novel_id}", timeout=30)
        created.append(
            {
                "run_kind": plan.run_kind,
                "arm": plan.arm,
                "planned_novel_id": plan.novel_id,
                "novel_id": novel_id,
                "chapter_count": plan.chapter_count,
                "evolution_enabled": plan.evolution_enabled,
                "creation_method": HOME_UI_CREATION_METHOD,
                "ui_form": form,
                "ui_result": ui_result,
                "api_response": novel_payload,
                "workbench_url": f"{frontend_url.rstrip('/')}/book/{novel_id}/workbench",
            }
        )
        set_auto_approve(base_url, novel_id, False)
        seed = seed_native_context_in_app_db(db_path, novel_id, chapter_limit=plan.chapter_count)
        seed.update(
            {
                "run_kind": plan.run_kind,
                "arm": plan.arm,
                "chapter_count": plan.chapter_count,
                "evolution_enabled": plan.evolution_enabled,
                "creation_method": HOME_UI_CREATION_METHOD,
            }
        )
        seed_records.append(seed)

    manifest = build_seed_manifest(seed_records)
    manifest["sandbox_foreign_key_check"] = check_sandbox_foreign_keys(db_path)
    manifest["created_novels"] = created
    manifest["creation_method"] = HOME_UI_CREATION_METHOD
    _write_json(run_dir / "seed_manifest.json", manifest)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update(
        {
            "novels": created,
            "seed_manifest": str(run_dir / "seed_manifest.json"),
            "creation_method": HOME_UI_CREATION_METHOD,
            "frontend_url": frontend_url,
        }
    )
    if not manifest["sandbox_foreign_key_check"]["ok"]:
        run_manifest["debug_only"] = True
        run_manifest["debug_only_reason"] = "sandbox_foreign_key_violation"
    _write_json(run_dir / "run_manifest.json", run_manifest)
    return manifest


def create_seeded_novels(run_dir: Path, plans: list[ArmPlan], *, base_url: str = DEFAULT_BACKEND_URL) -> dict[str, Any]:
    db_path = run_dir / "data" / "aitext.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Sandbox database does not exist: {db_path}")

    created: list[dict[str, Any]] = []
    seed_records: list[dict[str, Any]] = []
    for plan in plans:
        set_evolution_enabled(base_url, plan.evolution_enabled)
        created.append(
            {
                "run_kind": plan.run_kind,
                "arm": plan.arm,
                "novel_id": plan.novel_id,
                "chapter_count": plan.chapter_count,
                "evolution_enabled": plan.evolution_enabled,
                "api_response": create_pressure_novel(base_url, plan),
                "workbench_url": f"{DEFAULT_FRONTEND_URL}/book/{plan.novel_id}/workbench",
            }
        )
        set_auto_approve(base_url, plan.novel_id, False)
        seed = seed_native_context_in_app_db(db_path, plan.novel_id, chapter_limit=plan.chapter_count)
        seed.update(
            {
                "run_kind": plan.run_kind,
                "arm": plan.arm,
                "chapter_count": plan.chapter_count,
                "evolution_enabled": plan.evolution_enabled,
            }
        )
        seed_records.append(seed)

    manifest = build_seed_manifest(seed_records)
    manifest["sandbox_foreign_key_check"] = check_sandbox_foreign_keys(db_path)
    manifest["created_novels"] = created
    _write_json(run_dir / "seed_manifest.json", manifest)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update({"novels": created, "seed_manifest": str(run_dir / "seed_manifest.json")})
    if not manifest["sandbox_foreign_key_check"]["ok"]:
        run_manifest["debug_only"] = True
        run_manifest["debug_only_reason"] = "sandbox_foreign_key_violation"
    _write_json(run_dir / "run_manifest.json", run_manifest)
    return manifest


def seed_native_context_in_app_db(db_path: Path, novel_id: str, *, chapter_limit: int = 10) -> dict[str, Any]:
    """Seed identical native PlotPilot context for one pressure-test novel."""

    now = _utc_now()
    bundle = build_native_seed_bundle(chapter_limit=chapter_limit)
    inserted: dict[str, int] = {}
    missing_tables: list[str] = []
    field_missing_sources: dict[str, list[str]] = {}

    with sqlite3.connect(str(db_path)) as conn:
        for table, rows in _rows_for_db_seed(novel_id, bundle, now).items():
            for row in rows:
                result = _insert_seed_row(conn, table, row)
                if result["status"] == "missing_table":
                    if table not in missing_tables:
                        missing_tables.append(table)
                    continue
                inserted[table] = inserted.get(table, 0) + int(result["inserted"])
                missing = result.get("missing_fields") or []
                if missing:
                    field_missing_sources.setdefault(table, [])
                    for field in missing:
                        if field not in field_missing_sources[table]:
                            field_missing_sources[table].append(field)
        conn.commit()

    seed_hash = _hash_json(bundle)
    return {
        "schema_version": 1,
        "novel_id": novel_id,
        "seed_hash": seed_hash,
        "premise_hash": _hash_text(EXPERIMENT_SPEC["premise"]),
        "chapter_outline_hash": _hash_json(_selected_chapter_outlines(chapter_limit)),
        "counts": inserted,
        "missing_tables": sorted(missing_tables),
        "field_missing_sources": field_missing_sources,
        "secret_copied_to_artifacts": False,
    }


def build_native_seed_bundle(*, chapter_limit: int = 10) -> dict[str, Any]:
    outlines = _selected_chapter_outlines(chapter_limit)
    return {
        "title": EXPERIMENT_SPEC["title"],
        "genre": EXPERIMENT_SPEC["genre"],
        "world_preset": EXPERIMENT_SPEC["world_preset"],
        "style_hint": EXPERIMENT_SPEC["style_hint"],
        "premise": EXPERIMENT_SPEC["premise"],
        "characters": EXPERIMENT_SPEC["characters"],
        "fixed_rules": EXPERIMENT_SPEC["fixed_rules"],
        "chapter_outlines": outlines,
        "locations": [
            "外门账房：林照夜整理月例账册的地方，第一章必须发现失踪弟子的灵石仍被领取。",
            "照影镜殿：宗门以照影镜审心问罪之处，镜面血字每章最多揭示一条有效线索。",
            "丹峰药庐：沈青蘅追查安神丹药性异常和换方线索的核心地点。",
            "禁地审心室：第八章后才能进入的废弃镜阵空间，困住失踪弟子的残影。",
        ],
        "seed_policy": {
            "control_and_experiment_identical": True,
            "theme_terms": list(THEME_TERMS),
            "topic_alignment": "positive_theme_coverage_only",
        },
    }


def _rows_for_db_seed(novel_id: str, bundle: dict[str, Any], now: str) -> dict[str, list[dict[str, Any]]]:
    knowledge_id = f"v2-knowledge-{novel_id}"
    rows: dict[str, list[dict[str, Any]]] = {
        "bibles": [
            {
                "id": f"bible-{novel_id}",
                "novel_id": novel_id,
                "schema_version": 1,
                "extensions": "{}",
                "created_at": now,
                "updated_at": now,
            }
        ],
        "knowledge": [
            {
                "id": knowledge_id,
                "novel_id": novel_id,
                "version": 1,
                "premise_lock": bundle["premise"],
                "created_at": now,
                "updated_at": now,
            }
        ],
        "bible_characters": [
            {
                "id": f"v2-char-{index}-{novel_id}",
                "novel_id": novel_id,
                "name": item.split("：", 1)[0],
                "description": item,
                "mental_state": "PRESSURE_LOCKED",
                "mental_state_reason": "v2压力测试固定人物边界",
                "verbal_tic": "先看账册和阵痕" if "林照夜" in item else ("戒律堂不信口供" if "谢无咎" in item else "药性不会骗人"),
                "idle_behavior": "核对灵石账册" if "林照夜" in item else ("检查戒律堂令牌" if "谢无咎" in item else "分辨药渣气味"),
                "created_at": now,
                "updated_at": now,
            }
            for index, item in enumerate(bundle["characters"], start=1)
        ],
        "bible_locations": [
            {
                "id": f"v2-loc-{index}-{novel_id}",
                "novel_id": novel_id,
                "name": item.split("：", 1)[0],
                "description": item,
                "location_type": "pressure_seed",
                "created_at": now,
                "updated_at": now,
            }
            for index, item in enumerate(bundle["locations"], start=1)
        ],
        "bible_world_settings": [
            {
                "id": f"v2-world-{index}-{novel_id}",
                "novel_id": novel_id,
                "name": f"核心规则{index}",
                "description": rule,
                "setting_type": "rule",
                "created_at": now,
                "updated_at": now,
            }
            for index, rule in enumerate(bundle["fixed_rules"], start=1)
        ],
        "bible_timeline_notes": [
            {
                "id": f"v2-timeline-note-1-{novel_id}",
                "novel_id": novel_id,
                "event": "陆闻钟失踪旧案",
                "time_point": "十年前",
                "description": "官方记录称陆闻钟追查禁地后失踪，但失踪前留下过戒律堂暗记。",
                "sort_order": 1,
            },
            {
                "id": f"v2-timeline-note-2-{novel_id}",
                "novel_id": novel_id,
                "event": "林照夜发现月例异常",
                "time_point": "第1章",
                "description": "林照夜在外门账册中发现失踪弟子月例仍被领取，不得切换成与本实验无关的通用开局。",
                "sort_order": 2,
            },
        ],
        "chapter_summaries": [
            {
                "id": f"v2-story-knowledge-0-{novel_id}",
                "knowledge_id": knowledge_id,
                "chapter_number": 0,
                "summary": "压力测试预置：仙侠宗门悬疑群像，林照夜从月例账册异常追查照影山失踪案。",
                "key_events": "照影镜尚未揭示真相；林照夜只知道账册异常；谢无咎和沈青蘅尚未互信。",
                "open_threads": "失踪弟子去向；陆闻钟是否还活着；照影镜血字来源；安神丹为何药性异常。",
                "consistency_note": "不得漂移到与本实验无关的通用开局、爽文升级体系或跨题材模板。",
                "beat_sections": json.dumps(bundle["chapter_outlines"], ensure_ascii=False),
                "micro_beats": json.dumps(["账册月例", "照影镜血字", "丹峰药渣", "禁地阵痕"], ensure_ascii=False),
                "sync_status": "seeded",
            }
        ],
        "triples": [
            _triple(novel_id, "照影镜", "揭示规则", "每章最多一条有效线索", "照影镜不能一次性解释全部真相。", now),
            _triple(novel_id, "陆闻钟", "信息边界", "前7章不能现身", "陆闻钟只能通过线索逐步出现，前7章不能现身。", now),
            _triple(novel_id, "谢无咎", "信任边界", "前5章不能完全信任林照夜", "谢无咎前5章不能完全信任林照夜。", now),
            _triple(novel_id, "题材", "禁止漂移", "跨题材模板", "本实验主题固定为仙侠宗门悬疑群像。", now),
        ],
        "storylines": [
            {
                "id": f"v2-storyline-main-{novel_id}",
                "novel_id": novel_id,
                "storyline_type": "main_plot",
                "status": "active",
                "estimated_chapter_start": 1,
                "estimated_chapter_end": len(bundle["chapter_outlines"]),
                "current_milestone_index": 0,
                "extensions": "{}",
                "name": "照影山失踪案与灵脉污染",
                "description": "三人围绕账册、照影镜、安神丹和禁地灵脉逐章推进旧案真相，保持仙侠宗门悬疑群像。",
                "last_active_chapter": 0,
                "progress_summary": "开局必须建立账册异常、照影镜血字和三人互不信任关系。",
                "created_at": now,
                "updated_at": now,
            }
        ],
        "storyline_milestones": [
            {
                "id": f"v2-milestone-{index}-{novel_id}",
                "storyline_id": f"v2-storyline-main-{novel_id}",
                "milestone_order": index,
                "title": f"第{index}章章纲锁",
                "description": outline,
                "target_chapter_start": index,
                "target_chapter_end": index,
                "prerequisite_list": "[]",
                "milestone_triggers": "[]",
            }
            for index, outline in enumerate(bundle["chapter_outlines"], start=1)
        ],
        "timeline_registries": [
            {
                "novel_id": novel_id,
                "data": json.dumps(
                    {
                        "id": f"timeline-{novel_id}",
                        "novel_id": novel_id,
                        "events": [
                            {
                                "id": "v2-tl-1",
                                "chapter_number": 1,
                                "event": "陆闻钟追查禁地后失踪",
                                "timestamp": "十年前",
                                "timestamp_type": "relative",
                            },
                            {
                                "id": "v2-tl-2",
                                "chapter_number": 1,
                                "event": "林照夜发现月例账册异常",
                                "timestamp": "第1章前",
                                "timestamp_type": "relative",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                "updated_at": now,
            }
        ],
        "novel_foreshadow_registry": [
            {
                "novel_id": novel_id,
                "payload": json.dumps(
                    {
                        "id": f"fr-{novel_id}",
                        "novel_id": novel_id,
                        "foreshadowings": [
                            {
                                "id": "v2-fs-ledger-stipend",
                                "description": "失踪弟子的灵石月例仍被领取，指向宗门内部有人持续遮掩。",
                                "importance": 3,
                                "status": "planted",
                                "planted_in_chapter": 1,
                                "suggested_resolve_chapter": 4,
                                "resolved_in_chapter": None,
                            },
                            {
                                "id": "v2-fs-mirror-blood",
                                "description": "照影镜血字只给出局部线索，镜面异常与禁地灵脉有关。",
                                "importance": 2,
                                "status": "planted",
                                "planted_in_chapter": 2,
                                "suggested_resolve_chapter": 5,
                                "resolved_in_chapter": None,
                            },
                        ],
                        "subtext_entries": [],
                    },
                    ensure_ascii=False,
                ),
                "updated_at": now,
            }
        ],
        "narrative_events": [
            {
                "event_id": f"v2-dialogue-seed-{novel_id}",
                "novel_id": novel_id,
                "chapter_number": 0,
                "event_summary": "林照夜说先看账册，谢无咎强调戒律堂不信口供，沈青蘅指出药性不会骗人。",
                "mutations": "[]",
                "tags": json.dumps(["林照夜：先看账册。", "谢无咎：戒律堂不信口供。", "沈青蘅：药性不会骗人。"], ensure_ascii=False),
                "timestamp_ts": now,
            }
        ],
        "memory_engine_states": [
            {
                "novel_id": novel_id,
                "state_json": json.dumps(
                    {
                        "fact_locks": [
                            "林照夜前6章不能知道陆闻钟还活着",
                            "谢无咎前5章不能完全信任林照夜",
                            "照影镜每章最多揭示一条有效线索",
                            "炼气修士不能正面击败筑基修士",
                            "不得漂移到与本实验无关的通用模板",
                        ],
                        "chapter_outline_locks": bundle["chapter_outlines"],
                    },
                    ensure_ascii=False,
                ),
                "last_updated_chapter": 0,
            }
        ],
    }
    return rows


def _triple(novel_id: str, subject: str, predicate: str, obj: str, description: str, now: str) -> dict[str, Any]:
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", f"{subject}-{predicate}")[:40]
    return {
        "id": f"v2-triple-{safe}-{novel_id}",
        "novel_id": novel_id,
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "chapter_number": None,
        "note": "",
        "entity_type": "fact",
        "importance": "high",
        "location_type": "",
        "description": description,
        "first_appearance": 0,
        "confidence": 0.98,
        "source_type": "frontend_pressure_v2_seed",
        "subject_entity_id": "",
        "object_entity_id": "",
        "created_at": now,
        "updated_at": now,
    }


def check_sandbox_foreign_keys(db_path: Path) -> dict[str, Any]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    violations = [
        {
            "table": str(row[0]),
            "rowid": row[1],
            "parent": str(row[2]),
            "fk_index": row[3],
        }
        for row in rows
    ]
    return {
        "schema_version": 1,
        "ok": not violations,
        "violation_count": len(violations),
        "violations": violations[:50],
    }


def _insert_seed_row(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> dict[str, Any]:
    columns = [item[1] for item in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if not columns:
        return {"status": "missing_table", "inserted": 0, "missing_fields": list(row)}
    usable = {key: value for key, value in row.items() if key in columns}
    if not usable:
        return {"status": "no_usable_fields", "inserted": 0, "missing_fields": list(row)}
    pk_columns = [item[1] for item in conn.execute(f"PRAGMA table_info({table})").fetchall() if int(item[5] or 0) > 0]
    delete_columns = [col for col in pk_columns if col in usable]
    if not delete_columns and "novel_id" in usable and table in {"timeline_registries", "novel_foreshadow_registry", "memory_engine_states"}:
        delete_columns = ["novel_id"]
    if delete_columns:
        where = " AND ".join(f"{col} = ?" for col in delete_columns)
        conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(usable[col] for col in delete_columns))
    names = list(usable)
    placeholders = ", ".join("?" for _ in names)
    try:
        conn.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
            tuple(usable[name] for name in names),
        )
    except sqlite3.IntegrityError:
        if "novel_id" not in usable or table == "novels":
            raise
        conn.execute(f"DELETE FROM {table} WHERE novel_id = ?", (usable["novel_id"],))
        conn.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
            tuple(usable[name] for name in names),
        )
    return {
        "status": "inserted",
        "inserted": 1,
        "missing_fields": [key for key in row if key not in columns],
    }


def build_seed_manifest(seed_records: list[dict[str, Any]]) -> dict[str, Any]:
    seed_hashes = sorted({record.get("seed_hash") for record in seed_records})
    premise_hashes = sorted({record.get("premise_hash") for record in seed_records})
    outline_hashes = sorted({record.get("chapter_outline_hash") for record in seed_records})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in seed_records:
        key = str(record.get("run_kind") or f"chapter_count:{record.get('chapter_count') or 'unknown'}")
        grouped.setdefault(key, []).append(record)
    group_gates: dict[str, dict[str, Any]] = {}
    for key, records in grouped.items():
        group_seed_hashes = sorted({record.get("seed_hash") for record in records})
        group_premise_hashes = sorted({record.get("premise_hash") for record in records})
        group_outline_hashes = sorted({record.get("chapter_outline_hash") for record in records})
        group_gates[key] = {
            "ok": len(group_seed_hashes) == 1 and len(group_premise_hashes) == 1 and len(group_outline_hashes) == 1,
            "seed_hashes": group_seed_hashes,
            "premise_hashes": group_premise_hashes,
            "chapter_outline_hashes": group_outline_hashes,
            "novel_ids": sorted(str(record.get("novel_id") or "") for record in records),
        }
    return {
        "schema_version": 1,
        "created_at": _utc_now(),
        "seed_records": seed_records,
        "seed_hash": seed_hashes[0] if len(seed_hashes) == 1 else "",
        "premise_hash": premise_hashes[0] if len(premise_hashes) == 1 else "",
        "chapter_outline_hash": outline_hashes[0] if len(outline_hashes) == 1 else "",
        "base_input_gate": {
            "ok": all(gate["ok"] for gate in group_gates.values()) if group_gates else False,
            "seed_hashes": seed_hashes,
            "premise_hashes": premise_hashes,
            "chapter_outline_hashes": outline_hashes,
            "groups": group_gates,
        },
        "secret_copied_to_artifacts": False,
    }


def check_native_sync_health(run_dir: Path) -> dict[str, Any]:
    log_path = run_dir / "logs" / "aitext.log"
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    error_patterns = [
        "FOREIGN KEY constraint failed",
        "StateUpdater 失败: 'timestamp_type'",
    ]
    log_hits = [
        {"pattern": pattern, "count": log_text.count(pattern)}
        for pattern in error_patterns
        if pattern in log_text
    ]
    db_path = run_dir / "data" / "aitext.db"
    if db_path.exists():
        fk_check = check_sandbox_foreign_keys(db_path)
    else:
        fk_check = {
            "schema_version": 1,
            "ok": False,
            "violation_count": 0,
            "violations": [],
            "missing_database": str(db_path),
        }
    ok = not log_hits and bool(fk_check.get("ok"))
    return {
        "schema_version": 1,
        "ok": ok,
        "native_sync_ok": ok,
        "invalid_reasons": ([] if not log_hits else ["native_sync_log_errors"]) + ([] if fk_check.get("ok") else ["sandbox_foreign_key_violation"]),
        "log_path": str(log_path),
        "log_error_hits": log_hits,
        "sandbox_foreign_key_check": fk_check,
    }


def load_audit_records(audit_dir: Path) -> list[dict[str, Any]]:
    calls_path = audit_dir / "calls.jsonl"
    records: list[dict[str, Any]] = []
    if not calls_path.exists():
        return records
    for line in calls_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_hosted_write_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates = [
        run_dir / "hosted_write_events.jsonl",
        run_dir / "frontend_events" / "hosted_write_events.jsonl",
        run_dir / "browser" / "hosted_write_events.jsonl",
    ]
    records: list[dict[str, Any]] = []
    for path in candidates:
        for item in _read_jsonl(path):
            item.setdefault("source_path", str(path))
            records.append(item)
    return records


def _read_record_text(record: dict[str, Any], kind: str) -> str:
    paths = record.get("paths") if isinstance(record.get("paths"), dict) else {}
    raw_path = paths.get(kind)
    if not raw_path:
        return ""
    path = Path(raw_path)
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if kind == "prompt":
        try:
            payload = json.loads(text)
            return json.dumps(payload.get("prompt") or payload, ensure_ascii=False)
        except json.JSONDecodeError:
            return text
    return text


def _is_chapterless_summary_record(record: dict[str, Any]) -> bool:
    """Identify legacy audit records for act/volume summaries without chapter ids."""

    if record.get("chapter_number"):
        return False
    prompt_text = _read_record_text(record, "prompt")
    return any(marker in prompt_text for marker in CHAPTERLESS_SUMMARY_MARKERS)


def evaluate_macro_planning_gate(
    records: list[dict[str, Any]],
    *,
    novel_id: str,
    premise: str = EXPERIMENT_SPEC["premise"],
) -> dict[str, Any]:
    candidates = [
        record
        for record in records
        if record.get("novel_id") == novel_id and record.get("phase") == "chapter_outline_suggestion"
    ]
    prompt_text = "\n".join(_read_record_text(record, "prompt") for record in candidates)
    output_text = "\n".join(_read_record_text(record, "output") for record in candidates)
    premise_hit = premise in prompt_text or premise[:80] in prompt_text
    prompt_hits = {term: term in prompt_text for term in MACRO_PROMPT_REQUIRED_TERMS}
    output_theme_hits = {term: term in output_text for term in THEME_TERMS}
    invalid_reasons: list[str] = []
    if not candidates:
        invalid_reasons.append("macro_planning_call_missing")
    if not premise_hit:
        invalid_reasons.append("macro_prompt_missing_premise")
    missing_prompt_terms = [term for term, ok in prompt_hits.items() if not ok]
    if missing_prompt_terms:
        invalid_reasons.append("macro_prompt_missing_required_terms")
    if sum(1 for ok in output_theme_hits.values() if ok) < 3:
        invalid_reasons.append("macro_output_theme_hits_below_threshold")
    return {
        "schema_version": 1,
        "novel_id": novel_id,
        "ok": not invalid_reasons,
        "invalid_reasons": invalid_reasons,
        "call_count": len(candidates),
        "premise_hash": _hash_text(premise),
        "premise_received": premise_hit,
        "prompt_required_hits": prompt_hits,
        "output_theme_hits": output_theme_hits,
        "topic_alignment": "ok" if not invalid_reasons else "needs_review",
        "prompt_chars": len(prompt_text),
        "output_chars": len(output_text),
    }


def evaluate_chapter_topic_alignment(chapter_text: str, *, min_theme_hits: int = 3) -> dict[str, Any]:
    theme_hits = {term: chapter_text.count(term) for term in THEME_TERMS if term in chapter_text}
    low_theme = len(theme_hits) < min_theme_hits
    return {
        "ok": not low_theme,
        "topic_alignment": "low_theme_coverage" if low_theme else "ok",
        "theme_hit_count": len(theme_hits),
        "theme_hits": theme_hits,
        "low_theme": low_theme,
    }


def evaluate_chapter_topic_alignment_series(chapters: list[dict[str, Any]], *, min_theme_hits: int = 3) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    consecutive_low = 0
    should_stop = False
    invalid_reasons: list[str] = []
    for chapter in chapters:
        result = evaluate_chapter_topic_alignment(str(chapter.get("content") or ""), min_theme_hits=min_theme_hits)
        result["chapter_number"] = int(chapter.get("chapter_number") or len(results) + 1)
        results.append(result)
        consecutive_low = consecutive_low + 1 if result["low_theme"] else 0
        if consecutive_low >= 2:
            should_stop = True
            invalid_reasons.append("chapter_theme_hits_low_for_two_consecutive_chapters")
    return {"ok": not should_stop, "should_stop": should_stop, "invalid_reasons": sorted(set(invalid_reasons)), "chapters": results}


def chapters_for_topic_alignment_gate(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return generated chapters and skip empty draft placeholders created by planning."""

    normalized: list[dict[str, Any]] = []
    for index, chapter in enumerate(chapters, start=1):
        content = str(chapter.get("content") or "")
        status = str(chapter.get("status") or "").lower()
        if not content.strip() and status not in {"completed", "published"}:
            continue
        normalized.append(
            {
                "chapter_number": chapter.get("number") or chapter.get("chapter_number") or index,
                "content": content,
                "status": status,
            }
        )
    return normalized


def check_audit_completeness(
    audit_dir: Path,
    *,
    expected_chapters: dict[str, int] | None = None,
    expected_novels: dict[str, int] | None = None,
) -> dict[str, Any]:
    records = load_audit_records(audit_dir)
    missing_files: list[dict[str, Any]] = []
    unexpected_unknown_chapter_calls: list[dict[str, Any]] = []
    allowed_chapterless_summary_calls: list[dict[str, Any]] = []
    chapters_with_generation: dict[str, set[int]] = {}
    chapters_by_novel: dict[str, set[int]] = {}
    for record in records:
        paths = record.get("paths") if isinstance(record.get("paths"), dict) else {}
        for kind in ("prompt", "output", "usage"):
            path = paths.get(kind)
            if not path or not Path(path).exists():
                missing_files.append({"call_id": record.get("call_id"), "kind": kind, "path": path or ""})
        if record.get("stream"):
            chunks = paths.get("chunks")
            if not chunks or not Path(chunks).exists():
                missing_files.append({"call_id": record.get("call_id"), "kind": "chunks", "path": chunks or ""})
        if record.get("phase") in AUDITED_CHAPTER_GENERATION_PHASES and record.get("chapter_number"):
            chapters_with_generation.setdefault(str(record.get("arm") or "unknown"), set()).add(int(record["chapter_number"]))
            if record.get("novel_id"):
                chapters_by_novel.setdefault(str(record.get("novel_id")), set()).add(int(record["chapter_number"]))
        if not record.get("chapter_number") and record.get("phase") not in AUDITED_CHAPTERLESS_PHASES:
            if _is_chapterless_summary_record(record):
                allowed_chapterless_summary_calls.append(
                    {
                        "call_id": record.get("call_id"),
                        "phase": record.get("phase"),
                        "novel_id": record.get("novel_id"),
                        "reason": "chapterless_act_volume_summary",
                    }
                )
                continue
            unexpected_unknown_chapter_calls.append(
                {
                    "call_id": record.get("call_id"),
                    "phase": record.get("phase"),
                    "novel_id": record.get("novel_id"),
                }
            )
    missing_chapters: list[dict[str, Any]] = []
    for arm, count in (expected_chapters or {}).items():
        seen = chapters_with_generation.get(arm, set())
        for chapter_number in range(1, int(count) + 1):
            if chapter_number not in seen:
                missing_chapters.append({"arm": arm, "chapter_number": chapter_number})
    for novel_id, count in (expected_novels or {}).items():
        seen = chapters_by_novel.get(novel_id, set())
        for chapter_number in range(1, int(count) + 1):
            if chapter_number not in seen:
                missing_chapters.append({"novel_id": novel_id, "chapter_number": chapter_number})
    return {
        "schema_version": 1,
        "ok": not missing_files and not missing_chapters and not unexpected_unknown_chapter_calls,
        "total_calls": len(records),
        "missing_files": missing_files,
        "missing_chapters": missing_chapters,
        "unexpected_unknown_chapter_calls": unexpected_unknown_chapter_calls,
        "allowed_chapterless_summary_calls": allowed_chapterless_summary_calls,
        "chapters_with_generation": {arm: sorted(values) for arm, values in chapters_with_generation.items()},
        "chapters_by_novel": {novel_id: sorted(values) for novel_id, values in chapters_by_novel.items()},
    }


def build_base_input_gate(seed_manifest: dict[str, Any], plans: list[ArmPlan]) -> dict[str, Any]:
    gate = seed_manifest.get("base_input_gate") if isinstance(seed_manifest.get("base_input_gate"), dict) else {}
    expected = {plan.novel_id for plan in plans}
    actual = {record.get("novel_id") for record in seed_manifest.get("seed_records", []) if isinstance(record, dict)}
    ok = bool(gate.get("ok")) and expected.issubset(actual)
    invalid_reasons: list[str] = []
    if not gate.get("ok"):
        invalid_reasons.append("seed_hashes_not_identical")
    if not expected.issubset(actual):
        invalid_reasons.append("missing_seeded_novels")
    return {
        "schema_version": 1,
        "ok": ok,
        "invalid_reasons": invalid_reasons,
        "expected_novels": sorted(expected),
        "seeded_novels": sorted(str(item) for item in actual if item),
        "seed_hash": seed_manifest.get("seed_hash"),
        "premise_hash": seed_manifest.get("premise_hash"),
        "chapter_outline_hash": seed_manifest.get("chapter_outline_hash"),
    }


def build_leakage_gate(
    *,
    control_agent_status: dict[str, Any],
    experiment_agent_status: dict[str, Any],
    control_diagnostics: dict[str, Any],
    experiment_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    control_counts = _agent_counts(control_agent_status)
    experiment_counts = _agent_counts(experiment_agent_status)
    control_active_counts = _active_leakage_counts(control_counts)
    experiment_active_counts = _active_leakage_counts(experiment_counts)
    control_context = _context_selection_count(control_agent_status, control_diagnostics)
    experiment_context = _context_selection_count(experiment_agent_status, experiment_diagnostics)
    experiment_agent_api = _agent_api_call_count(experiment_agent_status)
    degraded = _agent_degraded_reason(experiment_agent_status, experiment_diagnostics)
    checks = [
        {
            "id": "control_has_no_evolution_assets",
            "ok": not any(control_active_counts.values()) and control_context == 0,
            "evidence": {
                "asset_counts": control_counts,
                "active_asset_counts": control_active_counts,
                "context_selection_count": control_context,
            },
        },
        {
            "id": "experiment_has_evolution_participation",
            "ok": experiment_context > 0 or any(experiment_active_counts.values()) or experiment_agent_api > 0 or bool(degraded),
            "evidence": {
                "asset_counts": experiment_counts,
                "active_asset_counts": experiment_active_counts,
                "context_selection_count": experiment_context,
                "agent_api_call_count": experiment_agent_api,
                "degraded_reason": degraded,
            },
        },
        {
            "id": "api2_control_card_chars_zero",
            "ok": _api2_chars(control_agent_status, control_diagnostics) == 0 and _api2_chars(experiment_agent_status, experiment_diagnostics) == 0,
            "evidence": {
                "control_api2_control_card_chars": _api2_chars(control_agent_status, control_diagnostics),
                "experiment_api2_control_card_chars": _api2_chars(experiment_agent_status, experiment_diagnostics),
            },
        },
    ]
    return {
        "schema_version": 1,
        "ok": all(item["ok"] for item in checks),
        "invalid_reasons": [item["id"] for item in checks if not item["ok"]],
        "checks": checks,
    }


def _agent_counts(status: dict[str, Any]) -> dict[str, int]:
    counts = status.get("asset_counts") if isinstance(status.get("asset_counts"), dict) else {}
    if not counts:
        orchestration = status.get("agent_orchestration") if isinstance(status.get("agent_orchestration"), dict) else {}
        counts = orchestration.get("decision_counts") if isinstance(orchestration.get("decision_counts"), dict) else {}
    return {str(key): int(value or 0) for key, value in counts.items()}


def _active_leakage_counts(counts: dict[str, int]) -> dict[str, int]:
    return {key: value for key, value in counts.items() if key in LEAKAGE_ACTIVE_ASSET_KEYS}


def _context_selection_count(status: dict[str, Any], diagnostics: dict[str, Any]) -> int:
    usage = status.get("plotpilot_context_usage") if isinstance(status.get("plotpilot_context_usage"), dict) else {}
    diag_budget = diagnostics.get("context_budget_summary") if isinstance(diagnostics.get("context_budget_summary"), dict) else {}
    return int(usage.get("selection_count") or usage.get("block_count") or diag_budget.get("evolution_block_count") or 0)


def _agent_api_call_count(status: dict[str, Any]) -> int:
    usage = status.get("agent_api_usage") if isinstance(status.get("agent_api_usage"), dict) else {}
    aggregate = usage.get("aggregate") if isinstance(usage.get("aggregate"), dict) else usage
    return int(aggregate.get("call_count") or 0)


def _api2_chars(status: dict[str, Any], diagnostics: dict[str, Any]) -> int:
    for source in (status, diagnostics):
        summary = source.get("context_budget_summary") if isinstance(source.get("context_budget_summary"), dict) else {}
        if summary.get("api2_control_card_chars") is not None:
            return int(summary.get("api2_control_card_chars") or 0)
    return 0


def _agent_degraded_reason(status: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    for source in (status, diagnostics):
        for key in ("degraded_agent_tools", "degraded_sources"):
            values = source.get(key)
            if values:
                return json.dumps(values, ensure_ascii=False)[:240]
    return ""


def fetch_evolution_snapshots(base_url: str, novel_id: str) -> dict[str, Any]:
    return {
        "status": http_json("GET", f"{base_url}/api/v1/plugins/evolution-world/status"),
        "agent_status": http_json("GET", f"{base_url}/api/v1/plugins/evolution-world/novels/{novel_id}/agent/status"),
        "diagnostics": http_json("GET", f"{base_url}/api/v1/plugins/evolution-world/novels/{novel_id}/diagnostics"),
    }


def select_arm_for_frontend(run_dir: Path, novel_id: str, *, base_url: str = DEFAULT_BACKEND_URL) -> dict[str, Any]:
    plans = _plans_from_manifest(run_dir)
    plan = next((item for item in plans if item.novel_id == novel_id), None)
    if plan is None:
        raise ValueError(f"Novel is not part of this pressure run: {novel_id}")
    plugin_toggle = set_evolution_enabled(base_url, plan.evolution_enabled)
    auto_approve = set_auto_approve(base_url, novel_id, False)
    snapshot = {
        "schema_version": 1,
        "captured_at": _utc_now(),
        "run_kind": plan.run_kind,
        "arm": plan.arm,
        "novel_id": plan.novel_id,
        "chapter_count": plan.chapter_count,
        "evolution_enabled": plan.evolution_enabled,
        "plugin_toggle": plugin_toggle,
        "auto_approve": auto_approve,
        "plugins": _safe_http_json("GET", f"{base_url}/api/v1/plugins"),
        "plugin_status": _safe_http_json("GET", f"{base_url}/api/v1/plugins/evolution-world/status"),
        "agent_status": _safe_http_json("GET", f"{base_url}/api/v1/plugins/evolution-world/novels/{novel_id}/agent/status"),
        "diagnostics": _safe_http_json("GET", f"{base_url}/api/v1/plugins/evolution-world/novels/{novel_id}/diagnostics"),
        "workbench_url": f"{DEFAULT_FRONTEND_URL}/book/{novel_id}/workbench",
    }
    path = run_dir / "snapshots" / f"{plan.run_kind}_{plan.arm}_{novel_id}_preflight.json"
    _write_json(path, snapshot)
    return {"snapshot_path": str(path), **snapshot}


def _safe_http_json(method: str, url: str) -> dict[str, Any]:
    try:
        return {"ok": True, "data": http_json(method, url)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def export_novel_markdown(base_url: str, novel_id: str, output_path: Path) -> Path:
    text = http_text("GET", f"{base_url}/api/v1/export/novel/{novel_id}?format=markdown", timeout=180)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return output_path


def fetch_chapters(base_url: str, novel_id: str) -> list[dict[str, Any]]:
    payload = http_json("GET", f"{base_url}/api/v1/novels/{novel_id}/chapters")
    if isinstance(payload, list):
        return payload
    return []


def gate_chapters_for_run(
    run_dir: Path,
    plans: list[ArmPlan],
    *,
    base_url: str = DEFAULT_BACKEND_URL,
    stop_on_fail: bool = False,
) -> dict[str, Any]:
    items: dict[str, Any] = {}
    stopped: list[str] = []
    for plan in plans:
        try:
            chapters = fetch_chapters(base_url, plan.novel_id)
            normalized = chapters_for_topic_alignment_gate(chapters)
            result = evaluate_chapter_topic_alignment_series(normalized)
            result["chapter_count"] = len(normalized)
            items[plan.novel_id] = result
            if result["should_stop"] and stop_on_fail:
                http_json("POST", f"{base_url}/api/v1/autopilot/{plan.novel_id}/stop")
                stopped.append(plan.novel_id)
        except Exception as exc:
            items[plan.novel_id] = {"ok": False, "should_stop": True, "invalid_reasons": ["chapter_fetch_failed"], "error": str(exc)}
    report = {
        "schema_version": 1,
        "ok": all(item.get("ok") for item in items.values()),
        "items": items,
        "stopped_novels": stopped,
        "generated_at": _utc_now(),
    }
    report["native_sync_health"] = check_native_sync_health(run_dir)
    report["native_sync_ok"] = bool(report["native_sync_health"].get("ok"))
    if not report["native_sync_ok"]:
        report["ok"] = False
    _write_json(run_dir / "chapter_topic_alignment_gate.json", report)
    return report


def build_report(run_dir: Path, plans: list[ArmPlan], *, base_url: str = DEFAULT_BACKEND_URL) -> dict[str, Any]:
    audit_manifest = write_audit_inventory(run_dir / "llm_calls")
    seed_manifest = _read_json(run_dir / "seed_manifest.json", default={}) or {}
    base_gate = build_base_input_gate(seed_manifest, plans)
    audit_gate = check_audit_completeness(
        run_dir / "llm_calls",
        expected_novels={
            plan.novel_id: plan.chapter_count
            for plan in plans
            if plan.run_kind == "formal"
        },
    )
    macro_records = load_audit_records(run_dir / "llm_calls")
    macro = {
        plan.novel_id: evaluate_macro_planning_gate(macro_records, novel_id=plan.novel_id)
        for plan in plans
    }
    _write_json(run_dir / "macro_planning_audit.json", {"schema_version": 1, "items": macro})

    formal_control = next((plan for plan in plans if plan.run_kind == "formal" and plan.arm == ARM_CONTROL), None)
    formal_experiment = next(plan for plan in plans if plan.run_kind == "formal" and plan.arm == ARM_EXPERIMENT)
    formal_plans = [plan for plan in (formal_control, formal_experiment) if plan is not None]
    formal_macro = {plan.novel_id: macro[plan.novel_id] for plan in formal_plans}
    leakage_gate: dict[str, Any]
    control_snapshots: dict[str, Any] = {}
    experiment_snapshots: dict[str, Any] = {}
    try:
        experiment_snapshots = fetch_evolution_snapshots(base_url, formal_experiment.novel_id)
        if formal_control is None:
            leakage_gate = {
                "schema_version": 1,
                "ok": True,
                "mode": "experiment_only",
                "invalid_reasons": [],
                "checks": [
                    {
                        "id": "control_leakage_skipped",
                        "ok": True,
                        "note": "No formal control arm was requested; leakage comparison is intentionally skipped.",
                    }
                ],
            }
        else:
            control_snapshots = fetch_evolution_snapshots(base_url, formal_control.novel_id)
            leakage_gate = build_leakage_gate(
                control_agent_status=control_snapshots["agent_status"],
                experiment_agent_status=experiment_snapshots["agent_status"],
                control_diagnostics=control_snapshots["diagnostics"],
                experiment_diagnostics=experiment_snapshots["diagnostics"],
            )
    except Exception as exc:
        leakage_gate = {"schema_version": 1, "ok": False, "invalid_reasons": ["snapshot_fetch_failed"], "error": str(exc)}
    _write_json(run_dir / "leakage_acceptance.json", leakage_gate)
    native_sync_gate = check_native_sync_health(run_dir)

    valid = (
        bool(base_gate.get("ok"))
        and bool(audit_gate.get("ok"))
        and bool(leakage_gate.get("ok"))
        and bool(native_sync_gate.get("ok"))
        and all(item.get("ok") for item in formal_macro.values())
    )
    formal_acceptance = build_formal_acceptance(
        run_dir=run_dir,
        formal_plans=formal_plans,
        audit_gate=audit_gate,
        audit_manifest=audit_manifest,
        leakage_gate=leakage_gate,
        native_sync_gate=native_sync_gate,
        formal_macro=formal_macro,
        report_valid_experiment=valid,
    )
    quality_metrics = build_quality_metrics(
        run_dir=run_dir,
        formal_control=formal_control,
        formal_experiment=formal_experiment,
        audit_records=macro_records,
        control_snapshots=control_snapshots,
        experiment_snapshots=experiment_snapshots,
        chapter_gate=_read_json(run_dir / "chapter_topic_alignment_gate.json", default={}) or {},
        valid_experiment=valid,
    )
    metrics = {
        "schema_version": 1,
        "valid_experiment": valid,
        "formal_acceptance": formal_acceptance,
        "base_input_gate": base_gate,
        "audit_gate": audit_gate,
        "macro_gate": macro,
        "native_sync_gate": native_sync_gate,
        "quality_metrics_path": str(run_dir / "quality_metrics.json"),
        "quality_report_path": str(run_dir / "quality_report.md"),
        "audit_manifest": audit_manifest,
        "generated_at": _utc_now(),
    }
    _write_json(run_dir / "metrics.json", metrics)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update(
        {
            "complete": valid,
            "valid_experiment": valid,
            "invalid_reasons": _collect_invalid_reasons(base_gate, audit_gate, leakage_gate, formal_macro, native_sync_gate),
            "reported_at": _utc_now(),
            "quality_report": str(run_dir / "quality_report.md"),
            "quality_metrics": str(run_dir / "quality_metrics.json"),
        }
    )
    _write_json(run_dir / "run_manifest.json", run_manifest)
    _write_json(run_dir / "quality_metrics.json", quality_metrics)
    (run_dir / "quality_report.md").write_text(render_quality_report(quality_metrics), encoding="utf-8")
    return metrics


def build_formal_acceptance(
    *,
    run_dir: Path,
    formal_plans: list[ArmPlan],
    audit_gate: dict[str, Any],
    audit_manifest: dict[str, Any],
    leakage_gate: dict[str, Any],
    native_sync_gate: dict[str, Any],
    formal_macro: dict[str, Any],
    report_valid_experiment: bool,
) -> dict[str, Any]:
    chapter_gate = _read_json(run_dir / "chapter_topic_alignment_gate.json", default={}) or {}
    chapter_items = chapter_gate.get("items") if isinstance(chapter_gate.get("items"), dict) else {}
    formal_chapter_counts = {
        plan.novel_id: int(chapter_items.get(plan.novel_id, {}).get("chapter_count") or 0)
        for plan in formal_plans
    }
    exports = {
        "control_off.md": _file_size(run_dir / "exports" / "control_off.md"),
        "experiment_on.md": _file_size(run_dir / "exports" / "experiment_on.md"),
    }
    formal_valid = (
        bool(report_valid_experiment)
        and all(formal_macro.get(plan.novel_id, {}).get("ok") for plan in formal_plans)
        and all(formal_chapter_counts.get(plan.novel_id) == plan.chapter_count for plan in formal_plans)
        and bool(audit_gate.get("ok"))
        and bool(audit_manifest.get("complete", True))
        and bool(leakage_gate.get("ok"))
        and bool(native_sync_gate.get("ok"))
    )
    payload = {
        "schema_version": 1,
        "formal_valid_experiment": formal_valid,
        "note": "Formal acceptance is computed only from formal control/experiment novels; calibration placeholders are excluded from final validity.",
        "formal_novels": [plan.novel_id for plan in formal_plans],
        "formal_macro_ok": {plan.novel_id: bool(formal_macro.get(plan.novel_id, {}).get("ok")) for plan in formal_plans},
        "formal_chapter_counts": formal_chapter_counts,
        "audit_ok": bool(audit_gate.get("ok")),
        "audit_total_calls": int(audit_gate.get("total_calls") or 0),
        "frontend_pressure_manifest_complete": bool(audit_manifest.get("complete", True)),
        "leakage_ok": bool(leakage_gate.get("ok")),
        "native_sync_ok": bool(native_sync_gate.get("ok")),
        "exports": exports,
        "report_valid_experiment": bool(report_valid_experiment),
    }
    _write_json(run_dir / "formal_acceptance.json", payload)
    return payload


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def build_quality_metrics(
    *,
    run_dir: Path,
    formal_control: ArmPlan | None,
    formal_experiment: ArmPlan,
    audit_records: list[dict[str, Any]],
    control_snapshots: dict[str, Any],
    experiment_snapshots: dict[str, Any],
    chapter_gate: dict[str, Any],
    valid_experiment: bool,
) -> dict[str, Any]:
    control_chapters = _load_chapters_from_sandbox(run_dir, formal_control.novel_id) if formal_control else []
    experiment_chapters = _load_chapters_from_sandbox(run_dir, formal_experiment.novel_id)
    control_quality = analyze_chapter_quality(control_chapters)
    experiment_quality = analyze_chapter_quality(experiment_chapters)
    costs = build_cost_breakdown(audit_records)
    experiment_agent_status = experiment_snapshots.get("agent_status") if isinstance(experiment_snapshots.get("agent_status"), dict) else {}
    experiment_diagnostics = experiment_snapshots.get("diagnostics") if isinstance(experiment_snapshots.get("diagnostics"), dict) else {}
    boundary_revision = summarize_boundary_revision(
        audit_records=audit_records,
        experiment_diagnostics=experiment_diagnostics,
        events=load_hosted_write_events(run_dir),
    )
    palette_status = _find_nested_dict(experiment_agent_status, "personality_palette_status") or _find_nested_dict(
        experiment_diagnostics,
        "personality_palette_status",
    ) or {}
    participation = {
        "agent_api_call_count": _agent_api_call_count(experiment_agent_status),
        "active_asset_counts": _active_leakage_counts(_agent_counts(experiment_agent_status)),
        "context_injection_summary": _find_nested_dict(experiment_agent_status, "context_injection_summary") or {},
        "agent_takeover_health": _find_nested_dict(experiment_diagnostics, "agent_takeover_health") or {},
    }
    residual_risks = build_quality_residual_risks(
        control_quality=control_quality,
        experiment_quality=experiment_quality,
        palette_status=palette_status,
        report_valid_experiment=valid_experiment,
        has_control=formal_control is not None,
    )
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "validity": {
            "formal_valid_experiment": bool(valid_experiment),
            "chapter_gate_ok": bool(chapter_gate.get("ok")),
            "native_sync_ok": bool(chapter_gate.get("native_sync_ok")),
        },
        "arms": {
            ARM_CONTROL: control_quality,
            ARM_EXPERIMENT: experiment_quality,
        },
        "comparison": compare_quality(control_quality, experiment_quality)
        if formal_control
        else {
            "mode": "experiment_only",
            "delta_total_chars": None,
            "delta_core_clue_density_per_1k": None,
            "delta_repetitive_phrase_density_per_1k": None,
            "delta_route_reentry_candidates": None,
            "control_low_theme_chapters": [],
            "experiment_low_theme_chapters": experiment_quality.get("low_theme_chapters") or [],
        },
        "evolution": {
            "participation": participation,
            "personality_palette_status": palette_status,
            "cost": costs.get(ARM_EXPERIMENT, {}).get("evolution_agent_control_card", {}),
            "boundary_revision": boundary_revision,
        },
        "costs": costs,
        "residual_risks": residual_risks,
        "artifact_refs": {
            "control_export": str(run_dir / "exports" / "control_off.md"),
            "experiment_export": str(run_dir / "exports" / "experiment_on.md"),
            "audit_dir": str(run_dir / "llm_calls"),
        },
    }


def _load_chapters_from_sandbox(run_dir: Path, novel_id: str) -> list[dict[str, Any]]:
    db_path = run_dir / "data" / "aitext.db"
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT number, title, content FROM chapters WHERE novel_id = ? AND length(coalesce(content, '')) > 0 ORDER BY number",
            (novel_id,),
        ).fetchall()
    return [
        {
            "chapter_number": int(row["number"] or index),
            "title": str(row["title"] or f"第{index}章"),
            "content": str(row["content"] or ""),
        }
        for index, row in enumerate(rows, start=1)
    ]


def analyze_chapter_quality(chapters: list[dict[str, Any]]) -> dict[str, Any]:
    total_chars = sum(len(chapter.get("content") or "") for chapter in chapters)
    chapter_items: list[dict[str, Any]] = []
    first_sentences: list[str] = []
    for chapter in chapters:
        text = str(chapter.get("content") or "")
        topic = evaluate_chapter_topic_alignment(text)
        repetition = phrase_counts(text, REPETITIVE_PHRASES)
        route = route_signals(text)
        first_sentence = normalize_sentence(first_sentence_of(text))
        first_sentences.append(first_sentence)
        chapter_items.append(
            {
                "chapter_number": int(chapter.get("chapter_number") or len(chapter_items) + 1),
                "char_count": len(text),
                "theme_hit_count": topic["theme_hit_count"],
                "topic_alignment": topic["topic_alignment"],
                "core_clue_hits": phrase_counts(text, CORE_CLUE_TERMS),
                "core_clue_density_per_1k": round(sum(phrase_counts(text, CORE_CLUE_TERMS).values()) * 1000 / max(len(text), 1), 3),
                "repetitive_phrase_count": sum(repetition.values()),
                "repetitive_phrase_density_per_1k": round(sum(repetition.values()) * 1000 / max(len(text), 1), 3),
                "route_marker_count": route["route_marker_count"],
                "location_mentions": route["location_mentions"],
                "opening": first_sentence[:80],
            }
        )
    repeated_openings = repeated_items([item for item in first_sentences if item])
    total_repetition = sum(item["repetitive_phrase_count"] for item in chapter_items)
    total_clue_hits = sum(sum(item["core_clue_hits"].values()) for item in chapter_items)
    return {
        "chapter_count": len(chapters),
        "total_chars": total_chars,
        "avg_chars_per_chapter": round(total_chars / len(chapters), 1) if chapters else 0,
        "topic_alignment_ok_chapters": sum(1 for item in chapter_items if item["topic_alignment"] == "ok"),
        "low_theme_chapters": [item["chapter_number"] for item in chapter_items if item["topic_alignment"] != "ok"],
        "core_clue_hits_total": total_clue_hits,
        "core_clue_density_per_1k": round(total_clue_hits * 1000 / max(total_chars, 1), 3),
        "repetitive_phrase_total": total_repetition,
        "repetitive_phrase_density_per_1k": round(total_repetition * 1000 / max(total_chars, 1), 3),
        "repeated_openings": repeated_openings,
        "route_marker_total": sum(item["route_marker_count"] for item in chapter_items),
        "route_reentry_candidates": route_reentry_candidates(chapter_items),
        "chapters": chapter_items,
    }


def phrase_counts(text: str, phrases: tuple[str, ...]) -> dict[str, int]:
    return {phrase: text.count(phrase) for phrase in phrases if text.count(phrase)}


def route_signals(text: str) -> dict[str, Any]:
    head = text[:260]
    return {
        "route_marker_count": sum(head.count(marker) for marker in ROUTE_MARKERS),
        "location_mentions": phrase_counts(head, LOCATION_TERMS),
    }


def route_reentry_candidates(chapter_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    previous_locations: set[str] = set()
    for item in chapter_items:
        locations = set((item.get("location_mentions") or {}).keys())
        repeated = sorted(locations & previous_locations)
        if repeated and int(item.get("route_marker_count") or 0) > 0:
            candidates.append(
                {
                    "chapter_number": item.get("chapter_number"),
                    "repeated_locations": repeated,
                    "route_marker_count": item.get("route_marker_count"),
                }
            )
        previous_locations = locations
    return candidates


def first_sentence_of(text: str) -> str:
    parts = re.split(r"[。！？\n]+", text.strip(), maxsplit=1)
    return parts[0].strip() if parts else ""


def normalize_sentence(text: str) -> str:
    return re.sub(r"\s+", "", text or "")[:80]


def repeated_items(values: list[str]) -> list[dict[str, Any]]:
    counts = Counter(values)
    return [{"text": text, "count": count} for text, count in counts.items() if count > 1]


def build_cost_breakdown(audit_records: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    costs: dict[str, dict[str, dict[str, Any]]] = {}
    for record in audit_records:
        arm = str(record.get("arm") or "unknown")
        phase = str(record.get("phase") or "unknown")
        usage = record.get("token_usage") if isinstance(record.get("token_usage"), dict) else {}
        bucket = costs.setdefault(arm, {}).setdefault(
            phase,
            {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "prompt_chars": 0,
                "output_chars": 0,
            },
        )
        bucket["call_count"] += 1
        bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
        bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
        bucket["total_tokens"] += int(usage.get("total_tokens") or 0)
        bucket["prompt_chars"] += int(record.get("prompt_chars") or 0)
        bucket["output_chars"] += int(record.get("output_chars") or 0)
    return costs


def summarize_boundary_revision(
    *,
    audit_records: list[dict[str, Any]],
    experiment_diagnostics: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    boundary_summary = _find_nested_dict(experiment_diagnostics, "boundary_continuity_summary")
    event_counts = Counter(str(event.get("type") or "") for event in events if str(event.get("type") or "") in BOUNDARY_REVISION_EVENTS)
    reason_counts = Counter(
        str(event.get("reason") or "unspecified")
        for event in events
        if str(event.get("type") or "") == "boundary_revision_required"
    )
    applied_chapters = sorted(
        {
            int(event.get("chapter"))
            for event in events
            if str(event.get("type") or "") == "boundary_revision_applied" and str(event.get("chapter") or "").isdigit()
        }
    )
    required_chapters = sorted(
        {
            int(event.get("chapter"))
            for event in events
            if str(event.get("type") or "") == "boundary_revision_required" and str(event.get("chapter") or "").isdigit()
        }
    )
    skipped_chapters = sorted(
        {
            int(event.get("chapter"))
            for event in events
            if str(event.get("type") or "") == "boundary_revision_skipped" and str(event.get("chapter") or "").isdigit()
        }
    )
    rewrite_records = [record for record in audit_records if str(record.get("phase") or "") == BOUNDARY_REVISION_PHASE]
    rewrite_usage = build_cost_breakdown(rewrite_records).get(ARM_EXPERIMENT, {}).get(
        BOUNDARY_REVISION_PHASE,
        {
            "call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "prompt_chars": 0,
            "output_chars": 0,
        },
    )
    failed_count = int(boundary_summary.get("boundary_failed_count") or 0)
    revision_required_count = int(boundary_summary.get("boundary_revision_required_count") or 0)
    return {
        "schema_version": 1,
        "baseline": {
            "failed_count": BOUNDARY_REVISION_BASELINE_FAILED,
            "edge_count": BOUNDARY_REVISION_BASELINE_TOTAL,
        },
        "target": {
            "max_failed_count": BOUNDARY_REVISION_TARGET_FAILED,
            "met": failed_count <= BOUNDARY_REVISION_TARGET_FAILED if boundary_summary else None,
        },
        "diagnostics": {
            "boundary_injected_count": int(boundary_summary.get("boundary_injected_count") or 0),
            "boundary_failed_count": failed_count,
            "boundary_revision_required_count": revision_required_count,
            "chapter_execution_draft_count": int(boundary_summary.get("chapter_execution_draft_count") or 0),
            "chapter_execution_draft_failed_count": int(boundary_summary.get("chapter_execution_draft_failed_count") or 0),
        },
        "sse_events": {
            "total_captured": len(events),
            "counts": {event_type: int(event_counts.get(event_type, 0)) for event_type in sorted(BOUNDARY_REVISION_EVENTS)},
            "required_reason_counts": dict(sorted(reason_counts.items())),
            "applied_chapters": applied_chapters,
            "required_chapters": required_chapters,
            "skipped_chapters": skipped_chapters,
        },
        "rewrite_llm": rewrite_usage,
        "audit_call_ids": [str(record.get("call_id") or "") for record in rewrite_records if record.get("call_id")],
    }


def compare_quality(control: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    return {
        "delta_total_chars": int(experiment.get("total_chars") or 0) - int(control.get("total_chars") or 0),
        "delta_core_clue_density_per_1k": round(
            float(experiment.get("core_clue_density_per_1k") or 0) - float(control.get("core_clue_density_per_1k") or 0),
            3,
        ),
        "delta_repetitive_phrase_density_per_1k": round(
            float(experiment.get("repetitive_phrase_density_per_1k") or 0)
            - float(control.get("repetitive_phrase_density_per_1k") or 0),
            3,
        ),
        "delta_route_reentry_candidates": len(experiment.get("route_reentry_candidates") or [])
        - len(control.get("route_reentry_candidates") or []),
        "control_low_theme_chapters": control.get("low_theme_chapters") or [],
        "experiment_low_theme_chapters": experiment.get("low_theme_chapters") or [],
    }


def build_quality_residual_risks(
    *,
    control_quality: dict[str, Any],
    experiment_quality: dict[str, Any],
    palette_status: dict[str, Any],
    report_valid_experiment: bool,
    has_control: bool = True,
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    missing = palette_status.get("missing") if isinstance(palette_status.get("missing"), list) else []
    polluted = [
        item
        for item in missing
        if isinstance(item, dict) and str(item.get("name") or "") and str(item.get("name") or "") not in EXPECTED_CHARACTER_NAMES
    ]
    if polluted:
        risks.append(
            {
                "id": "evolution_non_character_palette_entities",
                "severity": "medium",
                "summary": "Evolution character extraction still treats some objects/locations as palette-bearing characters.",
                "evidence": polluted[:10],
            }
        )
    if has_control and (experiment_quality.get("repetitive_phrase_density_per_1k") or 0) > (control_quality.get("repetitive_phrase_density_per_1k") or 0):
        risks.append(
            {
                "id": "experiment_repetition_density_not_lower",
                "severity": "low",
                "summary": "Experiment did not reduce the heuristic repetitive phrase density versus control.",
                "evidence": {
                    ARM_CONTROL: control_quality.get("repetitive_phrase_density_per_1k"),
                    ARM_EXPERIMENT: experiment_quality.get("repetitive_phrase_density_per_1k"),
                },
            }
        )
    if not report_valid_experiment:
        risks.append(
            {
                "id": "formal_validity_failed",
                "severity": "high",
                "summary": "Formal validity gates failed; quality comparison should not be used as a conclusion.",
                "evidence": {},
            }
        )
    return risks


def _find_nested_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    if key in payload and isinstance(payload[key], dict):
        return payload[key]
    for value in payload.values():
        if isinstance(value, dict):
            found = _find_nested_dict(value, key)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found = _find_nested_dict(item, key)
                    if found:
                        return found
    return {}


def render_quality_report(metrics: dict[str, Any]) -> str:
    control = metrics.get("arms", {}).get(ARM_CONTROL, {})
    experiment = metrics.get("arms", {}).get(ARM_EXPERIMENT, {})
    comparison = metrics.get("comparison", {})
    evolution = metrics.get("evolution", {})
    palette = evolution.get("personality_palette_status") if isinstance(evolution.get("personality_palette_status"), dict) else {}
    costs = metrics.get("costs", {})
    evo_cost = evolution.get("cost") if isinstance(evolution.get("cost"), dict) else {}
    boundary_revision = evolution.get("boundary_revision") if isinstance(evolution.get("boundary_revision"), dict) else {}
    boundary_diag = boundary_revision.get("diagnostics") if isinstance(boundary_revision.get("diagnostics"), dict) else {}
    boundary_events = boundary_revision.get("sse_events") if isinstance(boundary_revision.get("sse_events"), dict) else {}
    boundary_counts = boundary_events.get("counts") if isinstance(boundary_events.get("counts"), dict) else {}
    boundary_rewrite = boundary_revision.get("rewrite_llm") if isinstance(boundary_revision.get("rewrite_llm"), dict) else {}
    boundary_target = boundary_revision.get("target") if isinstance(boundary_revision.get("target"), dict) else {}
    lines = [
        "# Evolution Frontend v2 Formal A/B Quality Report",
        "",
        "## Validity",
        "",
        f"- Formal valid experiment: `{metrics.get('validity', {}).get('formal_valid_experiment')}`",
        f"- Native sync ok: `{metrics.get('validity', {}).get('native_sync_ok')}`",
        f"- Chapter gate ok: `{metrics.get('validity', {}).get('chapter_gate_ok')}`",
        "",
        "## Quality Comparison",
        "",
        f"- Control: {control.get('chapter_count', 0)} chapters, {control.get('total_chars', 0)} chars, clue density {control.get('core_clue_density_per_1k', 0)}/1k, repetition density {control.get('repetitive_phrase_density_per_1k', 0)}/1k.",
        f"- Experiment: {experiment.get('chapter_count', 0)} chapters, {experiment.get('total_chars', 0)} chars, clue density {experiment.get('core_clue_density_per_1k', 0)}/1k, repetition density {experiment.get('repetitive_phrase_density_per_1k', 0)}/1k.",
        f"- Delta chars: `{comparison.get('delta_total_chars')}`; delta clue density: `{comparison.get('delta_core_clue_density_per_1k')}`; delta repetition density: `{comparison.get('delta_repetitive_phrase_density_per_1k')}`.",
        f"- Route reentry candidates: control `{len(control.get('route_reentry_candidates') or [])}`, experiment `{len(experiment.get('route_reentry_candidates') or [])}`.",
        "",
        "## Evolution Participation And Cost",
        "",
        f"- Agent control-card calls: `{evo_cost.get('call_count', 0)}`; tokens: `{evo_cost.get('total_tokens', 0)}`; prompt chars: `{evo_cost.get('prompt_chars', 0)}`; output chars: `{evo_cost.get('output_chars', 0)}`.",
        f"- Palette coverage: `{palette.get('coverage')}` ({palette.get('complete_count', 0)}/{palette.get('character_count', 0)} complete).",
        "",
        "## Boundary Revision Loop",
        "",
        f"- Baseline boundary failures: `{BOUNDARY_REVISION_BASELINE_FAILED}/{BOUNDARY_REVISION_BASELINE_TOTAL}`; target for this retest: `<= {BOUNDARY_REVISION_TARGET_FAILED}/{BOUNDARY_REVISION_BASELINE_TOTAL}`.",
        f"- Current diagnostics: injected `{boundary_diag.get('boundary_injected_count', 0)}`, failed `{boundary_diag.get('boundary_failed_count', 0)}`, revision-required `{boundary_diag.get('boundary_revision_required_count', 0)}`, target met `{boundary_target.get('met')}`.",
        f"- Chapter execution drafts: locked `{boundary_diag.get('chapter_execution_draft_count', 0)}`, unfulfilled `{boundary_diag.get('chapter_execution_draft_failed_count', 0)}`.",
        f"- Captured SSE events: start `{boundary_counts.get('boundary_revision_start', 0)}`, applied `{boundary_counts.get('boundary_revision_applied', 0)}`, required `{boundary_counts.get('boundary_revision_required', 0)}`, skipped `{boundary_counts.get('boundary_revision_skipped', 0)}`.",
        f"- Applied chapters: `{boundary_events.get('applied_chapters', [])}`; required chapters: `{boundary_events.get('required_chapters', [])}`.",
        f"- Boundary rewrite LLM calls: `{boundary_rewrite.get('call_count', 0)}`; tokens: `{boundary_rewrite.get('total_tokens', 0)}`; prompt chars: `{boundary_rewrite.get('prompt_chars', 0)}`; output chars: `{boundary_rewrite.get('output_chars', 0)}`.",
        "",
        "## Residual Risks",
        "",
    ]
    risks = metrics.get("residual_risks") or []
    if risks:
        for risk in risks:
            lines.append(f"- `{risk.get('id')}` ({risk.get('severity')}): {risk.get('summary')}")
    else:
        lines.append("- No residual risks detected by the heuristic evaluator.")
    lines.extend(
        [
            "",
            "## Artifact References",
            "",
            f"- Control export: `{metrics.get('artifact_refs', {}).get('control_export')}`",
            f"- Experiment export: `{metrics.get('artifact_refs', {}).get('experiment_export')}`",
            f"- Audit dir: `{metrics.get('artifact_refs', {}).get('audit_dir')}`",
            "",
            "Note: this report uses deterministic heuristics and existing audit artifacts only; it does not trigger generation or rewrite story data.",
            "",
        ]
    )
    return "\n".join(lines)


def _collect_invalid_reasons(
    base_gate: dict[str, Any],
    audit_gate: dict[str, Any],
    leakage_gate: dict[str, Any],
    macro: dict[str, Any],
    native_sync_gate: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    for prefix, gate in (("base", base_gate), ("audit", audit_gate), ("leakage", leakage_gate)):
        if not gate.get("ok"):
            reasons.extend(f"{prefix}:{reason}" for reason in gate.get("invalid_reasons", []))
    for novel_id, gate in macro.items():
        if not gate.get("ok"):
            reasons.extend(f"macro:{novel_id}:{reason}" for reason in gate.get("invalid_reasons", []))
    if not native_sync_gate.get("ok"):
        reasons.extend(f"native_sync:{reason}" for reason in native_sync_gate.get("invalid_reasons", []))
    return reasons


def _plans_from_manifest(run_dir: Path) -> list[ArmPlan]:
    manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    novels = manifest.get("novels") if isinstance(manifest.get("novels"), list) else []
    plans: list[ArmPlan] = []
    for item in novels:
        if not isinstance(item, dict):
            continue
        plans.append(
            ArmPlan(
                str(item.get("run_kind") or "formal"),
                str(item.get("arm") or ARM_CONTROL),
                str(item.get("novel_id") or ""),
                int(item.get("chapter_count") or (2 if item.get("run_kind") == "calibration" else 10)),
                bool(item.get("evolution_enabled")),
            )
        )
    if plans:
        return plans
    return build_arm_plan(run_dir.name)


def _write_runbook(run_dir: Path, plans: list[ArmPlan]) -> Path:
    manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    has_control = any(plan.run_kind == "formal" and plan.arm == ARM_CONTROL for plan in plans)
    creation_method = str(manifest.get("creation_method") or "")
    lines = [
        "# Evolution Frontend Pressure v2 Runbook",
        "",
        f"- Run dir: `{run_dir}`",
        f"- Sandbox data dir: `{run_dir / 'data'}`",
        f"- Audit dir: `{run_dir / 'llm_calls'}`",
        f"- Creation method: `{creation_method or 'pending_browser_use_plotpilot_home_ui'}`",
        "",
        "## Backend",
        "",
        "```bash",
        f"source {run_dir / 'env.sh'}",
        "python -m uvicorn interfaces.main:app --host 127.0.0.1 --port 8005",
        "```",
        "",
        "## Frontend",
        "",
        "```bash",
        f"python scripts/evaluation/evolution_frontend_pressure_v2.py start-frontend --run-dir {run_dir}",
        "```",
        "",
        "## Browser Use Creation",
        "",
        "Use Browser Use with the in-app browser (`iab`) to open the PlotPilot Home UI and create the novel. Fill the original Home form with the pressure-test premise, `仙侠修真`, `修仙风（宗门、境界、机缘）`, advanced chapter count `10`, and `2500` words/chapter.",
        "",
        "After Browser Use lands on `/book/<actual_novel_id>/workbench`, record the result:",
        "",
        "```bash",
        f"python scripts/evaluation/evolution_frontend_pressure_v2.py record-browser-use-created --run-dir {run_dir} --novel-id <actual_novel_id> --screenshot-path <browser_use_screenshot_path>",
        "```",
        "",
        "If the Browser Use `node_repl js` tool is unavailable, record the blocker instead of falling back to Python Playwright or direct API creation:",
        "",
        "```bash",
        f"python scripts/evaluation/evolution_frontend_pressure_v2.py browser-use-blocker --run-dir {run_dir}",
        "```",
        "",
        "## UI Order",
        "",
        "Create novels through Browser Use operating the original PlotPilot Home UI first. Then use the real Workbench UI for generation. Keep auto approve off for the macro gate; after the macro audit passes, enable full auto in the workbench and continue the same arm.",
        "",
    ]
    for index, plan in enumerate(plans, start=1):
        lines.append(f"{index}. `{plan.run_kind}` `{plan.arm}` Evolution=`{plan.evolution_enabled}`")
        lines.append(f"   - Novel: `{plan.novel_id}`")
        lines.append(f"   - Before opening: `python scripts/evaluation/evolution_frontend_pressure_v2.py select-arm --run-dir {run_dir} --novel-id {plan.novel_id}`")
        lines.append(f"   - URL: `{DEFAULT_FRONTEND_URL}/book/{plan.novel_id}/workbench`")
    lines.extend(
        [
            "",
            "## Gates",
            "",
            "- Macro gate: run `gate-macro` after the first planning review pause.",
            "- Topic alignment gate: run `gate-chapters` after each completed chapter or at least after each arm.",
            "- Final metrics: run `report` after the formal arm finishes."
            if not has_control
            else "- Final metrics: run `report` after both formal arms finish.",
            "- Article issue review: run `python scripts/evaluation/evolution_article_issue_report.py --run-dir "
            f"{run_dir} --novel-id <formal_experiment_novel_id>` after the 10 chapters are generated.",
            "",
        ]
    )
    path = run_dir / "frontend_pressure_runbook.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _prepare_command(args: argparse.Namespace) -> int:
    run_id = args.run_id or f"evolution-frontend-ab-v2-{_now_slug()}"
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else ARTIFACT_ROOT / run_id
    manifest = prepare_sandbox(run_dir, source_data_dir=Path(args.source_data_dir), overwrite=args.overwrite)
    plans = build_arm_plan(run_dir.name, calibration_chapters=args.calibration_chapters, formal_chapters=args.formal_chapters)
    plan_payload = [plan.__dict__ | {"workbench_url": f"{DEFAULT_FRONTEND_URL}/book/{plan.novel_id}/workbench"} for plan in plans]
    manifest["planned_novels"] = plan_payload
    _write_json(run_dir / "run_manifest.json", manifest)
    runbook = _write_runbook(run_dir, plans)
    print(json.dumps({"run_dir": str(run_dir), "runbook": str(runbook), "planned_novels": plan_payload}, ensure_ascii=False, indent=2))
    return 0


def _start_backend_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    proc = start_backend(run_dir, port=args.port)
    ok = wait_for_backend(f"http://127.0.0.1:{args.port}", timeout_seconds=args.timeout)
    print(json.dumps({"pid": proc.pid, "healthy": ok, "backend_url": f"http://127.0.0.1:{args.port}"}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _start_frontend_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    proc = start_frontend(run_dir, port=args.port, backend_url=args.backend_url)
    frontend_url = f"http://127.0.0.1:{args.port}"
    ok = wait_for_frontend(frontend_url, timeout_seconds=args.timeout)
    print(json.dumps({"pid": proc.pid, "healthy": ok, "frontend_url": frontend_url}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _create_novels_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    plans = build_arm_plan(run_dir.name, calibration_chapters=args.calibration_chapters, formal_chapters=args.formal_chapters)
    manifest = create_seeded_novels(run_dir, plans, base_url=args.base_url)
    runbook = _write_runbook(run_dir, plans)
    print(json.dumps({"seed_manifest": manifest, "runbook": str(runbook)}, ensure_ascii=False, indent=2))
    return 0


def _create_via_ui_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    plans = build_arm_plan(run_dir.name, calibration_chapters=args.calibration_chapters, formal_chapters=args.formal_chapters)
    manifest = create_seeded_novels_via_home_ui(
        run_dir,
        plans,
        base_url=args.base_url,
        frontend_url=args.frontend_url,
        timeout_seconds=args.timeout,
    )
    created_plans = _plans_from_manifest(run_dir)
    runbook = _write_runbook(run_dir, created_plans)
    print(json.dumps({"seed_manifest": manifest, "runbook": str(runbook)}, ensure_ascii=False, indent=2))
    return 0


def _record_browser_use_created_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest = record_browser_use_created_novel(
        run_dir,
        novel_id=args.novel_id,
        run_kind=args.run_kind,
        arm=args.arm,
        chapter_count=args.chapter_count,
        evolution_enabled=args.evolution_enabled,
        screenshot_path=args.screenshot_path,
        base_url=args.base_url,
        frontend_url=args.frontend_url,
    )
    runbook = _write_runbook(run_dir, _plans_from_manifest(run_dir))
    print(json.dumps({"seed_manifest": manifest, "runbook": str(runbook)}, ensure_ascii=False, indent=2))
    return 0 if manifest["created_novels"][0]["ui_validation"]["ok"] else 2


def _browser_use_blocker_command(args: argparse.Namespace) -> int:
    result = record_browser_use_blocker(Path(args.run_dir).expanduser().resolve(), reason=args.reason)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 2


def _select_arm_command(args: argparse.Namespace) -> int:
    result = select_arm_for_frontend(Path(args.run_dir).expanduser().resolve(), args.novel_id, base_url=args.base_url)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _gate_macro_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    records = load_audit_records(run_dir / "llm_calls")
    novel_ids = args.novel_id or [plan.novel_id for plan in _plans_from_manifest(run_dir)]
    result = {novel_id: evaluate_macro_planning_gate(records, novel_id=novel_id) for novel_id in novel_ids}
    _write_json(run_dir / "macro_planning_audit.json", {"schema_version": 1, "items": result})
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(item["ok"] for item in result.values()) else 2


def _gate_audit_command(args: argparse.Namespace) -> int:
    result = check_audit_completeness(
        Path(args.run_dir).expanduser().resolve() / "llm_calls",
        expected_chapters={ARM_CONTROL: args.control_chapters, ARM_EXPERIMENT: args.experiment_chapters},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


def _gate_chapters_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    result = gate_chapters_for_run(
        run_dir,
        _plans_from_manifest(run_dir),
        base_url=args.base_url,
        stop_on_fail=args.stop_on_fail,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


def _report_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    plans = _plans_from_manifest(run_dir)
    metrics = build_report(run_dir, plans, base_url=args.base_url)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if metrics["valid_experiment"] else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and validate UI-first Evolution frontend pressure v2.")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Create an isolated artifact/data directory and runbook.")
    prepare.add_argument("--run-id", default="")
    prepare.add_argument("--run-dir", default="")
    prepare.add_argument("--source-data-dir", default=str(PROJECT_ROOT / "data"))
    prepare.add_argument("--calibration-chapters", type=int, default=2)
    prepare.add_argument("--formal-chapters", type=int, default=10)
    prepare.add_argument("--overwrite", action="store_true")
    prepare.set_defaults(func=_prepare_command)

    backend = sub.add_parser("start-backend", help="Start a sandbox backend with audit env enabled.")
    backend.add_argument("--run-dir", required=True)
    backend.add_argument("--port", type=int, default=8005)
    backend.add_argument("--timeout", type=int, default=60)
    backend.set_defaults(func=_start_backend_command)

    frontend = sub.add_parser("start-frontend", help="Start the original PlotPilot frontend for UI-first setup.")
    frontend.add_argument("--run-dir", required=True)
    frontend.add_argument("--port", type=int, default=3010)
    frontend.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    frontend.add_argument("--timeout", type=int, default=60)
    frontend.set_defaults(func=_start_frontend_command)

    record_browser = sub.add_parser("record-browser-use-created", help="Record a novel created by Browser Use in the original PlotPilot Home UI, then seed native context.")
    record_browser.add_argument("--run-dir", required=True)
    record_browser.add_argument("--novel-id", required=True)
    record_browser.add_argument("--screenshot-path", default="")
    record_browser.add_argument("--base-url", default=DEFAULT_BACKEND_URL)
    record_browser.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    record_browser.add_argument("--run-kind", default="formal")
    record_browser.add_argument("--arm", default=ARM_EXPERIMENT)
    record_browser.add_argument("--chapter-count", type=int, default=10)
    record_browser.add_argument("--evolution-enabled", action=argparse.BooleanOptionalAction, default=True)
    record_browser.set_defaults(func=_record_browser_use_created_command)

    browser_blocker = sub.add_parser("browser-use-blocker", help="Record that Browser Use could not run because node_repl js was unavailable.")
    browser_blocker.add_argument("--run-dir", required=True)
    browser_blocker.add_argument("--reason", default=BROWSER_USE_BLOCKER)
    browser_blocker.set_defaults(func=_browser_use_blocker_command)

    create_ui = sub.add_parser("create-via-ui", help="Deprecated fallback: Python Playwright UI creation, not the formal Browser Use path.")
    create_ui.add_argument("--run-dir", required=True)
    create_ui.add_argument("--base-url", default=DEFAULT_BACKEND_URL)
    create_ui.add_argument("--frontend-url", default=DEFAULT_FRONTEND_URL)
    create_ui.add_argument("--calibration-chapters", type=int, default=0)
    create_ui.add_argument("--formal-chapters", type=int, default=10)
    create_ui.add_argument("--timeout", type=int, default=120)
    create_ui.set_defaults(func=_create_via_ui_command)

    create = sub.add_parser("create-novels", help="Legacy fallback: create pressure novels through the sandbox API, not the formal UI-first path.")
    create.add_argument("--run-dir", required=True)
    create.add_argument("--base-url", default=DEFAULT_BACKEND_URL)
    create.add_argument("--calibration-chapters", type=int, default=2)
    create.add_argument("--formal-chapters", type=int, default=10)
    create.set_defaults(func=_create_novels_command)

    select = sub.add_parser("select-arm", help="Toggle Evolution for one arm and save preflight snapshots before UI generation.")
    select.add_argument("--run-dir", required=True)
    select.add_argument("--novel-id", required=True)
    select.add_argument("--base-url", default=DEFAULT_BACKEND_URL)
    select.set_defaults(func=_select_arm_command)

    gate_macro = sub.add_parser("gate-macro", help="Validate macro planning audit for one or more novels.")
    gate_macro.add_argument("--run-dir", required=True)
    gate_macro.add_argument("--novel-id", action="append", default=[])
    gate_macro.set_defaults(func=_gate_macro_command)

    gate_audit = sub.add_parser("gate-audit", help="Validate prompt/output/chunk/usage files for completed chapters.")
    gate_audit.add_argument("--run-dir", required=True)
    gate_audit.add_argument("--control-chapters", type=int, default=10)
    gate_audit.add_argument("--experiment-chapters", type=int, default=10)
    gate_audit.set_defaults(func=_gate_audit_command)

    gate_chapters = sub.add_parser("gate-chapters", help="Fetch completed chapters and stop/report if theme coverage stays low.")
    gate_chapters.add_argument("--run-dir", required=True)
    gate_chapters.add_argument("--base-url", default=DEFAULT_BACKEND_URL)
    gate_chapters.add_argument("--stop-on-fail", action="store_true")
    gate_chapters.set_defaults(func=_gate_chapters_command)

    report = sub.add_parser("report", help="Write final manifests, gates, inventory, leakage, and metrics.")
    report.add_argument("--run-dir", required=True)
    report.add_argument("--base-url", default=DEFAULT_BACKEND_URL)
    report.set_defaults(func=_report_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
