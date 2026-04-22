"""Generic PlotPilot plugin loader.

Loads zero-intrusion plugins from plugins/* and provides:
- API initialization hooks: init_api(app)
- Daemon initialization hooks: init_daemon()
- Frontend script discovery for index.html injection
- Optional plugin manifest metadata via plugins/*/plugin.json
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PLUGINS_ROOT = _PROJECT_ROOT / "plugins"


def _discover_plugin_dirs() -> List[Path]:
    if not _PLUGINS_ROOT.exists():
        return []
    return sorted(
        [p for p in _PLUGINS_ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()],
        key=lambda p: p.name,
    )


def _load_manifest(plugin_dir: Path) -> Dict[str, Any]:
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        return {}

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        logger.warning("⚠️ Plugin %s manifest is not an object; ignored", plugin_dir.name)
    except Exception as exc:
        logger.warning("⚠️ Plugin %s manifest read failed: %s", plugin_dir.name, exc)
    return {}


def _import_plugin_module(plugin_dir: Path):
    module_name = f"plugins.{plugin_dir.name}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        init_file = plugin_dir / "__init__.py"
        spec = importlib.util.spec_from_file_location(module_name, init_file)
        if spec is None or spec.loader is None:
            raise
        module = importlib.util.module_from_spec(spec)
        import sys

        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


def _is_enabled(manifest: Dict[str, Any]) -> bool:
    return manifest.get("enabled", True) is not False


def _resolve_frontend_script(plugin_name: str, script: str) -> str:
    if script.startswith("/"):
        return script
    return f"/plugins/{plugin_name}/{script.lstrip('/')}"


def _collect_frontend_scripts_for_plugin(plugin_dir: Path, manifest: Dict[str, Any]) -> List[str]:
    frontend = manifest.get("frontend") if isinstance(manifest, dict) else None
    manifest_scripts = frontend.get("scripts", []) if isinstance(frontend, dict) else []
    if isinstance(manifest_scripts, list) and manifest_scripts:
        scripts: List[str] = []
        for script in manifest_scripts:
            if isinstance(script, str) and script.strip():
                scripts.append(_resolve_frontend_script(plugin_dir.name, script.strip()))
        return scripts

    script_path = plugin_dir / "static" / "inject.js"
    if script_path.exists():
        return [f"/plugins/{plugin_dir.name}/static/inject.js"]
    return []


def _build_plugin_manifest_record(plugin_dir: Path) -> Dict[str, Any] | None:
    manifest = _load_manifest(plugin_dir)
    if not _is_enabled(manifest):
        return None

    frontend_scripts = _collect_frontend_scripts_for_plugin(plugin_dir, manifest)
    return {
        "name": plugin_dir.name,
        "display_name": manifest.get("display_name") or manifest.get("name") or plugin_dir.name,
        "version": manifest.get("version"),
        "enabled": True,
        "frontend_scripts": frontend_scripts,
        "manifest": manifest,
    }


def _include_plugin_router(app, plugin_name: str) -> None:
    plugin_route_prefix = f"/api/v1/plugins/{plugin_name}"
    if any(getattr(route, "path", "").startswith(plugin_route_prefix) for route in app.routes):
        return

    try:
        routes_module = importlib.import_module(f"plugins.{plugin_name}.routes")
    except ModuleNotFoundError:
        return
    except Exception as exc:
        logger.warning("⚠️ Plugin %s routes import failed: %s", plugin_name, exc)
        return

    router = getattr(routes_module, "router", None)
    if router is None:
        return

    try:
        app.include_router(router)
        logger.info("✅ Plugin router included: %s", plugin_name)
    except Exception as exc:
        logger.warning("⚠️ Plugin %s router include failed: %s", plugin_name, exc)


def _mount_plugin_static(app, plugin_name: str) -> None:
    static_mount = f"/plugins/{plugin_name}/static"
    if any(
        getattr(route, "path", "") == static_mount or getattr(route, "name", "") == f"plugin-static-{plugin_name}"
        for route in app.routes
    ):
        return

    static_dir = _PLUGINS_ROOT / plugin_name / "static"
    if not static_dir.exists():
        return

    try:
        app.mount(static_mount, StaticFiles(directory=str(static_dir)), name=f"plugin-static-{plugin_name}")
        logger.info("✅ Plugin static mounted: %s", plugin_name)
    except Exception as exc:
        logger.warning("⚠️ Plugin %s static mount failed: %s", plugin_name, exc)


def list_plugin_names() -> List[str]:
    return [p.name for p in _discover_plugin_dirs()]


def list_plugin_manifests() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for plugin_dir in _discover_plugin_dirs():
        record = _build_plugin_manifest_record(plugin_dir)
        if record is not None:
            items.append(record)
    return items


def collect_manifest_frontend_scripts(items: List[Dict[str, Any]]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for item in items:
        for script in item.get("frontend_scripts", []):
            if script not in seen:
                seen.add(script)
                deduped.append(script)
    return deduped


def load_plugins() -> List[Dict[str, Any]]:
    loaded: List[Dict[str, Any]] = []
    for plugin_dir in _discover_plugin_dirs():
        plugin_name = plugin_dir.name
        manifest = _load_manifest(plugin_dir)
        if not _is_enabled(manifest):
            logger.info("⏭️ Plugin %s disabled by manifest", plugin_name)
            continue
        try:
            mod = _import_plugin_module(plugin_dir)
            loaded.append({"name": plugin_name, "module": mod, "manifest": manifest})
        except Exception as exc:
            logger.warning("⚠️ Plugin %s import failed: %s", plugin_name, exc)
    return loaded


def init_api_plugins(app) -> List[str]:
    initialized: List[str] = []
    loaded_state = getattr(app.state, "loaded_plugins", None)
    if not isinstance(loaded_state, set):
        loaded_state = set()
        app.state.loaded_plugins = loaded_state

    for plugin in load_plugins():
        plugin_name = plugin["name"]
        mod = plugin["module"]
        if plugin_name in loaded_state:
            initialized.append(plugin_name)
            continue
        init_api = getattr(mod, "init_api", None)
        if callable(init_api):
            try:
                init_api(app)
                _include_plugin_router(app, plugin_name)
                _mount_plugin_static(app, plugin_name)
                loaded_state.add(plugin_name)
                initialized.append(plugin_name)
                logger.info("✅ API plugin loaded: %s", plugin_name)
            except Exception as exc:
                logger.warning("⚠️ API plugin %s load failed: %s", plugin_name, exc)
    return initialized


def init_daemon_plugins() -> List[str]:
    initialized: List[str] = []
    if not hasattr(init_daemon_plugins, "_loaded_plugins") or not isinstance(init_daemon_plugins._loaded_plugins, set):
        init_daemon_plugins._loaded_plugins = set()
    loaded_state = init_daemon_plugins._loaded_plugins

    for plugin in load_plugins():
        plugin_name = plugin["name"]
        mod = plugin["module"]
        if plugin_name in loaded_state:
            initialized.append(plugin_name)
            continue
        init_daemon = getattr(mod, "init_daemon", None)
        if callable(init_daemon):
            try:
                init_daemon()
                loaded_state.add(plugin_name)
                initialized.append(plugin_name)
                logger.info("✅ Daemon plugin loaded: %s", plugin_name)
            except Exception as exc:
                logger.warning("⚠️ Daemon plugin %s load failed: %s", plugin_name, exc)
    return initialized


def create_plugin_manifest_router() -> APIRouter:
    router = APIRouter(prefix="/plugins", tags=["plugins"])

    @router.get("")
    async def list_plugins():
        items = list_plugin_manifests()
        frontend_scripts = collect_manifest_frontend_scripts(items)
        return {
            "items": items,
            "total": len(items),
            "frontend_scripts": frontend_scripts,
            "runtime": {
                "manifest_endpoint": "/api/v1/plugins/manifest",
                "plugins_endpoint": "/api/v1/plugins",
                "frontend_loader": "/plugin-loader.js",
            },
        }

    @router.get("/manifest")
    async def get_plugin_manifest():
        items = list_plugin_manifests()
        frontend_scripts = collect_manifest_frontend_scripts(items)
        return {
            "items": items,
            "total": len(items),
            "frontend_scripts": frontend_scripts,
            "runtime": {
                "manifest_endpoint": "/api/v1/plugins/manifest",
                "plugins_endpoint": "/api/v1/plugins",
                "frontend_loader": "/plugin-loader.js",
            },
        }

    return router


def collect_frontend_scripts() -> List[str]:
    scripts: List[str] = []
    for plugin_dir in _discover_plugin_dirs():
        manifest = _load_manifest(plugin_dir)
        if not _is_enabled(manifest):
            continue
        scripts.extend(_collect_frontend_scripts_for_plugin(plugin_dir, manifest))
    return scripts
