# Setup and lifecycle

Run commands inside the intended project or pass `--project /absolute/path`. A missing database is created only by `setup`; `serve`, `doctor` and `view` refuse a missing database rather than silently create an empty project.

## Select existing documents

```sh
project-memory setup --client codex --trust --document VISION.md --document DECISIONS.md
```

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

The Codex and Claude Code plugins provide the same three MCP tools and a short workflow skill; the Claude Code plugin also carries the lifecycle hooks, which capture only in projects that have a Project Memory database. Install it from this repository's marketplace. If a project already has a setup-managed MCP connection, avoid enabling a second copy through the plugin. Hooks require project setup and host trust; plugin discovery alone does not enable capture.

## Verify the connection

`project-memory doctor` starts a separate installed MCP process, initialises it, lists tools and requests the decision schema. It also checks SQLite integrity and reports observed hook counts and unconfirmed actions. The expected lifecycle events follow the client recorded at setup: nine for Codex, eight for Claude Code. Missing lifecycle events may not have happened yet; existing receipt counts are historical evidence, not proof that today's host configuration still works.

In a new Codex task, ask the assistant to capture a selected document, record a decision with evidence, perform a small project action and record its actual outcome. Inspect the records with `view` and `doctor`. Reproducible live interruption and lifecycle harnesses are in `examples/`; they require a signed-in local Codex and intentionally execute synthetic work. [Evidence](evidence.md) separates those checks from unit tests.

## Back up, upgrade and remove

```sh
project-memory backup /absolute/path/to/backup.sqlite
```

The destination must be new. SQLite's backup API provides a consistent database copy. Setup also creates a backup of the database and existing Codex configuration on first connection. Keep backups on storage you control.

Upgrade by installing the next published wheel using `uv tool install --force --from RELEASE_WHEEL_URL project-memory-mcp`, then rerun `project-memory setup --client codex --trust`. The recorded launcher is versioned; setup updates its configuration and verifies new host hashes. Run `doctor` and a new task after an upgrade. A configuration edit within the managed block produces a conflict rather than overwriting it.

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
