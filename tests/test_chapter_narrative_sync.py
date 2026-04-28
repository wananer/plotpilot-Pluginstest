import asyncio
import time

import pytest

from application.world.services import chapter_narrative_sync as sync_module


class FastIndexingService:
    def __init__(self):
        self.calls = []

    async def ensure_collection(self, novel_id: str) -> None:
        self.calls.append(("ensure", novel_id))

    async def index_chapter_summary(self, novel_id: str, chapter_number: int, summary: str) -> None:
        self.calls.append(("index", novel_id, chapter_number, summary))


class SlowIndexingService:
    async def ensure_collection(self, novel_id: str) -> None:
        await asyncio.sleep(0.2)

    async def index_chapter_summary(self, novel_id: str, chapter_number: int, summary: str) -> None:
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_index_chapter_summary_with_timeout_records_vector(monkeypatch):
    monkeypatch.setattr(sync_module, "CHAPTER_VECTOR_INDEX_MODE", "inline")
    monkeypatch.setattr(sync_module, "CHAPTER_VECTOR_INDEX_TIMEOUT_SECONDS", 1)
    indexing = FastIndexingService()

    ok = await sync_module._index_chapter_summary_with_timeout(
        indexing,
        "novel-fast",
        3,
        "chapter summary",
    )

    assert ok is True
    assert indexing.calls == [
        ("ensure", "novel-fast"),
        ("index", "novel-fast", 3, "chapter summary"),
    ]


@pytest.mark.asyncio
async def test_index_chapter_summary_with_timeout_does_not_block_daemon(monkeypatch):
    monkeypatch.setattr(sync_module, "CHAPTER_VECTOR_INDEX_MODE", "inline")
    monkeypatch.setattr(sync_module, "CHAPTER_VECTOR_INDEX_TIMEOUT_SECONDS", 0.01)
    started = time.perf_counter()

    with pytest.raises(asyncio.TimeoutError):
        await sync_module._index_chapter_summary_with_timeout(
            SlowIndexingService(),
            "novel-slow",
            1,
            "chapter summary",
        )

    assert time.perf_counter() - started < 0.15


@pytest.mark.asyncio
async def test_index_chapter_summary_defaults_to_background(monkeypatch):
    monkeypatch.setattr(sync_module, "CHAPTER_VECTOR_INDEX_MODE", "background")
    monkeypatch.setattr(sync_module, "CHAPTER_VECTOR_INDEX_TIMEOUT_SECONDS", 1)
    started = time.perf_counter()

    ok = await sync_module._index_chapter_summary_with_timeout(
        SlowIndexingService(),
        "novel-background",
        1,
        "chapter summary",
    )

    assert ok is False
    assert time.perf_counter() - started < 0.05
    await asyncio.sleep(0.45)
