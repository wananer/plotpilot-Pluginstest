import json
from types import SimpleNamespace

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
