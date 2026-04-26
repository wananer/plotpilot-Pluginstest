"""Sample hello plugin for PlotPilot plugin-platform verification."""
from __future__ import annotations


def init_api(app) -> None:
    existing = getattr(app.state, "sample_hello_plugin_loaded", False)
    if not existing:
        app.state.sample_hello_plugin_loaded = True


def init_daemon() -> None:
    return None
