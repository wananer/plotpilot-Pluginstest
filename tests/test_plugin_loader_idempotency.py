import sys
from pathlib import Path

from fastapi import FastAPI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import plugins.loader as plugin_loader


def _write_plugin(plugin_root: Path, name: str) -> None:
    plugin_dir = plugin_root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "__init__.py").write_text(
        f"""
def init_api(app):
    loaded = getattr(app.state, 'loaded_calls', [])
    loaded.append('api:{name}')
    app.state.loaded_calls = loaded


def init_daemon():
    import plugins.loader as _loader
    calls = getattr(_loader.init_daemon_plugins, '_test_calls', [])
    calls.append('daemon:{name}')
    _loader.init_daemon_plugins._test_calls = calls
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_init_api_plugins_is_idempotent(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "alpha")
    _write_plugin(plugins_root, "beta")
    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", plugins_root)

    app = FastAPI()
    first = plugin_loader.init_api_plugins(app)
    second = plugin_loader.init_api_plugins(app)

    assert first == ["alpha", "beta"]
    assert second == ["alpha", "beta"]
    assert app.state.loaded_calls == ["api:alpha", "api:beta"]
    assert app.state.loaded_plugins == {"alpha", "beta"}


def test_init_daemon_plugins_is_idempotent(tmp_path, monkeypatch):
    plugins_root = tmp_path / "plugins"
    _write_plugin(plugins_root, "alpha")
    _write_plugin(plugins_root, "beta")
    monkeypatch.setattr(plugin_loader, "_PLUGINS_ROOT", plugins_root)
    monkeypatch.delattr(plugin_loader.init_daemon_plugins, "_loaded_plugins", raising=False)
    monkeypatch.delattr(plugin_loader.init_daemon_plugins, "_test_calls", raising=False)

    first = plugin_loader.init_daemon_plugins()
    second = plugin_loader.init_daemon_plugins()

    assert first == ["alpha", "beta"]
    assert second == ["alpha", "beta"]
    assert plugin_loader.init_daemon_plugins._loaded_plugins == {"alpha", "beta"}
    assert plugin_loader.init_daemon_plugins._test_calls == ["daemon:alpha", "daemon:beta"]
