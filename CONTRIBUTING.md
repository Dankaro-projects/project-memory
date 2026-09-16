# Contributing

Keep the runtime in Python's standard library unless a measured defect requires a dependency. Preserve immutable history, explicit acceptance, source provenance, subject boundaries and whole record retrieval. Use ordinary professional sentences for record explanations and interface text.

Nothing in the model may assume that work is code. A change must also make sense for a consulting engagement and for a workflow automation delivered to a client, where the deliverables are documents and exported workflows rather than source files.

During iteration, run the local checks that cover the behaviour you changed. Full GitHub Actions verification runs on request and as a release gate; ordinary pushes and pull request updates do not trigger it. Use Actions, then Verify, then Run workflow when full verification is needed outside a release.

Include a reproduction of the original defect, the changed behaviour and the relevant result in each pull request. For a performance claim, use comparable tasks with equal quality checks, and report complete model inputs separately from retrieved characters. Count failed attempts and abandoned tasks; do not improve a ratio by excluding difficult work.

Examples and public evidence must be synthetic. Never commit project databases, personal paths, configuration, transcripts or private source documents. Propose a schema change with a tested migration and a backup path. Do not rewrite a previously recorded decision to match a newer interpretation.

Use GitHub issues for reproducible defects and for discussions about proposed changes. Maintainers are the owners of the Dankaro-projects repository. Beta feedback should describe installation success, completed work, preserved constraints, repeated research, corrections and the time spent maintaining the records; omit project content unless it is deliberately shareable.

## Repository layout

`memory_module/` holds the runtime, the agent role instructions and the control panel assets. `docs/` holds the maintained product documentation. `examples/` holds one small invented project. Unit tests live directly under `tests/`; `tests/integration/` holds repeatable host cases and `tests/browser/` holds the browser regressions. Build and publication tools live in `scripts/`.

## Local checks

```sh
python scripts/check_publication.py
python -m unittest discover -s tests -q
python -m tests.integration.document_case --output results/documents
python -m tests.integration.host_example --output results/host-example
node tests/browser/static_check.cjs
uv build
python scripts/check_artifacts.py dist
python scripts/installed_smoke.py dist
```

For a change to the control panel, install Playwright and its Chromium browser as development tools, then run `node tests/browser/panel_browser.cjs` and `node tests/browser/export_browser.cjs`. `MEMORY_PLAYWRIGHT` selects an existing Playwright installation and `MEMORY_PYTHON` selects the project interpreter. The checks that use a real Codex or Claude account are opt in and consume that account's usage; see [testing and limitations](docs/evidence.md).

Run the publication check before committing, then use `python scripts/check_publication.py --ref HEAD` to check the committed tree before pushing. It compares files against the public layout and scans text for likely credentials, personal paths, session identifiers and private panel addresses. A new documentation file needs an intentional addition to the allowlist in `scripts/check_publication.py` and to the source archive list in `pyproject.toml`. The release gate also inspects the actual wheel and source archive.

Keep generated results under the ignored `results/` directory or outside the checkout. Do not add screenshots, traces, session reports or private audit archives to the documentation. Review the diff and your git identity before pushing; automated checks cannot establish that every name or statement is safe to publish, and they do not erase git history.
