import io
import json
import sys
import zipfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import plugins.loader as plugin_loader


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for relative_path, content in files.items():
            zf.writestr(relative_path, content)
    return buffer.getvalue()


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(plugin_loader.create_plugin_manifest_router(), prefix="/api/v1")
    return TestClient(app)


def _strip_asset_version(url: str) -> str:
    return url.split("?", 1)[0]


def test_plugin_upload_import_appears_in_plugin_list(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", tmp_path / "plugins")
    client = _make_client()

    plugin_zip = _make_zip_bytes(
        {
            "sample_plugin/__init__.py": "def init_api(app):\n    return None\n",
            "sample_plugin/plugin.json": json.dumps(
                {
                    "name": "sample-plugin",
                    "display_name": "Sample Plugin",
                    "enabled": True,
                    "frontend": {"scripts": ["static/inject.js"]},
                }
            ),
            "sample_plugin/static/inject.js": "console.log('sample plugin loaded');\n",
        }
    )

    response = client.post(
        "/api/v1/plugins/import/upload",
        files={"file": ("sample_plugin.zip", plugin_zip, "application/zip")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["plugin_name"] == "sample-plugin"

    list_response = client.get("/api/v1/plugins")
    assert list_response.status_code == 200
    payload = list_response.json()

    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "sample-plugin"
    assert payload["items"][0]["display_name"] == "Sample Plugin"
    assert [_strip_asset_version(url) for url in payload["frontend_scripts"]] == [
        "/plugins/sample-plugin/static/inject.js"
    ]
    assert "?v=" in payload["frontend_scripts"][0]


def test_disabled_plugin_is_filtered_from_plugin_list(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    disabled_dir = plugins_root / "disabled_plugin"
    (disabled_dir / "static").mkdir(parents=True)
    (disabled_dir / "__init__.py").write_text("def init_api(app):\n    return None\n", encoding="utf-8")
    (disabled_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "disabled-plugin",
                "display_name": "Disabled Plugin",
                "enabled": False,
                "frontend": {"scripts": ["static/inject.js"]},
            }
        ),
        encoding="utf-8",
    )
    (disabled_dir / "static" / "inject.js").write_text("console.log('disabled');\n", encoding="utf-8")

    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", plugins_root)
    monkeypatch.setattr(plugin_loader, "_PLUGIN_CONTROL_PATH", tmp_path / "data" / "plugin_controls.json")
    client = _make_client()

    response = client.get("/api/v1/plugins")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "disabled_plugin"
    assert payload["items"][0]["enabled"] is False
    assert payload["items"][0]["manifest_enabled"] is False
    assert payload["items"][0]["configured_enabled"] is None
    assert payload["frontend_scripts"] == []

    manifest_response = client.get("/api/v1/plugins/manifest")
    assert manifest_response.status_code == 200
    manifest_payload = manifest_response.json()
    assert manifest_payload["total"] == 1
    assert manifest_payload["items"][0]["enabled"] is False
    assert manifest_payload["frontend_scripts"] == []


def test_plugin_enabled_endpoint_toggles_single_plugin(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "toggle_plugin"
    (plugin_dir / "static").mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("def init_api(app):\n    return None\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "toggle_plugin",
                "display_name": "Toggle Plugin",
                "enabled": True,
                "frontend": {"scripts": ["static/inject.js"]},
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "static" / "inject.js").write_text("console.log('toggle');\n", encoding="utf-8")

    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", plugins_root)
    monkeypatch.setattr(plugin_loader, "_PLUGIN_CONTROL_PATH", tmp_path / "data" / "plugin_controls.json")
    client = _make_client()

    disable_response = client.put("/api/v1/plugins/toggle_plugin/enabled", json={"enabled": False})
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled"] is False

    disabled_payload = client.get("/api/v1/plugins").json()
    assert disabled_payload["items"][0]["enabled"] is False
    assert disabled_payload["items"][0]["configured_enabled"] is False
    assert disabled_payload["frontend_scripts"] == []
    assert plugin_loader.load_plugins() == []

    enable_response = client.put("/api/v1/plugins/toggle_plugin/enabled", json={"enabled": True})
    assert enable_response.status_code == 200
    enabled_payload = client.get("/api/v1/plugins").json()

    assert enabled_payload["items"][0]["enabled"] is True
    assert enabled_payload["items"][0]["configured_enabled"] is True
    assert [_strip_asset_version(url) for url in enabled_payload["frontend_scripts"]] == [
        "/plugins/toggle_plugin/static/inject.js"
    ]


def test_plugin_upload_rejects_zip_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", tmp_path / "plugins")
    client = _make_client()

    plugin_zip = _make_zip_bytes(
        {
            "evil_plugin/__init__.py": "",
            "../escape.txt": "owned",
        }
    )

    response = client.post(
        "/api/v1/plugins/import/upload",
        files={"file": ("evil.zip", plugin_zip, "application/zip")},
    )

    assert response.status_code == 400
    assert not (tmp_path / "escape.txt").exists()


def test_plugin_manifest_exposes_styles_and_capabilities(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "stateful_plugin"
    (plugin_dir / "static").mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("def init_api(app):\n    return None\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "stateful_plugin",
                "display_name": "Stateful Plugin",
                "enabled": True,
                "capabilities": {"context_injection": True},
                "permissions": ["read_novel", "write_plugin_storage"],
                "hooks": ["before_context_build", "after_commit"],
                "frontend": {
                    "scripts": ["static/inject.js"],
                    "styles": ["static/style.css"],
                },
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "static" / "inject.js").write_text("console.log('ok');\n", encoding="utf-8")
    (plugin_dir / "static" / "style.css").write_text(".x{}\n", encoding="utf-8")

    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", plugins_root)
    client = _make_client()

    response = client.get("/api/v1/plugins/manifest")
    assert response.status_code == 200
    payload = response.json()

    assert [_strip_asset_version(url) for url in payload["frontend_styles"]] == [
        "/plugins/stateful_plugin/static/style.css"
    ]
    assert "?v=" in payload["frontend_styles"][0]
    assert payload["items"][0]["capabilities"] == {"context_injection": True}
    assert payload["items"][0]["permissions"] == ["read_novel", "write_plugin_storage"]
    assert payload["items"][0]["hooks"] == ["before_context_build", "after_commit"]
