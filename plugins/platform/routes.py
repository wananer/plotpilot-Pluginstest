"""Shared platform API for plugin runtime status and hook introspection."""
from __future__ import annotations

from fastapi import APIRouter

from plugins.loader import list_plugin_manifests

from .compat import FRONTEND_RUNTIME_VERSION, PLATFORM_RUNTIME_API_VERSION
from .hook_dispatcher import list_hooks
from .host_database import create_default_readonly_host_database
from .plugin_storage import PluginStorage

router = APIRouter(prefix="/api/v1/plugins/platform", tags=["plugins:platform"])


@router.get("/status")
async def get_platform_status():
    storage = PluginStorage()
    host_database = create_default_readonly_host_database()
    plugins = list_plugin_manifests()
    incompatible = [
        {
            "plugin_name": item.get("name"),
            "reasons": ((item.get("compatibility") or {}).get("reasons") or []),
        }
        for item in plugins
        if not ((item.get("compatibility") or {}).get("compatible", True))
    ]
    return {
        "ok": True,
        "runtime_api_version": PLATFORM_RUNTIME_API_VERSION,
        "frontend_runtime_version": FRONTEND_RUNTIME_VERSION,
        "features": {
            "manifest_capabilities": True,
            "manifest_version_negotiation": True,
            "compatibility_report": True,
            "frontend_lifecycle": True,
            "frontend_styles": True,
            "hook_dispatcher": True,
            "plugin_storage": True,
            "job_registry": True,
            "host_facade": True,
            "host_database_readonly": host_database is not None,
        },
        "plugins": {
            "total": len(plugins),
            "enabled": sum(1 for item in plugins if item.get("enabled") is not False),
            "incompatible": len(incompatible),
            "items": [
                {
                    "name": item.get("name"),
                    "enabled": item.get("enabled"),
                    "compatibility": item.get("compatibility"),
                    "disabled_reason": item.get("disabled_reason"),
                }
                for item in plugins
            ],
            "incompatible_items": incompatible,
        },
        "storage_root": str(storage.root),
        "storage": storage.status(),
        "host_database": {
            "available": host_database is not None,
            "access": "read_only" if host_database is not None else "unconfigured",
        },
    }


@router.get("/hooks")
async def get_platform_hooks():
    return {"items": list_hooks()}
