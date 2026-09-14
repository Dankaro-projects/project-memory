# Releasing

The canonical repository is `Dankaro-projects/project-memory`. GitHub Releases distribute the wheel and source archive. PyPI uses a project-scoped Trusted Publisher for `project-memory-mcp`, owner `Dankaro-projects`, repository `project-memory`, workflow `release.yml`, environment `pypi`. Enable the repository variable `PYPI_PUBLISH_ENABLED=true` only after that publisher exists.

Update `pyproject.toml`, `memory_module.__version__`, the Codex plugin version, `server.json`, the release commands and changelog together. Python uses `0.5.0b7`; plugin metadata uses its SemVer form `0.5.0-beta.7`. The CLI constructs the versioned GitHub wheel URL from the Python version.

Use targeted local checks during iteration. Full verification runs automatically as a release gate; do not run the same matrix manually immediately before tagging. For verification outside a release, use Actions > Verify > Run workflow. Review `git ls-files` and both distribution member lists. The source distribution uses an explicit allowlist; the wheel includes only runtime files and package metadata. Operational databases, transcripts and generated viewers must remain excluded. Do not publish the old private prototype ZIP.

Push a `vVERSION` tag matching the Python package version. Before building or publishing, the release workflow requires the complete Verify workflow to pass on that tagged commit: the operating system and Python matrix, installed-wheel checks and browser checks. It then verifies the tag, builds and checks the release artifacts, installs the release wheel in a fresh environment and publishes a prerelease. A failed verification prevents publication. PyPI publishing uses GitHub's short-lived OIDC identity; no API token is stored in the repository.

MCP registry metadata identifies the public PyPI package and its exact version. Publish only after the package resolves publicly. Directory listings, plugin availability and client compatibility are separate states. Check the actual listing and execute its documented launch path before marking a channel verified.

After release, use the published command in a fresh project and perform actual capture and recovery. Keep raw host logs private; publish a reviewed aggregate report with reproduction commands and limitations. Never replace an already published wheel with different code. Fixes receive a new beta version.

## Smithery

Smithery CLI 1.2.0 copies MCPB tool summaries into an API field that requires complete MCP tool schemas. The official MCPB validator rejects those additional fields in the manifest itself. Keep the validated bundle unchanged and publish its actual `tools/list` response separately:

```sh
npm exec --yes --package smithery@1.2.0 -- smithery auth login
python scripts/publish_smithery.py dist/project-memory-0.5.0-beta.7.mcpb --name YOUR_NAMESPACE/project-memory
```

This development-only script uses the existing CLI login without printing its credential, runs the trusted release bundle in a temporary project, and submits the bundle with its real tool schemas. It creates the named server only if Smithery reports that it is missing. Check the result, public metadata and downloaded bundle hash before marking publication verified. Do not rerun a deployment after an ambiguous network response without inspecting its release status first. Set the server's description, repository, homepage and licence in the provider listing when creating a new listing.

## Glama

Sign in through GitHub and claim the [existing listing](https://glama.ai/mcp/servers/Dankaro-projects/project-memory). The root `glama.json` identifies authorised maintainers. Search for the repository before submitting another entry.

In Admin > Dockerfile, pin the GitHub release commit. The tested configuration uses `debian:trixie-slim`, Python `3.14` and Glama's Node `24` proxy. Set build steps to:

```json
[
  "uv sync --no-dev",
  ".venv/bin/project-memory setup --client mcp --project . --no-view"
]
```

Set CMD arguments to:

```json
["mcp-proxy", "--", "/app/.venv/bin/project-memory", "serve", "--project", "/app"]
```

No credentials or environment variables are required. Glama clones the public repository into `/app`; setup creates a new empty database there. `uv sync` installs the executable inside `.venv/bin`, so an unqualified `project-memory` command fails in Glama's default PATH. Setup must complete before `serve` starts.

Run Build, inspect the actual handshake and `tools/list` result, then create a release using the matching SemVer beta version. Auto-Release is disabled because this configuration pins a specific commit; update the pin and test each release before publishing. The [dated evidence](verification-glama-2026-09-14.json) includes the tested commit, provider test URL and image reference. This container supports provider inspection; it does not connect Glama to a user's local database. Follow the README for normal local installation.
