# Setup and lifecycle

Run every command inside the intended project, or pass `--project /absolute/path`. Only `setup` and `init` create a database. `serve`, `doctor` and `view` refuse a missing database rather than creating an empty project.

For everyday use, start with the [user guide](user-guide.md). Setup includes the control panel; `memory_module/viewer.html` is an internal template, not your records. There is no separate interface to install and nothing to rebuild.

## A new project from a template

```sh
project-memory init ~/projects/pricing-tool --template product --client codex --client claude
```

`--template` takes `product`, `engagement` or `automation`. The command creates the folder, runs `git init` unless `--no-git` is given, writes the starter documents of the template that do not exist yet, captures them, connects each client you name, and records the phases of the template as work items with their dependencies. `--name` sets the project name and `--no-view` skips opening the panel. Repeating the command writes no document twice and creates no phase twice.

Delegated work starts from the latest git commit, so commit the documents the work depends on before delegating.

## An existing project

```sh
project-memory setup --client codex --trust --document VISION.md --document DECISIONS.md
```

Setup connects one client and keeps the others. It preserves existing records, captures the documents you select, and opens the panel unless `--no-view` is given. Repeating it duplicates neither hooks nor unchanged source versions. Source documents stay where they are.

If the project has no approved requirements, setup records explicitly that none are approved. You can supply agreed text with repeated `--requirement` arguments. Changing existing requirements needs a separate evidenced approval, not a setup flag.

`.memory/install.json` records, per client, the exact configuration that setup wrote. Adding a second client keeps the first one intact.

## Codex

`--trust` enables the project integration so that Codex discovers the hooks without a further step. Setup writes a managed block to `.codex/config.toml` and the hook commands to `.codex/hooks.json`, and leaves every other entry alone. Codex receives nine lifecycle events. A configuration edit inside the managed block produces a conflict instead of being overwritten.

## Claude Code

```sh
project-memory setup --client claude --trust --document VISION.md
```

Setup adds a `project_memory` entry to `.mcp.json` in the project root and the hook commands to `.claude/settings.local.json`. Both files keep their other entries. The local settings file holds machine specific paths and stays out of version control. `--trust` lists the server in `enabledMcpjsonServers` so that Claude Code does not ask for approval; without it, approve the project server when a new session asks. Claude Code needs no separate hook trust step.

The hook command carries `--host claude`. Claude Code sends the same field names as Codex for tool events and adds `transcript_path`, `cwd` and `permission_mode`, which are not stored. Codex numbers turns; Claude Code does not, so repeated lifecycle events in one session stay distinct through the prompt identifier or the capture time. Three differences remain:

- Claude Code has no `Interrupt` hook, so `doctor` expects eight events for this client. An interrupted tool call leaves its `PreToolUse` receipt unconfirmed, which the assistant reconciles with evidence as after a Codex interruption.
- Claude Code raises `PostToolUseFailure` instead of `PostToolUse` when a tool fails. It is stored as a `PostToolUse` receipt with the failure retained and the original event name in the payload.
- Claude Code compacts the conversation automatically. `PreCompact` and `PostCompact` are recorded, and `SessionStart` returns context at startup, at resume and after compaction: the memory session, any active decision with its version, the number of unconfirmed tool calls and, in a templated project without approved requirements, a pointer to the kickoff.

Memory tool calls made through any server name ending in the three tool names are not captured as receipts, so a server installed as `project_memory`, `project-memory` or through a plugin creates no recursive noise.

## Other MCP clients

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

Use the absolute installed path if the client cannot find the command on PATH. `PROJECT_MEMORY_PROJECT` supplies the project directory where a client supports environment configuration. There is no global pointer to an active project, and a cloud only client cannot reach a local stdio process. Generic MCP setup installs no lifecycle hooks and configures no agent host, so capture is limited to what the assistant records explicitly.

## Plugins

The repository also publishes a plugin for Codex and for Claude Code. The plugin provides the same three MCP tools and the workflow skill, and the Claude Code plugin adds the lifecycle hooks:

```sh
codex plugin marketplace add Dankaro-projects/project-memory
codex plugin add project-memory@personal
```

```sh
claude plugin marketplace add Dankaro-projects/project-memory
claude plugin install project-memory@dankaro
```

