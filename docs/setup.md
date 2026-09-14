# Setup and lifecycle

Run commands inside the intended project or pass `--project /absolute/path`. A missing database is created only by `setup`; `serve`, `doctor` and `view` refuse a missing database rather than silently create an empty project.

## Select existing documents

```sh
project-memory setup --client codex --trust --document VISION.md --document DECISIONS.md
```

The command opens the included live HTML viewer automatically. Use `--no-view` for headless setup. There is no separate HTML installation. `project-memory view` reopens the same local service and browser address.

Select **Work board** to see sprint work, blockers and decision history. The [work board guide](work-board.md) explains how the assistant records intent and resumes from the same state.

Repeated setup does not duplicate hooks or unchanged source versions. Source documents remain in place. If the project has no agreed requirements, setup stores an explicit statement that none have been approved. You can supply already agreed text using repeated `--requirement` arguments. Changing existing requirements requires a separate, evidenced approval, not a setup flag.

The assistant can capture another selected file using `memory_write document`. It should use `memory_get direction` before an explicitly authorised `approve_requirements` operation. That operation requires complete requirement sentences, an approval reason, actor, evidence references and the current version. File capture and approval are different actions.

## Claude Code

```sh
project-memory setup --client claude --trust --document VISION.md
```

Setup adds a `project_memory` entry to `.mcp.json` in the project root and the hook commands to `.claude/settings.local.json`. Both files keep their other entries. The local settings file holds machine-specific launch paths and stays out of version control. `--trust` lists the server in `enabledMcpjsonServers` so Claude Code does not ask for approval; without it, approve the project server when a new session asks. Claude Code needs no separate hook trust step.

The hook command carries `--host claude`. Claude Code sends the same field names as Codex for tool events (`tool_name`, `tool_input`, `tool_use_id`, `tool_response`) and adds `transcript_path`, `cwd` and `permission_mode`, which are not stored. Codex numbers turns; Claude Code does not, so repeated lifecycle events in one session are kept distinct by their prompt identifier or capture time. Three differences from Codex remain:

- Claude Code has no `Interrupt` hook. `doctor` does not expect it for this client. An interrupted tool call leaves its `PreToolUse` receipt unconfirmed, which the assistant reconciles with evidence in the same way as after a Codex interruption.
- Claude Code raises `PostToolUseFailure` instead of `PostToolUse` when a tool fails. It is stored as a `PostToolUse` receipt with `failed` set and the original event name in the payload, so the tool receipt closes and the failure remains visible.
- Claude Code compacts the conversation automatically. `PreCompact` and `PostCompact` are recorded. The `SessionStart` hook returns additional context at startup, resume and after compaction: the memory session, any active decision with its version, and the number of unconfirmed tool calls. It never returns record text; the assistant retrieves that explicitly.

Memory tool calls made through any server name that ends in the three tool names are not captured as receipts, which avoids recursive noise when the server is installed as `project_memory`, `project-memory` or through a plugin.

## Generic MCP configuration

After `project-memory setup --client mcp`, adapt this configuration to the client's documented MCP settings:

```json
{
  "mcpServers": {
    "project_memory": {
      "command": "project-memory",
      "args": ["serve", "--project", "/absolute/path/to/project"]
    }
  }
}
```

Use the absolute installed executable if the desktop client cannot find it on PATH. `PROJECT_MEMORY_PROJECT` can supply the project directory when a client supports environment configuration. There is no global active-project pointer. Cloud-only clients cannot reach a local stdio process.

The Codex and Claude Code plugins provide the same three MCP tools and a short workflow skill; the Claude Code plugin also carries the lifecycle hooks, which capture only in projects that have a Project Memory database. Install it from this repository's marketplace. If a project already has a setup-managed MCP connection, keep only one MCP connection enabled. Claude plugin hooks skip capture when the same project has managed Claude hooks. Codex explicitly excludes the Claude hook file from plugin discovery. Codex hooks require project setup and host trust; plugin discovery alone does not enable capture.

## Verify the connection

`project-memory doctor` starts a separate installed MCP process, initialises it, lists tools and requests the decision schema. It also checks SQLite integrity, identifies a missing requirements baseline, reports measurement coverage, and shows observed hook counts and unconfirmed actions. For a Codex installation, it asks the actual host to list enabled hooks and trust, and reports missing events, duplicate commands or Claude hooks discovered in Codex. It does not change trust. An unavailable host produces an explicit unverified result. The expected lifecycle events follow the client recorded at setup: nine for Codex, eight for Claude Code. Missing lifecycle events may not have happened yet; existing receipt counts are historical evidence, not proof that today's host configuration still works.

A successful subprocess check does not establish that the three native tools are available in an already open task. In that task, call `memory_get` with `view: health` once. If the tools are absent, open a new task after setup; do not report a custom stdio bridge as native integration.

In a new Codex task, ask the assistant to capture a selected document, record a decision with evidence, perform a small project action and record its actual outcome. Inspect the records with `view` and `doctor`. Reproducible live interruption and lifecycle harnesses are in `examples/`; they require a signed-in local Codex and intentionally execute synthetic work. [Evidence](evidence.md) separates those checks from unit tests.

## Back up, upgrade and remove

```sh
project-memory backup /absolute/path/to/backup.sqlite
```

The destination must be new. SQLite's backup API provides a consistent database copy. Setup also creates a backup of the database and existing Codex configuration on first connection. Keep backups on storage you control.

Upgrade by running the current version's setup command inside the intended project:

```sh
uvx project-memory-mcp@0.5.0b4 setup --client codex --trust
uvx project-memory-mcp@0.5.0b4 doctor
```

