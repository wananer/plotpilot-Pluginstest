import json
import sqlite3
from types import SimpleNamespace

from scripts.evaluation.evolution_frontend_pressure_v2 import (
    ARM_CONTROL,
    ARM_EXPERIMENT,
    BROWSER_USE_BLOCKER,
    BROWSER_USE_CREATION_METHOD,
    HOME_UI_CREATION_METHOD,
    analyze_chapter_quality,
    build_base_input_gate,
    build_arm_plan,
    build_cost_breakdown,
    build_browser_use_creation_record,
    build_home_ui_creation_form,
    build_formal_acceptance,
    build_leakage_gate,
    build_quality_residual_risks,
    build_seed_manifest,
    chapters_for_topic_alignment_gate,
    check_native_sync_health,
    check_sandbox_foreign_keys,
    check_audit_completeness,
    evaluate_chapter_topic_alignment_series,
    evaluate_macro_planning_gate,
    create_seeded_novels_via_home_ui,
    record_browser_use_blocker,
    record_browser_use_created_novel,
    seed_native_context_in_app_db,
    summarize_boundary_revision,
    start_backend,
)
import scripts.evaluation.evolution_frontend_pressure_v2 as frontend_pressure_v2
from scripts.evaluation.evolution_article_issue_report import (
    build_quality_summary as build_article_quality_summary,
    build_report as build_article_issue_report,
    deterministic_issues as deterministic_article_issues,
    issue_quality_category,
    parse_llm_issue_json,
    write_report as write_article_issue_report,
)
from scripts.evaluation.evolution_pressure_test import (
    ChapterResult,
    EXPERIMENT_SPEC,
    _agent_api_usage_from_records,
    _build_leakage_acceptance_report,
    _build_preflight_snapshot,
    _embedding_preflight_status,
    _load_existing_arm,
    _compute_metrics,
    _repetitive_phrase_counts,
    _repetitive_phrase_total,
    _seed_pressure_host_context,
    _selected_chapter_outlines,
    _write_experiment_protocol,
)
from plugins.world_evolution_core.agent_orchestrator import AgentOrchestrator, decision_to_context_blocks
from plugins.world_evolution_core.host_context import HostContextReader
from infrastructure.persistence.mappers.foreshadowing_mapper import ForeshadowingMapper
from infrastructure.persistence.database.sqlite_knowledge_repository import SqliteKnowledgeRepository


def test_repetitive_phrase_metrics_catch_silent_templates():
    content = "沈砚没有说话。顾岚没有回答。两人沉默了几秒，然后继续沉默。"

    counts = _repetitive_phrase_counts(content)

    assert counts["没有说话"] == 1
    assert counts["没有回答"] == 1
    assert counts["沉默了几秒"] == 1
    assert counts["沉默"] == 2
    assert _repetitive_phrase_total(content) == 4


def test_agent_orchestrator_outputs_t0_t1_context_blocks():
    orchestrator = AgentOrchestrator(
        run_agent=lambda phase, prompt, payload: {
            "ok": True,
            "model": "agent-model",
            "token_usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
            "structured": {
                "intent": "context_control",
                "evidence_refs": [{"source_type": "chapter_full_text", "chapter_number": 6}],
                "t0_constraints": ["沈砚已经进入C307，黑匣子仍在他手里。"],
                "t1_strategy": ["避免使用没有说话/没有回答等模板句。"],
                "actions": [],
                "issues": [],
                "gene_patches": [],
            },
        }
    )
    decision = orchestrator.decide_context(
        novel_id="novel-pressure",
        chapter_number=7,
        outline="三人潜入潮汐机房，黑匣子投影出争执。",
        patch_summary="上一章结尾：沈砚已经进入C307，黑匣子仍在他手里。",
        knowledge={"items": []},
        tier_summary={"t0_block_count": 1, "t1_block_count": 1},
    )
    blocks = decision_to_context_blocks(decision, metadata={"novel_id": "novel-pressure"})

    assert [block["tier"] for block in blocks] == ["intended_t0", "intended_t1"]
    assert blocks[0]["kind"] == "hard_constraint"
    assert "沈砚已经进入C307" in blocks[0]["content"]
    assert "没有说话/没有回答" in blocks[1]["content"]
    assert blocks[0]["metadata"]["agent_orchestrated"] is True
    assert blocks[0]["metadata"]["evidence_refs"][0]["source_type"] == "chapter_full_text"


