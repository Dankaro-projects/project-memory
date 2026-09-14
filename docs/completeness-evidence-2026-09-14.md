# Completeness and recovery evaluation

The unreleased beta now detects missing recording work before a host finishes a turn. A new request needs an explicit assessment when it affects ongoing work or leads to tool activity. A note that says work is finished cannot replace its outcome. Review reports must address every numbered criterion. These checks preserve uncertainty and do not infer intent, success or lesson acceptance from tool events.

The baseline was commit `9b85d68`, with 168 passing Python tests in 9.137 seconds. Those tests did not expose the omissions below. The evaluation first reproduced the omissions, then changed their causes and reran comparable cases. Public release remains beta 4; this branch has not published beta 5 or dispatched GitHub Actions.

## What changed

- `coverage.py` derives unassessed requests, unassigned tool activity, missing outcomes and capture gaps from existing immutable receipts. It adds no SQLite table or runtime dependency. Historical records remain unchanged.
- An explicit `checkpoint` links the agent's interpretation to observed prompt IDs and a current plan. New or changed work records conditions and exceptions. Changed work requires a revised plan. A delayed acknowledgement of an unseen request or plan is rejected. Bundling the checkpoint into a plan or record write commits both atomically.
- Stop gives one opportunity to repair missing records. A greeting needs no administrative record. An informational lookup needs no episode. An explicitly assessed unknown execution remains visible without another Stop intervention merely to repeat the uncertainty. Unknown execution still cannot establish completion or authorise retrying an effect.
- Review packets contain numbered entries for the completion criterion, scope, governing requirements and the latest explicit request conditions. Every entry must appear exactly once in the report. Unknown evidence cannot support a pass. Original failures, later repairs and proposed lessons remain distinct.
- Capture failure reaches the host and, when the directory is writable, a small local sidecar. The live viewer notices that failure without a SQLite revision. Recovery retains a gap receipt for explicit assessment. The workspace shows recording issues and their references without regenerating HTML.
- Repeated delivery preserves the original prompt snapshot and tool binding. Another session must explicitly claim current work before binding its decision. An earlier missing outcome remains visible when a session switches work.

The existing `status` response remains compatible. The new session-scoped `coverage` view supplies bounded recording state and unresolved tool IDs without the global pending-work inventory. Page with `limit` and `offset`; complete records remain available by ID.

## Measurements

[Before](completeness-before-2026-09-14.json) and [after](completeness-after-2026-09-14.json) use actual hook subprocesses and SQLite. They do not use a model. One lookup has no initial assessment, a chat-only completion, and three Stop deliveries.

| Observation | Baseline | Updated |
|---|---:|---:|
| Stop interventions for the omitted assessment | 0 | 1 |
| Explicitly visible omissions | No coverage view | Unassessed request and unassigned activity |
| A report that checks one of two criteria is accepted | Yes | No |
| Episodes created for the informational lookup | 0 | 0 |
| Remaining recording issues after explicit assessment | Unmeasured | 0 |
| Total hook output, characters | 480 | 1,274 |
| Individual hook subprocess time | 23.89–31.05 ms | 22.84–31.61 ms |
| SQLite integrity | OK | OK |

The extra hook text implements the missing check; it is not a token saving. Subprocess timings are individual observations, not performance distributions.

[Native host results and reviewer reports](completeness-verification-2026-09-14.json) retain measured usage, failures and limitations. The fixtures use actual installed Codex and Claude CLIs, their hooks, MCP and SQLite. They deliberately describe the test protocol; these are controlled cases, not an unprompted longitudinal project study.

