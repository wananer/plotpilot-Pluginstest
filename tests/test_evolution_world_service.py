import json
import sqlite3
import time

import pytest

from domain.ai.services.llm_service import GenerationResult
from domain.ai.value_objects.token_usage import TokenUsage
from plugins.world_evolution_core.agent_assets import select_agent_assets
from plugins.world_evolution_core.continuity import analyze_chapter_transitions
from plugins.world_evolution_core.extractor import extract_chapter_facts
from plugins.world_evolution_core import service as evolution_service_module
from plugins.world_evolution_core.host_context import HostContextReader
from plugins.world_evolution_core.service import EvolutionWorldAssistantService
from plugins.world_evolution_core.local_semantic_memory import LocalSemanticMemory
from plugins.world_evolution_core.structured_extractor import LLMStructuredExtractorProvider
from plugins.platform.host_database import ReadOnlyHostDatabase
from plugins.platform.job_registry import PluginJobRegistry
from plugins.platform.plugin_storage import PluginStorage


class FakeControlCardLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, config):
        self.calls.append({"prompt": prompt, "config": config})
        return GenerationResult(
            content="【承接】沈砚已经在C307内部。\n【禁写】不要重复进入C307，不要使用没有说话。",
            token_usage=TokenUsage(input_tokens=123, output_tokens=45),
        )

    async def stream_generate(self, prompt, config):
        yield "unused"


class FakeConnectionLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, config):
        self.calls.append({"prompt": prompt, "config": config})
        return GenerationResult(
            content="OK",
            token_usage=TokenUsage(input_tokens=3, output_tokens=1),
        )

    async def stream_generate(self, prompt, config):
        yield "unused"


class FakeSemanticMemory:
    def __init__(self):
        self.calls = []

    def search(self, novel_id, query, *, before_chapter=None, limit=8):
        self.calls.append(
            {
                "novel_id": novel_id,
                "query": query,
                "before_chapter": before_chapter,
                "limit": limit,
            }
        )
        return {
            "source": "local_vector",
            "vector_enabled": True,
            "items": [
                {
                    "source_type": "triple_vector",
                    "chapter_number": 1,
                    "text": "林澈 —持有→ 黑色钥匙；钥匙只能响应黑塔密门",
                    "score": 0.91,
                }
            ],
        }


class SlowHostContextReader:
    def read(self, *_args, **_kwargs):
        time.sleep(0.05)
        return {"source": "too_slow"}

    def summary(self, context):
        return HostContextReader().summary(context)


class SlowSemanticMemory:
    def search(self, *_args, **_kwargs):
        time.sleep(0.05)
        return {"source": "too_slow", "vector_enabled": True, "items": [{"source_type": "slow"}]}


class FakeEmbeddingService:
    def get_dimension(self):
        return 3

    async def embed(self, text):
        return [1.0, 0.0, 0.0]


class FakeVectorStore:
    def __init__(self):
        self.calls = []

    async def search(self, collection, query_vector, limit):
        self.calls.append({"collection": collection, "query_vector": query_vector, "limit": limit})
        if collection.endswith("_chunks"):
            return [
                {
                    "score": 0.88,
                    "payload": {
                        "kind": "chapter_summary",
                        "chapter_number": 1,
                        "text": "上一章林澈把黑色钥匙带进黑塔。",
                    },
                }
            ]
        if collection.endswith("_triples"):
            return [
                {
                    "score": 0.93,
                    "payload": {
                        "triple_id": "t-1",
                        "subject": "黑色钥匙",
                        "predicate": "开启",
                        "object": "黑塔密门",
                        "text": "黑色钥匙开启黑塔密门",
                        "chapter_number": 1,
                    },
                }
            ]
        return []


class FakePaletteLLM:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, config):
        self.calls.append({"prompt": prompt, "config": config})
        return GenerationResult(
            content=json.dumps(
                {
                    "summary": "测试角色甲拆开旧式门锁。",
                    "characters": [
                        {
                            "name": "测试角色甲",
                            "summary": "拆开旧式门锁时表现出谨慎和固执。",
                            "personality_palette": {
                                "metaphor": "人的性格像调色盘。",
                                "base": "谨慎",
                                "main_tones": ["固执"],
                                "accents": ["好奇"],
                                "derivatives": [
                                    {
                                        "tone": "固执",
                                        "title": "反复验证",
                                        "description": "遇到异常门锁时会反复验证，不轻易接受第一结论。",
                                        "trigger": "线索不闭合时",
                                    }
                                ],
                            },
                        }
                    ],
                    "locations": ["C307"],
                    "world_events": [{"summary": "测试角色甲拆开C307旧式门锁", "characters": ["测试角色甲"], "locations": ["C307"]}],
                },
                ensure_ascii=False,
            ),
            token_usage=TokenUsage(input_tokens=80, output_tokens=40),
        )

    async def stream_generate(self, prompt, config):
        yield "unused"


def test_local_semantic_memory_searches_chunk_and_triple_vectors():
    vector_store = FakeVectorStore()
    memory = LocalSemanticMemory(
        vector_store=vector_store,
        embedding_service=FakeEmbeddingService(),
    )

    result = memory.search("novel-semantic", "林澈打开黑塔密门", before_chapter=2, limit=4)

    assert result["source"] == "local_vector"
    assert result["vector_enabled"] is True
    assert [call["collection"] for call in vector_store.calls][:2] == [
        "novel_novel-semantic_chunks",
        "novel_novel-semantic_triples",
    ]
    assert "novel_novel-semantic_world" in [call["collection"] for call in vector_store.calls]
    assert "novel_novel-semantic_foreshadows" in [call["collection"] for call in vector_store.calls]
    texts = [item["text"] for item in result["items"]]
    assert "上一章林澈把黑色钥匙带进黑塔。" in texts
    assert "黑色钥匙开启黑塔密门" in texts


