# Evolution Unified Constraint Release Candidate

## Summary

This RC consolidates chapter continuity, execution drafts, route and character state, object/deadline constraints, and style drift into one auditable quality pipeline. The release goal is stability and reviewability: no new constraint families, no schema expansion beyond existing compatibility fields, and no automatic LLM pressure generation during release checks.

## Functional Groups

- **Unified constraint kernel**: `continuity_constraints` now represents route, entity identity, character state, object state, deadline, goal, and threat obligations with a common issue shape: `constraint_type`, `severity`, `evidence`, `repair_hint`, and `confidence`.
- **Chapter execution draft and route continuity**: every generated chapter can lock an opening state before prose generation, then validate the opening against previous boundary and route state. Automatic repair still targets only the opening segment.
- **Style drift audit**: `narrative_voice` enters the unified issue stream, but ordinary drift remains `severity=warning` with Gate status `passed`. Only severe consecutive drift can request review.
- **Reports and diagnostics**: article and diagnostic reports separate continuity blocking from style warnings through `continuity_blocking_count`, `style_warning_count`, and `style_needs_review_count`.
- **Workbench compatibility**: legacy fields remain available: `boundary_gate_status`, `route_gate_status`, `chapter_draft_status`, `constraint_gate_status`, `last_constraint_issue`, and `last_chapter_audit.issues`.
- **Persistence compatibility**: existing file and SQLite repositories persist the new status and audit fields without requiring a destructive migration.

## Public Contract

- Gate statuses are restricted to `passed`, `auto_revised`, `needs_review`, and `skipped`.
- `warning` is allowed only as issue severity, never as a Gate status.
- Style drift is reported separately from continuity blocking and must not fail continuity acceptance unless it escalates to `needs_review`.
- Existing generated novels are not automatically repaired; they need explicit chapter-opening revision or regeneration.

## Verification

Reviewer quick path:

1. Read this release note and the latest generated RC report.
2. Run the fast check:

```bash
.venv/bin/python scripts/evaluation/evolution_release_candidate_check.py --skip-frontend-build --pytest-timeout 300
```

3. Run the full check before approving the branch:

```bash
.venv/bin/python scripts/evaluation/evolution_release_candidate_check.py --pytest-timeout 300 --frontend-timeout 240
```

Run the fast RC check while iterating:

```bash
.venv/bin/python scripts/evaluation/evolution_release_candidate_check.py --skip-frontend-build
```

Run the full RC check before review:

```bash
.venv/bin/python scripts/evaluation/evolution_release_candidate_check.py
```

Run strict sample validation when the sample was generated after the latest status fields landed:

```bash
.venv/bin/python scripts/evaluation/evolution_release_candidate_check.py \
  --strict-sample-status \
  --sample-run-dir <existing-run-dir> \
  --sample-novel-id <novel-id>
```

The script runs targeted backend checks, the Gate status scan, frontend build, and optional existing 10-chapter sample validation. It writes:

- `.omx/artifacts/evolution-release-candidate-*/release_candidate_report.json`
- `.omx/artifacts/evolution-release-candidate-*/release_candidate_report.md`
- `.omx/artifacts/evolution-release-candidate-latest.json`

Sample validation is read-only. If no 10-chapter artifact is available, the script records `sample_status=missing` without blocking local code validation. Auto-discovery chooses the newest artifact database with at least 10 completed chapters.

## Reviewer Checklist

- Gate statuses are only `passed`, `auto_revised`, `needs_review`, or `skipped`; `warning` appears only as issue severity.
- Unified issues include `constraint_type`, `severity`, `evidence`, `repair_hint`, and `confidence` for continuity and style paths.
- Style drift `narrative_voice` warnings are reported separately and do not increase continuity blocking counts.
- Article and diagnostics reports expose `continuity_blocking_count`, `style_warning_count`, and `style_needs_review_count`.
- Existing 10-chapter sample validation reports `completed_chapters=10` and `continuity_blocking_count=0`.
- Frontend verification is build-level: Workbench/Autopilot/VoiceDriftPanel API contracts compile.
- Strict sample mode should be used for fresh samples; older artifacts may show `needs_review=null` or `constraint_gate_status=null` because those columns were not present.

## PR Summary

This PR hardens Evolution chapter generation by consolidating continuity, route, character, object, deadline, goal, threat, and style-drift checks into a unified constraint/audit pipeline. The main behavior change is that chapter generation now carries explicit pre-generation and post-generation continuity state, while reports and Workbench status surfaces distinguish continuity blocking from ordinary style warnings.

