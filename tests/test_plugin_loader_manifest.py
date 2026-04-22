"""插件 loader manifest 驱动能力测试。"""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins import loader


_RUNTIME_LOADER_FILE = Path(__file__).resolve().parents[1] / "platform" / "frontend" / "public" / "plugin-loader.js"
_RUNTIME_INJECTOR_SAMPLE = """
(function bootstrapSamplePluginInjector() {
  const runtime = window.PlotPilotPlugins || null;
  const host = window.__SamplePluginHost || (window.__SamplePluginHost = {});

  function refreshCurrentNovel() {
    return null;
  }

  host.__sampleRefresh = refreshCurrentNovel;

  if (runtime) {
    runtime.events.on('chapter:loaded', () => refreshCurrentNovel());
    runtime.events.on('chapter:saved', () => refreshCurrentNovel());
    runtime.events.on('route:changed', () => refreshCurrentNovel());
  }
})();
"""


def _make_plugin(
    tmp_path: Path,
    name: str,
    manifest: str | None = None,
    init_api_body: str | None = None,
    init_daemon_body: str | None = None,
) -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    api_body = init_api_body or f"app.state.loaded.append('api:{name}')"
    daemon_body = init_daemon_body or f"return 'daemon:{name}'"
    (plugin_dir / "__init__.py").write_text(
        "def init_api(app):\n    " + api_body + "\n\n"
        "def init_daemon():\n    " + daemon_body + "\n",
        encoding="utf-8",
    )
    if manifest is not None:
        (plugin_dir / "plugin.json").write_text(manifest, encoding="utf-8")
    return plugin_dir


def test_collect_frontend_scripts_prefers_manifest_entries(monkeypatch, tmp_path):
    _make_plugin(
        tmp_path,
        "alpha",
        manifest='{"frontend": {"scripts": ["static/a.js", "/plugins/shared/b.js"]}}',
    )
    _make_plugin(tmp_path, "beta")
    (tmp_path / "beta" / "static").mkdir(parents=True, exist_ok=True)
    (tmp_path / "beta" / "static" / "inject.js").write_text("console.log('beta');", encoding="utf-8")

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)

    assert loader.collect_frontend_scripts() == [
        "/plugins/alpha/static/a.js",
        "/plugins/shared/b.js",
        "/plugins/beta/static/inject.js",
    ]


def test_collect_frontend_scripts_skips_disabled_manifest(monkeypatch, tmp_path):
    _make_plugin(
        tmp_path,
        "gamma",
        manifest='{"enabled": false, "frontend": {"scripts": ["static/inject.js"]}}',
    )

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)

    assert loader.collect_frontend_scripts() == []


def test_load_plugins_returns_manifest_metadata(monkeypatch, tmp_path):
    _make_plugin(
        tmp_path,
        "delta",
        manifest='{"name": "delta-display", "version": "1.2.3", "enabled": true}',
    )

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    sys.modules.pop("plugins.delta", None)

    loaded = loader.load_plugins()

    assert len(loaded) == 1
    assert loaded[0]["name"] == "delta"
    assert loaded[0]["manifest"]["name"] == "delta-display"
    assert loaded[0]["manifest"]["version"] == "1.2.3"


def test_init_api_plugins_is_idempotent_and_tracks_loaded_state(monkeypatch, tmp_path):
    _make_plugin(tmp_path, "alpha")
    _make_plugin(tmp_path, "beta")

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    sys.modules.pop("plugins.alpha", None)
    sys.modules.pop("plugins.beta", None)

    app = FastAPI()
    app.state.loaded = []

    first = loader.init_api_plugins(app)
    second = loader.init_api_plugins(app)

    assert first == ["alpha", "beta"]
    assert second == ["alpha", "beta"]
    assert app.state.loaded == ["api:alpha", "api:beta"]
    assert app.state.loaded_plugins == {"alpha", "beta"}


def test_init_daemon_plugins_is_idempotent(monkeypatch, tmp_path):
    _make_plugin(tmp_path, "alpha")
    _make_plugin(tmp_path, "beta")

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    sys.modules.pop("plugins.alpha", None)
    sys.modules.pop("plugins.beta", None)
    if hasattr(loader.init_daemon_plugins, "_loaded_plugins"):
        loader.init_daemon_plugins._loaded_plugins = set()

    first = loader.init_daemon_plugins()
    second = loader.init_daemon_plugins()

    assert first == ["alpha", "beta"]
    assert second == ["alpha", "beta"]


