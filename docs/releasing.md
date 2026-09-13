# Releasing

The canonical repository is `Dankaro-projects/project-memory`. GitHub Releases distribute the wheel and source archive. PyPI uses a project-scoped Trusted Publisher for `project-memory-mcp`, owner `Dankaro-projects`, repository `project-memory`, workflow `release.yml`, environment `pypi`. Enable the repository variable `PYPI_PUBLISH_ENABLED=true` only after that publisher exists.

Update `pyproject.toml`, `memory_module.__version__`, the Codex plugin version, `server.json`, the release commands and changelog together. Python uses `0.5.0b1`; plugin metadata uses its SemVer form `0.5.0-beta.1`. The CLI constructs the versioned GitHub wheel URL from the Python version.

Run CI and the installed-wheel checks before tagging. Review `git ls-files` and both distribution member lists. The source distribution uses an explicit allowlist; the wheel includes only runtime files and package metadata. Operational databases, transcripts and generated viewers must remain excluded. Do not publish the old private prototype ZIP.

Push a `vVERSION` tag matching the Python package version. The release workflow verifies that match, runs the suite, builds and checks artifacts, installs the wheel in a fresh environment and publishes a prerelease. PyPI publishing uses GitHub's short-lived OIDC identity; no API token is stored in the repository.

MCP registry metadata identifies the public PyPI package and its exact version. Publish only after the package resolves publicly. Directory listings, plugin availability and client compatibility are separate states. Check the actual listing and execute its documented launch path before marking a channel verified.

After release, use the published command in a fresh project and perform actual capture and recovery. Keep raw host logs private; publish a reviewed aggregate report with reproduction commands and limitations. Never replace an already published wheel with different code. Fixes receive a new beta version.
