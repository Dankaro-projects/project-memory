# Setup and lifecycle

Run commands inside the intended project or pass `--project /absolute/path`. A missing database is created only by `setup`; `serve`, `doctor` and `view` refuse a missing database rather than silently create an empty project.

## Select existing documents

```sh
project-memory setup --client codex --trust --document VISION.md --document DECISIONS.md
```

Repeated setup does not duplicate hooks or unchanged source versions. Source documents remain in place. If the project has no agreed requirements, setup stores an explicit statement that none have been approved. You can supply already agreed text using repeated `--requirement` arguments. Changing existing requirements requires a separate, evidenced approval, not a setup flag.

The assistant can capture another selected file using `memory_write document`. It should use `memory_get direction` before an explicitly authorised `approve_requirements` operation. That operation requires complete requirement sentences, an approval reason, actor, evidence references and the current version. File capture and approval are different actions.

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

The Codex plugin provides the same three MCP tools and a short workflow skill. Install it from this repository's marketplace. If a project already has a setup-managed MCP connection, avoid enabling a second copy through the plugin. Hooks require project setup and host trust; plugin discovery alone does not enable capture.

## Verify the connection

`project-memory doctor` starts a separate installed MCP process, initialises it, lists tools and requests the decision schema. It also checks SQLite integrity and reports observed hook counts and unconfirmed actions. Missing lifecycle events may not have happened yet; existing receipt counts are historical evidence, not proof that today's host configuration still works.

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

Uninstall removes only its project connection and exact hook commands. It preserves records, source documents, backups and other client configuration. Codex can retain inactive trust entries for removed commands. Setup never edits unrelated global MCP connectors.

## Existing memory-module databases

Back up the database first. If the old project already has the legacy `memory` MCP adapter and its command hooks, disable that connection before adding the public adapter so both versions do not capture the same work. The installer detects the legacy MCP entry and refuses to create a duplicate. Use `project-memory setup --db /absolute/path/to/existing.sqlite --client codex --trust` to explicitly connect it. Core history remains in SQLite schema 2; optional host and project-revision tables are additive. Original immutable requirements remain revision zero. Older versions do not understand revised requirements: after the first approved revision, use this version or newer; restore the pre-upgrade backup for a deliberate rollback.

Do not copy an old private evidence ZIP into the public repository. `.memory` is private operational data, including generated HTML and backups.

## Desktop bundle

The GitHub release also provides an MCPB file. A compatible desktop client asks you to select a project directory and starts the bundled Python server. The directory picker replaces hand-edited MCP configuration. Python 3.11+ must be available to the client (`python3` on macOS/Linux, `python` on Windows). The bundle initialises an empty selected project when needed, with no approved project-specific requirements; it preserves an existing setup. It supports generic explicit capture, not Codex lifecycle hooks. The bundle entry point is tested independently; see the compatibility evidence before assuming support in a particular client.
