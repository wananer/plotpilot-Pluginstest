import sys
import types
import importlib.util

import pytest

from domain.novel.entities.novel import AutopilotStatus, Novel, NovelStage
from domain.novel.value_objects.novel_id import NovelId

if "openai" not in sys.modules and importlib.util.find_spec("openai") is None:
    openai_stub = types.ModuleType("openai")
    openai_stub.AsyncOpenAI = object
    openai_stub.NotFoundError = RuntimeError
    openai_stub.BadRequestError = RuntimeError
    sys.modules["openai"] = openai_stub

from interfaces.api.v1.engine import autopilot_routes


class InMemoryNovelRepository:
    def __init__(self, novel):
        self.novel = novel
        self.saved = None

    def get_by_id(self, novel_id):
        if novel_id == self.novel.novel_id:
            return self.novel
        return None

    def save(self, novel):
        self.saved = novel


class RuntimeStub:
    def __init__(self, using_mock):
        self.using_mock = using_mock


class LLMControlServiceStub:
    def __init__(self, using_mock):
        self._using_mock = using_mock

    def get_runtime_summary(self):
        return RuntimeStub(self._using_mock)


@pytest.mark.asyncio
async def test_stop_autopilot_clears_auditing_progress(monkeypatch):
    novel = Novel(
        id=NovelId("novel-auditing"),
        title="测试小说",
        author="tester",
        target_chapters=10,
        autopilot_status=AutopilotStatus.RUNNING,
        current_stage=NovelStage.AUDITING,
        audit_progress="aftermath_pipeline",
    )
    repo = InMemoryNovelRepository(novel)
    monkeypatch.setattr(autopilot_routes, "get_novel_repository", lambda: repo)

    response = await autopilot_routes.stop_autopilot("novel-auditing")

    assert response["success"] is True
    assert repo.saved is novel
    assert novel.autopilot_status == AutopilotStatus.STOPPED
    assert novel.current_stage == NovelStage.PAUSED_FOR_REVIEW
    assert novel.audit_progress is None


@pytest.mark.asyncio
async def test_start_autopilot_rejects_mock_llm(monkeypatch):
    monkeypatch.setattr(
        autopilot_routes,
        "LLMControlService",
        lambda: LLMControlServiceStub(using_mock=True),
    )

    with pytest.raises(autopilot_routes.HTTPException) as exc:
        await autopilot_routes.start_autopilot("novel-mock")

    assert exc.value.status_code == 400
    assert "MockProvider" in exc.value.detail


@pytest.mark.asyncio
async def test_start_autopilot_allows_real_llm(monkeypatch):
    novel = Novel(
        id=NovelId("novel-real"),
        title="测试小说",
        author="tester",
        target_chapters=10,
        autopilot_status=AutopilotStatus.STOPPED,
        current_stage=NovelStage.PLANNING,
    )
    repo = InMemoryNovelRepository(novel)
    monkeypatch.setattr(autopilot_routes, "get_novel_repository", lambda: repo)
    monkeypatch.setattr(
        autopilot_routes,
        "LLMControlService",
        lambda: LLMControlServiceStub(using_mock=False),
    )

    response = await autopilot_routes.start_autopilot("novel-real")

    assert response["success"] is True
    assert repo.saved is novel
    assert novel.autopilot_status == AutopilotStatus.RUNNING
    assert novel.current_stage == NovelStage.MACRO_PLANNING