| Actual case | Result | Duration | Recording or context cost |
|---|---|---:|---|
| Codex informational lookup | One read, one assessment, no episode | 28.557 s | 4 memory calls; largest reported input 16,937 tokens |
| Claude informational lookup | One read, one assessment, no episode | 18.834 s | 34,518 aggregate input tokens including cache reads/writes; per-input maximum unavailable |
| Codex deliberately omits its assessment | Stop intervenes once; the agent repairs it without rereading | 49.325 s | 7 memory calls; 150,256 aggregate input tokens; largest reported input 18,398 |
| Same Codex case after clearer guidance and a useful write receipt | Same correct result; one intervention; no reread | 30.200 s | 3 memory calls; 93,984 aggregate input tokens; largest reported input 17,084 |
| Claude deliberately omits its assessment | Stop intervenes once; the agent repairs it without rereading | 25.234 s | 51,268 aggregate input tokens including cache reads/writes; per-input maximum unavailable |
| Codex interruption and new-process resumption | Marker remains one write; final process completion remains unknown | See raw usage | Recovery uses 14 memory calls, one rejected write and one unnecessary Stop intervention |
| Comparable interruption after improved reconciliation guidance | Marker remains one write; uncertainty remains explicit | See raw usage | Recovery uses 10 memory calls, zero rejected writes and zero Stop interventions |

The omitted-assessment comparison used 37.5% fewer aggregate input tokens and four fewer memory calls. It is one paired observation. The requested below-10K full-input target is **not met**; features and conditions were not removed to reach it. Cached tokens are included in the aggregate counts and separately retained in the JSON. Memory-output characters are not presented as model-token counts.

No human correction prompts were added during the lookup cases. The interruption/resume prompt is part of the test protocol, not counted as a correction. The fixtures read the target file once and did not repeat the side-effect command. Those observations do not measure general research reuse across projects. The first interruption attempt rejected an empty reconciliation evidence list; the revised guidance preserved that validation requirement instead of weakening it.

The actual Codex reviewer rejected the parser with the omitted Latin-1 exception despite two passing implemented tests. It addressed all six checklist IDs and returned `changes_required` in 57.061 seconds. After the parser and missing test were repaired, the same six criteria passed in 74.201 seconds. Initial packets were 7,188 and 10,643 characters; aggregate provider inputs were 37,663 and 41,423 tokens. Both runs used a 90-second limit. Proposed lessons were not accepted automatically, and the production effect remained explicitly unmeasured.

## Coverage of the ten agreed cases

| Case | Evidence and resulting behaviour |
|---|---|
| 1. Work proceeds without a plan or binding | Hook-process comparison and native omission runs expose the gap. Informational classification is explicit; material work requires a plan. |
| 2. A chat instruction never reaches the plan | Regression cases reject changed intent without a revised plan, a delayed prompt acknowledgement, and a stale plan acknowledgement. This verifies linkage, not whether the interpretation preserves every word's meaning. |
| 3. Chat says finished, but there is no outcome | A note and successful tool receipt leave `outcome_missing`. Switching decisions does not hide the earlier missing outcome. |
| 4. Tests pass while an exception is omitted | Actual before/after reviewer cases distinguish two passing but incomplete tests from the repaired three-test contract. The validator rejects missing or duplicated checklist IDs. |
| 5. Interruption after an external effect | Two actual Codex runs interrupt after a marker write and resume the same thread in a new process. Both retain one write and unresolved final execution status. No retry occurs. |
| 6. Concurrent or resumed work gets the wrong lineage | Original Pre/Post bindings survive redelivery. Current plan ownership and immutable plan IDs reject cross-session or stale assessment. Existing version-conflict and draft-retention cases also pass. |
| 7. Approved evidence becomes stale | Existing source-revision, review-due, changed-artifact and scope-drift cases pass. An external fact that changes without a refresh remains unverified; the software does not pretend a timestamp proves truth. |
| 8. Recovery erases failure or creates a blanket rule | Actual reviewer history retains failure and repair. Existing metrics and lesson tests preserve attempted-work denominators, conditions, exceptions and explicit lesson acceptance. |
| 9. Capture, database or viewer fails | A real competing SQLite write lock makes the hook fail. HTTP health notices the sidecar even with its previous ETag. Recovery persists the gap, and acknowledgement requires its IDs. Browser checks confirm visible recording issues appear and resolve without reload. |
| 10. Reviews loop, time out, overfetch or block unnecessarily | Stop interventions deduplicate; greetings create no records; assessed unknown execution does not trigger a repeat intervention. Existing cancelled/interrupted-worker and explicit-retry cases pass. The native paired cases quantify reduced correction and memory-call cost. |

