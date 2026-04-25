import json
from pathlib import Path

import pytest

from plugins.platform.context_bridge import dispatch_hook_sync, render_context_blocks
from plugins.platform.hook_dispatcher import clear_hooks, dispatch_hook, list_hooks, register_hook
from plugins.platform.host_integration import build_generation_context_patch, notify_chapter_committed, review_chapter_with_plugins
from plugins.platform.job_registry import PluginJobRecord, PluginJobRegistry
from plugins.platform.plugin_storage import PluginStorage


@pytest.mark.asyncio
async def test_hook_dispatcher_runs_registered_handlers():
    clear_hooks()

    def handler(payload):
        return {
            "ok": True,
            "data": {"novel_id": payload["novel_id"]},
            "context_blocks": [{"title": "Role State", "content": "A is here"}],
        }

    register_hook("dynamic_rolecard", "before_context_build", handler)

    assert list_hooks() == {"before_context_build": ["dynamic_rolecard"]}
    results = await dispatch_hook("before_context_build", {"novel_id": "novel-1"})

    assert results == [
        {
            "plugin_name": "dynamic_rolecard",
            "hook_name": "before_context_build",
            "ok": True,
            "data": {"novel_id": "novel-1"},
            "context_blocks": [{"title": "Role State", "content": "A is here"}],
        }
    ]
    clear_hooks()


def test_plugin_storage_scopes_state_under_plugin_root(tmp_path):
    storage = PluginStorage(root=tmp_path)

    path = storage.write_json("dynamic_rolecard", ["novels", "novel-1", "state.json"], {"ok": True})

    assert path == tmp_path / "dynamic_rolecard" / "novels" / "novel-1" / "state.json"
    assert storage.read_json("dynamic_rolecard", ["novels", "novel-1", "state.json"]) == {"ok": True}

    with pytest.raises(ValueError):
        storage.write_json("dynamic_rolecard", ["..", "escape.json"], {})


def test_job_registry_appends_jsonl_and_builds_dedup_key(tmp_path):
    storage = PluginStorage(root=tmp_path)
    registry = PluginJobRegistry(storage=storage)
    dedup_key = registry.build_dedup_key(
        "dynamic_rolecard",
        "after_commit",
        "novel-1",
        chapter_number=3,
        content_hash="abc",
    )
    record = PluginJobRecord(
        plugin_name="dynamic_rolecard",
        hook_name="after_commit",
        novel_id="novel-1",
        chapter_number=3,
        trigger_type="auto",
        dedup_key=dedup_key,
        input_json={"chapter": 3},
    )

    registry.append(record)

    jobs_path = tmp_path / "dynamic_rolecard" / "jobs.jsonl"
    lines = jobs_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["dedup_key"] == "dynamic_rolecard:after_commit:novel-1:3:abc:auto"
    assert payload["status"] == "pending"



def test_context_bridge_renders_before_context_blocks():
    clear_hooks()

    def handler(payload):
        assert payload["chapter_number"] == 3
        return {
            "ok": True,
            "context_blocks": [
                {"title": "动态角色状态", "content": "林澈最近在黑塔，持有黑色钥匙。", "priority": 88}
            ],
        }

    register_hook("world_evolution_core", "before_context_build", handler)

    results = dispatch_hook_sync("before_context_build", {"novel_id": "novel-1", "chapter_number": 3})
    rendered = render_context_blocks(results)

    assert "【动态角色状态】" in rendered
    assert "黑色钥匙" in rendered
    clear_hooks()



def test_host_integration_builds_generation_context_patch():
    clear_hooks()

    register_hook(
        "world_evolution_core",
        "before_context_build",
        lambda payload: {
            "ok": True,
            "context_blocks": [{"title": "Evolution World State", "content": "林澈持有黑色钥匙。"}],
        },
    )

    context = build_generation_context_patch("novel-1", 3, "林澈进入黑塔")

    assert "Evolution World State" in context
    assert "黑色钥匙" in context
    clear_hooks()


@pytest.mark.asyncio
async def test_host_integration_notifies_chapter_committed():
    clear_hooks()
    seen = {}

    async def handler(payload):
        seen.update(payload)
        return {"ok": True, "data": {"updated": True}}

    register_hook("world_evolution_core", "after_commit", handler)

    results = await notify_chapter_committed("novel-1", 2, "《林澈》进入黑塔。")

    assert results[0]["data"] == {"updated": True}
    assert seen["source"] == "chapter_aftermath_pipeline"
    assert seen["payload"]["content"] == "《林澈》进入黑塔。"
    clear_hooks()


@pytest.mark.asyncio
async def test_host_integration_reviews_chapter_with_plugins():
    clear_hooks()
    seen = {}

    async def handler(payload):
        seen.update(payload)
        return {
            "ok": True,
            "data": {
                "issues": [
                    {
                        "issue_type": "evolution_character_logic",
                        "severity": "warning",
                        "description": "林澈突然知道钥匙代价，但此前状态仍标记为未知。",
                        "location": "Chapter 4",
                        "suggestion": "补一笔他如何得知代价，或改为怀疑/推测。",
                    }
                ],
                "suggestions": ["让 Evolution 审稿意见作为 PlotPilot 原有审稿建议的补充。"],
            },
        }

    register_hook("world_evolution_core", "review_chapter", handler)

    results = await review_chapter_with_plugins("novel-1", 4, "林澈知道钥匙会消耗记忆。")

    assert results[0]["data"]["issues"][0]["issue_type"] == "evolution_character_logic"
    assert seen["source"] == "chapter_review_service"
    assert seen["chapter_number"] == 4
    assert seen["payload"]["content"] == "林澈知道钥匙会消耗记忆。"
    clear_hooks()