@pytest.mark.asyncio
async def test_after_commit_writes_facts_characters_and_context_block(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.after_commit(
        {
            "novel_id": "novel-1",
            "chapter_number": 1,
            "payload": {"content": "《林澈》抵达雾城，并见到了失踪多年的导师。导师发现城门外爆发袭击。"},
        }
    )

    assert result["ok"] is True
    facts = storage.read_json(
        "world_evolution_core",
        ["novels", "novel-1", "facts", "chapter_1.json"],
    )
    assert facts["chapter_number"] == 1
    assert "林澈" in facts["characters"]
    assert "雾城" in facts["locations"]

    characters = service.list_characters("novel-1")
    assert characters["items"][0]["name"] == "林澈"

    context = service.before_context_build({"novel_id": "novel-1", "chapter_number": 2})
    assert context["ok"] is True
    content = context["context_blocks"][0]["content"]
    assert "本章焦点角色" in content
    assert "林澈" in content
    assert "《林澈》" not in content
    assert "雾城" in content
    patch = context["context_patch"]
    assert patch["merge_strategy"] == "append_by_priority"
    assert patch["estimated_token_budget"] > 0
    assert [block["id"] for block in patch["blocks"]][:4] == [
        "evolution_usage_protocol",
        "chapter_state_bridge",
        "focus_characters",
        "recent_facts",
    ]
    assert patch["blocks"][1]["kind"] == "chapter_state_bridge"
    assert patch["blocks"][2]["kind"] == "focus_character_state"
    assert "上一章小总结" in content
    assert "下一章开头必须承接上一章结尾" in content
    assert service.repository.list_agent_events("novel-1")[-1]["intent"] == "inject"


def test_agent_default_genes_are_available_and_isolated_by_novel(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    genes_a = service.repository.list_agent_genes("novel-agent-a")
    genes_b = service.repository.list_agent_genes("novel-agent-b")

    assert {item["id"] for item in genes_a} >= {
        "gene_chapter_bridge_continuity",
        "gene_route_conflict_guard",
        "gene_character_cognition_boundary",
    }
    service.repository.append_agent_capsule(
        "novel-agent-a",
        {
            "type": "Capsule",
            "id": "cap_local_only",
            "signals": ["route_conflict"],
            "guidance": "只属于 A 小说。",
            "updated_at": "2026-04-27T00:00:00+00:00",
        },
    )
    assert genes_b
    assert service.repository.list_agent_capsules("novel-agent-b") == []
    assert service.repository.list_agent_capsules("novel-agent-a")[0]["id"] == "cap_local_only"


@pytest.mark.asyncio
async def test_agent_selector_injects_strategy_block_from_signals(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.after_commit(
        {
            "novel_id": "novel-agent-context",
            "chapter_number": 1,
            "payload": {"content": "沈砚进入C307，结尾时仍在C307内部观察墙面划痕。"},
        }
    )
    context = service.before_context_build(
        {
            "novel_id": "novel-agent-context",
            "chapter_number": 2,
            "payload": {"outline": "承接上一章，沈砚继续在C307调查，不要重复进入。"},
        }
    )

    assert context["ok"] is True
    agent_block = next(block for block in context["context_patch"]["blocks"] if block["id"] == "evolution_agent_strategy")
    assert "章节承接" in agent_block["content"]
    assert "gene_chapter_bridge_continuity" in agent_block["items"]["selected_gene_ids"]
    status = service.get_agent_status("novel-agent-context")
    assert status["asset_counts"]["events"] >= 2
    assert status["latest_selection"]["selected_gene_ids"]


def test_agent_solidifies_review_issues_conservatively_and_dedupes(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    issue = {
        "issue_type": "evolution_character_cognition",
        "severity": "warning",
        "description": "林澈不知道钥匙会消耗记忆，但本章直接利用了这个信息。",
        "suggestion": "补充林澈如何得知或推断钥匙代价。",
        "evidence": [{"event_id": "evt-1", "summary": "林澈并不知道钥匙会消耗记忆"}],
    }

    first = service.after_chapter_review(
        {
            "novel_id": "novel-agent-solidify",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": [issue, {**issue, "severity": "suggestion"}]}},
        }
    )
    second = service.after_chapter_review(
        {
            "novel_id": "novel-agent-solidify",
            "chapter_number": 3,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )

    assert len(first["data"]["solidified_capsules"]) == 1
    assert len(second["data"]["solidified_capsules"]) == 1
    capsules = service.repository.list_agent_capsules("novel-agent-solidify")
    assert len(capsules) == 1
    assert capsules[0]["success_count"] == 2
    assert capsules[0]["signals"] == ["review_feedback", "character_cognition", "knowledge_boundary"]
    assert service.get_agent_status("novel-agent-solidify")["latest_solidified"]


def test_agent_evaluates_selected_strategy_after_review(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.append_agent_selection_record(
        "novel-agent-eval",
        {
            "id": "sel-1",
            "chapter_number": 2,
            "selected_gene_ids": ["gene_route_conflict_guard"],
            "selected_capsule_ids": [],
        },
    )
    issue = {
        "issue_type": "evolution_route_repeated_arrival",
        "severity": "warning",
        "description": "第2章重复进入C307。",
        "suggestion": "补足离开和再次抵达。",
        "evidence": [{"current_opening": "沈砚重新进入C307。"}],
    }

    service.after_chapter_review(
        {
            "novel_id": "novel-agent-eval",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )

    route_gene = next(item for item in service.repository.list_agent_genes("novel-agent-eval") if item["id"] == "gene_route_conflict_guard")
    assert route_gene["hit_count"] == 1
    assert route_gene["failure_count"] == 1
    status = service.get_agent_status("novel-agent-eval")
    assert any(event["intent"] == "evaluate" for event in status["latest_learning"])


def test_agent_gene_positive_measurement_rewards_protection(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.append_agent_selection_record(
        "novel-agent-positive",
        {
            "id": "sel-positive",
            "chapter_number": 2,
            "selected_gene_ids": ["gene_route_conflict_guard"],
            "selected_capsule_ids": [],
        },
    )

    service.after_chapter_review(
        {
            "novel_id": "novel-agent-positive",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": []}},
        }
    )

    route_gene = next(item for item in service.repository.list_agent_genes("novel-agent-positive") if item["id"] == "gene_route_conflict_guard")
    assert route_gene["hit_count"] == 1
    assert route_gene["protected_count"] == 1
    assert route_gene["helpful_count"] == 1
    assert route_gene["positive_score"] == 3
    assert "有效保护" in route_gene["last_positive_reason"]
    event = next(event for event in service.repository.list_agent_events("novel-agent-positive") if event["intent"] == "evaluate")
    assert event["outcome"]["protected"] == ["gene_route_conflict_guard"]
    assert event["outcome"]["helpful"] == ["gene_route_conflict_guard"]


def test_agent_gene_single_failure_does_not_clear_positive_score(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.save_agent_genes(
        "novel-agent-positive-failure",
        [
            {
                "type": "Gene",
                "id": "gene_route_conflict_guard",
                "category": "route",
                "title": "路线冲突守卫",
                "signals_match": ["route_conflict", "location_jump", "repeat_entry"],
                "strategy": ["移动必须有路线、时间消耗或明确省略。"],
                "priority": 90,
                "positive_score": 9,
                "protected_count": 2,
                "helpful_count": 1,
            }
        ],
    )
    service.repository.append_agent_selection_record(
        "novel-agent-positive-failure",
        {
            "id": "sel-positive-failure",
            "chapter_number": 2,
            "selected_gene_ids": ["gene_route_conflict_guard"],
            "selected_capsule_ids": [],
        },
    )
    issue = {
        "issue_type": "evolution_route_repeated_arrival",
        "severity": "warning",
        "description": "第2章重复进入C307。",
        "suggestion": "补足离开和再次抵达。",
        "evidence": [{"current_opening": "沈砚重新进入C307。"}],
    }

    service.after_chapter_review(
        {
            "novel_id": "novel-agent-positive-failure",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )

    route_gene = next(item for item in service.repository.list_agent_genes("novel-agent-positive-failure") if item["id"] == "gene_route_conflict_guard")
    assert route_gene["failure_count"] == 1
    assert route_gene["positive_score"] == 8
    assert route_gene["protected_count"] == 2
    assert "仍需增强" in route_gene["last_improvement_advice"]


def test_agent_selector_prefers_positive_gene_over_single_failure():
    genes = [
        {
            "id": "gene_a",
            "title": "A",
            "signals_match": ["route_conflict"],
            "strategy": ["A"],
            "priority": 50,
            "failure_count": 1,
            "positive_score": 6,
            "protected_count": 2,
            "helpful_count": 1,
        },
        {
            "id": "gene_b",
            "title": "B",
            "signals_match": ["route_conflict"],
            "strategy": ["B"],
            "priority": 50,
            "failure_count": 0,
        },
    ]

    selection = select_agent_assets(
        novel_id="novel-agent-selector-positive",
        chapter_number=3,
        signals=["route_conflict"],
        genes=genes,
        capsules=[],
        max_genes=1,
    )

    assert selection["selected_gene_ids"] == ["gene_a"]


def test_agent_reflections_and_candidates_are_isolated_by_novel(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.append_agent_reflection("novel-agent-a", {"id": "ref-a", "problem_pattern": "A"})
    service.repository.append_agent_gene_candidate("novel-agent-a", {"id": "genc-a", "status": "pending_review"})

    assert service.repository.list_agent_reflections("novel-agent-b") == []
    assert service.repository.list_agent_gene_candidates("novel-agent-b") == []
    assert service.repository.list_agent_reflections("novel-agent-a")[0]["id"] == "ref-a"
    assert service.repository.list_agent_gene_candidates("novel-agent-a")[0]["id"] == "genc-a"


def test_agent_review_writes_fallback_reflection_and_memory_index(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    issue = {
        "issue_type": "evolution_character_cognition",
        "severity": "warning",
        "description": "林澈不知道钥匙代价，但本章直接利用该信息。",
        "suggestion": "补充林澈如何得知或推断。",
        "evidence": [{"event_id": "evt-1", "summary": "林澈不知道钥匙代价"}],
    }

    result = service.after_chapter_review(
        {
            "novel_id": "novel-agent-reflection",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )

    reflection = result["data"]["reflection"]
    assert reflection["type"] == "Reflection"
    assert reflection["source"] == "deterministic_fallback"
    assert reflection["next_chapter_constraints"]
    reflections = service.repository.list_agent_reflections("novel-agent-reflection")
    assert reflections[0]["id"] == reflection["id"]
    memory_index = service.repository.get_agent_memory_index("novel-agent-reflection")
    assert memory_index["summary"]["reflections"] == 1
    status = service.get_agent_status("novel-agent-reflection")
    assert status["asset_counts"]["reflections"] == 1
    assert status["memory_layers"]["reflective"] == 1


def test_repeated_capsules_generate_pending_gene_candidate(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    issue = {
        "issue_type": "evolution_route_repeated_arrival",
        "severity": "warning",
        "description": "第2章重复进入C307。",
        "suggestion": "下一章必须承接上一章终点，若再次抵达必须补足离开过程。",
        "evidence": [{"current_opening": "沈砚重新进入C307。"}],
    }

    service.after_chapter_review(
        {
            "novel_id": "novel-agent-candidate",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )
    first_candidates = service.repository.list_agent_gene_candidates("novel-agent-candidate")
    assert first_candidates == []

    result = service.after_chapter_review(
        {
            "novel_id": "novel-agent-candidate",
            "chapter_number": 3,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )

    candidates = service.repository.list_agent_gene_candidates("novel-agent-candidate")
    assert len(candidates) == 1
    assert candidates[0]["type"] == "GeneCandidate"
    assert candidates[0]["status"] == "pending_review"
    assert "chapter_bridge" in candidates[0]["signals_match"]
    assert result["data"]["gene_candidates"][0]["id"] == candidates[0]["id"]
    genes = service.repository.list_agent_genes("novel-agent-candidate")
    assert all(gene["id"] != candidates[0]["id"] for gene in genes)
    status = service.get_agent_status("novel-agent-candidate")
    assert status["asset_counts"]["gene_candidates"] == 1
    assert status["memory_index_summary"]["gene_candidates"] == 1


@pytest.mark.asyncio
async def test_after_commit_builds_story_graph_routes_and_conflicts(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.after_commit(
        {
            "novel_id": "novel-routes",
            "chapter_number": 1,
            "payload": {"content": "沈砚进入C307，找到黑匣子。结尾时沈砚仍在C307内部观察墙面划痕。"},
        }
    )
    second = await service.after_commit(
        {
            "novel_id": "novel-routes",
            "chapter_number": 2,
            "payload": {"content": "沈砚推开C307的门，重新走进房间。他把黑匣子放在桌上。"},
        }
    )

    assert second["data"]["story_graph"]["route_edges"]
    route_map = service.get_global_route_map("novel-routes")
    assert route_map["aggregate"]["route_edge_count"] >= 2
    assert route_map["aggregate"]["hard_conflict_count"] >= 1
    assert any(item["type"] == "repeated_arrival" for item in route_map["conflicts"])
    assert route_map["vector_index"]["count"] >= 2
    assert route_map["nodes"]
    assert "人物路线与世界线图" in service.build_context_summary("novel-routes", 3)
    review_context = service.before_chapter_review(
        {"novel_id": "novel-routes", "chapter_number": 3, "payload": {"content": "沈砚继续检查C307。"}}
    )
    assert review_context["data"]["route_conflicts"]


@pytest.mark.asyncio
async def test_evolution_keeps_query_indexes_inside_plugin_state(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    for chapter in range(1, 18):
        await service.after_commit(
            {
                "novel_id": "novel-indexed",
                "chapter_number": chapter,
                "payload": {"content": f"《林澈》在雾城第{chapter}区记录黑塔线索。"},
            }
        )

    facts_index = storage.read_json("world_evolution_core", ["novels", "novel-indexed", "facts_index.json"])
    character_index = storage.read_json("world_evolution_core", ["novels", "novel-indexed", "characters_index.json"])

    assert [item["chapter_number"] for item in facts_index["items"]][-3:] == [15, 16, 17]
    assert character_index["items"][0]["name"] == "林澈"
    assert service.repository.list_fact_snapshots("novel-indexed", before_chapter=17, limit=5)[0]["chapter_number"] == 12
    assert service.repository.list_fact_snapshots("novel-indexed", before_chapter=17, limit=5)[-1]["chapter_number"] == 16
    assert service.repository.list_relevant_character_cards("novel-indexed", "林澈继续调查黑塔")["items"][0]["name"] == "林澈"


@pytest.mark.asyncio
async def test_after_commit_writes_chapter_and_volume_summaries(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    for chapter in range(1, 11):
        await service.after_commit(
            {
                "novel_id": "novel-summary",
                "chapter_number": chapter,
                "payload": {"content": f"《林澈》进入雾城第{chapter}区，发现黑塔线索。结尾时林澈留在黑塔门前，问题还没有答案。"},
            }
        )

    chapter_summaries = service.repository.list_chapter_summaries("novel-summary", limit=20)
    volume_summaries = service.repository.list_volume_summaries("novel-summary", limit=20)

    assert len(chapter_summaries) == 10
    assert chapter_summaries[-1]["carry_forward"]["required_next_bridge"]
    assert len(volume_summaries) == 1
    assert volume_summaries[0]["chapter_start"] == 1
    assert volume_summaries[0]["chapter_end"] == 10

    context = service.before_context_build({"novel_id": "novel-summary", "chapter_number": 11})
    content = context["context_blocks"][0]["content"]
    assert "最近10章大总结" in content
    assert "上一章小总结" in content
    assert "上一章结尾状态" in content


@pytest.mark.asyncio
async def test_context_patch_records_capsule_audit(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.after_commit(
        {
            "novel_id": "novel-capsule-audit",
            "chapter_number": 1,
            "payload": {"content": "《林澈》进入C307，找到黑匣子。结尾时林澈留在C307内部。"},
        }
    )

    context = service.before_context_build({"novel_id": "novel-capsule-audit", "chapter_number": 2})

    assert context["ok"] is True
    patch = context["context_patch"]
    assert patch["blocks"]
    for block in patch["blocks"]:
        assert block["capsule_id"].startswith("cap_")
        assert block["content_hash"].startswith("sha256:")
        assert block["semantic_key"]
        assert block["capsule"]["content_hash"] == block["content_hash"]

    record = context["context_injection_record"]
    assert record["selected_count"] == len(patch["blocks"])
    assert record["estimated_token_budget"] == patch["estimated_token_budget"]
    saved_records = service.repository.list_context_injection_records("novel-capsule-audit")
    assert saved_records[-1]["selected"][0]["content_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_context_patch_injects_local_semantic_memory(tmp_path):
    storage = PluginStorage(root=tmp_path)
    semantic_memory = FakeSemanticMemory()
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        semantic_memory=semantic_memory,
    )
    await service.after_commit(
        {
            "novel_id": "novel-local-memory",
            "chapter_number": 1,
            "payload": {"content": "《林澈》进入黑塔，拿到黑色钥匙。"},
        }
    )

    context = service.before_context_build(
        {
            "novel_id": "novel-local-memory",
            "chapter_number": 2,
            "payload": {"outline": "林澈用黑色钥匙尝试打开黑塔密门。"},
        }
    )

    assert semantic_memory.calls[-1]["before_chapter"] == 2
    block = next(block for block in context["context_patch"]["blocks"] if block["id"] == "local_semantic_memory")
    assert block["kind"] == "local_semantic_memory"
    assert "本地知识库/向量库" in block["content"]
    assert "钥匙只能响应黑塔密门" in block["content"]
    assert "本地语义记忆召回" in context["context_blocks"][0]["content"]


@pytest.mark.asyncio
async def test_context_patch_dedupes_stable_protocol_but_keeps_handoff(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.after_commit(
        {
            "novel_id": "novel-context-dedupe",
            "chapter_number": 1,
            "payload": {"content": "《沈砚》进入C307，拿起黑匣子。结尾时沈砚仍在C307内部观察墙面划痕。"},
        }
    )

    first = service.before_context_build({"novel_id": "novel-context-dedupe", "chapter_number": 2})
    second = service.before_context_build({"novel_id": "novel-context-dedupe", "chapter_number": 2})

    assert any(block["id"] == "evolution_usage_protocol" for block in first["context_patch"]["blocks"])
    assert not any(block["id"] == "evolution_usage_protocol" for block in second["context_patch"]["blocks"])
    assert any(block["id"] == "chapter_state_bridge" for block in second["context_patch"]["blocks"])
    assert "上一章结尾状态" in second["context_blocks"][0]["content"]
    assert any(item["reason"] == "stable_protocol_already_injected" for item in second["context_patch"]["skipped_blocks"])
    assert len(service.repository.list_context_injection_records("novel-context-dedupe")) == 2


@pytest.mark.asyncio
async def test_after_commit_extracts_unquoted_chinese_character_names(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.after_commit(
        {
            "novel_id": "novel-unquoted",
            "chapter_number": 1,
            "payload": {
                "content": (
                    "沈砚回到雾港学院。顾岚警告他别查坠塔事故，陆行舟登记他的临时访客权限。"
                    "顾珩站在走廊尽头看着黑匣子发热。"
                )
            },
        }
    )

    assert result["ok"] is True
    assert result["data"]["facts"]["characters"] == ["沈砚", "顾岚", "陆行舟", "顾珩"]
    cards = service.list_characters("novel-unquoted")["items"]
    assert {card["name"] for card in cards} == {"沈砚", "顾岚", "陆行舟", "顾珩"}


@pytest.mark.asyncio
async def test_extractor_does_not_include_motion_particles_in_names(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.after_commit(
        {
            "novel_id": "novel-name-particles",
            "chapter_number": 1,
            "payload": {"content": "顾岚从走廊来到C307门边，沈砚进入C307，陆行舟赶到走廊。"},
        }
    )

    assert "顾岚" in result["data"]["facts"]["characters"]
    assert "顾岚从" not in result["data"]["facts"]["characters"]

    follow_up = await service.after_commit(
        {
            "novel_id": "novel-name-particles",
            "chapter_number": 2,
            "payload": {"content": "沈砚留在走廊，继续记录黑匣子。"},
        }
    )
    assert "沈砚" in follow_up["data"]["facts"]["characters"]
    assert "沈砚留" not in follow_up["data"]["facts"]["characters"]

    state_follow_up = await service.after_commit(
        {
            "novel_id": "novel-name-particles",
            "chapter_number": 3,
            "payload": {
                "content": "结尾时沈砚仍站在C307门内，陆行舟已经抵达走廊，顾岚却没有说话。"
            },
        }
    )
    names = state_follow_up["data"]["facts"]["characters"]
    assert "沈砚" in names
    assert "陆行舟" in names
    assert "顾岚" in names
    assert "沈砚仍" not in names
    assert "陆行舟已" not in names
    assert "顾岚却" not in names


@pytest.mark.asyncio
async def test_extractor_filters_phrase_and_title_noise_from_character_cards(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.after_commit(
        {
            "novel_id": "novel-name-noise",
            "chapter_number": 1,
            "payload": {
                "content": (
                    "《云海导航基础教程》被压在桌角，旁边写着《文字》两个字。"
                    "云逸没说话，林晚星知道前哨站的暗号，阿铁点头，顾青崖拦住了门。"
                    "旁白写道《很聪明》《也知道》《真正想》《说得对》都不是人物。"
                )
            },
        }
    )

    names = result["data"]["facts"]["characters"]
    assert set(names) == {"云逸", "林晚星", "阿铁", "顾青崖"}
    cards = service.list_characters("novel-name-noise")["items"]
    assert {card["name"] for card in cards} == {"云逸", "林晚星", "阿铁", "顾青崖"}


@pytest.mark.asyncio
async def test_character_pollution_is_marked_but_hidden_from_main_cards(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.after_commit(
        {
            "novel_id": "novel-pollution-hidden",
            "chapter_number": 1,
            "payload": {"content": "《沈砚》检查金属牌方向，章节标题写着记忆真相。"},
        }
    )

    assert "沈砚" in result["data"]["facts"]["characters"]
    assert "金属牌" not in result["data"]["facts"]["characters"]
    assert "章节标题" not in result["data"]["facts"]["characters"]
    service.repository.write_character_card(
        "novel-pollution-hidden",
        {"name": "查询记录", "status": "invalid_entity", "entity_type": "non_person", "last_seen_chapter": 1},
    )

    assert {card["name"] for card in service.list_characters("novel-pollution-hidden")["items"]} == {"沈砚"}
    all_names = {card["name"] for card in service.repository.list_all_character_cards("novel-pollution-hidden")["items"]}
    assert "查询记录" in all_names

    diagnostics = service.get_diagnostics("novel-pollution-hidden")
    pollution_risk = next(item for item in diagnostics["risks"] if item["affected_feature"] == "character_cards")
    assert pollution_risk["evidence"]["invalid_entities"][0]["name"] == "查询记录"


@pytest.mark.asyncio
async def test_agent_api_control_card_setting_compresses_context_inside_evolution(tmp_path):
    storage = PluginStorage(root=tmp_path)
    fake_llm = FakeControlCardLLM()
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        agent_llm_service=fake_llm,
    )
    saved = service.update_settings(
        {
            "api2_control_card": {"enabled": True},
            "agent_api": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {
                    "protocol": "openai",
                    "base_url": "https://api.example.test/v1",
                    "api_key": "secret",
                    "model": "agent-model",
                    "temperature": 0.1,
                    "max_tokens": 900,
                },
            },
        }
    )

    assert saved["api2_control_card"]["enabled"] is True
    assert saved["agent_api"]["enabled"] is True
    assert saved["agent_api"]["custom_profile"]["api_key"] == ""
    assert saved["agent_api"]["custom_profile"]["api_key_configured"] is True

    await service.after_commit(
        {
            "novel_id": "novel-agent-control-card",
            "chapter_number": 1,
            "payload": {"content": "沈砚进入C307，拿起黑匣子。结尾时沈砚仍在C307内部观察墙面划痕。"},
        }
    )
    context = service.before_context_build(
        {
            "novel_id": "novel-agent-control-card",
            "chapter_number": 2,
            "payload": {"outline": "沈砚继续调查C307内部的划痕。"},
        }
    )

    block = context["context_blocks"][0]
    assert block["title"] == "Evolution 智能体写作控制卡"
    assert "沈砚已经在C307内部" in block["content"]
    assert "不要重复进入C307" in block["content"]
    assert block["metadata"]["api2_control_card_enabled"] is False
    assert block["metadata"]["agent_control_card_enabled"] is True
    assert block["metadata"]["agent_provider_mode"] == "custom"
    assert fake_llm.calls
    assert "智能体控制卡" in fake_llm.calls[0]["prompt"].user
    records = service.repository.list_context_control_card_records("novel-agent-control-card")
    assert records[-1]["provider_mode"] == "custom"
    assert records[-1]["source"] == "agent_api"
    assert records[-1]["token_usage"]["total_tokens"] == 168


@pytest.mark.asyncio
async def test_api2_control_card_setting_no_longer_compresses_context(tmp_path):
    storage = PluginStorage(root=tmp_path)
    fake_llm = FakeControlCardLLM()
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
    )
    service.update_settings(
        {
            "api2_control_card": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {
                    "protocol": "openai",
                    "base_url": "https://api.example.test/v1",
                    "api_key": "secret",
                    "model": "api2-model",
                    "temperature": 0.1,
                    "max_tokens": 900,
                },
            },
            "agent_api": {"enabled": False},
        }
    )

    await service.after_commit(
        {
            "novel_id": "novel-api2",
            "chapter_number": 1,
            "payload": {"content": "沈砚进入C307，拿起黑匣子。结尾时沈砚仍在C307内部观察墙面划痕。"},
        }
    )
    context = service.before_context_build(
        {
            "novel_id": "novel-api2",
            "chapter_number": 2,
            "payload": {"outline": "沈砚继续调查C307内部的划痕。"},
        }
    )

    block = context["context_blocks"][0]
    assert block["title"] == "Evolution World State"
    assert block["metadata"]["api2_control_card_enabled"] is False
    assert block["metadata"]["agent_control_card_enabled"] is False
    assert not fake_llm.calls
    records = service.repository.list_context_control_card_records("novel-api2")
    assert records == []


@pytest.mark.asyncio
async def test_legacy_api2_model_fetch_is_deprecated_and_does_not_call_provider(tmp_path, monkeypatch):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.update_settings(
        {
            "api2_control_card": {
                "provider_mode": "custom",
                "custom_profile": {
                    "protocol": "openai",
                    "base_url": "https://api.old.example/v1",
                    "api_key": "stored-secret",
                },
            }
        }
    )

    async def fake_fetch_model_items(request):
        raise AssertionError("legacy API2 must not fetch models")

    monkeypatch.setattr(evolution_service_module, "_fetch_model_list_items", fake_fetch_model_items)

    result = await service.fetch_api2_models(
        {
            "api2_control_card": {
                "provider_mode": "custom",
                "custom_profile": {
                    "protocol": "openai",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "typed-secret",
                    "model": "deepseek-chat",
                },
            },
            "timeout_ms": 60000,
        }
    )

    assert result["ok"] is False
    assert result["deprecated"] is True
    assert result["replacement"] == "agent_api"
    assert result["items"] == []
    assert "stored-secret" not in str(result)
    assert "typed-secret" not in str(result)


@pytest.mark.asyncio
async def test_legacy_api2_connection_test_is_deprecated_and_does_not_call_llm(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.test_api2_connection(
        {
            "api2_control_card": {
                "provider_mode": "custom",
                "custom_profile": {
                    "protocol": "openai",
                    "base_url": "https://api.example.test/v1",
                    "api_key": "typed-secret",
                    "model": "deepseek-test-model",
                },
            }
        }
    )

    assert result["ok"] is False
    assert result["deprecated"] is True
    assert result["replacement"] == "agent_api"
    assert "typed-secret" not in str(result)


def test_api2_settings_preserves_custom_key_when_update_leaves_key_blank(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.update_settings(
        {
            "api2_control_card": {
                "provider_mode": "custom",
                "custom_profile": {"api_key": "first-key", "model": "api2-model"},
            }
        }
    )
    service.update_settings(
        {
            "api2_control_card": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {"api_key": "", "model": "api2-model-2"},
            }
        }
    )

    raw = service.get_settings(safe=False)
    assert raw["api2_control_card"]["custom_profile"]["api_key"] == "first-key"
    assert raw["api2_control_card"]["custom_profile"]["model"] == "api2-model-2"


@pytest.mark.asyncio
async def test_agent_api_connection_test_uses_separate_service_and_current_values(tmp_path):
    storage = PluginStorage(root=tmp_path)
    agent_llm = FakeConnectionLLM()
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        agent_llm_service=agent_llm,
    )

    result = await service.test_agent_connection(
        {
            "agent_api": {
                "provider_mode": "custom",
                "custom_profile": {
                    "protocol": "openai",
                    "base_url": "https://api.agent.example/v1",
                    "api_key": "agent-secret",
                    "model": "agent-reflection-model",
                },
            }
        }
    )

    assert result["ok"] is True
    assert result["model"] == "agent-reflection-model"
    assert result["preview"] == "OK"
    assert agent_llm.calls
    assert "agent-secret" not in str(result)


def test_agent_api_settings_preserves_custom_key_and_redacts(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.update_settings(
        {
            "agent_api": {
                "provider_mode": "custom",
                "custom_profile": {"api_key": "agent-key", "model": "agent-model"},
            }
        }
    )
    service.update_settings(
        {
            "agent_api": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {"api_key": "", "model": "agent-model-2"},
            }
        }
    )

    raw = service.get_settings(safe=False)
    safe = service.get_settings(safe=True)
    assert raw["agent_api"]["custom_profile"]["api_key"] == "agent-key"
    assert raw["agent_api"]["custom_profile"]["model"] == "agent-model-2"
    assert safe["agent_api"]["custom_profile"]["api_key"] == ""
    assert safe["agent_api"]["custom_profile"]["api_key_configured"] is True


def test_agent_api_reflection_runs_after_solidifying_capsules(tmp_path):
    storage = PluginStorage(root=tmp_path)
    agent_llm = FakeConnectionLLM()
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        agent_llm_service=agent_llm,
    )
    service.update_settings(
        {
            "agent_api": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {"api_key": "agent-key", "model": "agent-model"},
            }
        }
    )
    issue = {
        "issue_type": "evolution_character_cognition",
        "severity": "warning",
        "description": "林澈不知道钥匙代价，但本章直接利用该信息。",
        "suggestion": "补充林澈如何得知或推断。",
        "evidence": [{"event_id": "evt-1", "summary": "林澈不知道钥匙代价"}],
    }

    result = service.after_chapter_review(
        {
            "novel_id": "novel-agent-api",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": [issue]}},
        }
    )

    assert result["data"]["agent_api_reflection"]["ok"] is True
    assert result["data"]["agent_api_reflection"]["model"] == "agent-model"
    assert result["data"]["reflection"]["source"] == "agent_api"
    assert service.repository.list_agent_reflections("novel-agent-api")[0]["id"] == result["data"]["reflection"]["id"]
    assert agent_llm.calls
    assert "智能体的反思器" in agent_llm.calls[-1]["prompt"].system
    assert any(event.get("intent") == "reflect" for event in service.repository.list_agent_events("novel-agent-api"))


@pytest.mark.asyncio
async def test_boundary_state_issue_solidifies_capsule(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    await service.after_commit(
        {
            "novel_id": "novel-boundary",
            "chapter_number": 1,
            "payload": {"content": "《沈砚》进入C307。结尾时沈砚仍在C307，门外警报响起。"},
        }
    )

    review = service.review_chapter(
        {
            "novel_id": "novel-boundary",
            "chapter_number": 2,
            "payload": {"content": "沈砚第一次找到C307，重新进入房间。"},
        }
    )
    assert any(item["issue_type"] == "evolution_boundary_state" for item in review["data"]["issues"])

    service.after_chapter_review(
        {
            "novel_id": "novel-boundary",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": review["data"]["issues"]}},
        }
    )
    capsules = service.repository.list_agent_capsules("novel-boundary")
    assert any(capsule["category"] == "continuity" for capsule in capsules)


def test_route_missing_transition_issue_solidifies_capsule(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.save_story_graph_chapter(
        "novel-missing-transition",
        2,
        {
            "schema_version": 1,
            "novel_id": "novel-missing-transition",
            "chapter_number": 2,
            "entities": [],
            "locations": [],
            "events": [],
            "route_edges": [],
            "conflicts": [
                {
                    "type": "location_jump_without_bridge",
                    "severity": "warning",
                    "character": "沈砚",
                    "chapter_previous": 1,
                    "chapter_current": 2,
                    "previous_location": "C307",
                    "current_location": "档案馆",
                    "message": "沈砚上一记录在C307，本章开头已在档案馆，缺少转场/移动桥段。",
                    "evidence": "沈砚来到档案馆。",
                }
            ],
            "vectors": [],
        },
    )

    review = service.review_chapter(
        {
            "novel_id": "novel-missing-transition",
            "chapter_number": 2,
            "payload": {"content": "沈砚来到档案馆。"},
        }
    )

    issues = review["data"]["issues"]
    assert any(item["issue_type"] == "evolution_route_missing_transition" for item in issues)
    service.after_chapter_review(
        {
            "novel_id": "novel-missing-transition",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": issues}},
        }
    )
    capsules = service.repository.list_agent_capsules("novel-missing-transition")
    assert any("route_conflict" in capsule.get("signals", []) for capsule in capsules)


def test_extractor_filters_non_characters_and_bad_location_fragments():
    snapshot = extract_chapter_facts(
        "novel-clean",
        1,
        "hash",
        "金属牌说方向，查询记录显示但他咬牙站。沈砚进入信息站，随后穿过道防火门。",
        "now",
    )

    assert "金属牌" not in snapshot.characters
    assert "方向" not in snapshot.characters
    assert "查询记录" not in snapshot.characters
    assert "但他咬牙站" not in snapshot.locations
    assert "道防火门" not in snapshot.locations


@pytest.mark.asyncio
async def test_evolution_builds_timeline_evidence_for_review_flow(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.after_commit(
        {
            "novel_id": "novel-review-flow",
            "chapter_number": 1,
            "payload": {"content": "《林澈》抵达雾城，并不知道钥匙会消耗记忆。"},
        }
    )

    events = service.repository.list_timeline_events("novel-review-flow")
    constraints = service.repository.list_continuity_constraints("novel-review-flow")
    assert events
    assert events[0]["event_id"].startswith("evt_")
    assert any(item["type"] in {"knowledge_boundary", "capability_boundary", "personality_boundary"} for item in constraints)

    before = service.before_chapter_review(
        {
            "novel_id": "novel-review-flow",
            "chapter_number": 2,
            "payload": {"content": "林澈知道钥匙会消耗记忆，并且直接解决黑塔机关。"},
        }
    )

    assert before["ok"] is True
    titles = [block["title"] for block in before["data"]["review_context_blocks"]]
    assert "Evolution 时间线证据" in titles
    assert "Evolution 连续性约束" in titles

    review = service.review_chapter(
        {
            "novel_id": "novel-review-flow",
            "chapter_number": 2,
            "payload": {"content": "林澈知道其他角色未在场经历，并且一眼看穿黑塔机关。"},
        }
    )
    assert review["data"]["evidence"]
    assert any(item.get("evidence") for item in review["data"]["issues"])

    after = service.after_chapter_review(
        {
            "novel_id": "novel-review-flow",
            "chapter_number": 2,
            "payload": {"review_result": {"issues": review["data"]["issues"], "overall_score": 90}},
        }
    )
    assert after["data"]["recorded"] is True
    assert service.repository.list_review_records("novel-review-flow")[-1]["issue_count"] == len(review["data"]["issues"])


@pytest.mark.asyncio
async def test_manual_rebuild_replays_chapter_payloads(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.manual_rebuild(
        {
            "novel_id": "novel-2",
            "chapters": [
                {"number": 1, "content": "《沈月》进入黑塔，发现塔顶爆发异象。"},
                {"number": 2, "content": "沈月离开黑塔，来到星港。"},
            ],
        }
    )

    assert result["ok"] is True
    assert result["data"]["novel_id"] == "novel-2"
    assert result["data"]["rebuilt_chapters"] == [1, 2]
    assert result["data"]["characters_rebuilt"] == 1
    card = service.get_character("novel-2", "沈月")
    assert card is not None
    assert card["last_seen_chapter"] == 2
    timeline = service.list_character_timeline("novel-2", card["character_id"])
    assert len(timeline["items"]) == 2


@pytest.mark.asyncio
async def test_rollback_removes_snapshot_and_rebuilds_character_cards(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.manual_rebuild(
        {
            "novel_id": "novel-3",
            "chapters": [
                {"number": 1, "content": "《林澈》抵达雾城。顾衡交给林澈一枚钥匙。"},
                {"number": 2, "content": "林澈离开雾城，顾衡留在黑塔。"},
            ],
        }
    )

    before = service.list_snapshots("novel-3")
    assert [item["chapter_number"] for item in before["items"]] == [1, 2]

    result = await service.rollback({"novel_id": "novel-3", "chapter_number": 2})

    assert result["ok"] is True
    assert result["data"]["removed_snapshot"] is True
    after = service.list_snapshots("novel-3")
    assert [item["chapter_number"] for item in after["items"]] == [1]
    card = service.get_character("novel-3", "林澈")
    assert card is not None
    assert card["last_seen_chapter"] == 1
    runs = service.list_runs("novel-3")
    assert any(run["hook_name"] == "rollback" for run in runs["items"])


class FakeStructuredProvider:
    async def extract(self, request):
        assert request["schema"]["required"] == ["summary", "characters", "locations", "world_events"]
        return {
            "summary": "林澈在雾城获得钥匙。",
            "characters": [
                {"name": "林澈", "summary": "获得黑色钥匙", "locations": ["雾城"], "confidence": 0.92},
                {"name": "沈月", "summary": "追捕白鸦", "status": "active"},
            ],
            "locations": ["雾城", "黑塔"],
            "world_events": [
                {"summary": "林澈获得黑色钥匙", "event_type": "item", "characters": ["林澈"], "locations": ["黑塔"]}
            ],
        }


class FailingStructuredProvider:
    async def extract(self, request):
        raise RuntimeError("provider offline")


class PaletteStructuredProvider:
    async def extract(self, request):
        character_schema = request["schema"]["properties"]["characters"]["items"]["properties"]
        assert "appearance" in character_schema
        assert "attributes" in character_schema
        assert "world_profile" in character_schema
        assert "personality_palette" in character_schema
        return {
            "summary": "测试角色甲在夜色里用吉他solo，测试角色乙在台下看着她。",
            "characters": [
                {
                    "name": "测试角色甲",
                    "summary": "在街头舞台短暂恢复自我",
                    "appearance": {
                        "summary": "黑色短发，舞台上常穿宽松外套和磨旧靴子。",
                        "features": ["黑色短发", "舞台眼线"],
                        "style": ["随意舒适", "摇滚感"],
                        "current_outfit": "宽松外套与磨旧靴子",
                    },
                    "attributes": [
                        {"category": "基础", "name": "身份", "value": "贵族学校大小姐", "description": "校内需要维持优秀形象"},
                        {"category": "音乐", "name": "擅长", "value": "吉他solo"},
                    ],
                    "world_profile": {
                        "schema_name": "现代校园摇滚",
                        "fields": [
                            {"category": "学校", "name": "校内伪装", "value": "优秀的大小姐"},
                            {"category": "关系", "name": "核心依赖", "value": "测试角色乙"},
                        ],
                    },
                    "personality_palette": {
                        "metaphor": "人的性格就像调色盘，叛逆是底色，热情与不拘一格是主色调。",
                        "base": "叛逆",
                        "main_tones": ["热情", "不拘一格"],
                        "accents": ["依赖"],
                        "derivatives": [
                            {
                                "tone": "热情",
                                "title": "摇滚燃烧",
                                "description": "创作、演唱和练习都会投入百分百热情。",
                                "trigger": "面对摇滚",
                            },
                            {
                                "tone": "依赖",
                                "title": "崩溃时靠近",
                                "description": "压力过大时会抓住测试角色乙的衣角寻求依靠。",
                                "visibility": "只在两人或崩溃时显露",
                            },
                        ],
                    },
                }
            ],
            "locations": ["夜街", "舞台"],
            "world_events": [{"summary": "测试角色甲在夜街舞台用吉他solo", "characters": ["测试角色甲"], "locations": ["夜街"]}],
        }


class NoisyCharacterStructuredProvider:
    async def extract(self, request):
        return {
            "summary": "林渊、沈雨和小诺在C307整理资料。",
            "characters": [
                {"name": "林渊", "summary": "翻开旧教程"},
                {"name": "沈雨", "summary": "记录设备读数"},
                {"name": "小诺", "summary": "检查门禁"},
                {"name": "云海导航基础教程", "summary": "误判成角色的书名"},
                {"name": "很聪明", "summary": "误判成角色的短语"},
            ],
            "locations": ["C307"],
            "world_events": [
                {
                    "summary": "小诺检查C307门禁",
                    "characters": ["小诺", "很聪明"],
                    "locations": ["C307"],
                }
            ],
        }


def _make_host_character_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE bible_characters (
            id TEXT PRIMARY KEY,
            novel_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            mental_state TEXT DEFAULT 'NORMAL',
            verbal_tic TEXT DEFAULT '',
            idle_behavior TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE cast_snapshots (
            novel_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            version INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE triples (
            id TEXT PRIMARY KEY,
            novel_id TEXT NOT NULL,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            entity_type TEXT,
            description TEXT,
            confidence REAL,
            subject_entity_id TEXT,
            object_entity_id TEXT,
            updated_at TEXT
        )
        """
    )
    for character_id, name, description in [
        ("char-linyuan", "林渊", "主角，擅长分析异常数据。"),
        ("char-shenyu", "沈雨", "工程师，负责记录设备读数。"),
        ("char-anuo", "阿诺", "安保机器人维护员。"),
    ]:
        conn.execute(
            "INSERT INTO bible_characters (id, novel_id, name, description) VALUES (?, ?, ?, ?)",
            (character_id, "novel-canonical", name, description),
        )
    conn.execute(
        "INSERT INTO cast_snapshots (novel_id, data) VALUES (?, ?)",
        (
            "novel-canonical",
            json.dumps(
                {
                    "characters": [
                        {
                            "id": "char-anuo",
                            "name": "阿诺",
                            "aliases": ["小诺"],
                            "role": "维护员",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ),
    )
    conn.commit()
    conn.close()


def _make_host_context_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE bible_world_settings (
            id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT, setting_type TEXT, updated_at TEXT
        );
        CREATE TABLE bible_characters (
            id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT,
            mental_state TEXT DEFAULT '', verbal_tic TEXT DEFAULT '', idle_behavior TEXT DEFAULT ''
        );
        CREATE TABLE bible_locations (
            id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT, location_type TEXT, parent_id TEXT, updated_at TEXT
        );
        CREATE TABLE bible_timeline_notes (
            id TEXT PRIMARY KEY, novel_id TEXT, event TEXT, time_point TEXT, description TEXT, sort_order INTEGER
        );
        CREATE TABLE knowledge (
            id TEXT PRIMARY KEY, novel_id TEXT, version INTEGER, premise_lock TEXT
        );
        CREATE TABLE chapter_summaries (
            id TEXT PRIMARY KEY, knowledge_id TEXT, chapter_number INTEGER, summary TEXT,
            key_events TEXT, open_threads TEXT, consistency_note TEXT, beat_sections TEXT, micro_beats TEXT, sync_status TEXT
        );
        CREATE TABLE triples (
            id TEXT PRIMARY KEY, novel_id TEXT, subject TEXT, predicate TEXT, object TEXT,
            chapter_number INTEGER, note TEXT, entity_type TEXT, importance TEXT, location_type TEXT,
            description TEXT, first_appearance INTEGER, confidence REAL, source_type TEXT,
            subject_entity_id TEXT, object_entity_id TEXT, updated_at TEXT
        );
        CREATE TABLE storylines (
            id TEXT PRIMARY KEY, novel_id TEXT, storyline_type TEXT, status TEXT,
            estimated_chapter_start INTEGER, estimated_chapter_end INTEGER, current_milestone_index INTEGER,
            name TEXT, description TEXT, last_active_chapter INTEGER, progress_summary TEXT, updated_at TEXT
        );
        CREATE TABLE storyline_milestones (
            id TEXT PRIMARY KEY, storyline_id TEXT, milestone_order INTEGER, title TEXT, description TEXT,
            target_chapter_start INTEGER, target_chapter_end INTEGER, prerequisite_list TEXT, milestone_triggers TEXT
        );
        CREATE TABLE timeline_registries (
            novel_id TEXT PRIMARY KEY, data TEXT, updated_at TEXT
        );
        CREATE TABLE novel_foreshadow_registry (
            novel_id TEXT PRIMARY KEY, payload TEXT, updated_at TEXT
        );
        CREATE TABLE novel_snapshots (
            id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT, created_at TEXT
        );
        CREATE TABLE narrative_events (
            event_id TEXT PRIMARY KEY, novel_id TEXT, chapter_number INTEGER, event_summary TEXT,
            mutations TEXT, tags TEXT, timestamp_ts TEXT
        );
        CREATE TABLE memory_engine_states (
            novel_id TEXT PRIMARY KEY, state_json TEXT, last_updated_chapter INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO bible_characters VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("bc-1", "novel-host", "林澈", "调查黑塔事故的主角。", "警惕", "我会查清", "反复检查钥匙"),
    )
    conn.execute(
        "INSERT INTO bible_world_settings VALUES (?, ?, ?, ?, ?, ?)",
        ("ws-1", "novel-host", "雾城禁令", "雾城夜晚禁止进入黑塔。", "rule", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO bible_locations VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("loc-1", "novel-host", "黑塔", "雾城中央的封锁建筑。", "landmark", None, "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO bible_timeline_notes VALUES (?, ?, ?, ?, ?, ?)",
        ("tn-1", "novel-host", "黑塔封锁", "开篇前十年", "黑塔在事故后被封锁。", 1),
    )
    conn.execute("INSERT INTO knowledge VALUES (?, ?, ?, ?)", ("k-1", "novel-host", 1, "雾城黑塔不能被公开解释"))
    conn.execute(
        "INSERT INTO chapter_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("cs-1", "k-1", 1, "林澈抵达雾城。", "获得钥匙", "黑塔真相", "钥匙代价未知", "[]", "[]", "synced"),
    )
    conn.execute(
        "INSERT INTO triples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("tri-1", "novel-host", "钥匙", "代价", "消耗记忆", 1, "", "prop", "high", "", "钥匙会消耗使用者记忆。", 1, 0.9, "chapter", "", "", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO storylines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sl-1", "novel-host", "main_plot", "active", 1, 10, 0, "黑塔主线", "调查黑塔事故真相。", 1, "钥匙已出现", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO storyline_milestones VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("slm-1", "sl-1", 1, "进入黑塔", "找到合法进入黑塔的方法。", 2, 3, "[]", "[]"),
    )
    conn.execute(
        "INSERT INTO timeline_registries VALUES (?, ?, ?)",
        (
            "novel-host",
            json.dumps({"events": [{"id": "tl-1", "chapter_number": 1, "event": "林澈得到钥匙", "timestamp": "第1章", "timestamp_type": "chapter"}]}, ensure_ascii=False),
            "2026-01-01",
        ),
    )
    conn.execute(
        "INSERT INTO novel_foreshadow_registry VALUES (?, ?, ?)",
        (
            "novel-host",
            json.dumps({"foreshadowings": [{"id": "fs-1", "description": "钥匙会消耗记忆", "status": "PLANTED", "chapter_planted": 1}]}, ensure_ascii=False),
            "2026-01-01",
        ),
    )
    conn.execute("INSERT INTO novel_snapshots VALUES (?, ?, ?, ?, ?)", ("snap-1", "novel-host", "首章快照", "林澈得到钥匙", "2026-01-01"))
    conn.execute(
        "INSERT INTO narrative_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ne-1", "novel-host", 1, "林澈说他会查清黑塔。", "[]", json.dumps(["林澈：我会查清黑塔。"], ensure_ascii=False), "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO memory_engine_states VALUES (?, ?, ?)",
        (
            "novel-host",
            json.dumps({"fact_lock": "林澈已经知道钥匙会消耗记忆", "completed_beats": ["抵达雾城"]}, ensure_ascii=False),
            1,
        ),
    )
    conn.commit()
    conn.close()


def test_host_context_reader_reads_plotpilot_sources(tmp_path):
    db_path = tmp_path / "host-context.sqlite3"
    _make_host_context_db(db_path)
    reader = HostContextReader(ReadOnlyHostDatabase(db_path))

    context = reader.read("novel-host", query="林澈调查黑塔钥匙", before_chapter=3)

    assert context["counts"]["world"] >= 2
    assert context["counts"]["bible"] >= 2
    assert context["counts"]["knowledge"] >= 1
    assert context["counts"]["story_knowledge"] >= 1
    assert context["counts"]["storyline"] == 1
    assert context["counts"]["timeline"] >= 2
    assert context["counts"]["foreshadow"] == 1
    assert context["counts"]["dialogue"] == 1
    assert context["counts"]["memory_engine"] == 1
    assert set(context["active_sources"]) >= {"bible", "world", "knowledge", "story_knowledge", "storyline", "timeline", "foreshadow", "dialogue", "triples", "memory_engine"}
    assert context["plotpilot_context_usage"]["mode"] == "strategy_only"


def test_host_context_reader_handles_base_storyline_schema_and_missing_sources(tmp_path):
    db_path = tmp_path / "host-context-lite.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE storylines (
            id TEXT PRIMARY KEY,
            novel_id TEXT NOT NULL,
            storyline_type TEXT NOT NULL,
            status TEXT NOT NULL,
            estimated_chapter_start INTEGER NOT NULL,
            estimated_chapter_end INTEGER NOT NULL,
            current_milestone_index INTEGER NOT NULL DEFAULT 0,
            extensions TEXT NOT NULL DEFAULT '{}',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE storyline_milestones (
            id TEXT PRIMARY KEY, storyline_id TEXT, milestone_order INTEGER, title TEXT, description TEXT,
            target_chapter_start INTEGER, target_chapter_end INTEGER, prerequisite_list TEXT, milestone_triggers TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO storylines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("sl-lite", "novel-lite", "main_plot", "active", 1, 8, 0, "{}", "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO storyline_milestones VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("slm-lite", "sl-lite", 1, "抵达旧城", "主角进入旧城并发现第一条线索。", 1, 2, "[]", "[]"),
    )
    conn.commit()
    conn.close()

    reader = HostContextReader(ReadOnlyHostDatabase(db_path))
    context = reader.read("novel-lite", query="旧城线索", before_chapter=2)

    assert context["counts"]["storyline"] == 1
    assert context["storyline"][0]["name"] == "main_plot"
    assert "storyline" in context["active_sources"]
    assert "world" in context["degraded_sources"]
    assert "knowledge" in context["degraded_sources"]


def test_host_context_reader_tolerates_minimal_native_schemas(tmp_path):
    db_path = tmp_path / "host-context-minimal.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE bible_characters (
            id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT
        );
        CREATE TABLE bible_locations (
            id TEXT PRIMARY KEY, novel_id TEXT, name TEXT, description TEXT
        );
        CREATE TABLE knowledge (
            id TEXT PRIMARY KEY, novel_id TEXT, version INTEGER, premise_lock TEXT
        );
        CREATE TABLE chapter_summaries (
            id TEXT PRIMARY KEY, knowledge_id TEXT, chapter_number INTEGER, summary TEXT
        );
        CREATE TABLE memory_engine_state (
            novel_id TEXT PRIMARY KEY, state_json TEXT, last_updated_chapter INTEGER
        );
        """
    )
    conn.execute("INSERT INTO bible_characters VALUES (?, ?, ?, ?)", ("char-1", "novel-min", "林澈", "调查员"))
    conn.execute("INSERT INTO bible_locations VALUES (?, ?, ?, ?)", ("loc-1", "novel-min", "黑塔", "封锁建筑"))
    conn.execute("INSERT INTO knowledge VALUES (?, ?, ?, ?)", ("k-1", "novel-min", 1, "黑塔不能公开解释"))
    conn.execute("INSERT INTO chapter_summaries VALUES (?, ?, ?, ?)", ("cs-1", "k-1", 1, "林澈抵达黑塔"))
    conn.execute(
        "INSERT INTO memory_engine_state VALUES (?, ?, ?)",
        ("novel-min", json.dumps({"completed_beats": ["抵达黑塔"]}, ensure_ascii=False), 1),
    )
    conn.commit()
    conn.close()

    context = HostContextReader(ReadOnlyHostDatabase(db_path)).read("novel-min", query="黑塔", before_chapter=2)

    assert context["counts"]["bible"] == 2
    assert context["counts"]["story_knowledge"] == 1
    assert context["counts"]["memory_engine"] == 1
    assert "bible" in context["active_sources"]
    assert "story_knowledge" in context["active_sources"]


def test_diagnostics_reports_risks_and_redacts_sensitive_settings(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.update_settings(
        {
            "api2": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {"api_key": "api2-secret", "model": "api2-model"},
            },
            "agent_api": {
                "enabled": True,
                "provider_mode": "custom",
                "custom_profile": {"api_key": "agent-secret", "model": "agent-model"},
            },
        }
    )
    service.repository.save_host_context_summary(
        "novel-diagnostics",
        {
            "source": "plotpilot_host_readonly",
            "active_sources": [],
            "degraded_sources": ["world", "knowledge"],
            "counts": {"world": 0, "knowledge": 0},
            "plotpilot_context_usage": {"mode": "strategy_only", "long_context_duplicated": False},
        },
    )
    service.repository.save_semantic_recall_summary(
        "novel-diagnostics",
        {
            "source": "none",
            "vector_enabled": False,
            "item_count": 0,
            "collection_status": {
                "missing": ["novel_novel-diagnostics_world"],
                "queried": ["novel_novel-diagnostics_chunks"],
            },
        },
    )
    service.repository.append_context_injection_record(
        "novel-diagnostics",
        {
            "blocks": [
                {"id": "duplicate", "token_budget": 4000},
                {"id": "duplicate", "token_budget": 3000},
            ]
        },
    )
    service.repository.write_character_card(
        "novel-diagnostics",
        {"name": "金属牌", "status": "invalid_entity", "entity_type": "non_person"},
    )

    diagnostics = service.get_diagnostics("novel-diagnostics")

    risk_sources = {item["source"] for item in diagnostics["risks"]}
    assert "host_context" in risk_sources
    assert "semantic_recall" in risk_sources
    assert "context_injection" in risk_sources
    assert "character_cards" in risk_sources
    assert "settings" in risk_sources
    assert diagnostics["summary"]["total"] >= 5
    assert service.repository.get_diagnostics_snapshot("novel-diagnostics")["summary"]["total"] == diagnostics["summary"]["total"]
    assert diagnostics["dependency_status"]
    assert diagnostics["host_feature_alignment"]["mode"] == "strategy_only"
    assert "source_status" in diagnostics["host_feature_alignment"]
    assert "novel_novel-diagnostics_world" in json.dumps(diagnostics, ensure_ascii=False)
    assert "api2-secret" not in json.dumps(diagnostics, ensure_ascii=False)
    assert "agent-secret" not in json.dumps(diagnostics, ensure_ascii=False)


def test_diagnostics_reports_plugin_disabled_without_hook_execution(tmp_path, monkeypatch):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    monkeypatch.setattr("plugins.world_evolution_core.diagnostics._plugin_enabled", lambda: False)

    diagnostics = service.get_diagnostics("novel-disabled")

    assert any(item["source"] == "plugin_runtime" and item["severity"] == "warning" for item in diagnostics["risks"])
    assert service.repository.list_agent_events("novel-disabled") == []


def test_diagnostics_degrades_when_snapshot_write_fails(tmp_path, monkeypatch):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    def fail_save(*_args, **_kwargs):
        raise OSError("storage is readonly")

    monkeypatch.setattr(service.repository, "save_diagnostics_snapshot", fail_save)

    diagnostics = service.get_diagnostics("novel-diagnostics-write-fail")

    assert any(item["source"] == "diagnostics" for item in diagnostics["risks"])
    assert diagnostics["summary"]["warning"] >= 1
    assert "storage is readonly" in json.dumps(diagnostics, ensure_ascii=False)


def test_diagnostics_degrades_when_route_map_fails(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    def fail_route_map(_novel_id):
        raise RuntimeError("route graph corrupted")

    service.diagnostics_service.route_map_provider = fail_route_map

    diagnostics = service.get_diagnostics("novel-route-map-fail")

    route_risks = [item for item in diagnostics["risks"] if item["source"] == "route_map"]
    assert route_risks
    assert route_risks[0]["affected_feature"] == "route_conflict"
    assert "route graph corrupted" in json.dumps(route_risks, ensure_ascii=False)


def test_context_patch_injects_host_context_blocks(tmp_path):
    db_path = tmp_path / "host-context.sqlite3"
    _make_host_context_db(db_path)
    storage = PluginStorage(root=tmp_path / "plugin")
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        host_database=ReadOnlyHostDatabase(db_path),
    )

    context = service.before_context_build(
        {
            "novel_id": "novel-host",
            "chapter_number": 3,
            "payload": {"outline": "林澈准备用钥匙进入黑塔，调查雾城禁令。"},
        }
    )

    block_ids = {block["id"] for block in context["context_patch"]["blocks"]}
    assert "plotpilot_native_strategy" in block_ids
    assert "host_world_context" not in block_ids
    strategy = next(block for block in context["context_patch"]["blocks"] if block["id"] == "plotpilot_native_strategy")
    assert "不重复注入全文资料" in strategy["content"]
    assert "伏笔账本" in strategy["content"]
    status = service.get_agent_status("novel-host")
    assert status["host_context_summary"]["counts"]["world"] >= 2
    assert status["plotpilot_context_usage"]["mode"] == "strategy_only"
    assert status["semantic_recall_summary"]["item_count"] >= 1


@pytest.mark.asyncio
async def test_after_commit_marks_native_sync_usage_and_degraded_fallback(tmp_path):
    db_path = tmp_path / "host-context.sqlite3"
    _make_host_context_db(db_path)
    storage = PluginStorage(root=tmp_path / "plugin")
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        host_database=ReadOnlyHostDatabase(db_path),
    )

    result = await service.after_commit(
        {
            "novel_id": "novel-host",
            "chapter_number": 2,
            "payload": {"content": "《林澈》带着钥匙靠近黑塔。"},
        }
    )

    native = result["data"]["native_after_commit"]
    assert native["has_native_sync"] is True
    assert native["fallback_degraded"] is False
    assert native["native_counts"]["story_knowledge"] >= 1
    assert result["data"]["extraction"]["fallback_degraded"] is False


def test_context_patch_degrades_slow_external_context_sources(tmp_path, monkeypatch):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        semantic_memory=SlowSemanticMemory(),
    )
    service.host_context_reader = SlowHostContextReader()
    monkeypatch.setattr(evolution_service_module, "CONTEXT_EXTERNAL_TIMEOUT_SECONDS", 0.001)

    result = service.before_context_build(
        {
            "novel_id": "novel-context-timeout",
            "chapter_number": 2,
            "payload": {"outline": "林澈调查黑塔。"},
        }
    )

    assert result["ok"] is True
    host_summary = service.repository.get_host_context_summary("novel-context-timeout")
    semantic_summary = service.repository.get_semantic_recall_summary("novel-context-timeout")
    assert "host_context_timeout" in host_summary["degraded_sources"]
    assert semantic_summary["source"] == "semantic_recall_timeout"
    assert semantic_summary["collection_status"]["degraded_reason"] == "semantic_recall_timeout"


def test_review_chapter_uses_host_context_as_evidence(tmp_path):
    db_path = tmp_path / "host-context.sqlite3"
    _make_host_context_db(db_path)
    storage = PluginStorage(root=tmp_path / "plugin")
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        host_database=ReadOnlyHostDatabase(db_path),
    )

    review = service.review_chapter(
        {
            "novel_id": "novel-host",
            "chapter_number": 3,
            "payload": {"content": "林澈拿着钥匙来到黑塔，低声说自己会查清雾城禁令。"},
        }
    )

    issue_types = {item["issue_type"] for item in review["data"]["issues"]}
    assert "evolution_worldbuilding_context" in issue_types
    assert "evolution_triples_context" in issue_types
    assert any(item.get("evidence") for item in review["data"]["issues"] if item["issue_type"].startswith("evolution_"))
    assert all(item.get("source_plugin") == "world_evolution_core" for item in review["data"]["issues"] if item["issue_type"].startswith("evolution_"))


@pytest.mark.asyncio
async def test_structured_provider_overrides_deterministic_extraction(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        extractor_provider=FakeStructuredProvider(),
    )

    result = await service.after_commit(
        {
            "novel_id": "novel-4",
            "chapter_number": 1,
            "payload": {"content": "这一章没有书名号，但结构化 provider 会返回人物。"},
        }
    )

    assert result["ok"] is True
    assert result["data"]["extraction"]["source"] == "structured"
    assert result["data"]["facts"]["characters"] == ["林澈", "沈月"]
    assert result["data"]["facts"]["locations"] == ["雾城", "黑塔"]
    runs = service.list_runs("novel-4")
    assert runs["items"][-1]["output"]["extraction_source"] == "structured"


@pytest.mark.asyncio
async def test_structured_provider_persists_rich_character_profile(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        extractor_provider=PaletteStructuredProvider(),
    )

    result = await service.after_commit(
        {
            "novel_id": "novel-rich",
            "chapter_number": 1,
            "payload": {"content": "《测试角色甲》在夜街舞台用吉他solo，测试角色乙在台下看着她。"},
        }
    )

    assert result["ok"] is True
    card = service.get_character("novel-rich", "测试角色甲")
    assert card is not None
    assert card["appearance"]["summary"].startswith("黑色短发")
    assert card["attributes"][0]["name"] == "身份"
    assert card["world_profile"]["schema_name"] == "现代校园摇滚"
    assert card["personality_palette"]["base"] == "叛逆"
    assert card["personality_palette"]["main_tones"] == ["热情", "不拘一格"]
    assert card["personality_palette"]["derivatives"][1]["tone"] == "依赖"

    context = service.before_context_build(
        {"novel_id": "novel-rich", "chapter_number": 2, "payload": {"outline": "测试角色甲结束演出后去找测试角色乙。"}}
    )
    content = context["context_blocks"][0]["content"]
    assert "外貌/出场识别" in content
    assert "性格调色盘" in content
    assert "底色=叛逆" in content


@pytest.mark.asyncio
async def test_llm_structured_extractor_provider_generates_palette(tmp_path):
    fake_llm = FakePaletteLLM()
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        extractor_provider=LLMStructuredExtractorProvider(fake_llm),
    )

    result = await service.after_commit(
        {
            "novel_id": "novel-llm-palette",
            "chapter_number": 1,
            "payload": {"content": "测试角色甲在C307拆开旧式门锁，确认门锁被人改造过。"},
        }
    )

    assert result["data"]["extraction"]["source"] == "structured"
    card = service.get_character("novel-llm-palette", "测试角色甲")
    assert card["personality_palette"]["base"] == "谨慎"
    assert card["personality_palette"]["main_tones"] == ["固执"]
    assert card["personality_palette"]["derivatives"][0]["title"] == "反复验证"
    assert fake_llm.calls
    assert "性格调色盘不是标签列表" in fake_llm.calls[0]["prompt"].user


@pytest.mark.asyncio
async def test_canonical_host_characters_filter_noise_and_normalize_aliases(tmp_path):
    db_path = tmp_path / "host.sqlite3"
    _make_host_character_db(db_path)
    storage = PluginStorage(root=tmp_path / "plugin")
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        extractor_provider=NoisyCharacterStructuredProvider(),
        host_database=ReadOnlyHostDatabase(db_path),
    )

    result = await service.after_commit(
        {
            "novel_id": "novel-canonical",
            "chapter_number": 1,
            "payload": {
                "content": (
                    "林渊把《云海导航基础教程》压在桌角，沈雨记录C307设备读数，"
                    "小诺检查门禁。旁白写道《很聪明》不是人物。"
                )
            },
        }
    )

    assert result["ok"] is True
    assert result["data"]["facts"]["characters"] == ["林渊", "沈雨", "阿诺"]
    assert "云海导航基础教程" in result["data"]["extraction"]["ignored_character_candidates"]
    assert "很聪明" in result["data"]["extraction"]["ignored_character_candidates"]

    cards = service.list_characters("novel-canonical")["items"]
    assert {card["name"] for card in cards} == {"林渊", "沈雨", "阿诺"}
    assert all(card.get("canonical_source") == "bible" for card in cards)
    anuo = service.get_character("novel-canonical", "阿诺")
    assert anuo is not None
    assert "小诺" in anuo["aliases"]
    assert anuo["canonical_character_id"] == "char-anuo"

    timeline = service.list_timeline_events("novel-canonical")["items"]
    assert timeline[0]["participants"] == ["阿诺"]
    assert "很聪明" not in {card["name"] for card in cards}


@pytest.mark.asyncio
async def test_structured_provider_failure_falls_back_to_deterministic(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        extractor_provider=FailingStructuredProvider(),
    )

    result = await service.after_commit(
        {
            "novel_id": "novel-5",
            "chapter_number": 1,
            "payload": {"content": "《顾衡》来到黑塔，发现雾城爆发异象。"},
        }
    )

    assert result["ok"] is True
    assert result["data"]["extraction"]["source"] == "deterministic"
    assert "顾衡" in result["data"]["facts"]["characters"]
    assert result["data"]["extraction"]["warnings"]


@pytest.mark.asyncio
async def test_context_patch_omits_future_chapter_facts(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.manual_rebuild(
        {
            "novel_id": "novel-6",
            "chapters": [
                {"number": 1, "content": "《林澈》抵达雾城。"},
                {"number": 2, "content": "林澈进入黑塔，发现星港信标。"},
            ],
        }
    )

    context = service.before_context_build({"novel_id": "novel-6", "chapter_number": 2})

    assert context["ok"] is True
    patch = context["context_patch"]
    recent_facts = next(block for block in patch["blocks"] if block["id"] == "recent_facts")
    assert [item["chapter_number"] for item in recent_facts["items"]] == [1]
    assert "第2章" not in recent_facts["content"]


def test_import_st_preset_converts_prompt_order_and_marks_unsupported(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = service.import_st_preset(
        "novel-7",
        {
            "name": "ST Flow",
            "temperature": 0.8,
            "top_p": 0.9,
            "prompts": [
                {"identifier": "main", "name": "Main", "role": "system", "content": "提取角色与世界状态。"},
                {"identifier": "world", "name": "World", "role": "system", "content": "世界与地点：{{char}}"},
            ],
            "prompt_order": [{"order": [{"identifier": "world", "enabled": True}, {"identifier": "main", "enabled": False}]}],
            "controller_model": {"activate_entries": []},
            "extensions": {"SPreset": {"RegexBinding": {"regexes": [{"id": "r1", "scriptName": "clean", "findRegex": "foo", "replaceString": "bar"}]}}},
        },
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["source"] == "sillytavern_preset"
    assert data["flows"][0]["name"] == "ST Flow"
    assert data["flows"][0]["generation_options"]["temperature"] == 0.8
    assert [entry["identifier"] for entry in data["flows"][0]["prompt_order"]] == ["world", "main"]
    assert data["flows"][0]["prompt_order"][1]["enabled"] is False
    assert data["flows"][0]["regex_rules"][0]["find_regex"] == "foo"
    assert "controller_model_ejs_execution" in data["flows"][0]["unsupported"]
    saved = service.list_imported_flows("novel-7")
    assert saved["flows"][0]["name"] == "ST Flow"



@pytest.mark.asyncio
async def test_context_patch_filters_unmentioned_recent_characters_into_risks(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.manual_rebuild(
        {
            "novel_id": "novel-8",
            "chapters": [
                {"number": 1, "content": "《林澈》在雾城得到黑色钥匙。"},
                {"number": 2, "content": "《沈月》在星港追踪白鸦，发现银色罗盘。"},
                {"number": 3, "content": "《顾衡》留在城门，调查旧案卷宗。"},
            ],
        }
    )

    context = service.before_context_build(
        {
            "novel_id": "novel-8",
            "chapter_number": 4,
            "payload": {"outline": "林澈独自进入黑塔，用黑色钥匙打开密门。"},
        }
    )

    focus = next(block for block in context["context_patch"]["blocks"] if block["id"] == "focus_characters")
    assert [item["name"] for item in focus["items"]] == ["林澈"]
    risks = next(block for block in context["context_patch"]["blocks"] if block["id"] == "continuity_risks")
    assert "沈月" in risks["content"]
    assert "顾衡" in risks["content"]
    assert "不要强行安排出场" in risks["content"]



@pytest.mark.asyncio
async def test_context_patch_separates_background_constraints_from_focus(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.manual_rebuild(
        {
            "novel_id": "novel-9",
            "chapters": [
                {"number": 1, "content": "《林澈》在雾城得到黑色钥匙。"},
                {"number": 2, "content": "《沈月》在星港追踪白鸦，发现银色罗盘。"},
            ],
        }
    )

    context = service.before_context_build(
        {
            "novel_id": "novel-9",
            "chapter_number": 3,
            "payload": {"outline": "林澈进入星港，寻找白鸦留下的密门线索。"},
        }
    )

    focus = next(block for block in context["context_patch"]["blocks"] if block["id"] == "focus_characters")
    background = next(block for block in context["context_patch"]["blocks"] if block["id"] == "background_constraints")
    assert [item["name"] for item in focus["items"]] == ["林澈"]
    assert [item["name"] for item in background["items"]] == ["沈月"]
    assert "只作为连续性约束" in background["content"]
    assert "不要因此强制安排出场" in background["content"]
    assert "《沈月》" not in background["content"]



class RichStructuredProvider:
    async def extract(self, request):
        return {
            "summary": "林澈第一次意识到黑色钥匙并不能直接解决所有问题。",
            "characters": [
                {
                    "name": "林澈",
                    "summary": "林澈试图用黑色钥匙开门，但发现自己并不了解机关规则。",
                    "locations": ["黑塔"],
                    "known_facts": ["黑色钥匙能响应黑塔密门", "顾衡曾提醒钥匙有代价"],
                    "unknowns": ["不知道密门后的守卫是谁", "不知道钥匙会消耗记忆"],
                    "misbeliefs": ["误以为钥匙可以打开所有门"],
                    "emotion": "谨慎中夹着急迫",
                    "inner_change": "从逞强独闯转向承认自己需要验证线索",
                    "growth_stage": "从冲动试探走向谨慎推理",
                    "growth_change": "开始用证据校正自信",
                    "capability_limits": ["不能凭空知道黑塔机关", "钥匙只能打开响应过的密门"],
                    "decision_biases": ["遇到同伴受威胁时会冒险", "倾向先保护钥匙秘密"],
                }
            ],
            "locations": ["黑塔"],
            "world_events": [],
        }


@pytest.mark.asyncio
async def test_rich_character_card_tracks_cognition_growth_and_limits(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(
        storage=storage,
        jobs=PluginJobRegistry(storage),
        extractor_provider=RichStructuredProvider(),
    )

    await service.after_commit(
        {
            "novel_id": "novel-10",
            "chapter_number": 1,
            "payload": {"content": "《林澈》把黑色钥匙插进黑塔密门，却发现机关没有立刻打开。"},
        }
    )

    card = service.get_character("novel-10", "林澈")
    assert "黑色钥匙能响应黑塔密门" in card["cognitive_state"]["known_facts"]
    assert "不知道钥匙会消耗记忆" in card["cognitive_state"]["unknowns"]
    assert "误以为钥匙可以打开所有门" in card["cognitive_state"]["misbeliefs"]
    assert card["growth_arc"]["stage"] == "从冲动试探走向谨慎推理"
    assert "不能凭空知道黑塔机关" in card["capability_limits"]

    context = service.before_context_build(
        {
            "novel_id": "novel-10",
            "chapter_number": 2,
            "payload": {"outline": "林澈继续调查黑塔密门。"},
        }
    )
    content = context["context_blocks"][0]["content"]
    assert "不是本章任务清单" in content
    assert "不要逐条复述" in content
    assert "硬边界（不可无过渡违反）" in content
    assert "软倾向（可被情境改变）" in content
    assert "可变状态（允许随新证据更新）" in content
    assert "已知=黑色钥匙能响应黑塔密门" in content
    assert "未知=不知道密门后的守卫是谁" in content
    assert "能力边界=不能凭空知道黑塔机关" in content
    assert "从逞强独闯转向承认自己需要验证线索" in content
    for locked_phrase in ["必须写", "必写", "必须展开", "固定发展路线"]:
        assert locked_phrase not in content


def test_review_chapter_flags_cognition_and_capability_without_transition(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.write_character_cards(
        "novel-review-1",
        [
            {
                "character_id": "lin-che",
                "name": "林澈",
                "first_seen_chapter": 1,
                "last_seen_chapter": 1,
                "aliases": [],
                "recent_events": [],
                "status": "active",
                "cognitive_state": {
                    "known_facts": ["黑色钥匙能响应黑塔密门"],
                    "unknowns": ["不知道钥匙会消耗记忆"],
                    "misbeliefs": ["误以为钥匙可以打开所有门"],
                },
                "emotional_arc": [],
                "growth_arc": {"stage": "谨慎试探", "changes": []},
                "capability_limits": ["不能凭空知道黑塔机关"],
                "decision_biases": [],
            }
        ],
    )

    result = service.review_chapter(
        {
            "novel_id": "novel-review-1",
            "chapter_number": 2,
            "payload": {"content": "林澈知道钥匙会消耗记忆，并且一眼看穿黑塔机关，直接打开所有门。"},
        }
    )

    issue_types = {item["issue_type"] for item in result["data"]["issues"]}
    assert "evolution_character_cognition" in issue_types
    assert "evolution_character_capability" in issue_types
    assert result["data"]["suggestions"]


def test_review_chapter_allows_explained_cognition_transition(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.write_character_cards(
        "novel-review-2",
        [
            {
                "character_id": "lin-che",
                "name": "林澈",
                "first_seen_chapter": 1,
                "last_seen_chapter": 1,
                "aliases": [],
                "recent_events": [],
                "status": "active",
                "cognitive_state": {
                    "known_facts": [],
                    "unknowns": ["不知道钥匙会消耗记忆"],
                    "misbeliefs": [],
                },
                "emotional_arc": [],
                "growth_arc": {"stage": "谨慎试探", "changes": []},
                "capability_limits": ["不能凭空知道黑塔机关"],
                "decision_biases": [],
            }
        ],
    )

    result = service.review_chapter(
        {
            "novel_id": "novel-review-2",
            "chapter_number": 2,
            "payload": {"content": "林澈从顾衡留下的线索得知钥匙会消耗记忆，于是先试探机关，没有直接断定答案。"},
        }
    )

    issue_types = {item["issue_type"] for item in result["data"]["issues"]}
    assert "evolution_character_cognition" not in issue_types
    assert "evolution_character_capability" not in issue_types


def test_review_chapter_flags_palette_missing_and_drift(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.write_character_cards(
        "novel-palette-review",
        [
            {
                "character_id": "shen-yan",
                "name": "沈砚",
                "first_seen_chapter": 1,
                "last_seen_chapter": 1,
                "status": "active",
                "recent_events": [{"chapter_number": 1, "summary": "沈砚进入C307。"}],
                "personality_palette": {"base": "", "main_tones": [], "accents": [], "derivatives": []},
            },
            {
                "character_id": "gu-lan",
                "name": "顾岚",
                "first_seen_chapter": 1,
                "last_seen_chapter": 1,
                "status": "active",
                "recent_events": [{"chapter_number": 1, "summary": "顾岚保持谨慎。"}],
                "personality_palette": {
                    "base": "谨慎",
                    "main_tones": ["克制"],
                    "accents": ["保护欲"],
                    "derivatives": [{"tone": "克制", "description": "行动前会先确认风险。"}],
                },
            },
        ],
    )

    missing_review = service.review_chapter(
        {
            "novel_id": "novel-palette-review",
            "chapter_number": 2,
            "payload": {"content": "沈砚站在C307门口，沈砚没有继续前进。"},
        }
    )
    assert any(item["issue_type"] == "evolution_palette_missing" for item in missing_review["data"]["issues"])

    drift_review = service.review_chapter(
        {
            "novel_id": "novel-palette-review",
            "chapter_number": 2,
            "payload": {"content": "顾岚突然变得像换了个人，毫无理由地冲进档案馆。"},
        }
    )
    assert any(item["issue_type"] == "evolution_palette_drift" for item in drift_review["data"]["issues"])


def test_review_chapter_reports_current_route_conflicts(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    service.repository.save_story_graph_chapter(
        "novel-route-review",
        2,
        {
            "schema_version": 1,
            "novel_id": "novel-route-review",
            "chapter_number": 2,
            "entities": [],
            "locations": [],
            "events": [],
            "route_edges": [],
            "conflicts": [
                {
                    "type": "repeated_arrival",
                    "severity": "hard",
                    "character": "沈砚",
                    "chapter_previous": 1,
                    "chapter_current": 2,
                    "previous_location": "C307",
                    "current_location": "C307",
                    "message": "沈砚上一记录已在C307，本章开头又写成重新抵达/进入，像状态重置。",
                    "evidence": "沈砚进入C307。",
                }
            ],
            "vectors": [],
        },
    )

    result = service.review_chapter(
        {
            "novel_id": "novel-route-review",
            "chapter_number": 2,
            "payload": {"content": "沈砚进入C307。"},
        }
    )

    issues = result["data"]["issues"]
    assert any(item["issue_type"] == "evolution_route_repeated_arrival" for item in issues)
    assert any(item["severity"] == "critical" for item in issues)


def test_transition_analysis_flags_repeated_arrival_time_and_object_conflicts():
    result = analyze_chapter_transitions(
        [
            {
                "chapter_number": 1,
                "content": "沈砚进入C307，找到黑匣子并播放第一段录音。结尾时沈砚离开C307。",
            },
            {
                "chapter_number": 2,
                "content": "沈砚在宿舍区走了十分钟，才找到C307。他把黑匣子放在桌上。",
            },
            {
                "chapter_number": 3,
                "content": "演习结束的警报响起。沈砚把黑匣子锁进书桌抽屉，随后离开宿舍区。",
            },
            {
                "chapter_number": 4,
                "content": "沈砚在C区避难点等待广播通知演习结束，随后从帆布包里取出黑匣子。",
            },
        ]
    )

    conflict_types = {item["type"] for item in result["conflicts"]}
    assert "repeated_arrival" in conflict_types
    assert "time_rollback" in conflict_types
    assert "object_teleport" in conflict_types
    assert result["aggregate"]["hard_conflict_count"] >= 3


def test_character_rebuild_replaces_stale_index_entries(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    service.repository.write_character_card(
        "novel-stale-index",
        {
            "character_id": "stale",
            "name": "顾岚从",
            "first_seen_chapter": 1,
            "last_seen_chapter": 1,
            "aliases": [],
            "recent_events": [],
            "status": "active",
        },
    )
    service.repository.write_character_cards(
        "novel-stale-index",
        [
            {
                "character_id": "gu-lan",
                "name": "顾岚",
                "first_seen_chapter": 1,
                "last_seen_chapter": 1,
                "aliases": [],
                "recent_events": [],
                "status": "active",
            }
        ],
    )

    names = [item["name"] for item in service.list_characters("novel-stale-index")["items"]]
    assert names == ["顾岚"]


@pytest.mark.asyncio
async def test_after_novel_created_seeds_prehistory_worldline_by_novel(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    result = await service.after_novel_created(
        {
            "novel_id": "novel-prehistory",
            "payload": {
                "title": "星海遗民",
                "genre": "星际史诗",
                "world_preset": "帝国衰亡后的多文明冲突",
                "premise": "主角在旧帝国档案中发现文明灭绝的真相。",
                "target_chapters": 800,
                "length_tier": "epic",
            },
        }
    )

    assert result["ok"] is True
    saved = service.repository.get_prehistory_worldline("novel-prehistory")
    assert saved is not None
    assert saved["novel_id"] == "novel-prehistory"
    assert saved["depth"]["tier"] == "epic"
    assert saved["depth"]["horizon_years"] >= 3000
    assert len(saved["eras"]) >= 6
    assert saved["foreshadow_seeds"]


@pytest.mark.asyncio
async def test_epic_prehistory_is_deeper_than_intimate_story(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))

    await service.after_novel_created(
        {
            "novel_id": "novel-epic-depth",
            "payload": {"title": "仙门旧纪", "genre": "修仙", "premise": "宗门隐藏飞升真相。", "target_chapters": 600},
        }
    )
    await service.after_novel_created(
        {
            "novel_id": "novel-intimate-depth",
            "payload": {"title": "夏日乐队", "genre": "校园恋爱", "premise": "少女在乐队中找回真实自我。", "target_chapters": 80},
        }
    )

    epic = service.repository.get_prehistory_worldline("novel-epic-depth")
    intimate = service.repository.get_prehistory_worldline("novel-intimate-depth")
    assert epic["depth"]["horizon_years"] > intimate["depth"]["horizon_years"]
    assert len(epic["eras"]) > len(intimate["eras"])


@pytest.mark.asyncio
async def test_before_story_planning_returns_worldline_and_foreshadow_context(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    await service.after_novel_created(
        {
            "novel_id": "novel-planning-context",
            "payload": {"title": "旧案回声", "genre": "悬疑权谋", "premise": "主角调查被抹去的贵族学校旧案。", "target_chapters": 240},
        }
    )

    result = service.before_story_planning(
        {"novel_id": "novel-planning-context", "payload": {"purpose": "setup_main_plot_options"}}
    )

    assert result["ok"] is True
    block = result["context_blocks"][0]
    assert block["title"] == "Evolution 故事前史与伏笔库"
    assert "故事开始前的世界线" in block["content"]
    assert "可用于大纲与伏笔的种子" in block["content"]
    assert result["data"]["foreshadow_seeds"]


@pytest.mark.asyncio
async def test_story_planning_context_adapts_to_runtime_style_hint(tmp_path):
    storage = PluginStorage(root=tmp_path)
    service = EvolutionWorldAssistantService(storage=storage, jobs=PluginJobRegistry(storage))
    await service.after_novel_created(
        {
            "novel_id": "novel-style-adapt",
            "payload": {
                "title": "雾港来信",
                "genre": "悬疑",
                "premise": "主角追查一封被迟寄十年的信。",
                "target_chapters": 180,
                "style_hint": "冷硬黑色侦探文风，短句，克制，像旧伤一样揭开真相。",
            },
        }
    )

    result = service.before_story_planning(
        {
            "novel_id": "novel-style-adapt",
            "payload": {
                "purpose": "macro_outline_planning",
                "style_hint": "改为诗性散文文风，意象浓，节奏舒缓，用海雾、灯和旧信承载伏笔。",
            },
        }
    )

    assert result["ok"] is True
    adapter = result["data"]["style_adapter"]
    content = result["context_blocks"][0]["content"]
    assert adapter["style_source"] == "runtime_payload"
    assert adapter["primary_style"] == "poetic_lyrical"
    assert "文风适配协议" in content
    assert "语义蓝图" in content
    assert "诗性散文" in content
    assert "不能原样写进正文" in content
