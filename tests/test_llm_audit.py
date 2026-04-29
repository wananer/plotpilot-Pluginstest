import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.ai.llm_audit import audit_generate_call, audit_stream_call, llm_audit_context, write_audit_inventory
from application.blueprint.services.volume_summary_service import VolumeSummaryService
from domain.ai.services.llm_service import GenerationConfig, GenerationResult
from domain.ai.value_objects.prompt import Prompt
from domain.ai.value_objects.token_usage import TokenUsage
from domain.structure.story_node import NodeType


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_generate_audit_writes_prompt_output_usage_and_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_RUN_ID", "unit-audit")
    monkeypatch.setenv("LLM_AUDIT_OUTPUT_DIR", str(tmp_path / "llm_calls"))

    prompt = Prompt(system="system text", user="user text")
    config = GenerationConfig(model="unit-model", max_tokens=128, temperature=0.2)

    async def call():
        return GenerationResult("model output", TokenUsage(input_tokens=11, output_tokens=7))

    with llm_audit_context(
        novel_id="frontend-experiment-on-unit",
        chapter_number=3,
        phase="chapter_narrative_sync",
        api_key="sk-secretshouldnotappear",
    ):
        result = await audit_generate_call(call, prompt=prompt, config=config)

    assert result.content == "model output"
    records = _read_jsonl(tmp_path / "llm_calls" / "calls.jsonl")
    assert len(records) == 1
    record = records[0]
    assert record["phase"] == "chapter_narrative_sync"
    assert record["arm"] == "experiment_on"
    assert record["chapter_number"] == 3
    assert record["token_usage"]["total_tokens"] == 18

    call_dir = Path(record["paths"]["dir"])
    assert json.loads((call_dir / "prompt.json").read_text(encoding="utf-8"))["prompt"]["user"] == "user text"
    assert (call_dir / "output.md").read_text(encoding="utf-8") == "model output"
    assert json.loads((call_dir / "usage.json").read_text(encoding="utf-8"))["status"] == "success"
    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "llm_calls").rglob("*") if path.is_file())
    assert "sk-secretshouldnotappear" not in artifact_text


@pytest.mark.asyncio
async def test_stream_audit_writes_chunks_and_joined_output(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_OUTPUT_DIR", str(tmp_path / "llm_calls"))

    prompt = Prompt(system="system", user="stream prompt")
    config = GenerationConfig(model="unit-model", max_tokens=128, temperature=0.2)

    async def stream():
        yield "第一段"
        yield "第二段"

    chunks = []
    with llm_audit_context(novel_id="frontend-control-off-unit", chapter_number=1, phase="chapter_generation_stream"):
        async for chunk in audit_stream_call(stream, prompt=prompt, config=config):
            chunks.append(chunk)

    assert chunks == ["第一段", "第二段"]
    records = _read_jsonl(tmp_path / "llm_calls" / "calls.jsonl")
    record = records[0]
    assert record["stream"] is True
    assert record["arm"] == "control_off"
    call_dir = Path(record["paths"]["dir"])
    chunk_lines = _read_jsonl(call_dir / "chunks.jsonl")
    assert [item["text"] for item in chunk_lines] == ["第一段", "第二段"]
    assert (call_dir / "output.md").read_text(encoding="utf-8") == "第一段第二段"
    assert json.loads((call_dir / "usage.json").read_text(encoding="utf-8"))["token_usage"]["estimated"] is True


@pytest.mark.asyncio
async def test_audit_redacts_common_secret_shapes(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_OUTPUT_DIR", str(tmp_path / "llm_calls"))
    secret = "sk-abcdefghijklmnopqrstuvwxyz"
    private_key = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"

    prompt = Prompt(system=f"api_key: {secret}", user=private_key)
    config = GenerationConfig(model="unit-model", max_tokens=32, temperature=0.1)

    async def call():
        return GenerationResult(f"done {secret}", TokenUsage(input_tokens=1, output_tokens=1))

    with llm_audit_context(phase="evolution_agent_control_card", private_key=private_key, headers={"Authorization": secret}):
        await audit_generate_call(call, prompt=prompt, config=config)

    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "llm_calls").rglob("*") if path.is_file())
    assert secret not in artifact_text
    assert "-----BEGIN PRIVATE KEY-----" not in artifact_text
    assert "sk-[REDACTED]" in artifact_text


