from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.loader as plugin_loader
from plugins.platform.context_bridge import dispatch_hook_sync, render_context_blocks
from plugins.platform.hook_dispatcher import clear_hooks, dispatch_hook, list_hooks, register_hook
from plugins.platform.host_database import ReadOnlyHostDatabase
from plugins.platform.host_facade import PlotPilotPluginHost
from plugins.platform.host_integration import (
    build_generation_context_patch,
    collect_story_planning_context_with_plugins,
    collect_chapter_review_context_with_plugins,
    notify_chapter_committed,
    notify_novel_created_with_plugins,
    notify_chapter_review_completed,
    review_chapter_with_plugins,
)
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

    register_hook("sample_state_plugin", "before_context_build", handler)

    assert list_hooks() == {"before_context_build": ["sample_state_plugin"]}
    results = await dispatch_hook("before_context_build", {"novel_id": "novel-1"})

    assert results == [
        {
            "plugin_name": "sample_state_plugin",
            "hook_name": "before_context_build",
            "ok": True,
            "data": {"novel_id": "novel-1"},
            "context_blocks": [{"title": "Role State", "content": "A is here"}],
        }
    ]
    clear_hooks()


def test_plugin_storage_scopes_state_under_plugin_root(tmp_path):
    storage = PluginStorage(root=tmp_path)

    path = storage.write_json("sample_state_plugin", ["novels", "novel-1", "state.json"], {"ok": True})

    assert path == tmp_path / "sample_state_plugin" / "novels" / "novel-1" / "state.json"
    assert not path.exists()
    assert (tmp_path / "plugin_platform.db").exists()
    assert storage.read_json("sample_state_plugin", ["novels", "novel-1", "state.json"]) == {"ok": True}
    assert storage.read_json("sample_state_plugin", ["novels", "novel-2", "state.json"], default=None) is None

    with pytest.raises(ValueError):
        storage.write_json("sample_state_plugin", ["..", "escape.json"], {})


def test_plugin_storage_lists_and_logs_by_novel_namespace(tmp_path):
    storage = PluginStorage(root=tmp_path)

    storage.write_json("sample_state_plugin", ["novels", "novel-a", "facts", "chapter_1.json"], {"novel_id": "novel-a", "chapter_number": 1})
    storage.write_json("sample_state_plugin", ["novels", "novel-a", "facts", "chapter_2.json"], {"novel_id": "novel-a", "chapter_number": 2})
    storage.write_json("sample_state_plugin", ["novels", "novel-a", "facts", "chapter_10.json"], {"novel_id": "novel-a", "chapter_number": 10})
    storage.write_json("sample_state_plugin", ["novels", "novel-b", "facts", "chapter_1.json"], {"novel_id": "novel-b", "chapter_number": 1})
    storage.append_jsonl("sample_state_plugin", ["novels", "novel-a", "runs.jsonl"], {"novel_id": "novel-a", "run": 1})
    storage.append_jsonl("sample_state_plugin", ["novels", "novel-b", "runs.jsonl"], {"novel_id": "novel-b", "run": 1})

    assert [item["chapter_number"] for item in storage.list_json("sample_state_plugin", ["novels", "novel-a", "facts"])] == [1, 2, 10]
    assert [
        item["chapter_number"]
        for item in storage.list_json(
            "sample_state_plugin",
            ["novels", "novel-a", "facts"],
            before_chapter=10,
            limit=1,
            reverse=True,
        )
    ] == [2]
    assert storage.list_json("sample_state_plugin", ["novels", "novel-b", "facts"]) == [{"chapter_number": 1, "novel_id": "novel-b"}]
    assert storage.read_jsonl("sample_state_plugin", ["novels", "novel-a", "runs.jsonl"]) == [{"novel_id": "novel-a", "run": 1}]


def test_plugin_storage_default_root_is_dedicated_plugin_platform_area():
    storage = PluginStorage()

    assert storage.root.name == "plugin_platform"


