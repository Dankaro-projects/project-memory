# Changelog

## 0.5.0b5 — Unreleased

The live viewer becomes an interactive project workspace. Users can create actions and sprints, select dependencies, revise plans and add comments while retaining the complete decision history. Concurrent edits preserve the draft and require a current version before saving. Offline exports remain available.

Codex and Claude setup enable conditional read-only outcome, intent and recovery agents through the installed host CLI. One durable SQLite table records requests, cancellation, explicit retries and immutable results. New completion claims require a current passing outcome check; existing historical completion remains intact. No runtime dependency or separate orchestration service is added.

Actual host evaluation exposed and repaired Codex configuration errors, repeated Stop feedback, and a browser save that invalidated unchanged evidence. Code changes and expired workers now invalidate live results without requiring another memory write. See [the evaluation report](workspace-agents-evidence-2026-09-14.md) for the paired cases, measured costs and unresolved limits.

## 0.5.0b4 — 14 September 2026

Work intent now connects to an explicit next action, scope, dependencies and sprint. The live and offline viewers add a read-only Kanban board with direct access to decisions, evidence, failures and recovery history.

- Atomic plan and sprint operations reuse existing episodes and append-only events. Version checks preserve concurrent edits and earlier plans.
- Bounded continuation distinguishes ready work, human proposals, dependency and evidence review, uncertain execution, another session's work, and completion that only needs its plan finalised.
- Decisions retain the work plan used at the time. Scope and prerequisite changes trigger review; routine progress changes do not.
- Startup and continuation hooks point to the planned work without starting another objective or claiming tool permissions.
- Setup opens the current viewer after an upgrade instead of reusing an older process.
- Existing records remain intact. The first plan write extends the existing event validation trigger transactionally; no dependency, task database or agent scheduler is added.

See [work board and continuation](docs/work-board.md) for usage and boundaries, and the [iteration evidence](docs/autonomy-2026-09-14.md) for measured failures and recovery.

## 0.5.0b3 — 14 September 2026

First-beta feedback exposed configured but inactive hooks, duplicate cross-host discovery, manual document refresh, an unclear requirements baseline and a static viewer.

- Setup opens an included live, read-only local viewer; offline exports remain available with explicit atomic replacement.
- Codex excludes Claude plugin hooks. Managed Claude hooks suppress duplicate plugin capture, and host receipt/result identities remain separate.
- Doctor checks actual Codex hook discovery and trust. Health distinguishes missing requirements, historical capture, native tool availability and unmeasured costs.
- Session start and stop refresh previously selected Markdown, preserving immutable evidence and explicit approval.
- Oversized context reports a recovery route; complete requirements can be paged and safely reused while unchanged. Omitted evidence stays discoverable.
- Review records explain changed dependencies and requirement revisions. Runtime dependencies remain zero.

See the dated feedback report for boundary verification and unresolved limitations.

## 0.5.0b2

Adds Claude Code as a setup client. `setup --client claude` writes the project MCP server to `.mcp.json` and the lifecycle hooks to `.claude/settings.local.json`; `--trust` pre-approves the server there. Uninstall removes exactly those entries.

The hook command accepts `--host claude`. Claude Code's `PostToolUseFailure` closes the matching tool receipt with the failure retained, and `SessionStart` now returns additional context at startup, resume and after compaction: memory session, active decision and unconfirmed tool calls. Codex receipt identifiers are unchanged; Claude Code receipts record their host and use the prompt identifier or capture time in place of a turn identifier.

`doctor` reports the configured client and expects eight lifecycle events for Claude Code, which has no interrupt hook. Memory tool calls are excluded from receipts under any server name. A source checkout with its own environment installs a `uv run --project` launcher instead of the release wheel.

On Claude Code, each prompt receives memory context only when an unconfirmed tool call or an unresolved decision needs attention, or when the prompt starts with an explicit `[memory:subject]` request; the session start context already carries the standing guidance. Codex prompts are unchanged.

Adds a Claude Code plugin manifest and a repository marketplace named `dankaro`. The plugin shares the Codex plugin's server and skill and adds the lifecycle hooks. The hook command accepts `--project` and exits silently in projects without a Project Memory database, so plugin hooks never block unrelated work.

## 0.5.0b1

First public beta of Project Memory. Adds an installable package, stable CLI, project setup and removal, an installed-process doctor, versioned launch configuration, a Codex plugin and public release checks. Project direction can change through explicit, evidenced revisions while retaining original requirements and flagging earlier dependent decisions for review.

Preserves the local SQLite core, three-tool MCP interface, mechanical Codex capture, interruption reconciliation, Markdown snapshots, bounded retrieval, separate subjects and offline HTML viewer from the private 0.4 evaluation.

The release does not claim universal client compatibility, guaranteed productivity gains or complete model inputs below 10K tokens. Those remain evaluation work.