@pytest.mark.asyncio
async def test_audit_normalizes_evolution_hook_phases(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_OUTPUT_DIR", str(tmp_path / "llm_calls"))

    prompt = Prompt(system="system", user="agent prompt")
    config = GenerationConfig(model="agent-model", max_tokens=32, temperature=0.1)

    async def call():
        return GenerationResult("{}", TokenUsage(input_tokens=1, output_tokens=1))

    with llm_audit_context(novel_id="frontend-experiment-on-unit", phase="evolution_before_context_build"):
        await audit_generate_call(call, prompt=prompt, config=config)

    with llm_audit_context(novel_id="frontend-experiment-on-unit", phase="evolution_after_chapter_review"):
        await audit_generate_call(call, prompt=prompt, config=config)

    records = _read_jsonl(tmp_path / "llm_calls" / "calls.jsonl")
    assert [record["phase"] for record in records] == [
        "evolution_agent_control_card",
        "evolution_agent_reflection",
    ]


@pytest.mark.asyncio
async def test_audit_inventory_lists_calls_by_chapter(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_RUN_ID", "inventory-unit")
    audit_dir = tmp_path / "run" / "llm_calls"
    monkeypatch.setenv("LLM_AUDIT_OUTPUT_DIR", str(audit_dir))

    prompt = Prompt(system="system", user="prompt")
    config = GenerationConfig(model="unit-model", max_tokens=32, temperature=0.1)

    async def call():
        return GenerationResult("output", TokenUsage(input_tokens=2, output_tokens=3))

    with llm_audit_context(novel_id="frontend-experiment-on-unit", chapter_number=2, phase="evolution_agent_reflection"):
        await audit_generate_call(call, prompt=prompt, config=config)

    manifest = write_audit_inventory(audit_dir)
    assert manifest["total_calls"] == 1
    assert manifest["phase_counts"]["evolution_agent_reflection"] == 1
    assert manifest["chapters"]["experiment_on/chapter_02"] == 1
    assert manifest["complete"] is True
    assert (tmp_path / "run" / "frontend_pressure_manifest.json").exists()
    inventory = (tmp_path / "run" / "llm_call_inventory.md").read_text(encoding="utf-8")
    assert "evolution_agent_reflection" in inventory
    assert "chapter_02" in inventory


@pytest.mark.asyncio
async def test_volume_summary_audit_context_marks_act_summary_as_planning(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_AUDIT_ENABLED", "true")
    monkeypatch.setenv("LLM_AUDIT_OUTPUT_DIR", str(tmp_path / "llm_calls"))

    class FakeLLM:
        async def generate(self, prompt, config):
            async def call():
                return GenerationResult("幕摘要输出", TokenUsage(input_tokens=5, output_tokens=3))

            return await audit_generate_call(call, prompt=prompt, config=config)

    class FakeStoryNodeRepository:
        def __init__(self):
            self.act = SimpleNamespace(
                id="act-1",
                title="噪声与徽章",
                description="开场幕",
                metadata={},
                chapter_start=1,
                chapter_end=2,
            )
            self.chapter = SimpleNamespace(
                id="chapter-node-1",
                node_type=NodeType.CHAPTER,
                number=1,
                title="黑匣子",
                outline="沈砚在海上城邦发现旧AI黑匣子。",
                description="",
            )

        async def get_by_id(self, node_id):
            return self.act if node_id == self.act.id else None

        def get_children_sync(self, node_id):
            return [self.chapter] if node_id == self.act.id else []

        async def update(self, node):
            self.updated = node

    service = VolumeSummaryService(
        llm_service=FakeLLM(),
        story_node_repository=FakeStoryNodeRepository(),
    )

    result = await service.generate_act_summary("frontend-experiment-on-unit", "act-1")

    assert result.success is True
    records = _read_jsonl(tmp_path / "llm_calls" / "calls.jsonl")
    assert len(records) == 1
    record = records[0]
    assert record["novel_id"] == "frontend-experiment-on-unit"
    assert record["phase"] == "chapter_outline_suggestion"
    assert record["chapter_number"] is None
    assert record["arm"] == "experiment_on"
    assert record["metadata"]["summary_level"] == "act"
