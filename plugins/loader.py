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
import hmac
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from application.paths import DATA_DIR
from plugins.platform.compat import (
    FRONTEND_RUNTIME_VERSION,
    PLATFORM_RUNTIME_API_VERSION,
    build_plugin_compatibility_report,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PLUGINS_ROOT = _PROJECT_ROOT / "plugins"
_PLUGIN_CONTROL_PATH: Path | None = None
_PLUGIN_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_PLUGIN_CONTROL_LOCK = threading.RLock()
_LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _plugin_control_path() -> Path:
    """Return the mutable plugin-control file for the active data directory."""

    if _PLUGIN_CONTROL_PATH is not None:
        return Path(_PLUGIN_CONTROL_PATH)
    return DATA_DIR / "plugin_platform" / "plugin_controls.json"


def _discover_plugin_dirs() -> List[Path]:
    if not _PLUGINS_ROOT.exists():
        return []
    return sorted(
        [p for p in _PLUGINS_ROOT.iterdir() if p.is_dir() and p.name != "platform" and (p / "__init__.py").exists()],
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


def _load_plugin_controls() -> Dict[str, Any]:
    path = _plugin_control_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("⚠️ Plugin control state read failed: %s", exc)
        return {}


def _write_plugin_controls(controls: Dict[str, Any]) -> None:
    with _PLUGIN_CONTROL_LOCK:
        path = _plugin_control_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(controls, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(path)


def _configured_plugin_enabled(plugin_name: str) -> bool | None:
    record = _load_plugin_controls().get(plugin_name)
    if not isinstance(record, dict):
        return None
    enabled = record.get("enabled")
    return enabled if isinstance(enabled, bool) else None


def set_plugin_enabled(plugin_name: str, enabled: bool) -> Dict[str, Any]:
    normalized = _normalize_plugin_name(plugin_name)
    if not normalized:
        raise HTTPException(status_code=400, detail="插件名称无效")
    with _PLUGIN_CONTROL_LOCK:
        controls = _load_plugin_controls()
        controls[normalized] = {
            "enabled": bool(enabled),
        }
        _write_plugin_controls(controls)
        return controls[normalized]


def _effective_plugin_enabled(plugin_name: str, manifest: Dict[str, Any]) -> bool:
    configured = _configured_plugin_enabled(plugin_name)
    if configured is not None:
        return configured
    return _is_enabled(manifest)


def is_plugin_enabled(plugin_name: str) -> bool:
    normalized = _normalize_plugin_name(plugin_name)
    if not normalized:
        return False
    plugin_dir = _PLUGINS_ROOT / normalized
    if not plugin_dir.exists():
        return True
    return _effective_plugin_enabled(normalized, _load_manifest(plugin_dir))


def _normalize_plugin_name(raw: str) -> str:
    name = (raw or "").strip().replace(" ", "-")
    safe = "".join(ch for ch in name if ch in _PLUGIN_NAME_CHARS)
    return safe.strip("-_")


def _validate_manifest_contract(manifest: Dict[str, Any], plugin_dir_name: str) -> Dict[str, Any]:
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="plugin.json 必须是 JSON object")

    raw_name = manifest.get("name")
    plugin_name = _normalize_plugin_name(str(raw_name)) if isinstance(raw_name, str) else plugin_dir_name
    plugin_name = _normalize_plugin_name(plugin_name)
    if not plugin_name:
        raise HTTPException(status_code=400, detail="插件名称无效")

    frontend = manifest.get("frontend")
    if frontend is not None and not isinstance(frontend, dict):
        raise HTTPException(status_code=400, detail="manifest.frontend 必须是 object")
    if isinstance(frontend, dict):
        scripts = frontend.get("scripts", [])
        styles = frontend.get("styles", [])
        if scripts is not None and not isinstance(scripts, list):
            raise HTTPException(status_code=400, detail="manifest.frontend.scripts 必须是数组")
        if styles is not None and not isinstance(styles, list):
            raise HTTPException(status_code=400, detail="manifest.frontend.styles 必须是数组")
        for item in list(scripts or []) + list(styles or []):
            if not isinstance(item, str) or not item.strip():
                raise HTTPException(status_code=400, detail="manifest.frontend 资源路径必须是非空字符串")
            _validate_frontend_asset_path(item)

    runtime = manifest.get("runtime")
    if runtime is not None and not isinstance(runtime, dict):
        raise HTTPException(status_code=400, detail="manifest.runtime 必须是 object")

    for key in ("plugin_api_version", "host_min_version", "host_max_version", "frontend_runtime_version"):
        value = manifest.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise HTTPException(status_code=400, detail=f"manifest.{key} 必须是非空字符串")
    if isinstance(runtime, dict):
        for key in ("api_version", "host_min_version", "host_max_version", "frontend_runtime_version"):
            value = runtime.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise HTTPException(status_code=400, detail=f"manifest.runtime.{key} 必须是非空字符串")

    for key in ("capabilities", "permissions", "hooks"):
        value = manifest.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            raise HTTPException(status_code=400, detail=f"manifest.{key} 必须是 object 或数组")

    route_aliases = manifest.get("route_aliases")
    if route_aliases is not None:
        if not isinstance(route_aliases, list) or any(not isinstance(item, str) or not _normalize_plugin_name(item) for item in route_aliases):
            raise HTTPException(status_code=400, detail="manifest.route_aliases 必须是非空字符串数组")

    return {"plugin_name": plugin_name, "manifest": manifest}


def _validate_frontend_asset_path(asset_path: str) -> None:
    raw = str(asset_path or "").strip()
    if raw.startswith(("/", "\\", "http://", "https://", "//")):
        raise HTTPException(status_code=400, detail="manifest.frontend 资源路径必须是插件 static/ 下的相对路径")
    normalized = raw.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=400, detail="manifest.frontend 资源路径包含非法片段")
    if not normalized.startswith("static/"):
        raise HTTPException(status_code=400, detail="manifest.frontend 资源路径只能位于 static/ 目录")


def _safe_extract_zip(zip_file: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in zip_file.infolist():
        member_name = member.filename
        if not member_name or member_name.startswith(("/", "\\")):
            raise HTTPException(status_code=400, detail="zip 包含非法绝对路径")
        target = (destination / member_name).resolve()
        if destination != target and destination not in target.parents:
            raise HTTPException(status_code=400, detail="zip 包含非法路径穿越")
        mode = member.external_attr >> 16
        if mode & 0o170000 == 0o120000:
            raise HTTPException(status_code=400, detail="zip 包含不支持的符号链接")
    zip_file.extractall(destination)


def _resolve_frontend_asset(plugin_dir: Path, asset: str) -> str | None:
    try:
        _validate_frontend_asset_path(asset)
    except HTTPException as exc:
        logger.warning("⚠️ Plugin %s frontend asset ignored: %s", plugin_dir.name, exc.detail)
        return None
    normalized = asset.strip().replace("\\", "/")
    asset_path = (plugin_dir / normalized).resolve()
    static_root = (plugin_dir / "static").resolve()
    if static_root != asset_path and static_root not in asset_path.parents:
        logger.warning("⚠️ Plugin %s frontend asset escaped static/: %s", plugin_dir.name, asset)
        return None
    if not asset_path.exists() or not asset_path.is_file():
        logger.warning("⚠️ Plugin %s frontend asset missing: %s", plugin_dir.name, asset)
        return None
    return f"/plugins/{plugin_dir.name}/{normalized}"


def _append_frontend_asset_version(plugin_dir: Path, asset_url: str) -> str:
    plugin_prefix = f"/plugins/{plugin_dir.name}/"
    if not asset_url.startswith(plugin_prefix):
        return asset_url

    path_part = asset_url.split("?", 1)[0]
    relative_path = path_part[len(plugin_prefix):]
    asset_path = plugin_dir / relative_path
    if not asset_path.exists() or not asset_path.is_file():
        return asset_url

    separator = "&" if "?" in asset_url else "?"
    return f"{asset_url}{separator}v={asset_path.stat().st_mtime_ns}"


def _collect_frontend_scripts_for_plugin(plugin_dir: Path, manifest: Dict[str, Any]) -> List[str]:
    frontend = manifest.get("frontend") if isinstance(manifest, dict) else None
    manifest_scripts = frontend.get("scripts", []) if isinstance(frontend, dict) else []
    if isinstance(manifest_scripts, list) and manifest_scripts:
        scripts: List[str] = []
        for script in manifest_scripts:
            if isinstance(script, str) and script.strip():
                resolved = _resolve_frontend_asset(plugin_dir, script.strip())
                if resolved:
                    scripts.append(_append_frontend_asset_version(plugin_dir, resolved))
        return scripts

    script_path = plugin_dir / "static" / "inject.js"
    if script_path.exists():
        return [_append_frontend_asset_version(plugin_dir, f"/plugins/{plugin_dir.name}/static/inject.js")]
    return []


def _collect_frontend_styles_for_plugin(plugin_dir: Path, manifest: Dict[str, Any]) -> List[str]:
    frontend = manifest.get("frontend") if isinstance(manifest, dict) else None
    manifest_styles = frontend.get("styles", []) if isinstance(frontend, dict) else []
    if isinstance(manifest_styles, list) and manifest_styles:
        styles: List[str] = []
        for style in manifest_styles:
            if isinstance(style, str) and style.strip():
                resolved = _resolve_frontend_asset(plugin_dir, style.strip())
                if resolved:
                    styles.append(_append_frontend_asset_version(plugin_dir, resolved))
        return styles

    style_path = plugin_dir / "static" / "style.css"
    if style_path.exists():
        return [_append_frontend_asset_version(plugin_dir, f"/plugins/{plugin_dir.name}/static/style.css")]
    return []


def _build_plugin_manifest_record(plugin_dir: Path) -> Dict[str, Any] | None:
    manifest = _load_manifest(plugin_dir)
    manifest_enabled = _is_enabled(manifest)
    configured_enabled = _configured_plugin_enabled(plugin_dir.name)
    compatibility = build_plugin_compatibility_report(manifest, plugin_name=plugin_dir.name)
    enabled = (configured_enabled if configured_enabled is not None else manifest_enabled) and bool(compatibility["compatible"])

    frontend_scripts = _collect_frontend_scripts_for_plugin(plugin_dir, manifest) if enabled else []
    frontend_styles = _collect_frontend_styles_for_plugin(plugin_dir, manifest) if enabled else []
    return {
        "name": plugin_dir.name,
        "display_name": manifest.get("display_name") or manifest.get("name") or plugin_dir.name,
        "version": manifest.get("version"),
        "enabled": enabled,
        "manifest_enabled": manifest_enabled,
        "configured_enabled": configured_enabled,
        "frontend_scripts": frontend_scripts,
        "frontend_styles": frontend_styles,
        "capabilities": manifest.get("capabilities") or {},
        "permissions": manifest.get("permissions") or [],
        "hooks": manifest.get("hooks") or [],
        "route_aliases": manifest.get("route_aliases") or [],
        "compatibility": compatibility,
        "disabled_reason": "; ".join(compatibility["reasons"]) if not compatibility["compatible"] else None,
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

    existing_paths = {getattr(route, "path", "") for route in app.routes}
    router_paths = {
        f"{getattr(router, 'prefix', '')}{getattr(route, 'path', '')}"
        for route in getattr(router, "routes", [])
    }
    if existing_paths & router_paths:
        return

    try:
        app.include_router(router)
        logger.info("✅ Plugin router included: %s", plugin_name)
    except Exception as exc:
        logger.warning("⚠️ Plugin %s router include failed: %s", plugin_name, exc)


def _include_platform_router(app) -> None:
    platform_route_prefix = "/api/v1/plugins/platform"
    if any(getattr(route, "path", "").startswith(platform_route_prefix) for route in app.routes):
        return

    try:
        from plugins.platform.routes import router as platform_router

        app.include_router(platform_router)
        logger.info("✅ Plugin platform router included")
    except Exception as exc:
        logger.warning("⚠️ Plugin platform router include failed: %s", exc)


def _plugin_name_from_runtime_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 4 and parts[:3] == ["api", "v1", "plugins"]:
        plugin_name = _normalize_plugin_name(parts[3])
        if not plugin_name or plugin_name in {"platform", "manifest", "import"}:
            return None
        if len(parts) == 5 and parts[4] == "enabled":
            return None
        return _resolve_plugin_route_alias(plugin_name)
    if len(parts) >= 3 and parts[0] == "plugins":
        plugin_name = _normalize_plugin_name(parts[1])
        return plugin_name or None
    return None


def _resolve_plugin_route_alias(route_name: str) -> str:
    plugin_dir = _PLUGINS_ROOT / route_name
    if plugin_dir.exists():
        return route_name
    for candidate in _discover_plugin_dirs():
        manifest = _load_manifest(candidate)
        aliases = manifest.get("route_aliases") if isinstance(manifest, dict) else None
        if route_name in {_normalize_plugin_name(str(item)) for item in aliases or []}:
            return candidate.name
    return route_name


def _install_plugin_disabled_route_guard(app) -> None:
    if getattr(app.state, "plugin_disabled_route_guard_installed", False):
        return
    app.state.plugin_disabled_route_guard_installed = True

    @app.middleware("http")
    async def block_disabled_plugin_routes(request, call_next):
        plugin_name = _plugin_name_from_runtime_path(request.url.path)
        if plugin_name and not is_plugin_enabled(plugin_name):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Plugin disabled: {plugin_name}", "plugin_name": plugin_name},
            )
        return await call_next(request)


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


def collect_manifest_frontend_styles(items: List[Dict[str, Any]]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for item in items:
        for style in item.get("frontend_styles", []):
            if style not in seen:
                seen.add(style)
                deduped.append(style)
    return deduped


def load_plugins() -> List[Dict[str, Any]]:
    loaded: List[Dict[str, Any]] = []
    for plugin_dir in _discover_plugin_dirs():
        plugin_name = plugin_dir.name
        manifest = _load_manifest(plugin_dir)
        compatibility = build_plugin_compatibility_report(manifest, plugin_name=plugin_name)
        if not compatibility["compatible"]:
            logger.warning("⏭️ Plugin %s disabled by compatibility check: %s", plugin_name, "; ".join(compatibility["reasons"]))
            continue
        if not _effective_plugin_enabled(plugin_name, manifest):
            logger.info("⏭️ Plugin %s disabled by platform control", plugin_name)
            continue
        try:
            mod = _import_plugin_module(plugin_dir)
            loaded.append({"name": plugin_name, "module": mod, "manifest": manifest})
        except Exception as exc:
            logger.warning("⚠️ Plugin %s import failed: %s", plugin_name, exc)
    return loaded


def init_api_plugins(app) -> List[str]:
    initialized: List[str] = []
    _include_platform_router(app)
    _install_plugin_disabled_route_guard(app)

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

    def _validate_plugin_dir(plugin_dir: Path) -> Dict[str, Any]:
        if not plugin_dir.exists() or not plugin_dir.is_dir():
            raise HTTPException(status_code=400, detail="插件目录不存在")
        if not (plugin_dir / "__init__.py").exists():
            raise HTTPException(status_code=400, detail="插件缺少 __init__.py")

        manifest = _load_manifest(plugin_dir)
        return _validate_manifest_contract(manifest, plugin_dir.name)

    def _install_plugin_from_dir(source_dir: Path) -> Dict[str, Any]:
        info = _validate_plugin_dir(source_dir)
        plugin_name = info["plugin_name"]
        target_dir = _PLUGINS_ROOT / plugin_name

        if target_dir.exists():
            raise HTTPException(status_code=409, detail=f"插件已存在：{plugin_name}")

        _PLUGINS_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir)

        return {
            "plugin_name": plugin_name,
            "target_dir": str(target_dir),
            "manifest": info["manifest"],
        }

    def _is_local_admin_request(request: Request) -> bool:
        host = request.client.host if request.client else ""
        if host in _LOCAL_ADMIN_HOSTS:
            return True
        forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        return forwarded_for in _LOCAL_ADMIN_HOSTS

    def _require_plugin_admin(request: Request) -> None:
        token = os.getenv("PLOTPILOT_PLUGIN_ADMIN_TOKEN", "").strip()
        if token:
            provided = request.headers.get("x-plugin-admin-token", "").strip()
            auth = request.headers.get("authorization", "").strip()
            if auth.lower().startswith("bearer "):
                provided = provided or auth[7:].strip()
            if hmac.compare_digest(provided, token):
                return
            raise HTTPException(status_code=403, detail="插件平台管理接口需要有效 admin token")
        if _is_local_admin_request(request):
            return
        raise HTTPException(status_code=403, detail="插件平台管理接口仅允许本机访问，或配置 PLOTPILOT_PLUGIN_ADMIN_TOKEN")

    def _assert_github_url_allowed(github_url: str) -> None:
        allowlist = [
            item.strip()
            for item in os.getenv("PLOTPILOT_PLUGIN_GITHUB_ALLOWLIST", "").split(",")
            if item.strip()
        ]
        if allowlist and not any(github_url.startswith(prefix) for prefix in allowlist):
            raise HTTPException(status_code=403, detail="GitHub 插件仓库不在允许列表中")

    @router.get("")
    async def list_plugins():
        items = list_plugin_manifests()
        frontend_scripts = collect_manifest_frontend_scripts(items)
        frontend_styles = collect_manifest_frontend_styles(items)
        return {
            "items": items,
            "total": len(items),
            "frontend_scripts": frontend_scripts,
            "frontend_styles": frontend_styles,
            "runtime": {
                "manifest_endpoint": "/api/v1/plugins/manifest",
                "plugins_endpoint": "/api/v1/plugins",
                "frontend_loader": "/plugin-loader.js",
                "runtime_api_version": PLATFORM_RUNTIME_API_VERSION,
                "frontend_runtime_version": FRONTEND_RUNTIME_VERSION,
            },
        }

    @router.put("/{plugin_name}/enabled")
    async def update_plugin_enabled(plugin_name: str, payload: Dict[str, Any], request: Request):
        _require_plugin_admin(request)
        normalized = _normalize_plugin_name(plugin_name)
        if not normalized:
            raise HTTPException(status_code=400, detail="插件名称无效")
        plugin_dir = _PLUGINS_ROOT / normalized
        if not plugin_dir.exists() or not plugin_dir.is_dir():
            raise HTTPException(status_code=404, detail="插件不存在")
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="enabled 必须是 boolean")

        set_plugin_enabled(normalized, enabled)
        record = _build_plugin_manifest_record(plugin_dir)
        return {
            "ok": True,
            "plugin_name": normalized,
            "enabled": enabled,
            "plugin": record,
            "message": "插件已启用" if enabled else "插件已停用",
        }

    @router.post("/import/github")
    async def import_plugin_from_github(payload: Dict[str, Any], request: Request):
        _require_plugin_admin(request)
        github_url = str(payload.get("github_url") or "").strip()
        if not github_url:
            raise HTTPException(status_code=400, detail="github_url 不能为空")
        if not (github_url.startswith("https://github.com/") or github_url.startswith("git@github.com:")):
            raise HTTPException(status_code=400, detail="仅支持 GitHub 仓库地址")
        _assert_github_url_allowed(github_url)

        with tempfile.TemporaryDirectory(prefix="plotpilot-plugin-gh-") as temp_dir:
            temp_path = Path(temp_dir)
            clone_dir = temp_path / "repo"
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", github_url, str(clone_dir)],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=120,
                )
            except subprocess.CalledProcessError as exc:
                raise HTTPException(status_code=400, detail=f"GitHub 拉取失败：{exc.stderr.strip() or exc.stdout.strip()}")
            except subprocess.TimeoutExpired:
                raise HTTPException(status_code=504, detail="GitHub 拉取超时")

            plugin_source = clone_dir
            if not (clone_dir / "__init__.py").exists():
                candidates = [p for p in clone_dir.iterdir() if p.is_dir() and (p / "__init__.py").exists()]
                if len(candidates) == 1:
                    plugin_source = candidates[0]
                else:
                    raise HTTPException(status_code=400, detail="仓库根目录不是可安装插件，且未识别到唯一插件子目录")

            installed = _install_plugin_from_dir(plugin_source)
            return {
                "ok": True,
                "source": "github",
                **installed,
                "message": "插件已导入，请刷新插件列表；如插件包含前端脚本，建议刷新页面。",
            }

    @router.post("/import/upload")
    async def import_plugin_from_upload(request: Request, file: UploadFile = File(...)):
        _require_plugin_admin(request)
        filename = file.filename or "plugin.zip"
        if not filename.lower().endswith(".zip"):
            raise HTTPException(status_code=400, detail="目前仅支持上传 zip 插件包")

        with tempfile.TemporaryDirectory(prefix="plotpilot-plugin-upload-") as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / filename
            zip_path.write_bytes(await file.read())

            extract_dir = temp_path / "extracted"
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    _safe_extract_zip(zf, extract_dir)
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="上传文件不是有效的 zip 包")

            candidates = []
            if (extract_dir / "__init__.py").exists():
                candidates.append(extract_dir)
            candidates.extend([p for p in extract_dir.rglob("*") if p.is_dir() and (p / "__init__.py").exists()])

            unique_candidates: List[Path] = []
            seen: set[str] = set()
            for candidate in candidates:
                key = str(candidate.resolve())
                if key not in seen:
                    seen.add(key)
                    unique_candidates.append(candidate)

            if not unique_candidates:
                raise HTTPException(status_code=400, detail="压缩包内未找到可安装插件目录（缺少 __init__.py）")
            if len(unique_candidates) > 1:
                raise HTTPException(status_code=400, detail="压缩包内识别到多个插件目录，请一次只导入一个插件")

            installed = _install_plugin_from_dir(unique_candidates[0])
            return {
                "ok": True,
                "source": "upload",
                **installed,
                "message": "插件包已导入，请刷新插件列表；如插件包含前端脚本，建议刷新页面。",
            }

    @router.get("/manifest")
    async def get_plugin_manifest():
        items = list_plugin_manifests()
        frontend_scripts = collect_manifest_frontend_scripts(items)
        frontend_styles = collect_manifest_frontend_styles(items)
        return {
            "items": items,
            "total": len(items),
            "frontend_scripts": frontend_scripts,
            "frontend_styles": frontend_styles,
            "runtime": {
                "manifest_endpoint": "/api/v1/plugins/manifest",
                "plugins_endpoint": "/api/v1/plugins",
                "frontend_loader": "/plugin-loader.js",
                "runtime_api_version": PLATFORM_RUNTIME_API_VERSION,
                "frontend_runtime_version": FRONTEND_RUNTIME_VERSION,
            },
        }

    return router


def collect_frontend_scripts() -> List[str]:
    scripts: List[str] = []
    for plugin_dir in _discover_plugin_dirs():
        manifest = _load_manifest(plugin_dir)
        if not _effective_plugin_enabled(plugin_dir.name, manifest):
            continue
        scripts.extend(_collect_frontend_scripts_for_plugin(plugin_dir, manifest))
    return scripts


def collect_frontend_styles() -> List[str]:
    styles: List[str] = []
    for plugin_dir in _discover_plugin_dirs():
        manifest = _load_manifest(plugin_dir)
        if not _effective_plugin_enabled(plugin_dir.name, manifest):
            continue
        styles.extend(_collect_frontend_styles_for_plugin(plugin_dir, manifest))
    return styles