## Reproduction

The final local suite passes 183 tests in 11.207 seconds. Chromium passes 20 live workspace checks, including concurrent edits, recording gaps and inspector refresh. The installed wheel passes MCP, HTTP, backup and uninstall preservation checks. The MCPB entry point also passes. These results do not substitute for the deferred release matrix.

From this checkout, run local checks without GitHub Actions:

```sh
uv run python -m unittest discover -s tests
uv run python -m examples.completeness_evaluation --output results/completeness-after.json
```

To compare the earlier code, export it to a separate directory and use the current evaluation driver against that package root:

```sh
git archive --format=tar --output=/tmp/project-memory-baseline.tar 9b85d68
mkdir -p /tmp/project-memory-baseline
tar -xf /tmp/project-memory-baseline.tar -C /tmp/project-memory-baseline
uv run python -m examples.completeness_evaluation --package-root /tmp/project-memory-baseline --output results/completeness-before.json
```

Actual host checks consume the selected host account's model usage. They require working CLI authentication and create a new temporary project. Use a fresh path outside another configured project, because inherited hooks must not be duplicated:

```sh
uv run python -m examples.completeness_host --host codex --omit-initial --project /tmp/memory-codex-completeness
uv run python -m examples.completeness_host --host claude --omit-initial --project /tmp/memory-claude-completeness
uv run python -m examples.completeness_review --host codex --project /tmp/memory-exception-check
```

For interruption, first set up a fresh temporary project, then run the existing harness in planned mode:

```sh
mkdir /tmp/memory-interruption-check
uv run project-memory setup --project /tmp/memory-interruption-check --client codex --trust --no-view
uv run python -m examples.codex_interrupt --project /tmp/memory-interruption-check --output /tmp/memory-interruption-check/.memory/interrupt-run --planned
```

The harness retains raw host transcripts, usage and results under the chosen project's `.memory` directory. Do not publish real-project transcripts. Browser reproduction uses `tests/workspace_browser.cjs` with `MEMORY_PYTHON` pointing to this environment and `MEMORY_PLAYWRIGHT` pointing to a development Playwright installation. This does not add a runtime dependency.

To use the source iteration in a project before publication:

```sh
uv run --project /path/to/project-memory project-memory setup --project /path/to/your-project --client codex --trust
```

Use `--client claude --trust` for Claude, or `--client mcp` for a generic MCP connection. Open a new host task so it loads the updated schemas and hooks. Setup opens the live viewer; it reads SQLite as the agent records work. No per-task frontend build is required. Generic MCP clients need their own lifecycle integration to obtain host completeness checks. This command uses the checkout, not an already published beta.

## Remaining limits

Mechanical completeness is not semantic completeness. The system cannot prove that an agent copied every condition from a prompt it stores only as a hash. Explicit assessments preserve that interpretation for review; a mistaken or dishonest assessment can still omit meaning. A numbered review can still misjudge the evidence. Neither mechanism grants permission to act or accepts a lesson automatically.

If both SQLite and its directory are unwritable, the host error remains the only failure signal. If a host does not invoke a hook at all, historical receipts cannot prove complete capture. Claude has no equivalent Interrupt hook; live Claude interruption and compaction were not rerun here. Latest full-input token maxima and exact MCP-call counts are unavailable in the retained Claude JSON format. Cross-platform native execution remains unverified in this iteration.

Review staleness still hashes the whole non-ignored project, so unrelated file changes can require a recheck. Daily task quality, correction rates, research savings, total maintenance effort and competitive advantage remain unmeasured. The 5.46 ms session-coverage read and 0.67 ms health read on the existing private database are single read-only samples, not scalability benchmarks. The existing beta gate and manual/release-only CI policy remain unchanged.
