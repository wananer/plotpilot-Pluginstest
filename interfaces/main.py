"""Minimal FastAPI host surface for the PlotPilot plugin platform.

This repository is a plugin-platform distribution, not the full PlotPilot
application. The file intentionally exposes only the smallest host API needed
to prove that plugins can be discovered, initialized, and listed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from plugins.loader import create_plugin_manifest_router, init_api_plugins


app = FastAPI(
    title="PlotPilot Plugin Platform Host",
    version="0.1.0",
    description="Minimal host used to validate the PlotPilot plugin runtime.",
)


def init_api(app: FastAPI) -> list[str]:
    loaded_plugins = init_api_plugins(app)
    manifest_route = "/api/v1/plugins/manifest"
    if not any(getattr(route, "path", "") == manifest_route for route in app.routes):
        app.include_router(create_plugin_manifest_router(), prefix="/api/v1")
    return loaded_plugins


_loaded_api_plugins = init_api(app)

_FRONTEND_PUBLIC_DIR = Path(__file__).resolve().parents[1] / "frontend" / "public"
_plugin_loader = _FRONTEND_PUBLIC_DIR / "plugin-loader.js"
if _plugin_loader.exists():
    app.get("/plugin-loader.js", include_in_schema=False, response_class=FileResponse)(
        lambda: FileResponse(str(_plugin_loader), media_type="application/javascript")
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {"ok": True, "loaded_plugins": _loaded_api_plugins}
