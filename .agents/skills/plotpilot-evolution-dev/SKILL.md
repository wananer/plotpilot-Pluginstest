---
name: plotpilot-evolution-dev
description: Project-specific PlotPilot Evolution development workflow. Use for any future work on Evolution, world_evolution_core, Evolution Codex prompts/skills, agent orchestration, context injection, chapter review continuity, pressure tests, prompt-routing design, or plugin lifecycle changes in this repository. Also use when a request says Evolution, Codex 提示词, Codex skill, 世界演化, dynamic role cards, agentic evolution, or asks to plan/implement/review/release Evolution-related work.
---

# PlotPilot Evolution Dev

Use this skill before changing Evolution-related code, prompts, tests, docs, or release artifacts in this repository.

This skill is the project-specific operating loop for Evolution development. It routes work through the right Codex prompts, keeps evidence grounded in the repo, and forces each change through planning, tests, review, verification, and memory capture.

## Quick Start

1. Classify the request:
   - unclear scope: use `analyst`, then `planner`
   - design/architecture: use `planner` + `architect` + `critic`
   - implementation: use `executor`
   - bug/failure: use `debugger`; use `build-fixer` for build/type/toolchain failures
   - testing: use `test-engineer`
   - review: use `code-reviewer`; add `security-reviewer` for permissions, tokens, file writes, external commands, MCP, or hook boundaries
   - completion proof: use `verifier`
   - docs/release: use `writer` + `git-master`

2. Inspect the repo before making claims. Prefer `rg` / `rg --files`.

3. For substantial work, create or reuse a context snapshot:
   - `.omx/context/evolution-<task-slug>-<timestamp>.md`

4. Run the closed loop:
   - intake -> context -> prompt routing -> plan -> test spec -> implement -> verify -> review -> final verify -> memory -> release

5. Before claiming done, run `verifier` logic: identify the proof, run/read it, and report evidence plus gaps.

For the full closed-loop playbook, read `references/development-loop.md`.

## Prompt Routing Matrix

| Work shape | Required Codex prompt(s) | Output |
| --- | --- | --- |
| clarify Evolution scope | `analyst`, `planner` | boundaries, assumptions, acceptance criteria |
| plan Evolution feature | `planner`, `architect`, `critic` | plan, ADR, test strategy |
| implement planned work | `executor` | minimal verified diff |
| add/change behavior | `test-engineer`, `executor`, `verifier` | regression tests and passing evidence |
| debug failure | `debugger`; `build-fixer` for toolchain | root cause and minimal fix |
| review branch | `code-reviewer`, optional `security-reviewer` | severity-rated findings |
| simplify changed code | `code-simplifier`, then `verifier` | simpler behavior-preserving diff |
| document/release | `writer`, `git-master`, `verifier` | docs, commit/PR text, release notes |

## Evolution-Specific Touchpoints

Common files and areas:

- Plugin runtime: `plugins/loader.py`, `plugins/platform/**`
- Evolution plugin: `plugins/world_evolution_core/**`
- Prompt registry: `plugins/world_evolution_core/prompt_registry.py`, `infrastructure/ai/**`
- Frontend plugin surface: `frontend/public/plugin-loader.js`, `plugins/world_evolution_core/static/**`
- Evaluation: `scripts/evaluation/evolution_*.py`, `.omx/artifacts/evolution-*`
- Tests: `tests/test_evolution_world_service.py`, `tests/test_plugin_platform_runtime.py`, `tests/test_evolution_pressure_test.py`, `tests/unit/**`
- Human docs: `docs/codex-evolution-plugin-development-guide.md`

Do not assume all Evolution work is in `plugins/world_evolution_core`; this project also has host integration, Prompt Plaza, frontend runtime, evaluation scripts, and artifact-based pressure tests.

## Required Gates

Use these gates unless the task is a tiny docs-only edit.

1. **Plan Gate**
   - State goal and non-goals.
   - Identify touched files.
   - Define test/verification target.

2. **Test Gate**
   - For behavior changes, lock expected behavior first or identify existing tests that cover it.
   - For prompt/skill changes, validate trigger conditions and output contract.

3. **Implementation Gate**
   - Keep diffs narrow.
   - Reuse existing patterns before adding abstractions.
   - Do not add dependencies without explicit request.

4. **Review Gate**
   - Run code-review stance for meaningful changes.
   - Add security review for permissions, tokens, raw SQL, file paths, external commands, MCP, and hooks.

5. **Verification Gate**
   - Run relevant tests/build/checks.
   - Read output before reporting.
   - State untested gaps honestly.

6. **Memory Gate**
   - Record non-obvious decisions in docs, `.omx/notepad.md`, or Lore commit trailers.
   - Capture rejected alternatives only when they prevent future rework.

## Default Verification Commands

Pick the smallest command set that proves the change:

```bash
.venv/bin/python -m pytest tests/test_plugin_platform_runtime.py tests/test_evolution_world_service.py -q
.venv/bin/python -m pytest tests/unit/infrastructure/ai/test_prompt_manager_registry.py -q
.venv/bin/python scripts/evaluation/evolution_smoke_test.py
cd frontend && npm run build
```

Use targeted tests first. Run broader suites when touching shared platform, prompt resolution, generation pipelines, or frontend runtime.

## Output Contract

When finishing Evolution work, report:

- changed files
- what changed and why
- tests/checks run with results
- remaining risks or not-tested items
- memory/decision records added, if any

For review tasks, findings come first and must include file/line references.
