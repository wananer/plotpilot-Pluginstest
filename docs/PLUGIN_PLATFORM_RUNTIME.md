# PlotPilot Plugin Platform Runtime

This document records the shared runtime primitives used by stateful PlotPilot plugins.

## Backend primitives

- `plugins.platform.hook_dispatcher`
  - Registers and dispatches backend hooks such as `before_context_build` and `after_commit`.
- `plugins.platform.plugin_storage`
  - Provides file-backed sidecar JSON/JSONL storage under `data/plugins/<plugin_name>/`.
- `plugins.platform.job_registry`
  - Defines `PluginJobRecord` and dedup-key helpers for replayable plugin workflows.
- `plugins.platform.host_facade`
  - Provides a stable host-facing adapter surface so plugins avoid importing deep host internals.
- `plugins.platform.routes`
  - Exposes `/api/v1/plugins/platform/status` and `/api/v1/plugins/platform/hooks`.

## Frontend lifecycle

Frontend plugins can register an object with optional lifecycle methods:

```js
window.PlotPilotPlugins.plugins.register({
  name: 'my_plugin',
  async init(ctx) {},
  async dispose(ctx) {},
});
```

The runtime also supports manifest-declared styles via `frontend.styles`.

## Installer behavior

`platform/scripts/install_plugin_platform.py` now copies both:

- `plugins/loader.py`
- `plugins/platform/*`

This keeps fresh PlotPilot hosts compatible with stateful workflow plugins.