The Codex marketplace in this repository is named `personal` and the Claude Code marketplace is named `dankaro`. Initialise the project with `project-memory setup --client mcp` before using a plugin, and keep only one MCP connection enabled for a project. Plugin hooks run in every project: they resolve the database from the project's install record and exit silently where no Project Memory database exists, and they skip capture where managed project hooks already exist. Codex excludes the Claude hook file from plugin discovery. The normal one command setup already connects the tools and the hooks, so the plugin is an alternative route, not an additional requirement.

## Verify the connection

`project-memory doctor` starts a separate installed MCP process, initialises it, lists the tools and requests the decision schema. It also checks SQLite integrity, identifies a missing requirements baseline, reports measurement coverage, and shows the observed hook counts and the unconfirmed actions. For a Codex installation it asks the host to list its enabled hooks and trust, and reports missing events, duplicate commands or Claude hooks discovered in Codex. It changes no trust, and an unavailable host produces an explicit unverified result. The expected lifecycle events follow the configured clients: nine for Codex, eight for Claude Code.

A successful subprocess check does not prove that the three tools are available in a task that is already open. In that task, call `memory_get` once with `view: health`. If the tools are absent, open a new task after setup.

## Back up, upgrade and remove

```sh
project-memory backup /absolute/path/to/backup.sqlite
```

The destination must be new. SQLite's backup API produces a consistent copy. Setup also backs up the database and the existing client configuration on first connection. Keep backups on storage you control.

Upgrade by running the current version's setup inside the project:

```sh
uvx project-memory-mcp@0.6.0b15 setup --client codex --trust
uvx project-memory-mcp@0.6.0b15 doctor
```

Use `--client claude --trust` for Claude Code or `--client mcp` for another client. The recorded launcher is versioned, so setup updates the configuration and verifies the new host hashes. Start a new task afterwards. If you use a plugin, update it through the host's plugin manager as well; a task that is already running keeps the tool schemas it loaded.

If setup is interrupted, run the same command again. A small ownership file lets it finish after either configuration write. Remove the connection when any pending setup is complete:

```sh
project-memory uninstall
project-memory uninstall --client claude
uv tool uninstall project-memory-mcp
```

Without `--client`, every client is removed. With one, only that client is removed and the install record remains while another client remains. Uninstall removes only its own project connection and its exact hook commands, including the `.mcp.json` entry and the local settings hooks written for Claude Code. It preserves records, source documents, backups and other client configuration. Codex can keep inactive trust entries for removed commands. Setup never edits unrelated global MCP connections.

When the package runs from a source checkout with its own environment, setup writes a launcher that runs that checkout, so unreleased changes can be exercised in a real host. An installed wheel keeps the released launcher.

## An existing database

Back up the database first. If an older project still has a legacy `memory` MCP adapter and its command hooks, disable that connection before adding this one so that both do not capture the same work; the installer detects the legacy entry and refuses to create a duplicate. Connect an existing file explicitly:

```sh
project-memory setup --db /absolute/path/to/existing.sqlite --client codex --trust
```

Core history stays in SQLite schema 2. The tables for links, agent runs, host receipts and project revisions are additive and are created when they are first needed. The original immutable requirements remain revision zero. Older releases do not understand the newer record kinds; restore the backup for a deliberate rollback.

Do not copy a private evidence archive into a public repository. `.memory` holds private operational data, including the worktrees of delegated runs, the agent run folders, generated pages and backups.

## Documents and refresh

Codex and Claude hooks refresh previously captured Markdown files at session start and at session stop, up to 100 paths per hook. An unchanged file creates no new version, and earlier text and its references stay intact. New files, moved files and symbolic links need explicit selection, because the hook does not crawl the project. A file that changes while it is read is rejected for that read.

```sh
project-memory check
project-memory sync
project-memory sync --offset 100
```

Both report the changed, missing and unreadable paths and whether more files remain. The equivalent MCP calls are `memory_get` with `view: documents` and `memory_write` with `operation: sync`; use a fresh request key for a new refresh. Neither approves a document proposal, changes requirements or accepts a lesson. A refresh at a lifecycle boundary cannot guarantee that an editor finished a save that is not atomic.

## The control panel and exports

The live service binds to `127.0.0.1`, uses a random capability in its address, validates the request host and origin, and retrieves through a read only connection. A separate write connection opens only for a validated action. The address and its credential stay in the private `.memory/viewer.json` file; do not share that address. No cloud service and no additional runtime package is involved.

