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
from application.engine.services.autopilot_daemon import AutopilotDaemon


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


class InMemoryChapterRepository:
    def __init__(self, chapters=None):
        self.chapters = chapters or []

    def list_by_novel(self, novel_id):
        return list(self.chapters)


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


@pytest.mark.asyncio
async def test_status_includes_boundary_gate_fields(monkeypatch):
    novel = Novel(
        id=NovelId("novel-boundary"),
        title="测试小说",
        author="tester",
        target_chapters=10,
        autopilot_status=AutopilotStatus.STOPPED,
        current_stage=NovelStage.PAUSED_FOR_REVIEW,
        boundary_gate_status="needs_review",
        last_boundary_issue={
            "chapter": 6,
            "reason": "recheck_still_failed",
        },
        revision_attempts=2,
        chapter_draft_status="auto_revised",
        last_chapter_draft_issue={"chapter": 6, "reason": "chapter_execution_draft_auto_revised"},
        route_gate_status="needs_review",
        last_route_issue={"issue_type": "evolution_route_missing_transition"},
        auto_revision_history=[{"chapter": 5}, {"chapter": 6}],
        constraint_gate_status="needs_review",
        last_constraint_issue={"constraint_type": "entity_identity"},
        constraint_revision_history=[{"chapter": 6, "constraint_type": "entity_identity"}],
    )
    monkeypatch.setattr(
        autopilot_routes,
        "get_novel_repository",
        lambda: InMemoryNovelRepository(novel),
    )
    monkeypatch.setattr(
        autopilot_routes,
        "get_chapter_repository",
        lambda: InMemoryChapterRepository(),
    )

    response = await autopilot_routes.get_autopilot_status("novel-boundary")

    assert response["needs_review"] is True
    assert response["boundary_gate_status"] == "needs_review"
    assert response["last_boundary_issue"]["chapter"] == 6
    assert response["revision_attempts"] == 2
    assert response["chapter_draft_status"] == "auto_revised"
    assert response["last_chapter_draft_issue"]["chapter"] == 6
    assert response["route_gate_status"] == "needs_review"
    assert response["last_route_issue"]["issue_type"] == "evolution_route_missing_transition"
    assert response["auto_revision_history"][-1]["chapter"] == 6
    assert response["constraint_gate_status"] == "needs_review"
    assert response["last_constraint_issue"]["constraint_type"] == "entity_identity"
    assert response["constraint_revision_history"][-1]["constraint_type"] == "entity_identity"


def test_style_drift_warning_stays_out_of_constraint_gate_status():
    novel = Novel(
        id=NovelId("novel-style"),
        title="测试小说",
        author="tester",
        target_chapters=10,
        constraint_gate_status="passed",
    )
    daemon = object.__new__(AutopilotDaemon)

    daemon._merge_style_constraint_issue(
        novel,
        {
            "drift_alert": True,
            "constraint_status": "passed",
            "constraint_issue": {
                "constraint_type": "narrative_voice",
                "severity": "warning",
                "confidence": 1.0,
                "repair_hint": "保持文风。",
                "evidence": [{"chapter_number": 5, "similarity_score": 0.7}],
            },
        },
    )

    assert novel.constraint_gate_status == "passed"
    assert novel.last_constraint_issue == {}
    assert novel.last_audit_issues[-1]["issue_type"] == "evolution_style_drift"
    assert novel.last_audit_issues[-1]["severity"] == "warning"


def test_style_drift_needs_review_sets_constraint_gate_status():
    novel = Novel(
        id=NovelId("novel-style-review"),
        title="测试小说",
        author="tester",
        target_chapters=10,
        constraint_gate_status="passed",
    )
    daemon = object.__new__(AutopilotDaemon)

    daemon._merge_style_constraint_issue(
        novel,
        {
            "drift_alert": True,
            "constraint_status": "needs_review",
            "constraint_issue": {
                "constraint_type": "narrative_voice",
                "severity": "needs_review",
                "confidence": 1.0,
                "repair_hint": "保持文风。",
                "evidence": [{"chapter_number": 5, "similarity_score": 0.5}],
            },
        },
    )

    assert novel.constraint_gate_status == "needs_review"
    assert novel.last_constraint_issue["constraint_type"] == "narrative_voice"
    assert novel.last_constraint_issue["issue_type"] == "evolution_style_drift"
