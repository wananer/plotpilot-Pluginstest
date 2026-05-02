import pytest

from application.engine.services import chapter_aftermath_pipeline as module


class _DummyPipeline(module.ChapterAftermathPipeline):
    def __init__(self):
        super().__init__(
            knowledge_service=object(),
            chapter_indexing_service=object(),
            llm_service=object(),
        )


@pytest.mark.asyncio
async def test_aftermath_skips_native_sync_when_evolution_after_commit_succeeds(monkeypatch):
    async def fake_notify(*args, **kwargs):
        return [{"plugin_name": "world_evolution_core", "ok": True, "data": {"facts": {}}}]

    async def fail_native(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("native narrative sync should be skipped")

    async def noop_infer(*args, **kwargs):
        return None

    monkeypatch.setattr(module, "notify_chapter_committed", fake_notify)
    monkeypatch.setattr(module, "infer_kg_from_chapter", noop_infer)

    import application.world.services.chapter_narrative_sync as sync_module

    monkeypatch.setattr(sync_module, "sync_chapter_narrative_after_save", fail_native)

    result = await _DummyPipeline().run_after_chapter_saved("novel-1", 1, "正文内容")

    assert result["narrative_sync_ok"] is True
    assert result["narrative_sync_source"] == "evolution"
    assert result["plugin_after_commit_ok"] is True


@pytest.mark.asyncio
async def test_aftermath_uses_native_sync_when_evolution_is_unavailable(monkeypatch):
    async def fake_notify(*args, **kwargs):
        return []

    async def fake_native(*args, **kwargs):
        return {"vector_stored": True, "foreshadow_stored": False, "triples_extracted": True}

    async def noop_infer(*args, **kwargs):
        return None

    monkeypatch.setattr(module, "notify_chapter_committed", fake_notify)
    monkeypatch.setattr(module, "infer_kg_from_chapter", noop_infer)

    import application.world.services.chapter_narrative_sync as sync_module

    monkeypatch.setattr(sync_module, "sync_chapter_narrative_after_save", fake_native)

    result = await _DummyPipeline().run_after_chapter_saved("novel-1", 1, "正文内容")

    assert result["narrative_sync_ok"] is True
    assert result["narrative_sync_source"] == "native_fallback"
    assert result["vector_stored"] is True
    assert result["triples_extracted"] is True
