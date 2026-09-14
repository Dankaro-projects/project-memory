# Distribution and compatibility

Status verified on 13 September 2026 for `0.5.0b1`.

| Channel | Verified result |
|---|---|
| [GitHub Release](https://github.com/Dankaro-projects/project-memory/releases/tag/v0.5.0b1) | Public wheel, source archive and MCPB. Release checks passed. |
| [PyPI](https://pypi.org/project/project-memory-mcp/0.5.0b1/) | Published through Trusted Publisher. The wheel matches GitHub byte for byte. The documented one-line command installed from a fresh uv cache. |
| [Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.Dankaro-projects%2Fproject-memory/versions/0.5.0b1) | Active, versioned PyPI/stdio record. |
| [Smithery](https://smithery.ai/servers/msuteu/project-memory) | Published local server with all three full tool schemas. Its downloaded MCPB matches the GitHub asset byte for byte. |
| Codex project setup | Actual capture of all nine hooks, one interrupted side effect, recovery in a new host, and four facts retained through compaction on Codex 0.153.4/macOS. |
| Codex plugin | Installed from the repository marketplace; a fresh host launched the published wheel and completed `memory_get direction`. Hooks require the separate project setup. |
| Other local MCP clients | The MCPB entry point and generic stdio process are tested. Installation inside each desktop client is not yet verified. |

Python tests, bundled process execution and installed-wheel checks pass on Windows, macOS and Linux with Python 3.11 and 3.14. That does not imply that Codex hooks have been exercised on every operating system.

## Codex plugin

The normal one-line installation in the README already connects the three tools and hooks. The plugin is an alternative way to discover the tools and workflow guidance:

```sh
codex plugin marketplace add Dankaro-projects/project-memory
codex plugin add project-memory@personal
```

The repository marketplace is named `personal`. If that name already belongs to another marketplace, use the project setup command instead of replacing an existing marketplace. Initialise the chosen project with `project-memory setup --client mcp` before using the plugin. Start a new task afterwards. Avoid enabling both the plugin and a setup-managed MCP server in the same project.

## Remaining channels

Glama metadata is present in `glama.json`; its account-based submission has not been completed. [PulseMCP](https://www.pulsemcp.com/servers) currently reports that new submissions and listing changes are paused. An entry in the official registry does not prove that another directory has imported or approved it.

Smithery identifies this as a local server. It does not provide a cloud service with access to a person's local database. Browser-only or cloud-only clients need a separate supported local execution path.

The [reviewed report](verification-0.5.0b1.json) includes published artifact hashes and measured host results. The [evidence notes](evidence.md) retain failures, token regressions and unmeasured daily-use outcomes.
