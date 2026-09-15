# Changelog

## 0.5.0b9 — 15 September 2026

The viewer opens on a project overview with current work, attention items and recent decisions. Five primary sections expose contextual views and filters. Work supports Board and List formats; record summaries retain a Table option. Expanded reading, document outlines and decision pages make the evidence easier to follow. Expected and observed consequences remain tied to the exact decision, including earlier failures, later revisions and stale evidence. No data migration, model call or runtime dependency is added.

Capture failures remain separate across sessions and recover once after interrupted cleanup, including legacy failure files. File-access errors return an MCP tool error without closing the connection. The viewer keeps failed detail and history refreshes visible and retries them. Unchanged viewer requests reuse content hashes; review creation and completion still check fresh files. Direction retrieval pages complete versioned requirements and approval evidence instead of duplicating them in its overview. Continuation guidance uses progress updates that preserve scope and evidence.

Agent reviews separate task acceptance from project constraints, with explicit applicability and preserved exceptions. Evidence manifests support selective reads. The wait command now honours its own limit. Review status preserves child activity, timeout diagnostics and available partial reports and usage without granting approval. A progress operation updates status without rewriting scope or its evidence.

The workspace adds project skills, explicit human requirement and lesson reviews, a Needs attention view, and a project map with recorded relationships, workflows and architecture. Skill selections preserve exact package versions, including supporting files; changed local files appear without a rebuild. Diagram changes preserve their reasons and earlier versions. Import does not activate instructions or execute scripts, and a skill-use report is distinct from a selection.

Frontend responsibilities now live in separate source files assembled into the existing self-contained viewer. The package still requires no runtime dependencies or frontend build command. Bundle validation checks the complete viewer assets. New browser cases cover product and consulting work, evidence links, skill drift and conflict recovery.

## 0.5.0b8 — 14 September 2026

Board refreshes preserve keyboard focus on the same action. A delayed-response browser case reproduces the focus loss and verifies the correction. Beta 7 was withheld after the browser gate exposed this race; all six operating-system and Python combinations passed.

Runtime subprocesses use UTF-8 explicitly, including on Windows where isolated Python ignores the parent UTF-8 environment setting. Beta 6 was withheld after the Windows installed-wheel gate failed. This release includes its import-isolation correction.

Installed launchers, diagnostics, live viewers and review workers now load the selected runtime even when a project contains an older `memory_module` package. An upgrade of the original prototype exposed this conflict after beta 5 publication. The installed-wheel check now includes a conflicting project package. Existing data and project files remain unchanged.

## 0.5.0b5 — 14 September 2026

Opening the internal HTML template now shows the workspace launch command instead of unstyled controls. Project information explains live updates and offline exports, and the [user guide](docs/user-guide.md) covers everyday use and recovery.

Host checks now expose unassessed requests, unassigned activity, missing outcomes and capture gaps. Intent checkpoints can accompany a plan or record write. Stop intervenes once, and an explicitly assessed unknown result remains visible without another intervention. Reviewer reports must cover every numbered criterion. The live workspace displays recording gaps and capture failures without a rebuild.

The live viewer becomes an interactive project workspace. Users can create actions and sprints, select dependencies, revise plans and add comments while retaining the complete decision history. Concurrent edits preserve the draft and require a current version before saving. Offline exports remain available.

Codex and Claude setup enable conditional read-only outcome, intent and recovery agents through the installed host CLI. One durable SQLite table records requests, cancellation, explicit retries and immutable results. New completion claims require a current passing outcome check; existing historical completion remains intact. No runtime dependency or separate orchestration service is added.

Actual host evaluation exposed and repaired Codex configuration errors, repeated Stop feedback, and a browser save that invalidated unchanged evidence. Code changes and expired workers now invalidate live results without requiring another memory write.

## 0.5.0b4 — 14 September 2026

Work intent now connects to an explicit next action, scope, dependencies and sprint. The live and offline viewers add a read-only Kanban board with direct access to decisions, evidence, failures and recovery history.

- Atomic plan and sprint operations reuse existing episodes and append-only events. Version checks preserve concurrent edits and earlier plans.
- Bounded continuation distinguishes ready work, human proposals, dependency and evidence review, uncertain execution, another session's work, and completion that only needs its plan finalised.
- Decisions retain the work plan used at the time. Scope and prerequisite changes trigger review; routine progress changes do not.
- Startup and continuation hooks point to the planned work without starting another objective or claiming tool permissions.
- Setup opens the current viewer after an upgrade instead of reusing an older process.
- Existing records remain intact. The first plan write extends the existing event validation trigger transactionally; no dependency, task database or agent scheduler is added.

See [work board and continuation](docs/work-board.md) for usage and boundaries.

## 0.5.0b3 — 14 September 2026

First-beta feedback exposed configured but inactive hooks, duplicate cross-host discovery, manual document refresh, an unclear requirements baseline and a static viewer.

- Setup opens an included live, read-only local viewer; offline exports remain available with explicit atomic replacement.
- Codex excludes Claude plugin hooks. Managed Claude hooks suppress duplicate plugin capture, and host receipt/result identities remain separate.
- Doctor checks actual Codex hook discovery and trust. Health distinguishes missing requirements, historical capture, native tool availability and unmeasured costs.
- Session start and stop refresh previously selected Markdown, preserving immutable evidence and explicit approval.
- Oversized context reports a recovery route; complete requirements can be paged and safely reused while unchanged. Omitted evidence stays discoverable.
- Review records explain changed dependencies and requirement revisions. Runtime dependencies remain zero.


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
