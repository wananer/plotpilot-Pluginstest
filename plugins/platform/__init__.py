"""Shared runtime support for PlotPilot plugins."""
from __future__ import annotations

from .context_bridge import dispatch_hook_sync, render_context_blocks
from .hook_dispatcher import clear_hooks, dispatch_hook, dispatch_hook_sync_best_effort, list_hooks, register_hook
from .host_integration import build_generation_context_patch, notify_chapter_committed
from .host_facade import PlotPilotPluginHost
from .job_registry import PluginJobRecord, PluginJobRegistry
from .plugin_storage import PluginStorage
from .runtime_types import PluginHookPayload, PluginHookResult

__all__ = [
    "PlotPilotPluginHost",
    "PluginHookPayload",
    "PluginHookResult",
    "PluginJobRecord",
    "PluginJobRegistry",
    "PluginStorage",
    "build_generation_context_patch",
    "clear_hooks",
    "dispatch_hook",
    "dispatch_hook_sync",
    "dispatch_hook_sync_best_effort",
    "list_hooks",
    "register_hook",
    "notify_chapter_committed",
    "render_context_blocks",
]
