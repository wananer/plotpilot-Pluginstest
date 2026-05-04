"""HostedWriteService 单元测试"""
import pytest
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace

import application.services.hosted_write_service as hosted_write_module
from application.services.hosted_write_service import HostedWriteService
from application.workflows.auto_novel_generation_workflow import AutoNovelGenerationWorkflow
from application.services.chapter_service import ChapterService
from application.services.novel_service import NovelService


@pytest.fixture
def mock_workflow():
    wf = Mock(spec=AutoNovelGenerationWorkflow)

    async def stream(*a, **k):
        yield {
            "type": "done",
            "content": "body",
            "consistency_report": {"issues": [], "warnings": [], "suggestions": []},
            "token_count": 10,
        }

    wf.generate_chapter_stream = stream
    wf.suggest_outline = AsyncMock(return_value="大纲要点")
    return wf


@pytest.fixture
def mock_chapter_service():
    svc = Mock(spec=ChapterService)
    svc.get_chapter_by_novel_and_number.return_value = None
    svc.update_chapter_by_novel_and_number = Mock()
    return svc


@pytest.fixture
def mock_novel_service():
    svc = Mock(spec=NovelService)
    svc.add_chapter = Mock()
    return svc


class FakeLLMService:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def generate(self, prompt, config):
        self.calls.append({"prompt": prompt, "config": config})
        return SimpleNamespace(content=self.content)

    async def stream_generate(self, prompt, config):
        yield self.content


def _boundary_issue():
    return {
        "issue_type": "evolution_unresolved_cliffhanger_skip",
        "severity": "critical",
        "description": "上一章尾钩被跳过。",
        "revision_required": True,
        "revision_mode": "manual_or_host_revision_required",
        "opening_revision_brief": {
            "target": "rewrite_opening_100_300_chars",
            "previous_ending_evidence": "沈砚停在B3门口，封条正在发光。",
            "current_opening_problem": "本章直接进入电梯井。",
            "required_bridge_type": "撤离/移动桥接",
            "preserve_after_opening": "保留后续电梯井探索内容。",
        },
    }


def _plugin_result(issues):
    return [
        {
            "plugin_name": "world_evolution_core",
            "ok": True,
            "data": {"issues": issues, "suggestions": []},
        }
    ]


@pytest.mark.asyncio
async def test_hosted_streams_events_and_saves(mock_workflow, mock_chapter_service, mock_novel_service):
    svc = HostedWriteService(mock_workflow, mock_chapter_service, mock_novel_service)
    events = []
    async for e in svc.stream_hosted_write(
        "novel-1", 1, 1, auto_save=True, auto_outline=True
    ):
        events.append(e)
    types = [x["type"] for x in events]
    assert "session" in types
    assert "outline" in types
    assert "saved" in types
    mock_chapter_service.update_chapter_by_novel_and_number.assert_called_once()


@pytest.mark.asyncio
async def test_suggest_outline_uses_llm(mock_workflow, mock_chapter_service, mock_novel_service):
    mock_workflow.suggest_outline = AsyncMock(return_value="auto")
    svc = HostedWriteService(mock_workflow, mock_chapter_service, mock_novel_service)
    events = []
    async for e in svc.stream_hosted_write(
        "novel-1", 2, 2, auto_save=False, auto_outline=True
    ):
        events.append(e)
    outline_ev = next(x for x in events if x["type"] == "outline")
    assert outline_ev["text"] == "auto"


@pytest.mark.asyncio
async def test_boundary_revision_skips_when_plugin_reports_no_issue(
    monkeypatch,
    mock_workflow,
    mock_chapter_service,
    mock_novel_service,
):
    async def no_issues(*args, **kwargs):
        return _plugin_result([])

    monkeypatch.setattr(hosted_write_module, "review_chapter_with_plugins", no_issues)
    llm = FakeLLMService("不应调用")
    svc = HostedWriteService(mock_workflow, mock_chapter_service, mock_novel_service, llm_service=llm)

    events = []
    async for event in svc.stream_hosted_write("novel-1", 1, 1, auto_save=True, auto_outline=False):
        events.append(event)

    assert any(event["type"] == "boundary_revision_skipped" and event["reason"] == "no_boundary_revision_required" for event in events)
    assert llm.calls == []
    mock_chapter_service.update_chapter_by_novel_and_number.assert_called_once_with("novel-1", 1, "body")


