import json
from pathlib import Path

import pytest

from plugins.platform.hook_dispatcher import clear_hooks, dispatch_hook, list_hooks, register_hook
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.loader import init_api_plugins
from plugins.platform.host_facade import PlotPilotPluginHost


@pytest.mark.asyncio
async def test_host_facade_uses_adapters_and_dispatches_hooks(tmp_path):
    clear_hooks()
    storage = PluginStorage(root=tmp_path)
    host = PlotPilotPluginHost(
        storage=storage,
        novel_reader=lambda novel_id: {"id": novel_id},
        chapter_reader=lambda novel_id, chapter_number: {"novel_id": novel_id, "number": chapter_number},
    )

    register_hook("sample", "before_context_build", lambda payload: {"ok": True, "data": {"seen": payload["novel_id"]}})

    assert await host.get_novel("n1") == {"id": "n1"}
    assert await host.get_chapter("n1", 2) == {"novel_id": "n1", "number": 2}
    assert (await host.dispatch_hook("before_context_build", {"novel_id": "n1"}))[0]["data"] == {"seen": "n1"}

    host.write_plugin_state("sample", ["novels", "n1", "state.json"], {"ok": True})
    assert host.read_plugin_state("sample", ["novels", "n1", "state.json"]) == {"ok": True}
    clear_hooks()


def test_init_api_plugins_mounts_platform_status_router(tmp_path, monkeypatch):
    import plugins.loader as plugin_loader

    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", tmp_path / "plugins")
    app = FastAPI()
    init_api_plugins(app)
    client = TestClient(app)

    response = client.get("/api/v1/plugins/platform/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["features"]["host_facade"] is True
