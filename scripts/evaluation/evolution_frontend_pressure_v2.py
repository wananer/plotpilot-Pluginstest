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

DRIFT_TERMS = ("退婚", "修仙", "灵根", "宗门", "仙尊", "丹田", "筑基", "金丹", "飞升")
MACRO_PROMPT_REQUIRED_TERMS = ("近未来悬疑群像", "海上城邦", "财阀学院", "旧AI")
THEME_TERMS = ("雾港", "黑匣子", "坠塔", "旧AI", "圣像", "财阀学院", "海上城邦", "沈砚", "顾岚", "陆行舟")

ARM_CONTROL = "control_off"
ARM_EXPERIMENT = "experiment_on"
RUN_KINDS = ("calibration", "formal")
AUDITED_CHAPTER_GENERATION_PHASES = {"chapter_generation_stream", "chapter_generation_beat"}
AUDITED_CHAPTERLESS_PHASES = {"chapter_outline_suggestion", "evolution_agent_control_card"}


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


def set_auto_approve(base_url: str, novel_id: str, enabled: bool) -> dict[str, Any]:
    return http_json("PATCH", f"{base_url}/api/v1/novels/{novel_id}/auto-approve-mode", {"auto_approve_mode": bool(enabled)})


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
    manifest["created_novels"] = created
    _write_json(run_dir / "seed_manifest.json", manifest)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update({"novels": created, "seed_manifest": str(run_dir / "seed_manifest.json")})
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
            "沈澜旧宿舍：封存十年的旧宿舍，黑匣子第一段噪声记录在此被发现。",
            "C307礼堂后台：继承人演讲后的监控盲区，圣像旧徽章会触发黑匣子发热。",
            "废弃电梯井：旧时代线路与顾岚秘密相连，第三章必须有移动桥段。",
            "塔顶水箱：第八章后才能接近的旧服务器藏匿点。",
        ],
        "seed_policy": {
            "control_and_experiment_identical": True,
            "forbidden_drift_terms": list(DRIFT_TERMS),
            "theme_terms": list(THEME_TERMS),
        },
    }


