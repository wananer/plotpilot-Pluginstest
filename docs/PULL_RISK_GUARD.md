# PlotPilot Pull Risk Guard

Run this before pulling remote updates:

```bash
python scripts/pre_pull_risk_check.py
```

Exit codes:

- `0`: no high-risk local changes detected.
- `1`: runtime code changes exist; create a safety branch or WIP commit before pulling.
- `2`: mixed staged/unstaged paths exist; do not pull yet.

High-risk examples include `MM`, `AM`, and `AD` paths from `git status --short`.
These mean the index and working tree disagree, so a merge or rebase can hide
which version was intended.

Recommended update flow:

```bash
python scripts/pre_pull_risk_check.py
git fetch origin
git diff --name-only HEAD origin/master
```

If the remote changed files under `application/`, `interfaces/`, `plugins/`,
`frontend/src/`, `frontend/public/`, or startup scripts, review overlap before
running `git pull --rebase origin master`.

Classification rules:

- Runtime code: `application/`, `interfaces/`, `plugins/`, `frontend/src/`,
  `frontend/public/`, `frontend/index.html`, `frontend/vite.config.ts`, and
  startup scripts such as `scripts/start_daemon.py`.
- Tests: `tests/` and `frontend/tests/`.
- Docs: `docs/`.
- Local or generated data: `data/`, `.omx/`, cache files, SQLite databases,
  logs, and Python bytecode.
- Plugin runtime state: `data/plugin_platform/` and `data/plugins/` are local
  install/runtime outputs. They should stay out of commits and do not need to
  block a source-code pull once ignored or cleaned.