def test_init_api_plugins_auto_includes_routes_and_static(monkeypatch, tmp_path):
    plugin_dir = tmp_path / "gamma"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(
        "def init_api(app):\n"
        "    app.state.gamma_initialized = getattr(app.state, 'gamma_initialized', 0) + 1\n",
        encoding="utf-8",
    )
    (plugin_dir / "routes.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter(prefix='/api/v1/plugins/gamma')\n\n"
        "@router.get('/ping')\n"
        "async def ping():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (plugin_dir / "static").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "static" / "inject.js").write_text("console.log('gamma');\n", encoding="utf-8")

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path.parent))
    sys.modules.pop("plugins.gamma", None)
    sys.modules.pop("plugins.gamma.routes", None)

    app = FastAPI()

    loaded = loader.init_api_plugins(app)

    assert loaded == ["gamma"]
    assert app.state.gamma_initialized == 1
    assert any(getattr(route, "path", "") == "/api/v1/plugins/gamma/ping" for route in app.routes)
    assert any(getattr(route, "path", "") == "/plugins/gamma/static" for route in app.routes)


def test_plugin_manifest_endpoint_lists_enabled_plugins_and_frontend_assets(monkeypatch, tmp_path):
    _make_plugin(
        tmp_path,
        "alpha",
        manifest='{"display_name": "Alpha Plugin", "version": "1.0.0", "frontend": {"scripts": ["static/a.js", "static/b.js"]}}',
    )
    _make_plugin(
        tmp_path,
        "beta",
        manifest='{"enabled": false, "display_name": "Beta Plugin", "frontend": {"scripts": ["static/disabled.js"]}}',
    )
    _make_plugin(tmp_path, "gamma")
    (tmp_path / "gamma" / "static").mkdir(parents=True, exist_ok=True)
    (tmp_path / "gamma" / "static" / "inject.js").write_text("console.log('gamma');", encoding="utf-8")

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(loader.create_plugin_manifest_router(), prefix="/api/v1")

    response = TestClient(app).get("/api/v1/plugins/manifest")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["alpha", "gamma"]
    assert body["items"][0]["display_name"] == "Alpha Plugin"
    assert body["items"][0]["version"] == "1.0.0"
    assert body["items"][0]["frontend_scripts"] == [
        "/plugins/alpha/static/a.js",
        "/plugins/alpha/static/b.js",
    ]
    assert body["items"][1]["display_name"] == "gamma"
    assert body["items"][1]["frontend_scripts"] == ["/plugins/gamma/static/inject.js"]
    assert body["runtime"] == {
        "manifest_endpoint": "/api/v1/plugins/manifest",
        "plugins_endpoint": "/api/v1/plugins",
        "frontend_loader": "/plugin-loader.js",
    }


def test_plugins_endpoint_returns_runtime_metadata(monkeypatch, tmp_path):
    _make_plugin(
        tmp_path,
        "alpha",
        manifest='{"display_name": "Alpha Plugin", "version": "1.0.0", "frontend": {"scripts": ["static/a.js"]}}',
    )

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(loader.create_plugin_manifest_router(), prefix="/api/v1")

    response = TestClient(app).get("/api/v1/plugins")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "alpha"
    assert body["frontend_scripts"] == ["/plugins/alpha/static/a.js"]
    assert body["runtime"] == {
        "manifest_endpoint": "/api/v1/plugins/manifest",
        "plugins_endpoint": "/api/v1/plugins",
        "frontend_loader": "/plugin-loader.js",
    }


def test_plugin_manifest_endpoint_deduplicates_frontend_scripts(monkeypatch, tmp_path):
    _make_plugin(
        tmp_path,
        "alpha",
        manifest='{"frontend": {"scripts": ["static/shared.js", "static/shared.js", "/plugins/shared/lib.js"]}}',
    )
    _make_plugin(
        tmp_path,
        "beta",
        manifest='{"frontend": {"scripts": ["/plugins/shared/lib.js", "static/extra.js"]}}',
    )

    monkeypatch.setattr(loader, "_PLUGINS_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(loader.create_plugin_manifest_router(), prefix="/api/v1")

    response = TestClient(app).get("/api/v1/plugins/manifest")

    assert response.status_code == 200
    body = response.json()
    assert body["frontend_scripts"] == [
        "/plugins/alpha/static/shared.js",
        "/plugins/shared/lib.js",
        "/plugins/beta/static/extra.js",
    ]


def test_plugin_loader_runtime_exposes_host_events_and_deduplicates_scripts():
    source = _RUNTIME_LOADER_FILE.read_text(encoding="utf-8")

    assert "window.PlotPilotPlugins = runtime" in source
    assert "emitChapterSaved(payload)" in source
    assert "emitChapterLoaded(payload)" in source
    assert "emitRouteChanged(payload)" in source
    assert "runtime.events.emit('plugins:loaded', pluginsPayload)" in source
    assert "queueMicrotask(() => {" in source
    assert "if (runtime.scripts.has(src)) return;" in source


def test_plugin_injector_sample_uses_runtime_events_instead_of_polling():
    source = _RUNTIME_INJECTOR_SAMPLE

    assert "window.PlotPilotPlugins || null" in source
    assert "runtime.events.on('chapter:loaded'" in source
    assert "runtime.events.on('chapter:saved'" in source
    assert "runtime.events.on('route:changed'" in source
    assert "host.__sampleRefresh = refreshCurrentNovel" in source
    assert "history.pushState = function () {" not in source
    assert "window.addEventListener('popstate', onUrlChange)" not in source
