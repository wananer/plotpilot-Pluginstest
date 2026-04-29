import json
import sqlite3
from types import SimpleNamespace

from scripts.evaluation.evolution_frontend_pressure_v2 import (
    ARM_CONTROL,
    ARM_EXPERIMENT,
    build_base_input_gate,
    build_leakage_gate,
    build_seed_manifest,
    chapters_for_drift_gate,
    check_audit_completeness,
    evaluate_chapter_drift_series,
    evaluate_macro_planning_gate,
    seed_native_context_in_app_db,
    start_backend,
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
        query="黑匣子 圣像 顾岚",
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
        seeded_sql = "\n".join(conn.iterdump())
        setting_types = {row[0] for row in conn.execute("SELECT DISTINCT setting_type FROM bible_world_settings")}
    assert setting_types == {"rule"}
    for drift_term in ("退婚", "修仙", "灵根", "宗门", "仙尊", "丹田"):
        assert drift_term not in seeded_sql


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


def test_frontend_pressure_v2_macro_gate_requires_premise_and_rejects_drift(tmp_path):
    prompt_path = tmp_path / "prompt.json"
    output_path = tmp_path / "output.md"
    prompt_path.write_text(
        json.dumps(
            {
                "prompt": {
                    "system": "规划",
                    "user": (
                        EXPERIMENT_SPEC["premise"]
                        + " 类型：近未来悬疑群像。世界观：海上城邦/财阀学院/旧AI遗迹。"
                    ),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path.write_text("雾港、黑匣子、坠塔、圣像和沈砚/顾岚/陆行舟构成十章规划。", encoding="utf-8")
    record = {
        "novel_id": "frontend-v2-control-off-test",
        "phase": "chapter_outline_suggestion",
        "paths": {"prompt": str(prompt_path), "output": str(output_path)},
    }

    ok = evaluate_macro_planning_gate([record], novel_id="frontend-v2-control-off-test")
    assert ok["ok"] is True
    assert ok["premise_received"] is True

    output_path.write_text("退婚后他测出灵根，进入宗门修仙。", encoding="utf-8")
    bad = evaluate_macro_planning_gate([record], novel_id="frontend-v2-control-off-test")
    assert bad["ok"] is False
    assert "macro_drift_terms_present" in bad["invalid_reasons"]


def test_frontend_pressure_v2_macro_gate_rejects_drift_in_prompt_even_when_output_is_clean(tmp_path):
    prompt_path = tmp_path / "prompt.json"
    output_path = tmp_path / "output.md"
    prompt_path.write_text(
        json.dumps(
            {
                "prompt": {
                    "system": "精通退婚流和克苏鲁修仙等爆款套路。",
                    "user": (
                        EXPERIMENT_SPEC["premise"]
                        + " 类型：近未来悬疑群像。世界观：海上城邦/财阀学院/旧AI遗迹。"
                    ),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path.write_text("雾港、黑匣子、坠塔、圣像和旧AI构成两章规划。", encoding="utf-8")
    record = {
        "novel_id": "frontend-v2-control-off-test",
        "phase": "chapter_outline_suggestion",
        "paths": {"prompt": str(prompt_path), "output": str(output_path)},
    }

    result = evaluate_macro_planning_gate([record], novel_id="frontend-v2-control-off-test")

    assert result["ok"] is False
    assert result["drift_hits"] == ["修仙", "退婚"]
    assert "macro_drift_terms_present" in result["invalid_reasons"]


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


def test_frontend_pressure_v2_chapter_drift_gate_stops_on_two_low_theme_chapters():
    result = evaluate_chapter_drift_series(
        [
            {"chapter_number": 1, "content": "陌生故事开场，没有雾港与黑匣子。"},
            {"chapter_number": 2, "content": "人物继续闲谈，仍没有旧AI和坠塔线。"},
        ]
    )

    assert result["should_stop"] is True
    assert "chapter_theme_hits_low_for_two_consecutive_chapters" in result["invalid_reasons"]


def test_frontend_pressure_v2_chapter_drift_gate_ignores_empty_draft_placeholders():
    chapters = chapters_for_drift_gate(
        [
            {"number": 1, "status": "completed", "content": "雾港里，沈砚带着黑匣子追查坠塔旧案。"},
            {"number": 2, "status": "completed", "content": "顾岚和陆行舟在财阀学院发现旧AI圣像线索。"},
            {"number": 3, "status": "draft", "content": ""},
            {"number": 4, "status": "draft", "content": "   "},
        ]
    )

    assert [chapter["chapter_number"] for chapter in chapters] == [1, 2]
    assert evaluate_chapter_drift_series(chapters)["ok"] is True


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
