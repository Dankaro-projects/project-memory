# Releasing

The canonical repository is `Dankaro-projects/project-memory`. There are two distribution channels: GitHub Releases, which carry the wheel and the source archive, and PyPI. PyPI uses a project scoped Trusted Publisher for `project-memory-mcp`, owner `Dankaro-projects`, repository `project-memory`, workflow `release.yml`, environment `pypi`. Enable the repository variable `PYPI_PUBLISH_ENABLED=true` only after that publisher exists.

## Version locations

Python uses `0.6.0b13` and plugin metadata uses the equivalent `0.6.0-beta.13`. Update these together:

| File | Form |
| --- | --- |
| `pyproject.toml` | `0.6.0b13` |
| `memory_module/__init__.py` | `0.6.0b13` |
| `plugins/project-memory/.claude-plugin/plugin.json` | `0.6.0-beta.13` |
| `plugins/project-memory/.codex-plugin/plugin.json` | `0.6.0-beta.13` |
| `.claude-plugin/marketplace.json` | `0.6.0-beta.13` |
| `plugins/project-memory/.mcp.json` | the release wheel URL |
| `plugins/project-memory/claude-hooks.json` | the release wheel URL |
| `README.md`, `docs/setup.md`, `docs/user-guide.md` | the install and upgrade commands |
| `CHANGELOG.md` | a new entry |

The installer builds its own wheel URL from `memory_module.__version__`, so the launcher needs no separate edit. The plugin files hold the URL literally and do need one.

## Checks before a tag

Use targeted local checks during iteration; the complete matrix runs as a release gate, so do not repeat it by hand immediately before tagging. For verification outside a release, use Actions, then Verify, then Run workflow.

```sh
python scripts/check_publication.py
python scripts/check_publication.py --ref HEAD
uv build
python scripts/check_artifacts.py dist
python scripts/installed_smoke.py dist
```

The publication check compares every file against the public layout and scans text for likely credentials, personal paths, session identifiers and private panel addresses. New documentation needs an intentional addition to its allowlist in `scripts/check_publication.py` and to the source archive list in `pyproject.toml`. The artifact check inspects the actual wheel and source archive: it requires every runtime module and the packaged panel assets, and it fails when a retired module is still packaged. The installed smoke check installs the built wheel into a fresh environment and reads the API endpoints from that installation. Review the output and the diff yourself; an automated check cannot decide that every name or statement is safe to publish, and it does not erase git history.

## Publish

Push a tag `vVERSION` that matches the Python version. The release workflow first requires the complete Verify workflow to pass on that commit: the operating system and Python matrix, the installed wheel checks and the browser checks. It then verifies the tag, builds, checks the artifacts, installs the wheel in a fresh environment and publishes a prerelease with the wheel and the source archive. A failed verification prevents publication. PyPI publishing uses the short lived OIDC identity of GitHub Actions, so no API token is stored in the repository.

After the release, run the published command in a fresh project and perform an actual capture and recovery. Keep raw logs and operational reports private. Public documentation carries maintained reproduction instructions and bounded claims, not installation diaries or session specific results.

Never replace an already published wheel with different code. A fix receives a new beta version.