Public interface additions remain compatibility-first:

- Existing status fields remain available: `boundary_gate_status`, `route_gate_status`, `chapter_draft_status`, `constraint_gate_status`, `last_constraint_issue`, and `last_chapter_audit.issues`.
- New issue records use the shared shape `constraint_type`, `severity`, `evidence`, `repair_hint`, and `confidence`.
- Report summaries expose `continuity_blocking_count`, `continuity_issue_count`, `style_warning_count`, `style_needs_review_count`, and `story_quality_issue_count`.
- Gate status values are fixed to `passed`, `auto_revised`, `needs_review`, and `skipped`; `warning` is reserved for issue severity only.

Review recommendation: read this file first, then inspect the diff by the functional groups below instead of raw path order. The cross-layer behavior is intentional: the generation chain emits constraint state, the Gate/report layers classify it, persistence preserves it, and the frontend displays it without making independent continuity decisions.

## Diff Review Groups

- **Constraint kernel**: unifies route, entity, deadline, object, goal, threat, and style issues into one auditable issue shape. Main risk is status semantic drift; covered by Gate status tests and scan.
- **Generation chain**: chapter execution drafts, boundary state, and route state feed the next chapter before prose generation. Main risk is over-constraining generation; covered by chapter execution draft and hosted write service tests.
- **Style drift**: reports `narrative_voice` through the unified issue stream while keeping ordinary drift as `warning`. Main risk is contaminating continuity blocking; covered by voice drift, API, and article report tests.
- **Reports and diagnostics**: separates continuity blocking from style warnings in pressure and diagnostic reports. Main risk is acceptance-count confusion; covered by article report and diagnostics summary tests.
- **Frontend and API compatibility**: Workbench, Autopilot, and VoiceDriftPanel consume added fields while preserving old names. Main risk is response-shape drift; covered by API tests and frontend build.
- **Persistence compatibility**: file and SQLite repositories preserve new audit/status fields without requiring destructive migration. Main risk is old artifact gaps; RC default mode tolerates missing status columns and strict mode rejects them.
- **RC tooling**: release candidate script ties together targeted tests, Gate scan, frontend build, sample validation, and latest index. Main risk is tool regressions; covered by dedicated RC script unit tests.

## Suggested Lore Commit Message

```text
Make Evolution continuity auditable before release

Chapter generation had accumulated several parallel checks for boundary,
route, character, deadline, and style quality. This release consolidates
those paths around a shared constraint issue contract so generation,
autopilot state, reports, persistence, and Workbench status speak the same
language while preserving legacy fields for compatibility.

Constraint: Gate statuses are limited to passed, auto_revised, needs_review, and skipped
Constraint: warning is issue severity only and must not be written as a Gate status
Constraint: Existing old pressure artifacts may not contain needs_review or constraint_gate_status columns
Rejected: Add another standalone style Gate | would repeat the split-Gate problem and risk polluting continuity acceptance
Rejected: Auto-generate fresh 10-chapter samples during RC checks | would make release validation costly and non-deterministic
Confidence: high
Scope-risk: broad
Reversibility: messy
Directive: Do not add new constraint families as parallel Gates; add adapters into the unified issue/report chain
Tested: Evolution RC check passed with targeted pytest, Gate scan, sample validation, and frontend build
Not-tested: Strict sample status against a freshly generated post-RC 10-chapter artifact
```

## Recommended Sample Evidence

- Latest verified RC report: `.omx/artifacts/evolution-release-candidate-20260504-175552-092070/release_candidate_report.json`
- Auto-discovered sample at that point:
  - `run_dir`: `.omx/artifacts/evolution-frontend-ab-v2-agent-arch-calib-20260501-013541`
  - `novel_id`: `novel-restart-experiment-20260427-184658`
  - `completed_chapters`: `10`
  - `continuity_blocking_count`: `0`
  - `style_warning_count`: `0`
  - status columns: unavailable in the old artifact; use strict mode with a fresh sample for final status-field acceptance.

## Remaining Risks

- The current branch contains a broad prior diff; reviewers should review by functional group rather than raw file order.
- Existing artifacts may not have the latest status columns, so RC sample validation treats missing status fields as informational when chapter/report checks pass.
- Frontend verification is build-level only; no new visual redesign was added in this RC.
