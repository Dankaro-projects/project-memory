# Distribution and compatibility

Release and client status was verified on 13 September 2026 for `0.5.0b1`. Glama and PulseMCP status was checked on 14 September 2026.

| Channel | Verified result |
|---|---|
| [GitHub Release](https://github.com/Dankaro-projects/project-memory/releases/tag/v0.5.0b1) | Public wheel, source archive and MCPB. Release checks passed. |
| [PyPI](https://pypi.org/project/project-memory-mcp/0.5.0b1/) | Published through Trusted Publisher. The wheel matches GitHub byte for byte. The documented one-line command installed from a fresh uv cache. |
| [Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.Dankaro-projects%2Fproject-memory/versions/0.5.0b1) | Active, versioned PyPI/stdio record. |
| [Smithery](https://smithery.ai/servers/msuteu/project-memory) | Published local server with all three full tool schemas. Its downloaded MCPB matches the GitHub asset byte for byte. |
| [Glama](https://glama.ai/mcp/servers/Dankaro-projects/project-memory) | The listing is public and author-verified. Container release `0.5.0-beta.1` is published from the GitHub release commit. Glama's build test passed the MCP handshake and discovered all three tool schemas. Public tool indexing and quality grading remain unverified. |
| Codex project setup | Actual capture of all nine hooks, one interrupted side effect, recovery in a new host, and four facts retained through compaction on Codex 0.153.4/macOS. |
| Codex plugin | Installed from the repository marketplace; a fresh host launched the published wheel and completed `memory_get direction`. Hooks require the separate project setup. |
| Claude Code project setup (unreleased) | Setup writes `.mcp.json` and `.claude/settings.local.json`; the installed hook command captured simulated Claude Code payloads for all eight applicable events, including compaction context and a tool failure, in real processes. A live Claude Code session has not yet been observed. |
| Other local MCP clients | The MCPB entry point and generic stdio process are tested. Installation inside each desktop client is not yet verified. |

Python tests, bundled process execution and installed-wheel checks pass on Windows, macOS and Linux with Python 3.11 and 3.14. That does not imply that Codex hooks have been exercised on every operating system.

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

Plugin hooks run in every project. They resolve the project's database from its install record and exit silently where no Project Memory database exists, so unrelated projects are not blocked. Initialise a project with `project-memory setup --client mcp` before use, and do not combine the plugin with `setup --client claude` in the same project. `claude plugin validate` accepts both manifests; a live Claude Code session with the installed plugin has not yet been observed.

## Remaining channels

Glama's account submission completed on 14 September 2026. A directory search also found an existing entry whose update timestamp preceded that submission. The maintainer claimed that entry through GitHub using `glama.json`, corrected its imported display name and published a tested container release. The [Glama evidence](verification-glama-2026-09-14.json) records the original launch failure, correction, successful test and release. Submission confirmation alone does not establish review approval; the public listing, author verification and published release were checked separately.

Glama's container starts with an empty database under `/app/.memory`. It has no access to a person's local project and does not capture Codex hooks. Container persistence across restarts is not verified. Use the local installation for actual project memory. The public Schema page still showed no indexed tools when checked after publication, although the build log contained all three schemas. Glama's automated grading is not evidence of daily-use quality.

[PulseMCP](https://www.pulsemcp.com/servers) still reports that new submissions and listing changes are paused. An entry in the official registry does not prove that another directory has imported or approved it.

Smithery identifies this as a local server. It does not provide a cloud service with access to a person's local database. Browser-only or cloud-only clients need a separate supported local execution path.

The [reviewed report](verification-0.5.0b1.json) includes published artifact hashes and measured host results. The [evidence notes](evidence.md) retain failures, token regressions and unmeasured daily-use outcomes.
