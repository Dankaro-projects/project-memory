# Project Memory

Project Memory keeps decisions, evidence, outcomes and reviewed lessons in a local SQLite database. Your assistant retrieves relevant records through three MCP tools. You follow the same work in a live workspace with a sprint board, decision history and searchable documents.

**Public beta.** Python 3.11+ is required. The runtime has no third-party dependencies, telemetry or hosted database. Optional reviewer agents use your installed Codex or Claude Code account.

<!-- mcp-name: io.github.Dankaro-projects/project-memory -->

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run this inside the project you want to remember:

```sh
uvx project-memory-mcp@0.5.0b8 setup --client codex --trust
```

For Claude Code, replace `--client codex` with `--client claude`. Setup preserves existing records and settings, connects MCP and lifecycle hooks, and opens the workspace. `--trust` enables the project integration; omit it to review trust in your client. Start a new assistant session afterwards. Add `--no-view` for headless setup.

For a permanent CLI:

```sh
uv tool install project-memory-mcp==0.5.0b8
project-memory doctor
project-memory view
```

Other local MCP clients can use `setup --client mcp` and the stdio server. See [setup](docs/setup.md) for client configuration, imports, upgrades, backup and uninstall, or [distribution](docs/distribution.md) for plugins and desktop bundles.

## Work with it

Ask your assistant:

> Capture VISION.md and our decision log. Separate evidence, proposals and agreed requirements. Before choosing an approach, retrieve relevant decisions and check whether their evidence is still current.

- Decisions retain their evidence, alternatives, uncertainty, expected consequences and revisions.
- The work board connects actions and sprints to decisions, outcomes and dependencies.
- Selected Markdown files refresh through hooks. Earlier versions remain available and changed evidence is flagged.
- Code reviews, writing corrections and research remain separate, with explicit dependencies between them.
- Successes, failures and recoveries can produce proposed lessons. Acceptance remains an explicit review.

Run `project-memory view` whenever you want to follow work or plan actions. The local workspace refreshes automatically; the assistant does not rebuild it after each change. Use `--output review.html --include-bodies` for an offline snapshot. The [user guide](docs/user-guide.md) covers everyday use and missing updates.

Hooks capture events mechanically. The assistant still needs to record meaning, decisions and outcomes correctly. An interrupted command stays uncertain until its actual effects are checked. Bounded retrieval limits returned characters; it cannot guarantee that the host's complete model input stays below 10K tokens. [Testing and limitations](docs/evidence.md) explains the verified boundaries and unmeasured claims.

## Documentation

- [User guide](docs/user-guide.md) and [work board](docs/work-board.md)
- [Setup and upgrades](docs/setup.md) and [client compatibility](docs/distribution.md)
- [Records and evidence](docs/record-fields.md) and [reviewer agents](docs/workspace-agents.md)
- [Workspace interface](docs/workspace-ui.md)
- [Contributing and tests](CONTRIBUTING.md), [security](SECURITY.md) and [releasing](docs/releasing.md)

Packages are available through [PyPI](https://pypi.org/project/project-memory-mcp/), [GitHub Releases](https://github.com/Dankaro-projects/project-memory/releases), the [MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.Dankaro-projects%2Fproject-memory/versions/0.5.0b8), [Smithery](https://smithery.ai/servers/msuteu/project-memory) and [Glama](https://glama.ai/mcp/servers/Dankaro-projects/project-memory). Directory availability does not establish compatibility with every client.
