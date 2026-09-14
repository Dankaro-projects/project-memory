# Testing and limitations

The regression suite checks immutable history, evidence freshness, subject boundaries, interruption receipts, recovery, setup, packaging and the interactive workspace. The [beta 8 release workflow](https://github.com/Dankaro-projects/project-memory/actions/runs/34884312832) passed 184 Python tests across Windows, macOS and Linux on Python 3.11 and 3.14, plus installed-wheel and browser checks. A passing suite does not establish daily productivity.

[Contributing](../CONTRIBUTING.md) provides the local test commands. `tests/integration/` contains synthetic document, failure, recovery and native-host cases. Generated results, databases, screenshots and host logs stay in ignored local directories. They are not public product documentation or release inputs.

## Native host checks

These opt-in cases use the signed-in Codex account and consume model usage. Run them in a disposable project, never a customer checkout. Use a fresh output directory for each case. Install the package and complete trusted project setup first.

```sh
project-memory setup --project "$TEST_PROJECT" --client codex --trust --no-view
python -m tests.integration.codex_documents --project "$TEST_PROJECT" --output results/live-documents
python -m tests.integration.codex_interrupt --project "$TEST_PROJECT" --output results/live-interruption
python -m tests.integration.codex_lifecycle --project "$TEST_PROJECT" --output results/live-lifecycle
project-memory doctor --project "$TEST_PROJECT"
python -m tests.integration.context_audit results/live-interruption
```

`TEST_PROJECT` must name an absolute path to an empty test directory. The interruption case verifies an actual side effect, stops the host and recovers through another host process. Unknown process completion remains unknown. Inspect existing output and side effects before retrying an interrupted run.

Codex capture, interruption recovery and compaction have been exercised in a live macOS host. Claude Code has a bounded live CLI check for applicable lifecycle events; live Claude interruption and compaction remain unverified. See [compatibility](distribution.md) for the tested scope. Payload simulations do not substitute for host execution.

## Evaluation requirements

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