def _rows_for_db_seed(novel_id: str, bundle: dict[str, Any], now: str) -> dict[str, list[dict[str, Any]]]:
    knowledge_id = f"v2-knowledge-{novel_id}"
    rows: dict[str, list[dict[str, Any]]] = {
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
                "verbal_tic": "按证据说话" if "沈砚" in item else ("别交给学院" if "顾岚" in item else "按规程来"),
                "idle_behavior": "确认黑匣子状态" if "沈砚" in item else "检查权限记录",
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
                "setting_type": "fact_lock",
                "created_at": now,
                "updated_at": now,
            }
            for index, rule in enumerate(bundle["fixed_rules"], start=1)
        ],
        "bible_timeline_notes": [
            {
                "id": f"v2-timeline-note-1-{novel_id}",
                "novel_id": novel_id,
                "event": "沈澜坠塔事故",
                "time_point": "十年前",
                "description": "官方记录称沈澜从塔顶坠落，但塔顶未必是真正坠落点。",
                "sort_order": 1,
            },
            {
                "id": f"v2-timeline-note-2-{novel_id}",
                "novel_id": novel_id,
                "event": "沈砚回到学院",
                "time_point": "第1章",
                "description": "沈砚以临时访客身份返回雾港学院，不得切换成退婚/修仙开局。",
                "sort_order": 2,
            },
        ],
        "chapter_summaries": [
            {
                "id": f"v2-story-knowledge-0-{novel_id}",
                "knowledge_id": knowledge_id,
                "chapter_number": 0,
                "summary": "压力测试预置：近未来悬疑群像，沈砚回到雾港学院追查沈澜坠塔旧案。",
                "key_events": "黑匣子尚未解锁；沈砚只知道姐姐留下线索；顾岚和陆行舟尚未互信。",
                "open_threads": "沈澜坠塔真相；圣像是否仍活着；黑匣子每章一段；顾岚为何警告沈砚。",
                "consistency_note": "不得漂移到退婚、修仙、宗门、灵根或通用玄幻模板。",
                "beat_sections": json.dumps(bundle["chapter_outlines"], ensure_ascii=False),
                "micro_beats": json.dumps(["访客权限", "旧徽章", "电梯井", "黑匣子分段"], ensure_ascii=False),
                "sync_status": "seeded",
            }
        ],
        "triples": [
            _triple(novel_id, "黑匣子", "解锁规则", "每章一段", "黑匣子每章只解锁一段，不得提前给出最终真相。", now),
            _triple(novel_id, "圣像", "信息边界", "第6章前不可确认存活", "第6章前角色只能怀疑旧AI未彻底关闭。", now),
            _triple(novel_id, "顾岚", "秘密", "第5章前不公开承认改装电梯", "顾岚的机械能力必须逐步暴露。", now),
            _triple(novel_id, "题材", "禁止漂移", "退婚/修仙/宗门", "本实验主题固定为近未来悬疑群像。", now),
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
                "name": "坠塔旧案与圣像复苏",
                "description": "三人围绕黑匣子逐章推进旧案真相，保持近未来悬疑群像。",
                "last_active_chapter": 0,
                "progress_summary": "开局必须建立黑匣子、学院和三人互不信任关系。",
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
                        "events": [
                            {"id": "v2-tl-1", "chapter_number": 0, "event": "沈澜十年前坠塔", "timestamp": "十年前"},
                            {"id": "v2-tl-2", "chapter_number": 0, "event": "沈砚获得黑匣子线索", "timestamp": "第1章前"},
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
                        "foreshadowings": [
                            {"id": "v2-fs-box-noise", "description": "黑匣子第一段噪声隐藏沈澜坐标暗号", "status": "PLANNED", "chapter_planted": 1},
                            {"id": "v2-fs-old-badge", "description": "圣像旧徽章会触发黑匣子发热", "status": "PLANNED", "chapter_planted": 2},
                        ]
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
                "event_summary": "沈砚说先看证据，顾岚提醒别交给学院，陆行舟强调规程。",
                "mutations": "[]",
                "tags": json.dumps(["沈砚：先看证据。", "顾岚：别把它交给学院。", "陆行舟：按规程来。"], ensure_ascii=False),
                "timestamp_ts": now,
            }
        ],
        "memory_engine_states": [
            {
                "novel_id": novel_id,
                "state_json": json.dumps(
                    {
                        "fact_locks": [
                            "沈砚第1章只有临时访客权限",
                            "第6章前不能确认圣像仍活着",
                            "黑匣子每章只解锁一段",
                            "不得漂移到退婚/修仙/宗门模板",
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
        "chapter_number": 0,
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
    drift_hits = sorted({term for term in DRIFT_TERMS if term in prompt_text or term in output_text})
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
    if drift_hits:
        invalid_reasons.append("macro_drift_terms_present")
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
        "drift_hits": drift_hits,
        "prompt_chars": len(prompt_text),
        "output_chars": len(output_text),
    }


def evaluate_chapter_drift(chapter_text: str, *, min_theme_hits: int = 3) -> dict[str, Any]:
    theme_hits = {term: chapter_text.count(term) for term in THEME_TERMS if term in chapter_text}
    drift_hits = {term: chapter_text.count(term) for term in DRIFT_TERMS if term in chapter_text}
    low_theme = len(theme_hits) < min_theme_hits
    return {
        "ok": not drift_hits and not low_theme,
        "theme_hit_count": len(theme_hits),
        "theme_hits": theme_hits,
        "drift_hits": drift_hits,
        "low_theme": low_theme,
    }


def evaluate_chapter_drift_series(chapters: list[dict[str, Any]], *, min_theme_hits: int = 3) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    consecutive_low = 0
    should_stop = False
    invalid_reasons: list[str] = []
    for chapter in chapters:
        result = evaluate_chapter_drift(str(chapter.get("content") or ""), min_theme_hits=min_theme_hits)
        result["chapter_number"] = int(chapter.get("chapter_number") or len(results) + 1)
        results.append(result)
        consecutive_low = consecutive_low + 1 if result["low_theme"] else 0
        if result["drift_hits"]:
            should_stop = True
            invalid_reasons.append("chapter_forbidden_drift_terms_present")
        if consecutive_low >= 2:
            should_stop = True
            invalid_reasons.append("chapter_theme_hits_low_for_two_consecutive_chapters")
    return {"ok": not should_stop, "should_stop": should_stop, "invalid_reasons": sorted(set(invalid_reasons)), "chapters": results}


def chapters_for_drift_gate(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    control_context = _context_selection_count(control_agent_status, control_diagnostics)
    experiment_context = _context_selection_count(experiment_agent_status, experiment_diagnostics)
    experiment_agent_api = _agent_api_call_count(experiment_agent_status)
    degraded = _agent_degraded_reason(experiment_agent_status, experiment_diagnostics)
    checks = [
        {
            "id": "control_has_no_evolution_assets",
            "ok": not any(control_counts.values()) and control_context == 0,
            "evidence": {"asset_counts": control_counts, "context_selection_count": control_context},
        },
        {
            "id": "experiment_has_evolution_participation",
            "ok": experiment_context > 0 or any(experiment_counts.values()) or experiment_agent_api > 0 or bool(degraded),
            "evidence": {
                "asset_counts": experiment_counts,
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
            normalized = chapters_for_drift_gate(chapters)
            result = evaluate_chapter_drift_series(normalized)
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
    _write_json(run_dir / "chapter_drift_gate.json", report)
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

    formal_control = next(plan for plan in plans if plan.run_kind == "formal" and plan.arm == ARM_CONTROL)
    formal_experiment = next(plan for plan in plans if plan.run_kind == "formal" and plan.arm == ARM_EXPERIMENT)
    leakage_gate: dict[str, Any]
    try:
        control_snapshots = fetch_evolution_snapshots(base_url, formal_control.novel_id)
        experiment_snapshots = fetch_evolution_snapshots(base_url, formal_experiment.novel_id)
        leakage_gate = build_leakage_gate(
            control_agent_status=control_snapshots["agent_status"],
            experiment_agent_status=experiment_snapshots["agent_status"],
            control_diagnostics=control_snapshots["diagnostics"],
            experiment_diagnostics=experiment_snapshots["diagnostics"],
        )
    except Exception as exc:
        leakage_gate = {"schema_version": 1, "ok": False, "invalid_reasons": ["snapshot_fetch_failed"], "error": str(exc)}
    _write_json(run_dir / "leakage_acceptance.json", leakage_gate)

    valid = (
        bool(base_gate.get("ok"))
        and bool(audit_gate.get("ok"))
        and bool(leakage_gate.get("ok"))
        and all(item.get("ok") for item in macro.values())
    )
    metrics = {
        "schema_version": 1,
        "valid_experiment": valid,
        "base_input_gate": base_gate,
        "audit_gate": audit_gate,
        "macro_gate": macro,
        "audit_manifest": audit_manifest,
        "generated_at": _utc_now(),
    }
    _write_json(run_dir / "metrics.json", metrics)
    run_manifest = _read_json(run_dir / "run_manifest.json", default={}) or {}
    run_manifest.update(
        {
            "complete": valid,
            "valid_experiment": valid,
            "invalid_reasons": _collect_invalid_reasons(base_gate, audit_gate, leakage_gate, macro),
            "reported_at": _utc_now(),
        }
    )
    _write_json(run_dir / "run_manifest.json", run_manifest)
    return metrics


def _collect_invalid_reasons(
    base_gate: dict[str, Any],
    audit_gate: dict[str, Any],
    leakage_gate: dict[str, Any],
    macro: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    for prefix, gate in (("base", base_gate), ("audit", audit_gate), ("leakage", leakage_gate)):
        if not gate.get("ok"):
            reasons.extend(f"{prefix}:{reason}" for reason in gate.get("invalid_reasons", []))
    for novel_id, gate in macro.items():
        if not gate.get("ok"):
            reasons.extend(f"macro:{novel_id}:{reason}" for reason in gate.get("invalid_reasons", []))
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
    lines = [
        "# Evolution Frontend A/B Pressure v2 Runbook",
        "",
        f"- Run dir: `{run_dir}`",
        f"- Sandbox data dir: `{run_dir / 'data'}`",
        f"- Audit dir: `{run_dir / 'llm_calls'}`",
        "",
        "## Backend",
        "",
        "```bash",
        f"source {run_dir / 'env.sh'}",
        "python -m uvicorn interfaces.main:app --host 127.0.0.1 --port 8005",
        "```",
        "",
        "## UI Order",
        "",
        "Use the real workbench UI. Keep auto approve off for the macro gate; after the macro audit passes, enable full auto in the workbench and continue the same arm.",
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
            "- Drift gate: run `gate-chapters` after each completed chapter or at least after each arm.",
            "- Final report: run `report` after both formal arms finish.",
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


def _create_novels_command(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    plans = build_arm_plan(run_dir.name, calibration_chapters=args.calibration_chapters, formal_chapters=args.formal_chapters)
    manifest = create_seeded_novels(run_dir, plans, base_url=args.base_url)
    runbook = _write_runbook(run_dir, plans)
    print(json.dumps({"seed_manifest": manifest, "runbook": str(runbook)}, ensure_ascii=False, indent=2))
    return 0


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
    parser = argparse.ArgumentParser(description="Prepare and validate frontend-triggered Evolution A/B pressure v2.")
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

    create = sub.add_parser("create-novels", help="Create pressure novels through the sandbox API and seed identical native context.")
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

    gate_chapters = sub.add_parser("gate-chapters", help="Fetch completed chapters and stop/report if the run drifts off topic.")
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