def test_disabled_plugin_routes_and_static_are_blocked(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "guard_plugin"
    (plugin_dir / "static").mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text(
        "\n".join(
            [
                "def init_api(app):",
                "    @app.get('/api/v1/plugins/guard_plugin/ping')",
                "    def ping():",
                "        return {'ok': True}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.json").write_text(
        '{"name":"guard_plugin","enabled":true,"frontend":{"scripts":["static/inject.js"]}}',
        encoding="utf-8",
    )
    (plugin_dir / "static" / "inject.js").write_text("console.log('guard');\n", encoding="utf-8")

    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", plugins_root)
    monkeypatch.setattr(plugin_loader, "_PLUGIN_CONTROL_PATH", tmp_path / "data" / "plugin_controls.json")

    app = FastAPI()
    assert plugin_loader.init_api_plugins(app) == ["guard_plugin"]
    client = TestClient(app)

    assert client.get("/api/v1/plugins/guard_plugin/ping").status_code == 200
    assert client.get("/plugins/guard_plugin/static/inject.js").status_code == 200

    plugin_loader.set_plugin_enabled("guard_plugin", False)

    api_response = client.get("/api/v1/plugins/guard_plugin/ping")
    static_response = client.get("/plugins/guard_plugin/static/inject.js")

    assert api_response.status_code == 403
    assert api_response.json()["plugin_name"] == "guard_plugin"
    assert static_response.status_code == 403
    assert static_response.json()["plugin_name"] == "guard_plugin"


def test_readonly_host_database_allows_reads_and_blocks_writes(tmp_path):
    import sqlite3

    db_path = tmp_path / "host.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE novels (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO novels (id, title) VALUES (?, ?)", ("novel-1", "雾城"))
    conn.commit()
    conn.close()

    host_db = ReadOnlyHostDatabase(db_path)
    assert host_db.fetch_one("SELECT title FROM novels WHERE id = ?", ("novel-1",)) == {"title": "雾城"}
    assert host_db.fetch_all("WITH selected AS (SELECT id FROM novels) SELECT id FROM selected") == [{"id": "novel-1"}]

    with pytest.raises(PermissionError):
        host_db.fetch_all("UPDATE novels SET title = 'changed'")
    with pytest.raises(PermissionError):
        host_db.execute("INSERT INTO novels (id, title) VALUES ('novel-2', 'x')")

    check = sqlite3.connect(db_path)
    assert check.execute("SELECT title FROM novels WHERE id = 'novel-1'").fetchone()[0] == "雾城"
    check.close()


def test_plugin_host_exposes_readonly_host_database_and_writable_plugin_area(tmp_path):
    import sqlite3

    db_path = tmp_path / "host.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE chapters (novel_id TEXT, chapter_number INTEGER, content TEXT)")
    conn.execute("INSERT INTO chapters VALUES ('novel-1', 1, '第一章')")
    conn.commit()
    conn.close()

    host = PlotPilotPluginHost(
        plugin_name="sample_state_plugin",
        storage=PluginStorage(root=tmp_path / "plugin_platform"),
        host_database=ReadOnlyHostDatabase(db_path),
    )

    with pytest.raises(PermissionError):
        host.read_host_row("SELECT content FROM chapters WHERE novel_id = ?", ("novel-1",))

    assert host.read_host_table_row("chapters", columns=["content"], novel_id="novel-1") == {"content": "第一章"}
    assert host.read_host_table("chapters", columns=["content"], limit=1000) == [{"content": "第一章"}]

    with pytest.raises(ValueError):
        host.read_host_table("chapters; DROP TABLE chapters", columns=["content"])
    with pytest.raises(ValueError):
        host.read_host_table("chapters", columns=["content FROM chapters; DROP TABLE chapters"])

    host.write_own_plugin_state(["novels", "novel-1", "state.json"], {"ok": True})
    assert host.read_own_plugin_state(["novels", "novel-1", "state.json"]) == {"ok": True}
    assert host.read_plugin_state("sample_state_plugin", ["novels", "novel-1", "state.json"]) == {"ok": True}
    with pytest.raises(PermissionError):
        host.write_plugin_state("other_plugin", ["state.json"], {"ok": False})

    raw_host = PlotPilotPluginHost(
        storage=PluginStorage(root=tmp_path / "plugin_platform"),
        host_database=ReadOnlyHostDatabase(db_path),
        allow_raw_host_sql=True,
    )
    assert raw_host.read_host_row("SELECT content FROM chapters WHERE novel_id = ?", ("novel-1",)) == {"content": "第一章"}
    with pytest.raises(PermissionError):
        raw_host.read_host_rows("DELETE FROM chapters")


def test_job_registry_appends_jsonl_and_builds_dedup_key(tmp_path):
    storage = PluginStorage(root=tmp_path)
    registry = PluginJobRegistry(storage=storage)
    dedup_key = registry.build_dedup_key(
        "sample_state_plugin",
        "after_commit",
        "novel-1",
        chapter_number=3,
        content_hash="abc",
    )
    record = PluginJobRecord(
        plugin_name="sample_state_plugin",
        hook_name="after_commit",
        novel_id="novel-1",
        chapter_number=3,
        trigger_type="auto",
        dedup_key=dedup_key,
        input_json={"chapter": 3},
    )

    registry.append(record)

    assert not (tmp_path / "sample_state_plugin" / "jobs.jsonl").exists()
    jobs = storage.read_jsonl("sample_state_plugin", ["jobs.jsonl"])
    assert len(jobs) == 1
    payload = jobs[0]
    assert payload["dedup_key"] == "sample_state_plugin:after_commit:novel-1:3:abc:auto"
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

    register_hook("sample_state_plugin", "before_context_build", handler)

    results = dispatch_hook_sync("before_context_build", {"novel_id": "novel-1", "chapter_number": 3})
    rendered = render_context_blocks(results)

    assert "【动态角色状态】" in rendered
    assert "黑色钥匙" in rendered
    clear_hooks()



def test_host_integration_builds_generation_context_patch():
    clear_hooks()

    register_hook(
        "sample_state_plugin",
        "before_context_build",
        lambda payload: {
            "ok": True,
            "context_blocks": [{"title": "Sample State", "content": "林澈持有黑色钥匙。"}],
        },
    )

    context = build_generation_context_patch("novel-1", 3, "林澈进入黑塔")

    assert "Sample State" in context
    assert "黑色钥匙" in context
    clear_hooks()


@pytest.mark.asyncio
async def test_host_integration_notifies_chapter_committed():
    clear_hooks()
    seen = {}

    async def handler(payload):
        seen.update(payload)
        return {"ok": True, "data": {"updated": True}}

    register_hook("sample_state_plugin", "after_commit", handler)

    results = await notify_chapter_committed("novel-1", 2, "《林澈》进入黑塔。")

    assert results[0]["data"] == {"updated": True}
    assert seen["source"] == "chapter_aftermath_pipeline"
    assert seen["payload"]["content"] == "《林澈》进入黑塔。"
    clear_hooks()


@pytest.mark.asyncio
async def test_host_integration_notifies_novel_created_and_collects_story_context():
    clear_hooks()
    seen = {}

    async def after_create(payload):
        seen.update(payload)
        return {"ok": True, "data": {"worldline_seeded": True}}

    def before_planning(payload):
        return {
            "ok": True,
            "context_blocks": [
                {
                    "title": "Sample Story Context",
                    "content": "开篇前约180-144年：旧案被粉饰。",
                }
            ],
        }

    register_hook("sample_state_plugin", "after_novel_created", after_create)
    register_hook("sample_state_plugin", "before_story_planning", before_planning)

    results = await notify_novel_created_with_plugins(
        "novel-1",
        "旧案回声",
        "主角调查被抹去的旧案。",
        genre="悬疑",
        world_preset="贵族学校",
        target_chapters=240,
    )
    context = collect_story_planning_context_with_plugins("novel-1", purpose="setup_main_plot_options")

    assert results[0]["data"] == {"worldline_seeded": True}
    assert seen["payload"]["genre"] == "悬疑"
    assert seen["payload"]["target_chapters"] == 240
    assert "Sample Story Context" in context
    assert "旧案被粉饰" in context
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
                        "issue_type": "sample_character_logic",
                        "severity": "warning",
                        "description": "林澈突然知道钥匙代价，但此前状态仍标记为未知。",
                        "location": "Chapter 4",
                        "suggestion": "补一笔他如何得知代价，或改为怀疑/推测。",
                    }
                ],
                "suggestions": ["让插件审稿意见作为 PlotPilot 原有审稿建议的补充。"],
            },
        }

    register_hook("sample_state_plugin", "review_chapter", handler)

    results = await review_chapter_with_plugins("novel-1", 4, "林澈知道钥匙会消耗记忆。")

    assert results[0]["data"]["issues"][0]["issue_type"] == "sample_character_logic"
    assert seen["source"] == "chapter_review_service"
    assert seen["chapter_number"] == 4
    assert seen["payload"]["content"] == "林澈知道钥匙会消耗记忆。"
    clear_hooks()