The page reads pages of records and loads source text on demand, and it checks for committed changes every second while the tab is visible. An unchanged check receives an HTTP 304 response. The local process stops after ten minutes without a request; run `project-memory view` to resume it at the same address. Replacing the database file requires a restart, while ordinary writes do not. When an upgrade changes the version, setup opens the current panel at a new address and leaves the old one to exit on its own.

```sh
project-memory view --output review.html --include-bodies --no-open
project-memory view --output review.html --include-bodies --replace --no-open
```

An explicit output path writes a static snapshot with the same views and no network access. Existing files are protected unless `--replace` is given, and replacement uses a temporary file and an atomic rename. Source bodies are optional. Check an export before sharing it, because it contains project evidence.

## The memory of this machine

One computer holds many projects, each with its own memory, and above them one memory for the machine itself. It holds the rules you promoted out of single projects and a registry of the projects on this computer. It is an ordinary Project Memory database, so the same store, validation and panel apply, and it adds no runtime dependency.

```sh
project-memory machine init
project-memory machine list
project-memory machine rules --role worker
```

`init` creates the database and records the project of the current folder in the registry. `list` reads the registry, and `rules` reads the promoted rules with the basis written at promotion and the number of projects that promoted each one. The location is `PROJECT_MEMORY_MACHINE_DB`, or `~/.project-memory/machine.sqlite` when that variable is not set. The first promotion you accept in the control panel creates the database as well, so `init` is optional.

The machine memory holds promoted rules and the registry. It holds no customer name, no record identifier of a project, no absolute project path, no document content, no diff and no evidence body. The registry stays on this computer and is never exported. See [agent checks and delegated work](agents.md) for how a rule is proposed, accepted and composed into a prompt.

## Agent hosts

Configuring Codex or Claude Code also enables agent checks and delegated work through that installed CLI and account. Those runs consume the account's usage. Configuring both hosts lets a check run on a host other than the one that did the work, and lets a delegated run reroute when one host reports a usage limit. See [agent checks and delegated work](agents.md).

Project Memory knows four hosts. Each has a profile that states its command lines, how its answer is read and which roles it may take.

| Host | Program | Roles before a probe | Roles after a passing probe |
| --- | --- | --- | --- |
| Codex | `codex` | review and work | review and work |
| Claude Code | `claude` | review and work | review and work |
| Grok | `grok` | review | review and work |
| OpenCode | `opencode` | review | review |

A program is found through `PROJECT_MEMORY_<HOST>_BIN`, then the search path, then its install folder in your home folder (`~/.grok/bin/grok` and `~/.opencode/bin/opencode`). A host that is not installed or not signed in is reported unavailable, and the other hosts keep working. OpenCode takes no work, because it offers no sandbox.

### The usage of each host

```sh
project-memory usage
project-memory usage --json
```

The command collects usage from the local logs of Codex and Claude Code and from the runs of every registered project, then prints the tokens of each host for the last 5 hours, the current day and the last 7 days, the cost where a host reports it, the latest reported limit state with its reset time, and which of these data are unavailable for each host. The ledger lives in the machine memory. It stores counts, times and limit states only: no message content, prompt, file path, project name or session title. Collection is incremental, so a second run over unchanged logs reads nothing. A forked Codex session that replays the reports of its parent is counted once, and a value too large to be a real count is skipped. It also runs at the end of each Project Memory run. There is no background service. The Usage view of the control panel shows the same ledger and the routing decisions of the recent runs of the project.

### The probe of a host

```sh
project-memory host probe grok
project-memory host probe codex --timeout 600
```

The probe starts the real host, so it spends tokens. Run it yourself in a terminal; it refuses to run from inside an assistant session. It checks that a small task returns a structured answer, that token usage can be read from the event log, and that a canned limit message is classified with its reset time, which needs no model. For a host that may work it also checks that a shell write outside the worktree is refused and, for Codex and Claude Code, that a write through the hive server is accepted. For Grok it checks that no MCP server or skill of your configuration is visible to it, and that a worker cannot write into the Grok configuration folder (`~/.grok`, or `GROK_HOME`), where later runs load hooks and configuration. The probe removes its file from that folder afterwards. This last check rests on the report of the host itself.

Each probe is recorded in the machine memory with the host, its installed version, each check and the roles it allows. Grok takes delegated work only while the latest probe of its installed version passed, so an upgrade of Grok needs a new probe.