@pytest.mark.asyncio
async def test_boundary_revision_rewrites_opening_and_saves_revised_content(
    monkeypatch,
    mock_workflow,
    mock_chapter_service,
    mock_novel_service,
):
    async def stream(*a, **k):
        yield {
            "type": "done",
            "content": "电梯井的门后是一片黑暗。\n\n后续正文保持不变，沈砚继续向下探索。",
        }

    mock_workflow.generate_chapter_stream = stream
    review_calls = []

    async def boundary_then_pass(*args, **kwargs):
        review_calls.append({"content": args[2], "source": kwargs.get("source")})
        return _plugin_result([_boundary_issue()] if len(review_calls) == 1 else [])

    monkeypatch.setattr(hosted_write_module, "review_chapter_with_plugins", boundary_then_pass)
    llm = FakeLLMService("沈砚先从B3门口撤离，沿着封条旁的检修梯进入电梯井，确认身后没有追兵。")
    svc = HostedWriteService(mock_workflow, mock_chapter_service, mock_novel_service, llm_service=llm)

    events = []
    async for event in svc.stream_hosted_write("novel-1", 2, 2, auto_save=True, auto_outline=False):
        events.append(event)

    saved_content = mock_chapter_service.update_chapter_by_novel_and_number.call_args.args[2]
    assert saved_content.startswith("沈砚先从B3门口撤离")
    assert "后续正文保持不变" in saved_content
    assert "电梯井的门后是一片黑暗" not in saved_content
    assert any(event["type"] == "boundary_revision_start" for event in events)
    assert any(event["type"] == "boundary_revision_applied" for event in events)
    assert [call["source"] for call in review_calls] == [
        "hosted_write_boundary_gate",
        "hosted_write_boundary_gate_recheck",
    ]


@pytest.mark.asyncio
async def test_boundary_revision_reports_required_when_recheck_still_fails(
    monkeypatch,
    mock_workflow,
    mock_chapter_service,
    mock_novel_service,
):
    async def always_boundary(*args, **kwargs):
        return _plugin_result([_boundary_issue()])

    monkeypatch.setattr(hosted_write_module, "review_chapter_with_plugins", always_boundary)
    llm = FakeLLMService("沈砚从B3门口撤离，但桥接仍然不足。")
    svc = HostedWriteService(mock_workflow, mock_chapter_service, mock_novel_service, llm_service=llm)

    events = []
    async for event in svc.stream_hosted_write("novel-1", 3, 3, auto_save=True, auto_outline=False):
        events.append(event)

    assert any(event["type"] == "boundary_revision_required" and event["reason"] == "recheck_still_failed" for event in events)
    assert not any(event["type"] == "boundary_revision_applied" for event in events)
    saved_content = mock_chapter_service.update_chapter_by_novel_and_number.call_args.args[2]
    assert saved_content.startswith("沈砚从B3门口撤离")


@pytest.mark.asyncio
async def test_boundary_revision_rejects_non_manuscript_llm_output(
    monkeypatch,
    mock_workflow,
    mock_chapter_service,
    mock_novel_service,
):
    async def boundary_issue(*args, **kwargs):
        return _plugin_result([_boundary_issue()])

    monkeypatch.setattr(hosted_write_module, "review_chapter_with_plugins", boundary_issue)
    llm = FakeLLMService('{"opening": "沈砚撤离"}')
    svc = HostedWriteService(mock_workflow, mock_chapter_service, mock_novel_service, llm_service=llm)

    events = []
    async for event in svc.stream_hosted_write("novel-1", 4, 4, auto_save=True, auto_outline=False):
        events.append(event)

    assert any(event["type"] == "boundary_revision_required" and event["reason"] == "rewrite_failed" for event in events)
    mock_chapter_service.update_chapter_by_novel_and_number.assert_called_once_with("novel-1", 4, "body")