@pytest.mark.asyncio
async def test_host_integration_collects_and_notifies_chapter_review_hooks():
    clear_hooks()
    seen_before = {}
    seen_after = {}

    async def before_handler(payload):
        seen_before.update(payload)
        return {
            "ok": True,
            "data": {
                "review_context_blocks": [
                    {
                        "title": "Sample Timeline Evidence",
                        "kind": "timeline_evidence",
                        "content": "第1章：林澈获得黑色钥匙。",
                    }
                ]
            },
        }

    async def after_handler(payload):
        seen_after.update(payload)
        return {"ok": True, "data": {"recorded": True}}

    register_hook("sample_state_plugin", "before_chapter_review", before_handler)
    register_hook("sample_state_plugin", "after_chapter_review", after_handler)

    before_results = await collect_chapter_review_context_with_plugins("novel-1", 4, "林澈走进黑塔。")
    after_results = await notify_chapter_review_completed(
        "novel-1",
        4,
        "林澈走进黑塔。",
        {"issues": [{"issue_type": "continuity"}], "overall_score": 82},
    )

    assert before_results[0]["data"]["review_context_blocks"][0]["kind"] == "timeline_evidence"
    assert seen_before["payload"]["review_targets"] == ["character", "timeline", "storyline", "foreshadowing"]
    assert seen_before["source"] == "chapter_review_service"
    assert after_results[0]["data"] == {"recorded": True}
    assert seen_after["payload"]["review_result"]["overall_score"] == 82
    assert seen_after["payload"]["content"] == "林澈走进黑塔。"
    clear_hooks()
