# Testing and limitations

The regression suite checks immutable history, evidence freshness, subject boundaries, interruption receipts, recovery, setup, packaging and the interactive workspace. The [beta 9 release workflow](https://github.com/Dankaro-projects/project-memory/actions/runs/34960179332) passed 228 Python tests across Windows, macOS and Linux on Python 3.11 and 3.14, plus installed-wheel and browser checks. A passing suite does not establish daily productivity.

[Contributing](../CONTRIBUTING.md) provides the local test commands. `tests/integration/` contains synthetic document, failure, recovery and native-host cases. Generated results, databases, screenshots and host logs stay in ignored local directories. They are not public product documentation or release inputs.

## Native host checks

These opt-in cases use the signed-in Codex account and consume model usage. Run them in a disposable project, never a customer checkout. Use a fresh output directory for each case. Install the package and complete trusted project setup first.

```sh
project-memory setup --project "$TEST_PROJECT" --client codex --trust --no-view
python -m tests.integration.codex_documents --project "$TEST_PROJECT" --output results/live-documents
python -m tests.integration.codex_interrupt --project "$TEST_PROJECT" --output results/live-interruption --planned
python -m tests.integration.codex_lifecycle --project "$TEST_PROJECT" --output results/live-lifecycle
project-memory doctor --project "$TEST_PROJECT"
python -m tests.integration.context_audit results/live-interruption
```

`TEST_PROJECT` must name an absolute path to an empty test directory outside another configured project, so parent hooks do not overlap the fixture hooks. The interruption case verifies an actual side effect, stops the host and recovers through another host process. Unknown process completion remains unknown. Inspect existing output and side effects before retrying an interrupted run.

Codex capture, interruption recovery and compaction have been exercised in a live macOS host. Claude Code has a bounded live CLI check for applicable lifecycle events; live Claude interruption and compaction remain unverified. See [compatibility](distribution.md) for the tested scope. Payload simulations do not substitute for host execution.

## Automatic review diagnostics

The focused regressions exercise real local child processes for timeouts, cancellation, partial reports, usage retention and wait limits. They also check complete task criteria, explicit constraint applicability and scope-preserving progress updates. These subprocess fixtures do not call a model.

```sh
python -m unittest tests.test_review_diagnostics -v
python -m tests.integration.completeness_review --project .memory/review-case --host codex
python -m tests.integration.completeness_review --project .memory/claude-review-case --host claude
```

Each native command runs two reviews through the authenticated host: one with a missing tagged decoding exception, then its repair. Use a new fixture directory. These commands consume host usage; the reviewer reads existing results rather than rerunning the application. The JSON output preserves verdicts, diagnostics and available provider counters. Count invalid reports and timeouts as unresolved, even when their underlying assessment appears correct.

Inspect existing logs without starting another review:

```sh
python -m tests.integration.review_log_diagnostic /absolute/path/to/.memory/agent-runs/CHECK_ID
```

This reads local events and timestamps. No recorded host error does not prove uninterrupted connectivity. A smaller initial packet does not establish lower aggregate usage. See [review controls and limits](workspace-agents.md#control-and-recovery) for execution versus wait deadlines, applicability checks and retained reports.

## Evaluation requirements

The viewer usability reproduction uses the same synthetic failed choice, successful revision, documented exception and work items across runtime versions. `node tests/browser/usability_browser.cjs` reports completion and click counts for two reading journeys, then checks planning, exact source text, stale evidence, failed-refresh recovery, table access and mobile widths. The other browser suites retain editing, approvals, skills, diagrams and offline coverage. See [workspace verification](workspace-ui.md#local-verification) for the baseline option. Scripted timings do not measure human task time or model-token savings.

The deterministic failure regressions run without a model or GitHub Actions:

```sh
python -m unittest tests.test_failure_recovery -v
node tests/browser/workspace_browser.cjs
```

The Python cases exercise concurrent failed hook processes against a locked SQLite database, legacy and interrupted recovery, an unreadable document over real MCP followed by a ping, complete versioned requirement paging, and conditional HTTP requests with measured file reads. The file-permission case requires an unprivileged POSIX process and is skipped where those permissions cannot reproduce the failure. The browser case injects detail and history failures into a real workspace, checks that warnings survive healthy polling, and restores each endpoint without another database write. Playwright is a development tool only; configure it as described in [workspace verification](workspace-ui.md#local-verification). These checks establish specific recovery behaviour, not general productivity or provider token savings.

Compare the same task, constraints and acceptance checks before and after a change. Include successes, failures and recoveries. Do not exclude difficult or abandoned work to improve a ratio.

| Measure | Record |
| --- | --- |
| Quality and completion | Acceptance checks, preserved conditions and unfinished work |
| Context | Provider-reported input for every request and aggregate usage; keep character estimates separate |
| Research | Repeated source reads and necessary stale-evidence refreshes |
| Corrections | Human reminders and rejected or corrected tool calls |
| Maintenance | Time spent on setup, repairs and record administration |

Keep raw evidence private. Public claims require a deliberately reviewed, synthetic reproduction with comparable conditions. Missing measurements remain unknown. Avoid publishing session IDs, local paths, project names, account metadata or operational reports.

## Remaining limits

Below 10K tokens in every complete model input remains an unmet target. Mandatory instructions, tool definitions and accumulated host history can exceed it before retrieval. Do not remove features, conditions or evidence to meet it.

Daily completion rates, human corrections, net maintenance time, long-term drift and competitor performance have not been measured in a representative public beta. There is no established general productivity or token-saving claim. The assistant can overlook evidence, write a poor interpretation or miss a dependency; unchanged text does not prove that a requirement is still appropriate.
