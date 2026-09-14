# Contributing

Keep the runtime in Python's standard library unless a measured defect requires a dependency. Preserve immutable history, explicit acceptance, source provenance, subject boundaries and whole-record retrieval. Use ordinary professional sentences for record explanations and interface copy.

During iteration, run local checks that cover the changed behaviour. Full GitHub Actions verification runs on request or before a beta is published; ordinary pushes and pull request updates do not trigger it. Use Actions > Verify > Run workflow when full verification is needed outside a release.

Include a reproduction of the original defect, the changed behaviour and the relevant result in each pull request. For performance claims, use comparable tasks with equal quality checks and report complete model inputs separately from retrieved characters. Count failed attempts and abandoned tasks; do not improve a ratio by excluding difficult work.

Examples and public evidence must be synthetic. Never commit project databases, personal paths, configuration, transcripts or private source documents. Propose schema changes with a tested migration and backup path. Do not rewrite previously recorded decisions to match a newer interpretation.

Use GitHub issues for reproducible defects and discussions about proposed changes. Maintainers are the Dankaro-projects repository owners. Beta feedback should describe installation success, completed work, preserved constraints, repeated research, corrections and time spent maintaining memory; omit project content unless it is deliberately shareable.

## Repository layout

`memory_module/` contains the runtime and workspace assets. `docs/` contains maintained product documentation. `examples/` contains one small invented project. Unit tests live directly under `tests/`; `tests/integration/` holds repeatable host cases and `tests/browser/` holds browser regressions. Build and publication tools live in `scripts/`.

## Local checks

```sh
python scripts/check_publication.py
python -m unittest discover -s tests -q
python -m tests.integration.document_case --output results/documents
python -m tests.integration.host_example --output results/host-example
node tests/browser/viewer_logic.cjs
python scripts/build_bundle.py
uv build
python scripts/check_artifacts.py dist
python scripts/installed_smoke.py dist
```

For UI changes, install development-only Playwright and its Chromium browser, then run `node tests/browser/browser_check.cjs` and `node tests/browser/workspace_browser.cjs`. `MEMORY_PLAYWRIGHT` can select an existing Playwright installation and `MEMORY_PYTHON` selects the project interpreter. Native model tests are opt-in; see [testing and limitations](docs/evidence.md).

Run the publication check before committing, then use `python scripts/check_publication.py --ref HEAD` to check the committed tree before pushing. It checks files against the public layout and scans text for likely credentials, personal paths, session identifiers and private workspace URLs. New documentation requires an intentional addition to the allowlist and source-package configuration. The release gate also inspects the actual wheel, source archive and MCPB.

Keep generated results under ignored `results/` or outside the checkout. Do not add screenshots, traces, session reports or private audit archives to documentation. Review the diff and your Git commit identity before pushing; automated checks cannot establish that every name or statement is safe to publish, and they do not erase Git history.
