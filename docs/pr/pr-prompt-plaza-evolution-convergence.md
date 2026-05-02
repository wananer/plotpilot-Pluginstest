# PR: Prompt Plaza + Evolution convergence

## Change Type

- [x] feat
- [x] fix
- [x] refactor
- [x] docs
- [x] chore

## Summary

This PR makes Evolution the authoritative layer for overlapping continuity work and turns Prompt Plaza into the runtime prompt registry for native and plugin prompts.

It also stabilizes the test base so the full suite can run against isolated file-backed app state instead of mixing in-memory SQLite with path-based repositories.

## Architecture Impact

- Layers touched: `application`, `infrastructure`, `interfaces`, `frontend`, `plugins`, `tests`.
- Database impact: extends `prompt_nodes` metadata fields through the existing schema and idempotent initialization path; no new standalone business table.
- API impact: Prompt Plaza prompt/node payloads expose runtime metadata (`owner`, `runtime_status`, `authority_domain`, `runtime_reader`, `editable`).

## Runtime Boundaries

- Prompt rendering resolves in this order: active Prompt Plaza DB version, seed JSON, caller fallback.
- Evolution plugin prompts are seeded into Prompt Plaza and still keep code fallbacks if DB access is unavailable.
- Evolution owns chapter fact extraction/review overlap when enabled; native paths remain fallback or supplemental checks.
- Tension scoring routes through the same prompt registry path instead of a separate hardcoded scoring prompt.

## Verification

```bash
.venv/bin/python -m pytest tests --collect-only -q
# 1126 tests collected / 1 skipped

.venv/bin/python -m pytest tests -q
# 1119 passed, 8 skipped, 10 warnings

.venv/bin/python -m pytest tests/integration/test_novel_workflow.py tests/integration/test_storyline_integration.py tests/test_plugin_import_api.py tests/unit/infrastructure/ai/test_prompt_resolver.py tests/unit/infrastructure/ai/test_prompt_manager_registry.py tests/unit/application/services/test_chapter_aftermath_evolution_authority.py tests/test_plugin_platform_runtime.py tests/test_evolution_world_service.py tests/test_autopilot_daemon_state.py -q
# 133 passed, 6 warnings

cd frontend && npm run build
# passed, with existing large chunk warning

git diff --check
# passed

DISABLE_AUTO_DAEMON=1 .venv/bin/python -c "from interfaces.main import app; print(len(app.routes))"
# 267
```

## Known Non-Blocking Items

- FastAPI `on_event` and `HTTP_422_UNPROCESSABLE_ENTITY` deprecation warnings remain.
- Vite reports one large bundled chunk after minification.
- 8 tests remain skipped as before.

## Rollback

- Disable the `world_evolution_core` plugin to use native fallback paths.
- If Prompt Plaza DB state is unavailable, runtime rendering falls back to seed JSON and then code fallback.