def test_load_existing_arm_preserves_reused_usage_metadata(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "out"
    source_dir.mkdir()
    output_dir.mkdir()
    for index in range(1, 11):
        (source_dir / f"control_off_chapter_{index:02d}.md").write_text(
            f"第{index}章正文。沈砚继续调查。",
            encoding="utf-8",
        )
    (source_dir / "llm_usage.json").write_text(
        json.dumps(
            {
                "generation": {
                    "control_off": {
                        "aggregate": {"call_count": 1, "total_tokens": 300, "total_cost_usd": 0.02},
                        "calls": [
                            {
                                "call_count": 1,
                                "chapter_number": 1,
                                "phase": "chapter_generation",
                                "input_tokens": 100,
                                "output_tokens": 200,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0,
                                "non_cache_tokens": 300,
                                "total_tokens": 300,
                                "total_cost_usd": 0.02,
                                "duration_seconds": 12.5,
                                "usage_source": "claude_json_usage",
                                "model": "sonnet",
                            }
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (source_dir / "metrics.json").write_text(
        json.dumps(
            {
                "control_off": {
                    "chapters": [
                        {
                            "chapter_number": 1,
                            "prompt_chars": 1234,
                            "evolution_context_chars": 0,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    chapters, meta = _load_existing_arm(source_dir, output_dir, "control_off")

    assert meta["llm_usage"]["call_count"] == 1
    assert meta["llm_calls"][0]["phase"] == "chapter_generation"
    assert chapters[0].prompt_chars == 1234
    assert chapters[0].llm_call_count == 1
    assert chapters[0].llm_total_tokens == 300
    assert chapters[0].llm_total_cost_usd == 0.02
    assert chapters[0].duration_seconds == 12.5
    assert (output_dir / "control_off_chapter_01.md").exists()


def test_preflight_snapshot_records_risk_inputs_without_model_calls(tmp_path):
    args = SimpleNamespace(
        model="sonnet",
        target_chars=2500,
        timeout=420,
        budget_usd=None,
        expand_short_chapters=False,
        expansion_min_ratio=0.9,
        reuse_control_dir="",
        chapter_limit=3,
    )

    snapshot = _build_preflight_snapshot(output_dir=tmp_path, args=args, started_at="2026-04-28T00:00:00")
    protocol_path = _write_experiment_protocol(tmp_path, snapshot)

    assert snapshot["generation_parameters"]["use_api2_control_card"] is False
    assert snapshot["generation_parameters"]["agent_api_is_primary"] is True
    assert snapshot["generation_parameters"]["chapter_limit"] == 3
    assert snapshot["generation_parameters"]["expected_chapters"] == 3
    assert snapshot["agent_api_config"]["enabled"] is True
    assert snapshot["agent_api_config"]["api_key_copied_to_artifacts"] is False
    assert snapshot["seeded_native_context"]["planned"] is True
    assert snapshot["script"]["sha256"]
    assert "head" in snapshot["git"]
    assert "branch" in snapshot["git"]
    assert "plugin_manifest_snapshot" in snapshot
    assert "embedding_status" in snapshot
    assert "sk-" not in json.dumps(snapshot["agent_api_config"], ensure_ascii=False)
    assert {item["id"] for item in snapshot["risk_register"]} >= {
        "dirty_worktree",
        "plugin_state_drift",
        "legacy_api2_residue",
    }
    assert "Control: Evolution 关闭" in protocol_path.read_text(encoding="utf-8")


def test_selected_chapter_outlines_supports_calibration_limit():
    assert len(_selected_chapter_outlines(2)) == 2
    assert len(_selected_chapter_outlines(999)) == EXPERIMENT_SPEC["target_chapters"]


def test_frontend_pressure_v2_zero_calibration_uses_experiment_only_formal_arm():
    plans = build_arm_plan("xianxia-case-test", calibration_chapters=0, formal_chapters=10)

    assert len(plans) == 1
    assert plans[0].run_kind == "formal"
    assert plans[0].arm == ARM_EXPERIMENT
    assert plans[0].chapter_count == 10
    assert plans[0].evolution_enabled is True


def test_frontend_pressure_v2_home_ui_form_uses_original_plotpilot_defaults():
    plan = build_arm_plan("xianxia-ui-test", calibration_chapters=0, formal_chapters=10)[0]

    form = build_home_ui_creation_form(plan)

    assert form["premise"] == EXPERIMENT_SPEC["premise"]
    assert form["genre"] == "仙侠修真"
    assert form["world_preset"] == "修仙风"
    assert form["world_preset_label"] == "修仙风（宗门、境界、机缘）"
    assert form["target_chapters"] == 10
    assert form["target_words_per_chapter"] == 2500
    assert form["use_advanced"] is True


def test_embedding_preflight_status_reports_vector_store_disabled(monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_ENABLED", "false")

    status = _embedding_preflight_status()

    assert status["ready"] is False
    assert status["degraded_reason"] == "vector_store_disabled"
    assert status["env"]["vector_store_enabled"] is False


def test_agent_api_usage_is_split_from_generation_metrics():
    usage = _agent_api_usage_from_records(
        [
            {
                "source": "agent_api",
                "chapter_number": 1,
                "token_usage": {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
            },
            {"source": "legacy_api2", "token_usage": {"input_tokens": 999, "output_tokens": 999}},
        ]
    )
    chapters = [
        ChapterResult("experiment_on", 1, "outline", "沈砚继续调查。", prompt_chars=10, duration_seconds=1.0, llm_call_count=1, llm_input_tokens=100, llm_output_tokens=50)
    ]
    metrics = _compute_metrics(
        chapters,
        {
            "agent_api_usage": usage,
            "diagnostics": {
                "host_context_summary": {
                    "active_sources": ["bible", "story_knowledge"],
                    "degraded_sources": ["foreshadow"],
                    "empty_sources": ["dialogue"],
                    "plotpilot_context_usage": {"mode": "strategy_only"},
                },
                "semantic_recall_summary": {"item_count": 3, "vector_enabled": True},
            },
        },
    )

    assert usage["aggregate"]["call_count"] == 1
    assert usage["aggregate"]["total_tokens"] == 42
    assert metrics["aggregate"]["generation_llm_call_count"] == 1
    assert metrics["aggregate"]["evolution_agent_api_call_count"] == 1
    assert metrics["aggregate"]["evolution_agent_api_total_tokens"] == 42
    assert metrics["aggregate"]["plotpilot_native_context_mode"] == "strategy_only"
    assert metrics["aggregate"]["plotpilot_native_active_source_count"] == 2
    assert metrics["aggregate"]["plotpilot_native_degraded_source_count"] == 1
    assert metrics["aggregate"]["plotpilot_native_empty_source_count"] == 1
    assert metrics["aggregate"]["semantic_recall_item_count"] == 3
    assert metrics["aggregate"]["semantic_recall_vector_enabled"] is True


def test_leakage_acceptance_fails_when_control_has_evolution_context():
    control = [
        ChapterResult("control_off", index, "outline", "正文", prompt_chars=10, duration_seconds=1.0, evolution_context_chars=1)
        for index in range(1, EXPERIMENT_SPEC["target_chapters"] + 1)
    ]
    experiment = [
        ChapterResult("experiment_on", index, "outline", "正文", prompt_chars=10, duration_seconds=1.0, evolution_context_chars=10)
        for index in range(1, EXPERIMENT_SPEC["target_chapters"] + 1)
    ]
    metrics = {
        "control_off": {"aggregate": {"generation_llm_call_count": 10, "generation_llm_total_tokens": 1000}},
        "experiment_on": {
            "aggregate": {
                "generation_llm_call_count": 10,
                "generation_llm_total_tokens": 1200,
                "evolution_agent_api_call_count": 1,
                "plotpilot_native_active_source_count": 2,
            }
        },
    }

    report = _build_leakage_acceptance_report(
        control=control,
        control_meta={},
        experiment=experiment,
        experiment_meta={
            "runs": {"items": [{"run_id": "run"}]},
            "review_records": {"items": [{"chapter_number": index} for index in range(1, 11)]},
            "agent_status": {"asset_counts": {"events": 10}},
        },
        metrics=metrics,
    )

    assert report["valid_experiment"] is False
    assert "control_has_no_evolution_context" in report["invalid_reasons"]


def test_leakage_acceptance_passes_for_isolated_control_and_invoked_experiment():
    control = [
        ChapterResult("control_off", index, "outline", "正文", prompt_chars=10, duration_seconds=1.0)
        for index in range(1, EXPERIMENT_SPEC["target_chapters"] + 1)
    ]
    experiment = [
        ChapterResult("experiment_on", index, "outline", "正文", prompt_chars=10, duration_seconds=1.0, evolution_context_chars=10)
        for index in range(1, EXPERIMENT_SPEC["target_chapters"] + 1)
    ]
    metrics = {
        "control_off": {"aggregate": {"generation_llm_call_count": 10, "generation_llm_total_tokens": 1000}},
        "experiment_on": {
            "aggregate": {
                "generation_llm_call_count": 10,
                "generation_llm_total_tokens": 1200,
                "evolution_agent_api_call_count": 1,
                "plotpilot_native_active_source_count": 2,
            }
        },
    }

    report = _build_leakage_acceptance_report(
        control=control,
        control_meta={},
        experiment=experiment,
        experiment_meta={
            "runs": {"items": [{"run_id": "run"}]},
            "review_records": {"items": [{"chapter_number": index} for index in range(1, 11)]},
            "agent_status": {"asset_counts": {"events": 10, "capsules": 1, "reflections": 1}},
        },
        metrics=metrics,
    )

    assert report["valid_experiment"] is True
    assert report["invalid_reasons"] == []


def test_leakage_acceptance_requires_agent_api_and_native_context_participation():
    control = [
        ChapterResult("control_off", index, "outline", "正文", prompt_chars=10, duration_seconds=1.0)
        for index in range(1, 4)
    ]
    experiment = [
        ChapterResult("experiment_on", index, "outline", "正文", prompt_chars=10, duration_seconds=1.0, evolution_context_chars=10)
        for index in range(1, 4)
    ]
    metrics = {
        "control_off": {"aggregate": {"generation_llm_call_count": 3, "generation_llm_total_tokens": 1000}},
        "experiment_on": {
            "aggregate": {
                "generation_llm_call_count": 3,
                "generation_llm_total_tokens": 1200,
                "evolution_agent_api_call_count": 0,
                "plotpilot_native_active_source_count": 0,
            }
        },
    }

    report = _build_leakage_acceptance_report(
        control=control,
        control_meta={},
        experiment=experiment,
        experiment_meta={
            "runs": {"items": [{"run_id": "run"}]},
            "review_records": {"items": [{"chapter_number": index} for index in range(1, 4)]},
            "agent_status": {"asset_counts": {"events": 10}},
        },
        metrics=metrics,
        expected_chapters=3,
    )

    assert report["valid_experiment"] is False
    assert "experiment_agent_api_participated" in report["invalid_reasons"]
    assert "experiment_native_context_participated" in report["invalid_reasons"]


def test_pressure_host_context_seed_is_readable_and_isolated(tmp_path):
    host_database, seed = _seed_pressure_host_context(tmp_path, "pressure-experiment-test")

    assert seed["active_sources"]
    assert "bible" in seed["active_sources"]
    assert "story_knowledge" in seed["active_sources"]
    assert "foreshadow" in seed["active_sources"]

    context = HostContextReader(host_database).read(
        "pressure-experiment-test",
        query="照影镜 安神丹 谢无咎",
        before_chapter=2,
    )

    assert context["counts"]["bible"] >= 2
    assert context["counts"]["story_knowledge"] >= 1
    assert context["counts"]["triples"] >= 1
    assert context["plotpilot_context_usage"]["mode"] == "strategy_only"

    assert "api_key" not in json.dumps(seed, ensure_ascii=False)


def _create_frontend_v2_seed_schema(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE knowledge (
                id TEXT PRIMARY KEY, novel_id TEXT, version INTEGER, premise_lock TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE bibles (
                id TEXT PRIMARY KEY, novel_id TEXT, schema_version INTEGER, extensions TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE bible_characters (
                id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT,
                mental_state TEXT, mental_state_reason TEXT, verbal_tic TEXT, idle_behavior TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE bible_locations (
                id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT, location_type TEXT, parent_id TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE bible_world_settings (
                id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT, setting_type TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE bible_timeline_notes (
                id TEXT PRIMARY KEY, novel_id TEXT, event TEXT, time_point TEXT, description TEXT, sort_order INTEGER
            );
            CREATE TABLE chapter_summaries (
                id TEXT PRIMARY KEY, knowledge_id TEXT, chapter_number INTEGER, summary TEXT,
                key_events TEXT, open_threads TEXT, consistency_note TEXT, beat_sections TEXT, micro_beats TEXT, sync_status TEXT
            );
            CREATE TABLE triples (
                id TEXT PRIMARY KEY, novel_id TEXT, subject TEXT, predicate TEXT, object TEXT,
                chapter_number INTEGER, note TEXT, entity_type TEXT, importance TEXT, location_type TEXT,
                description TEXT, first_appearance INTEGER, confidence REAL, source_type TEXT,
                subject_entity_id TEXT, object_entity_id TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE storylines (
                id TEXT PRIMARY KEY, novel_id TEXT, storyline_type TEXT, status TEXT,
                estimated_chapter_start INTEGER, estimated_chapter_end INTEGER, current_milestone_index INTEGER,
                extensions TEXT, created_at TEXT, updated_at TEXT, name TEXT, description TEXT,
                last_active_chapter INTEGER, progress_summary TEXT
            );
            CREATE TABLE storyline_milestones (
                id TEXT PRIMARY KEY, storyline_id TEXT, milestone_order INTEGER, title TEXT, description TEXT,
                target_chapter_start INTEGER, target_chapter_end INTEGER, prerequisite_list TEXT, milestone_triggers TEXT
            );
            CREATE TABLE timeline_registries (novel_id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
            CREATE TABLE novel_foreshadow_registry (novel_id TEXT PRIMARY KEY, payload TEXT, updated_at TEXT);
            CREATE TABLE narrative_events (
                event_id TEXT PRIMARY KEY, novel_id TEXT, chapter_number INTEGER, event_summary TEXT,
                mutations TEXT, tags TEXT, timestamp_ts TEXT
            );
            CREATE TABLE memory_engine_states (novel_id TEXT PRIMARY KEY, state_json TEXT, last_updated_chapter INTEGER);
            """
        )


def test_frontend_pressure_v2_seeds_identical_native_context_for_both_arms(tmp_path):
    db_path = tmp_path / "aitext.db"
    _create_frontend_v2_seed_schema(db_path)

    control_seed = seed_native_context_in_app_db(db_path, "frontend-v2-control-off-test", chapter_limit=2)
    experiment_seed = seed_native_context_in_app_db(db_path, "frontend-v2-experiment-on-test", chapter_limit=2)
    manifest = build_seed_manifest([control_seed, experiment_seed])
    gate = build_base_input_gate(
        manifest,
        [
            SimpleNamespace(novel_id="frontend-v2-control-off-test"),
            SimpleNamespace(novel_id="frontend-v2-experiment-on-test"),
        ],
    )

    assert manifest["base_input_gate"]["ok"] is True
    assert control_seed["seed_hash"] == experiment_seed["seed_hash"]
    assert control_seed["premise_hash"] == experiment_seed["premise_hash"]
    assert control_seed["chapter_outline_hash"] == experiment_seed["chapter_outline_hash"]
    assert control_seed["counts"]["bibles"] == 1
    assert control_seed["counts"]["bible_characters"] >= 3
    assert control_seed["counts"]["triples"] >= 3
    assert gate["ok"] is True
    assert "api_key" not in json.dumps(manifest, ensure_ascii=False)
    with sqlite3.connect(db_path) as conn:
        setting_types = {row[0] for row in conn.execute("SELECT DISTINCT setting_type FROM bible_world_settings")}
        seeded_chapters = {
            row[0] for row in conn.execute("SELECT DISTINCT chapter_number FROM triples")
        }
        timeline_payload = json.loads(
            conn.execute(
                "SELECT data FROM timeline_registries WHERE novel_id = ?",
                ("frontend-v2-control-off-test",),
            ).fetchone()[0]
        )
    assert setting_types == {"rule"}
    assert seeded_chapters == {None}
    assert timeline_payload["id"] == "timeline-frontend-v2-control-off-test"
    assert timeline_payload["novel_id"] == "frontend-v2-control-off-test"
    assert {event["timestamp_type"] for event in timeline_payload["events"]} == {"relative"}
    assert {event["chapter_number"] for event in timeline_payload["events"]} == {1}


def test_frontend_pressure_v2_create_via_home_ui_records_actual_novel_and_seed(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "aitext.db"
    _create_frontend_v2_seed_schema(db_path)
    (run_dir / "run_manifest.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    plans = build_arm_plan("xianxia-ui-create", calibration_chapters=0, formal_chapters=10)
    calls: list[dict[str, str]] = []

    def fake_http_json(method, url, payload=None, *, timeout=30):
        calls.append({"method": method, "url": url})
        if method == "GET" and "/api/v1/novels/" in url:
            novel_id = url.rsplit("/", 1)[-1]
            return {
                "id": novel_id,
                "title": "照影山疑案",
                "premise": EXPERIMENT_SPEC["premise"],
                "target_chapters": 10,
                "target_words_per_chapter": 2500,
            }
        return {"ok": True}

    def fake_ui_create(frontend_url, form, *, screenshot_dir, timeout_seconds):
        assert frontend_url == "http://127.0.0.1:3010"
        assert form["target_chapters"] == 10
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / "created.png"
        screenshot_path.write_text("fake screenshot", encoding="utf-8")
        return {
            "novel_id": "novel-created-by-home-ui",
            "api_response": {"id": "novel-created-by-home-ui"},
            "screenshot_path": str(screenshot_path),
            "final_url": f"{frontend_url}/book/novel-created-by-home-ui/workbench",
        }

    monkeypatch.setattr(frontend_pressure_v2, "http_json", fake_http_json)

    manifest = create_seeded_novels_via_home_ui(
        run_dir,
        plans,
        base_url="http://127.0.0.1:8005",
        frontend_url="http://127.0.0.1:3010",
        ui_create_func=fake_ui_create,
    )

    run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    created = run_manifest["novels"][0]
    assert manifest["creation_method"] == HOME_UI_CREATION_METHOD
    assert run_manifest["creation_method"] == HOME_UI_CREATION_METHOD
    assert created["novel_id"] == "novel-created-by-home-ui"
    assert created["planned_novel_id"] == plans[0].novel_id
    assert created["creation_method"] == HOME_UI_CREATION_METHOD
    assert created["ui_form"]["genre"] == "仙侠修真"
    assert created["ui_form"]["world_preset"] == "修仙风"
    assert manifest["seed_records"][0]["novel_id"] == "novel-created-by-home-ui"
    assert any(call["method"] == "PUT" and "/plugins/world_evolution_core/enabled" in call["url"] for call in calls)
    assert any(call["method"] == "PATCH" and "/auto-approve-mode" in call["url"] for call in calls)


def test_frontend_pressure_v2_browser_use_record_validates_actual_novel_and_seed(monkeypatch, tmp_path):
    run_dir = tmp_path / "run-browser"
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "aitext.db"
    _create_frontend_v2_seed_schema(db_path)
    (run_dir / "run_manifest.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    calls: list[dict[str, str]] = []

    def fake_http_json(method, url, payload=None, *, timeout=30):
        calls.append({"method": method, "url": url})
        if method == "GET" and "/api/v1/novels/" in url:
            return {
                "id": "novel-browser-use",
                "title": "照影山疑案",
                "premise": EXPERIMENT_SPEC["premise"],
                "target_chapters": 10,
                "target_words_per_chapter": 2500,
            }
        return {"ok": True}

    monkeypatch.setattr(frontend_pressure_v2, "http_json", fake_http_json)

    manifest = record_browser_use_created_novel(
        run_dir,
        novel_id="novel-browser-use",
        screenshot_path=str(run_dir / "screenshots" / "browser-use.png"),
        base_url="http://127.0.0.1:8005",
        frontend_url="http://127.0.0.1:3010",
    )

    run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    created = run_manifest["novels"][0]
    assert manifest["creation_method"] == BROWSER_USE_CREATION_METHOD
    assert run_manifest["creation_method"] == BROWSER_USE_CREATION_METHOD
    assert created["creation_method"] == BROWSER_USE_CREATION_METHOD
    assert created["browser_use"]["backend"] == "iab"
    assert created["browser_use"]["screenshot_path"].endswith("browser-use.png")
    assert created["ui_validation"]["ok"] is True
    assert created["ui_form"]["title"] == "照影山疑案"
    assert created["ui_form"]["target_chapters"] == 10
    assert manifest["seed_records"][0]["novel_id"] == "novel-browser-use"
    assert any(call["method"] == "PUT" and "/plugins/world_evolution_core/enabled" in call["url"] for call in calls)
    assert any(call["method"] == "PATCH" and "/auto-approve-mode" in call["url"] for call in calls)


def test_frontend_pressure_v2_browser_use_blocker_is_recorded(tmp_path):
    run_dir = tmp_path / "run-blocker"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    blocker = record_browser_use_blocker(run_dir)
    run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))

    assert blocker["blocked"] is True
    assert blocker["blocker"] == BROWSER_USE_BLOCKER
    assert run_manifest["creation_method"] == BROWSER_USE_CREATION_METHOD
    assert run_manifest["debug_only"] is True
    assert run_manifest["debug_only_reason"] == BROWSER_USE_BLOCKER
    assert (run_dir / "browser_use_blocker.json").exists()


def test_frontend_pressure_v2_browser_use_creation_record_flags_mismatched_ui_values():
    plan = build_arm_plan("browser-use-record", calibration_chapters=0, formal_chapters=10)[0]

    record = build_browser_use_creation_record(
        plan=plan,
        novel_id="novel-bad",
        novel_payload={
            "id": "novel-bad",
            "premise": "缺少主题",
            "target_chapters": 8,
            "target_words_per_chapter": 1800,
        },
        screenshot_path="/tmp/s.png",
    )

    assert record["creation_method"] == BROWSER_USE_CREATION_METHOD
    assert record["ui_validation"]["ok"] is False
    assert record["ui_validation"]["premise_contains_title"] is False


def test_frontend_pressure_v2_fk_gate_detects_bad_global_triple_anchor(tmp_path):
    db_path = tmp_path / "aitext.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE novels (id TEXT PRIMARY KEY);
            CREATE TABLE chapters (
                id TEXT PRIMARY KEY,
                novel_id TEXT NOT NULL,
                number INTEGER NOT NULL,
                FOREIGN KEY (novel_id) REFERENCES novels(id),
                UNIQUE(novel_id, number)
            );
            CREATE TABLE triples (
                id TEXT PRIMARY KEY,
                novel_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                chapter_number INTEGER,
                FOREIGN KEY (novel_id) REFERENCES novels(id),
                FOREIGN KEY (novel_id, chapter_number) REFERENCES chapters(novel_id, number) ON DELETE SET NULL
            );
            INSERT INTO novels (id) VALUES ('novel-v2');
            PRAGMA foreign_keys = OFF;
            INSERT INTO triples (id, novel_id, subject, predicate, object, chapter_number)
            VALUES ('bad-global', 'novel-v2', '黑匣子', '解锁规则', '每章一段', 0);
            """
        )

    gate = check_sandbox_foreign_keys(db_path)

    assert gate["ok"] is False
    assert gate["violation_count"] == 1
    assert gate["violations"][0]["table"] == "triples"


def test_knowledge_repository_treats_non_positive_chapter_as_global_fact():
    assert SqliteKnowledgeRepository._chapter_number_from_fact({"chapter_number": 0}) is None
    assert SqliteKnowledgeRepository._chapter_number_from_fact({"chapter_number": -1}) is None
    assert SqliteKnowledgeRepository._chapter_number_from_fact({"chapter_number": 2}) == 2


def test_frontend_pressure_v2_native_sync_health_reads_log_and_fk(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "data").mkdir()
    db_path = run_dir / "data" / "aitext.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("CREATE TABLE novels (id TEXT PRIMARY KEY)")
        conn.commit()
    (run_dir / "logs" / "aitext.log").write_text("all good", encoding="utf-8")

    ok = check_native_sync_health(run_dir)
    assert ok["ok"] is True

    (run_dir / "logs" / "aitext.log").write_text("FOREIGN KEY constraint failed", encoding="utf-8")
    bad = check_native_sync_health(run_dir)
    assert bad["ok"] is False
    assert bad["invalid_reasons"] == ["native_sync_log_errors"]


def test_frontend_pressure_v2_seeds_native_foreshadow_registry_payload(tmp_path):
    db_path = tmp_path / "aitext.db"
    _create_frontend_v2_seed_schema(db_path)
    novel_id = "frontend-v2-control-off-test"

    seed_native_context_in_app_db(db_path, novel_id, chapter_limit=2)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM novel_foreshadow_registry WHERE novel_id = ?",
            (novel_id,),
        ).fetchone()
    assert row is not None
    registry = ForeshadowingMapper.from_dict(json.loads(row[0]))

    assert registry.id == f"fr-{novel_id}"
    assert registry.novel_id.value == novel_id
    assert len(registry.get_unresolved()) == 2


def test_frontend_pressure_v2_seed_replaces_ui_created_unique_context(tmp_path):
    db_path = tmp_path / "aitext.db"
    _create_frontend_v2_seed_schema(db_path)
    novel_id = "browser-use-ui-created"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE UNIQUE INDEX ux_bibles_novel_id ON bibles(novel_id)")
        conn.execute(
            "INSERT INTO bibles (id, novel_id, schema_version, extensions) VALUES (?, ?, ?, ?)",
            ("ui-bible", novel_id, 1, "{}"),
        )
        conn.commit()

    seed = seed_native_context_in_app_db(db_path, novel_id, chapter_limit=2)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM bibles WHERE novel_id = ?", (novel_id,)).fetchall()
    assert seed["counts"]["bibles"] == 1
    assert len(rows) == 1
    assert rows[0][0] != "ui-bible"


def test_frontend_pressure_v2_seed_gate_compares_control_and_experiment_per_run_kind(tmp_path):
    db_path = tmp_path / "aitext.db"
    _create_frontend_v2_seed_schema(db_path)
    records = []
    for run_kind, chapter_limit in (("calibration", 2), ("formal", 10)):
        for arm in (ARM_CONTROL, ARM_EXPERIMENT):
            novel_id = f"frontend-v2-{run_kind}-{arm}-test"
            seed = seed_native_context_in_app_db(db_path, novel_id, chapter_limit=chapter_limit)
            seed.update({"run_kind": run_kind, "arm": arm, "chapter_count": chapter_limit})
            records.append(seed)

    manifest = build_seed_manifest(records)

    assert manifest["base_input_gate"]["ok"] is True
    assert manifest["chapter_outline_hash"] == ""
    assert manifest["base_input_gate"]["groups"]["calibration"]["ok"] is True
    assert manifest["base_input_gate"]["groups"]["formal"]["ok"] is True


def test_frontend_pressure_v2_macro_gate_requires_premise_and_positive_theme_hits(tmp_path):
    prompt_path = tmp_path / "prompt.json"
    output_path = tmp_path / "output.md"
    prompt_path.write_text(
        json.dumps(
            {
                "prompt": {
                    "system": "规划",
                    "user": (
                        EXPERIMENT_SPEC["premise"]
                        + " 类型：仙侠宗门悬疑群像。世界观：照影山/照影镜/禁地灵脉。"
                    ),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path.write_text("照影山、照影镜、禁地灵脉、林照夜、谢无咎和沈青蘅构成十章规划。", encoding="utf-8")
    record = {
        "novel_id": "frontend-v2-control-off-test",
        "phase": "chapter_outline_suggestion",
        "paths": {"prompt": str(prompt_path), "output": str(output_path)},
    }

    ok = evaluate_macro_planning_gate([record], novel_id="frontend-v2-control-off-test")
    assert ok["ok"] is True
    assert ok["premise_received"] is True
    assert ok["topic_alignment"] == "ok"

    output_path.write_text("故事转向无关的办公室争执和家庭琐事，没有核心线索推进。", encoding="utf-8")
    bad = evaluate_macro_planning_gate([record], novel_id="frontend-v2-control-off-test")
    assert bad["ok"] is False
    assert bad["topic_alignment"] == "needs_review"
    assert "macro_output_theme_hits_below_threshold" in bad["invalid_reasons"]


def test_frontend_pressure_v2_macro_gate_requires_positive_terms_in_prompt(tmp_path):
    prompt_path = tmp_path / "prompt.json"
    output_path = tmp_path / "output.md"
    prompt_path.write_text(
        json.dumps(
            {
                "prompt": {
                    "system": "规划",
                    "user": EXPERIMENT_SPEC["premise"],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path.write_text("照影山、照影镜、禁地和灵脉构成两章规划。", encoding="utf-8")
    record = {
        "novel_id": "frontend-v2-control-off-test",
        "phase": "chapter_outline_suggestion",
        "paths": {"prompt": str(prompt_path), "output": str(output_path)},
    }

    result = evaluate_macro_planning_gate([record], novel_id="frontend-v2-control-off-test")

    assert result["ok"] is False
    assert result["topic_alignment"] == "needs_review"
    assert "macro_prompt_missing_required_terms" in result["invalid_reasons"]


def test_frontend_pressure_v2_audit_gate_requires_files_per_novel(tmp_path):
    call_dir = tmp_path / "llm_calls" / "by_chapter" / ARM_CONTROL / "chapter_01" / "call"
    call_dir.mkdir(parents=True)
    for filename in ("prompt.json", "output.md", "usage.json", "chunks.jsonl"):
        (call_dir / filename).write_text("{}" if filename.endswith(".json") else "正文", encoding="utf-8")
    record = {
        "call_id": "call-1",
        "arm": ARM_CONTROL,
        "novel_id": "frontend-v2-control-off-test",
        "chapter_number": 1,
        "phase": "chapter_generation_beat",
        "stream": True,
        "paths": {
            "prompt": str(call_dir / "prompt.json"),
            "output": str(call_dir / "output.md"),
            "usage": str(call_dir / "usage.json"),
            "chunks": str(call_dir / "chunks.jsonl"),
        },
    }
    (tmp_path / "llm_calls" / "calls.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    ok = check_audit_completeness(tmp_path / "llm_calls", expected_novels={"frontend-v2-control-off-test": 1})
    assert ok["ok"] is True

    (call_dir / "usage.json").unlink()
    bad = check_audit_completeness(tmp_path / "llm_calls", expected_novels={"frontend-v2-control-off-test": 1})
    assert bad["ok"] is False
    assert bad["missing_files"][0]["kind"] == "usage"


def test_frontend_pressure_v2_audit_gate_allows_planning_calls_without_chapter(tmp_path):
    call_root = tmp_path / "llm_calls" / "by_chapter" / ARM_EXPERIMENT / "chapter_unknown"
    control_card_dir = call_root / "control-card"
    outline_dir = call_root / "outline"
    unknown_dir = call_root / "unknown"
    for call_dir in (control_card_dir, outline_dir, unknown_dir):
        call_dir.mkdir(parents=True)
        for filename in ("prompt.json", "output.md", "usage.json"):
            (call_dir / filename).write_text("{}" if filename.endswith(".json") else "正文", encoding="utf-8")

    records = [
        {
            "call_id": "control-card",
            "arm": ARM_EXPERIMENT,
            "novel_id": "frontend-v2-experiment-on-test",
            "chapter_number": None,
            "phase": "evolution_agent_control_card",
            "stream": False,
            "paths": {
                "prompt": str(control_card_dir / "prompt.json"),
                "output": str(control_card_dir / "output.md"),
                "usage": str(control_card_dir / "usage.json"),
            },
        },
        {
            "call_id": "outline",
            "arm": ARM_EXPERIMENT,
            "novel_id": "frontend-v2-experiment-on-test",
            "chapter_number": None,
            "phase": "chapter_outline_suggestion",
            "stream": False,
            "paths": {
                "prompt": str(outline_dir / "prompt.json"),
                "output": str(outline_dir / "output.md"),
                "usage": str(outline_dir / "usage.json"),
            },
        },
    ]
    calls_path = tmp_path / "llm_calls" / "calls.jsonl"
    calls_path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")

    ok = check_audit_completeness(tmp_path / "llm_calls")
    assert ok["ok"] is True
    assert ok["unexpected_unknown_chapter_calls"] == []

    records.append(
        {
            "call_id": "unknown",
            "arm": ARM_EXPERIMENT,
            "novel_id": "frontend-v2-experiment-on-test",
            "chapter_number": None,
            "phase": "unknown",
            "stream": False,
            "paths": {
                "prompt": str(unknown_dir / "prompt.json"),
                "output": str(unknown_dir / "output.md"),
                "usage": str(unknown_dir / "usage.json"),
            },
        }
    )
    calls_path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")

    bad = check_audit_completeness(tmp_path / "llm_calls")
    assert bad["ok"] is False
    assert bad["unexpected_unknown_chapter_calls"][0]["phase"] == "unknown"


def test_frontend_pressure_v2_audit_gate_allows_legacy_chapterless_act_summary(tmp_path):
    call_dir = tmp_path / "llm_calls" / "by_chapter" / "unknown" / "chapter_unknown" / "act-summary"
    call_dir.mkdir(parents=True)
    (call_dir / "prompt.json").write_text(
        json.dumps(
            {
                "prompt": {
                    "system": "你是一位专业的小说编辑，擅长提炼故事精华。你的任务是为一幕（Act）生成简洁的摘要。",
                    "user": "幕标题：噪声与徽章\n请生成这一幕的摘要。",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (call_dir / "output.md").write_text("", encoding="utf-8")
    (call_dir / "usage.json").write_text(json.dumps({"status": "error"}), encoding="utf-8")
    record = {
        "call_id": "chapter_generation_beat_na_legacy",
        "arm": "unknown",
        "novel_id": "",
        "chapter_number": None,
        "phase": "chapter_generation_beat",
        "stream": False,
        "paths": {
            "prompt": str(call_dir / "prompt.json"),
            "output": str(call_dir / "output.md"),
            "usage": str(call_dir / "usage.json"),
        },
    }
    (tmp_path / "llm_calls" / "calls.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    result = check_audit_completeness(tmp_path / "llm_calls")

    assert result["ok"] is True
    assert result["unexpected_unknown_chapter_calls"] == []
    assert result["allowed_chapterless_summary_calls"][0]["reason"] == "chapterless_act_volume_summary"


def test_frontend_pressure_v2_start_backend_detaches_process(tmp_path, monkeypatch):
    calls = []

    class FakeProcess:
        pid = 12345

    def fake_popen(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr("scripts.evaluation.evolution_frontend_pressure_v2.subprocess.Popen", fake_popen)

    proc = start_backend(tmp_path, port=8123)

    assert proc.pid == 12345
    assert calls[0]["kwargs"]["start_new_session"] is True
    assert calls[0]["kwargs"]["stdin"] is not None
    assert (tmp_path / "backend_process.json").exists()


def test_frontend_pressure_v2_topic_alignment_gate_stops_on_two_low_theme_chapters():
    result = evaluate_chapter_topic_alignment_series(
        [
            {"chapter_number": 1, "content": "陌生故事开场，没有照影山与照影镜。"},
            {"chapter_number": 2, "content": "人物继续闲谈，仍没有禁地和灵脉线。"},
        ]
    )

    assert result["should_stop"] is True
    assert "chapter_theme_hits_low_for_two_consecutive_chapters" in result["invalid_reasons"]


def test_frontend_pressure_v2_topic_alignment_gate_ignores_empty_draft_placeholders():
    chapters = chapters_for_topic_alignment_gate(
        [
            {"number": 1, "status": "completed", "content": "照影山里，林照夜带着账册追查照影镜血字。"},
            {"number": 2, "status": "completed", "content": "谢无咎和沈青蘅在丹峰发现安神丹与禁地灵脉线索。"},
            {"number": 3, "status": "draft", "content": ""},
            {"number": 4, "status": "draft", "content": "   "},
        ]
    )

    assert [chapter["chapter_number"] for chapter in chapters] == [1, 2]
    assert evaluate_chapter_topic_alignment_series(chapters)["ok"] is True


def test_frontend_pressure_v2_leakage_gate_requires_control_clean_and_experiment_active():
    clean_control = {"asset_counts": {"genes": 6}}
    clean_diag = {"context_budget_summary": {"api2_control_card_chars": 0}}
    active_experiment = {
        "asset_counts": {"events": 2, "reflections": 1},
        "agent_api_usage": {"aggregate": {"call_count": 1}},
        "plotpilot_context_usage": {"selection_count": 1},
    }

    ok = build_leakage_gate(
        control_agent_status=clean_control,
        experiment_agent_status=active_experiment,
        control_diagnostics=clean_diag,
        experiment_diagnostics=clean_diag,
    )
    assert ok["ok"] is True
    assert ok["checks"][0]["evidence"]["asset_counts"]["genes"] == 6
    assert "genes" not in ok["checks"][0]["evidence"]["active_asset_counts"]

    leaked = build_leakage_gate(
        control_agent_status={"asset_counts": {"events": 1}},
        experiment_agent_status=active_experiment,
        control_diagnostics=clean_diag,
        experiment_diagnostics=clean_diag,
    )
    assert leaked["ok"] is False
    assert "control_has_no_evolution_assets" in leaked["invalid_reasons"]


def test_frontend_pressure_v2_formal_acceptance_ignores_calibration_placeholders(tmp_path):
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "control_off.md").write_text("control", encoding="utf-8")
    (tmp_path / "exports" / "experiment_on.md").write_text("experiment", encoding="utf-8")
    (tmp_path / "chapter_topic_alignment_gate.json").write_text(
        json.dumps(
            {
                "items": {
                    "calib-control": {"chapter_count": 0, "ok": True},
                    "formal-control": {"chapter_count": 10, "ok": True},
                    "formal-experiment": {"chapter_count": 10, "ok": True},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plans = [
        SimpleNamespace(novel_id="formal-control", chapter_count=10),
        SimpleNamespace(novel_id="formal-experiment", chapter_count=10),
    ]

    result = build_formal_acceptance(
        run_dir=tmp_path,
        formal_plans=plans,
        audit_gate={"ok": True, "total_calls": 3},
        audit_manifest={"complete": True},
        leakage_gate={"ok": True},
        native_sync_gate={"ok": True},
        formal_macro={"formal-control": {"ok": True}, "formal-experiment": {"ok": True}},
        report_valid_experiment=True,
    )

    assert result["formal_valid_experiment"] is True
    assert result["formal_chapter_counts"] == {"formal-control": 10, "formal-experiment": 10}
    assert "calib-control" not in result["formal_macro_ok"]


def test_frontend_pressure_v2_quality_metrics_surface_repetition_and_palette_risks():
    control_quality = analyze_chapter_quality(
        [
            {"chapter_number": 1, "content": "照影山里，林照夜带着账册追查照影镜血字。"},
            {"chapter_number": 2, "content": "谢无咎和沈青蘅在丹峰发现安神丹与禁地灵脉线索。"},
        ]
    )
    experiment_quality = analyze_chapter_quality(
        [
            {"chapter_number": 1, "content": "照影山里，林照夜带着账册追查照影镜血字。林照夜没有说话。"},
            {"chapter_number": 2, "content": "谢无咎和沈青蘅在丹峰发现安神丹与禁地灵脉线索。谢无咎没有回答。"},
        ]
    )
    costs = build_cost_breakdown(
        [
            {
                "arm": "experiment_on",
                "phase": "evolution_agent_control_card",
                "prompt_chars": 100,
                "output_chars": 20,
                "token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        ]
    )
    risks = build_quality_residual_risks(
        control_quality=control_quality,
        experiment_quality=experiment_quality,
        palette_status={
            "missing": [
                {"name": "水箱", "missing_fields": ["base"], "source": "unspecified"},
                {"name": "林照夜", "missing_fields": ["base"], "source": "native_bible_derived"},
            ]
        },
        report_valid_experiment=True,
    )

    assert control_quality["chapter_count"] == 2
    assert experiment_quality["repetitive_phrase_total"] == 2
    assert costs["experiment_on"]["evolution_agent_control_card"]["total_tokens"] == 15
    assert any(risk["id"] == "evolution_non_character_palette_entities" for risk in risks)


def test_article_issue_report_deterministic_checks_flag_theme_rules_and_repetition():
    chapters = [
        {
            "chapter_number": 1,
            "content": "陌生都市开局。" * 120,
        },
        {
            "chapter_number": 2,
            "content": "照影山外门账房里，林照夜查账，照影镜血字仍在。林照夜击败筑基修士。没有说话。没有回答。沉默了几秒。",
        },
        {
            "chapter_number": 3,
            "content": "照影山禁地旁，谢无咎和沈青蘅查到安神丹药渣，照影镜照出阵痕。",
        },
    ]

    issues = deterministic_article_issues(chapters, expected_chapters=10)
    issue_types = {item["type"] for item in issues}

    assert "章节大纲偏离" in issue_types
    assert "题材/世界观漂移" in issue_types
    assert "境界规则冲突" in issue_types
    assert "重复套话" in issue_types
    assert any(item["chapter_number"] == 0 and item["severity"] == "critical" for item in issues)


def test_article_issue_report_parses_llm_json_and_normalizes_issue_types():
    parsed = parse_llm_issue_json(
        """
```json
{
  "issues": [
    {
      "type": "境界规则冲突",
      "severity": "high",
      "chapter_number": 2,
      "evidence": "林照夜击败筑基修士",
      "impact": "破坏境界规则",
      "suggestion": "改成借助阵法逃脱"
    }
  ],
  "overall_assessment": "主要问题是越级破局。"
}
```
"""
    )

    assert parsed is not None
    assert parsed["issues"][0]["type"] == "境界规则冲突"
    assert parsed["issues"][0]["source"] == "llm"
    assert parsed["overall_assessment"] == "主要问题是越级破局。"


def test_article_issue_report_separates_continuity_blocking_from_style_warning():
    issues = [
        {
            "type": "章节承接失败",
            "severity": "high",
            "chapter_number": 2,
            "evidence": "上一章地下大厅，下一章直接档案馆。",
        },
        {
            "issue_type": "evolution_style_drift",
            "constraint_type": "narrative_voice",
            "severity": "warning",
            "chapter_number": 8,
            "evidence": [{"similarity_score": 0.7}],
            "repair_hint": "保持句长和视角一致。",
            "confidence": 1.0,
        },
    ]

    summary = build_article_quality_summary(issues)

    assert issue_quality_category(issues[0]) == "continuity"
    assert issue_quality_category(issues[1]) == "style"
    assert summary["continuity_blocking_count"] == 1
    assert summary["style_warning_count"] == 1
    assert summary["style_needs_review_count"] == 0


def test_article_issue_report_writes_json_and_markdown(tmp_path):
    run_dir = tmp_path / "run"
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "aitext.db"
    novel_id = "frontend-v2-experiment-on-test"
    content = "照影山外门账房里，林照夜查账，照影镜血字仍在，谢无咎和沈青蘅追查安神丹。" * 80
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE chapters (novel_id TEXT, number INTEGER, title TEXT, content TEXT)")
        for chapter_number in range(1, 11):
            conn.execute(
                "INSERT INTO chapters VALUES (?, ?, ?, ?)",
                (novel_id, chapter_number, f"第{chapter_number}章", content),
            )
        conn.commit()

    report = build_article_issue_report(run_dir, novel_id, no_llm=True)
    json_path, md_path = write_article_issue_report(run_dir, report)

    assert report["chapter_count"] == 10
    assert report["llm_review"]["status"] == "not_run"
    assert "continuity_blocking_count" in report["summary"]
    assert "style_warning_count" in report["summary"]
    assert json_path.exists()
    assert md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["novel_id"] == novel_id


def test_frontend_pressure_v2_boundary_revision_metrics_count_events_and_rewrite_cost():
    summary = summarize_boundary_revision(
        audit_records=[
            {
                "arm": "experiment_on",
                "phase": "hosted_write_boundary_revision",
                "call_id": "call-1",
                "prompt_chars": 1200,
                "output_chars": 180,
                "token_usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140},
            },
            {
                "arm": "experiment_on",
                "phase": "chapter_generation_stream",
                "call_id": "call-2",
                "token_usage": {"total_tokens": 999},
            },
        ],
        experiment_diagnostics={
            "boundary_continuity_summary": {
                "boundary_injected_count": 9,
                "boundary_failed_count": 2,
                "boundary_revision_required_count": 1,
            }
        },
        events=[
            {"type": "boundary_revision_start", "chapter": 2},
            {"type": "boundary_revision_applied", "chapter": 2},
            {"type": "boundary_revision_required", "chapter": 6, "reason": "recheck_still_failed"},
            {"type": "boundary_revision_skipped", "chapter": 7, "reason": "no_boundary_revision_required"},
        ],
    )

    assert summary["target"]["met"] is True
    assert summary["diagnostics"]["boundary_failed_count"] == 2
    assert summary["sse_events"]["counts"]["boundary_revision_applied"] == 1
    assert summary["sse_events"]["applied_chapters"] == [2]
    assert summary["sse_events"]["required_reason_counts"] == {"recheck_still_failed": 1}
    assert summary["rewrite_llm"]["call_count"] == 1
    assert summary["rewrite_llm"]["total_tokens"] == 140
    assert summary["audit_call_ids"] == ["call-1"]
