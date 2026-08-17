---
name: manage-project-files
description: Establish, explain, inspect, and safely remediate governed team project workspaces and project file trees. Use when Codex needs to create a workspace or YYYYMM project structure, explain what belongs in each folder, classify a file, audit directory and naming compliance, produce a remediation plan, apply explicitly approved moves or renames, verify fixes, or prepare project closeout and knowledge promotion. Supports Chinese Windows paths and keeps all mutations preview-first and confirmation-gated.
---

# Manage Project Files

Use the bundled command-line tool for deterministic work. Keep client files, live paths, credentials, team-specific AI mappings, and project exceptions outside this reusable package.

## Core rules

1. Treat the workspace, active project, archive, and external knowledge base as separate layers.
2. Require the eight project directories in `references/governance.md`. Create `99_未归档` only when requested.
3. Classify by content nature, authority, maturity, and current state. If evidence is insufficient, place or recommend the file under `00_输入待分配`; never guess.
4. Keep one current version per topic. Move superseded copies to `98_历史归档` and mark them `旧版`, `已替代`, or `重复`.
5. Treat the project root as an entry surface, not a business-file store. Allow approved `00_*.md` files and configured infrastructure directories only.
6. Never delete permanently. Preview every write. Require explicit user approval before applying a remediation plan.
7. Do not weaken the required tree, preview gate, no-delete rule, or source-change checks through project exceptions.

Read `references/governance.md` when explaining folder purpose, flow, precedence, naming, or controlled exceptions. Read `references/configuration.md` when a workspace needs custom paths, AI mappings, infrastructure allowlists, or project exceptions.

## Run the tool

Locate `scripts/manage_project_files.py`, then use the available Python runtime.

### Create structures

- Preview a workspace: `python manage_project_files.py init-workspace --root <path> [--knowledge-root <path>]`
- Create after approval: add `--apply`.
- Preview a project: `python manage_project_files.py init-project --root <workspace> --name "YYYYMM 项目名" [--with-unfiled]`
- Create after approval: add `--apply`.

Never pass `--apply` until the user has seen the preview or explicitly requested immediate creation.

### Classify

Run `classify --name <filename> --kind <kind>`. Prefer an explicit kind based on the user's description. Valid kinds are `unknown`, `requirement`, `planning`, `reference`, `execution`, `knowledge`, `obsolete`, `misc`, and `temporary`. If details remain ambiguous, use `unknown`.

### Audit and verify

Run `audit --root <workspace> --format markdown` for a read-only report. Use `--config <policy.json>` when a workspace has approved exceptions. Run `verify` after changes; it returns a failing exit code while issues remain.

### Remediate

1. Run `plan-fix --root <workspace> --output <plan.json>`.
2. Review every action with the user. `review` items are informational and cannot execute.
3. For ambiguous root files, prepare an external mapping JSON from relative source path to a standard destination folder and regenerate with `--mapping <mapping.json>`.
4. Apply only approved action IDs: `apply --plan <plan.json> --approve A001,A002 --confirm APPLY`.
5. Run `verify` and report resolved, remaining, and skipped items.

Abort rather than overwrite an existing destination, escape the workspace root, or act on a source whose fingerprint changed after planning.

## Output expectations

- Lead with the current compliance status or proposed destination.
- Explain each classification using purpose, prohibited content, and next flow.
- Separate automatic safe fixes from human-review items.
- State clearly whether any files were actually created, moved, or renamed.