Use `--client claude --trust` for Claude Code, or `--client mcp` for another local MCP client. The recorded launcher is versioned; setup updates its configuration and verifies new Codex host hashes. Start a new task after an upgrade. A configuration edit within the managed block produces a conflict rather than overwriting it. If you use the optional plugin, update it through the host's plugin manager as well; an already running task retains its loaded tool schemas.

If setup is interrupted, repeat the same setup command. A small ownership file lets it finish after either configuration write. Uninstall after completing any pending setup:

```sh
project-memory uninstall
uv tool uninstall project-memory-mcp
```

Uninstall removes only its project connection and exact hook commands, including the `.mcp.json` entry and local settings hooks written for Claude Code. It preserves records, source documents, backups and other client configuration. Codex can retain inactive trust entries for removed commands. Setup never edits unrelated global MCP connectors.

When the package runs from a source checkout with its own `.venv`, setup writes a launcher that runs that checkout through `uv run --project`, so unreleased changes can be exercised in a real host. An installed wheel keeps the versioned release launcher.

## Existing memory-module databases

Back up the database first. If the old project already has the legacy `memory` MCP adapter and its command hooks, disable that connection before adding the public adapter so both versions do not capture the same work. The installer detects the legacy MCP entry and refuses to create a duplicate. Use `project-memory setup --db /absolute/path/to/existing.sqlite --client codex --trust` to explicitly connect it. Core history remains in SQLite schema 2; optional host and project-revision tables are additive. Original immutable requirements remain revision zero. Older versions do not understand revised requirements: after the first approved revision, use this version or newer; restore the pre-upgrade backup for a deliberate rollback.

Do not copy an old private evidence ZIP into the public repository. `.memory` is private operational data, including generated HTML and backups.

## Desktop bundle

The GitHub release also provides an MCPB file. A compatible desktop client asks you to select a project directory and starts the bundled Python server. The directory picker replaces hand-edited MCP configuration. Python 3.11+ must be available to the client (`python3` on macOS/Linux, `python` on Windows). The bundle initialises an empty selected project when needed, with no approved project-specific requirements; it preserves an existing setup. It supports generic explicit capture, not Codex lifecycle hooks. The bundle entry point is tested independently; see the compatibility evidence before assuming support in a particular client.

## Daily use and document refresh

`memory_get health` distinguishes an unestablished baseline, recorded requirements, observed capture and unmeasured costs. A populated database does not prove complete product coverage. Capture vision, decisions, research and ideas as selected evidence; the assistant still needs to check their meaning and preserve explicit approval.

Codex and Claude hooks refresh previously selected Markdown files at session start and stop, up to 100 paths per hook. Unchanged files do not create another source version. Older source text and decision references remain intact. New files, moved files and symlinks require explicit selection or review; the hook does not crawl the project. A file that changes during reading is rejected for that read. Use the supported commands for a check or an immediate refresh:

```sh
project-memory check
project-memory sync
project-memory sync --offset 100
```

Both report changed, missing and unreadable paths and whether more files remain. The equivalent MCP operations are `memory_get` with `view: documents` and `memory_write` with `operation: sync`; use a fresh request key for a new refresh. Reusing a request key returns the original receipt. Neither command approves document proposals, extracts PDF capabilities, changes requirements or accepts a lesson. Sync at a lifecycle boundary does not guarantee that an editor has finished a non-atomic save.

When mandatory context exceeds a requested budget, the error reports the minimum size and a recovery call. `memory_get` with `view: requirements` pages complete requirement sentences. Only after reading every page may the caller reuse that revision's signature as `seen.requirements`. The complete requirements must still be present in the model's current context; discard the signature after compaction unless the requirements themselves were retained. Revision or freshness changes invalidate the signature. Omitted optional records expose up to five IDs plus a paged search route. Expand the relevant records before relying on them; a title or signature does not preserve unread evidence.

## Live viewer and offline exports

The live viewer binds only to `127.0.0.1`, uses a random capability in its URL, validates the request host and origin, and opens SQLite read-only. The address and credential remain in private `.memory/viewer.json`; do not share that URL. No cloud service, browser SQLite engine or additional runtime package is needed.

The browser reads pages of records and loads source text on demand. It checks committed SQLite changes and selected-file state every two seconds while visible. Unchanged checks receive an HTTP 304 response. Filters, page selection, the open record and its scroll position survive refresh. Related evidence remains available outside the current page. The viewer identifies stale evidence and gives concrete reasons for dependent decisions that need review. Unreported costs appear as not measured.

The local process stops after ten minutes without a request. If it stops, the browser displays the last successful check and a reconnect instruction. Run `project-memory view` to reuse its address and resume updates. This is an on-demand local process, not an installed operating-system service. Database replacement requires a restart; ordinary writes and WAL commits do not. Large derived-status or drift queries still scan matching records. The episode selector shows the latest 1,000 entries; older episodes remain reachable in the paged Episodes view.

For a portable offline file:

```sh
project-memory view --output review.html --include-bodies --no-open
project-memory view --output review.html --include-bodies --replace --no-open
```

An explicit output path creates a static snapshot. Existing files are protected unless `--replace` is supplied; replacement uses a temporary file and atomic rename. Offline files state that they need regeneration. Source bodies remain optional. Avoid sharing an export containing private project evidence.

When upgrading, setup checks the version of a running viewer. If it belongs to an older release, setup opens the current viewer at a new local address. Close the old tab; its read-only service exits after ten minutes without requests. Same-version restarts retain the existing address.
