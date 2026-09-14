# Changelog

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
