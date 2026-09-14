# Distribution and compatibility

The local package contains the SQLite database interface, MCP adapter, hooks and workspace. A directory listing does not give a cloud client access to your local project.

| Channel | Installation |
| --- | --- |
| [PyPI](https://pypi.org/project/project-memory-mcp/) | Use the versioned setup command in the README. |
| [GitHub Releases](https://github.com/Dankaro-projects/project-memory/releases) | Download the wheel or MCPB desktop bundle. |
| [MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.Dankaro-projects%2Fproject-memory/versions/0.5.0b8) | Discover the versioned PyPI/stdio record. |
| [Smithery](https://smithery.ai/servers/msuteu/project-memory) | Install the local server bundle. |
| [Glama](https://glama.ai/mcp/servers/Dankaro-projects/project-memory) | Inspect the listed tools; use local setup for project memory. |

GitHub, PyPI, the registry, Smithery and Glama publication were checked for beta 8 on 14 September 2026. Provider availability and imported listings can change independently of a release.

## Tested clients

| Client | Verified boundary and limitation |
| --- | --- |
| Codex | Native MCP and hook capture passed on beta 8. Earlier live macOS checks exercised all nine lifecycle events, interruption recovery and compaction on Codex 0.153.4; those cases were not all repeated in beta 8. |
| Claude Code | A live 2.1.270 CLI session exercised native retrieval, capture and six applicable lifecycle event types. Simulated payloads cover the other events. Live interruption and compaction remain unverified. |
| Other local MCP clients | Generic stdio and the actual bundled entry point are tested. Installation inside each desktop client remains unverified. |

The release workflow checks Python and installed packages on Windows, macOS and Linux. It does not establish native hook compatibility on every operating system. [Testing and limitations](evidence.md) provides reproduction commands.

## Codex plugin

The normal one-line installation in the README already connects the three tools and hooks. The plugin is an alternative way to discover the tools and workflow guidance:

```sh
codex plugin marketplace add Dankaro-projects/project-memory
codex plugin add project-memory@personal
```

The repository marketplace is named `personal`. If that name already belongs to another marketplace, use the project setup command instead of replacing an existing marketplace. Initialise the chosen project with `project-memory setup --client mcp` before using the plugin. Start a new task afterwards. Avoid enabling both the plugin and a setup-managed MCP server in the same project.

## Claude Code plugin

The repository root declares a Claude Code marketplace named `dankaro`. The plugin shares the Codex plugin's MCP server and skill, and adds the lifecycle hooks:

```sh
claude plugin marketplace add Dankaro-projects/project-memory
claude plugin install project-memory@dankaro
```

Plugin hooks run in every project. They resolve the project's database from its install record and exit silently where no Project Memory database exists, so unrelated projects are not blocked. Initialise a project with `project-memory setup --client mcp` before use, and keep one MCP connection enabled. Plugin hooks skip capture when managed Claude project hooks already exist. `claude plugin validate` accepts both manifests; a live Claude Code session with the installed plugin has not yet been observed.

## Hosted directory containers

Glama's inspection container starts with an empty database under `/app/.memory`. It cannot access your local project or capture its hooks. Container persistence across restarts is unverified. Smithery also lists a local server, rather than a hosted service with access to your database.

Browser-only clients need a supported local execution path. Use the README installation for normal project work.
