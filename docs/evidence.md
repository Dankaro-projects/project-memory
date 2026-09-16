# Testing and limitations

The regression suite checks immutable history, evidence freshness, subject boundaries, interruption receipts and recovery, record validation, work planning, typed links, architecture extraction, lesson guards and path scope, host selection and fallback, delegated work with cross review and merge, the MCP tool tables, the read API, the control panel and packaging. The release workflow runs the suite on Windows, macOS and Linux with Python 3.11 and 3.14, together with the installed wheel checks and the browser checks. A passing suite establishes the behaviour it checks. It does not establish daily productivity.

[Contributing](../CONTRIBUTING.md) lists the local commands. `tests/integration/` holds synthetic document, failure, recovery and host cases. Generated results, databases, screenshots and host logs stay in ignored local directories and are not release inputs or public documentation.

## Checks that run without a model

These cases use real local child processes, real git repositories and real local servers, with fake host executables in place of Codex and Claude. No model is called.

```sh
python -m unittest discover -s tests -q
python -m unittest tests.test_review_diagnostics -v
python -m unittest tests.test_failure_recovery -v
python -m tests.integration.document_case --output results/documents
python -m tests.integration.host_example --output results/host-example
```

The review diagnostics exercise execution deadlines, cancellation, partial reports, retained provider counters and the wait limit, and check that every numbered criterion and constraint is addressed exactly once. The failure regressions exercise concurrent failed hook processes against a locked database, legacy and interrupted recovery, an unreadable document over real MCP followed by a ping, complete versioned requirement paging, and conditional HTTP requests with measured file reads. The file permission case needs an unprivileged POSIX process and is skipped where those permissions cannot be reproduced.

The delegation cases create real git repositories and run fake workers: a successful run commits on its branch, produces a diff, is reviewed on the other host and merges; a change outside the plan paths becomes a scope violation; uncommitted changes inside the plan paths are refused; a simulated usage limit reroutes the run once to the other host; a merge without a passing review needs your override; a merge conflict aborts cleanly; and a discard removes the worktree and the branch.

## Browser checks

```sh
node tests/browser/static_check.cjs
node tests/browser/panel_browser.cjs
node tests/browser/export_browser.cjs
```

The static check parses the page assets and verifies the content security policy hashes without a browser. The panel case starts a real local server over a generated fixture for each of the three project templates, with work items, a blocked chain, decisions with a failure and a recovery, an accepted guard with a recurrence, a proposed lesson, a completed delegated run awaiting a merge, a scope block, documents and a small source tree. It asserts that every view renders its content, that the graphs draw the expected number of items, that the counts in a sentence agree with the picture, that creating a plan and recovering from a concurrent edit work, that a lesson is accepted with its triggers, that paths can be widened, that the drawer opens from the keyboard and returns focus on Escape, and that no page overflows at 1440, 768, 390 and 320 pixels. The export case opens the offline file and checks that it makes no network request, raises no script error and renders injected payloads as text.

Playwright is a development tool only. `MEMORY_PLAYWRIGHT` selects an existing installation and `MEMORY_PYTHON` selects the interpreter. These are scripted workflow checks. They do not establish complete assistive technology compatibility or any productivity effect.

## Checks that use a host account

These cases are opt in. They use your signed in Codex or Claude Code account and consume its usage. Run them in a disposable project, never in a customer checkout, and use a fresh output directory for each case. Install the package and complete a trusted setup first.

```sh
project-memory setup --project "$TEST_PROJECT" --client codex --trust --no-view
python -m tests.integration.codex_documents --project "$TEST_PROJECT" --output results/live-documents
python -m tests.integration.codex_interrupt --project "$TEST_PROJECT" --output results/live-interruption --planned
python -m tests.integration.codex_lifecycle --project "$TEST_PROJECT" --output results/live-lifecycle
python -m tests.integration.completeness_review --project .memory/review-case --host codex
python -m tests.integration.completeness_review --project .memory/claude-review-case --host claude
project-memory doctor --project "$TEST_PROJECT"
```

`TEST_PROJECT` must be an absolute path to an empty directory outside another configured project, so that parent hooks do not overlap the fixture hooks. The interruption case verifies an actual side effect, stops the host and recovers through another host process; an unknown completion stays unknown. Inspect existing output and side effects before repeating an interrupted run. The completeness cases run two reviews through the authenticated host, one with a missing exception and then its repair, and preserve the verdicts, the diagnostics and any provider counters. Count invalid reports and timeouts as unresolved, even when the underlying assessment looks correct.

Inspect an existing run without starting another one:

```sh
python -m tests.integration.review_log_diagnostic /absolute/path/to/.memory/agent-runs/RUN_ID
```

This reads local events and timestamps. The absence of a recorded host error does not prove uninterrupted connectivity, and a smaller initial packet does not establish lower total usage.

## What is not measured

| Measure | What a claim would require |
| --- | --- |
| Quality and completion | The same task and acceptance checks before and after, including the failures and the abandoned work |
| Context | The provider reported input of every request and the total usage, kept separate from character counts |
| Research | Repeated source reads and the refreshes that stale evidence made necessary |
| Corrections | Human reminders and the tool calls that were rejected or corrected |
| Maintenance | The time spent on setup, repairs and record administration |

None of these has been measured in a representative public beta. There is no established productivity claim and no token saving claim. Do not improve a ratio by excluding difficult or abandoned work, and keep the raw evidence private: a public claim needs a deliberately reviewed synthetic reproduction under comparable conditions.

## Remaining limits

A complete model input below any particular token target remains unmet. Mandatory instructions, tool definitions and accumulated host history can exceed it before retrieval begins. Bounded retrieval limits the characters that a call returns and nothing more; do not remove features, conditions or evidence to meet a number.

The assistant can overlook evidence, write a poor interpretation or miss a dependency. An agent verdict is an interpretation, not proof. Static import analysis does not detect dynamic loading. The scope guard does not parse shell redirection or other indirect writes inside shell commands. Unchanged text does not prove that a requirement is still appropriate.
