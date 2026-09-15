# Interactive workspace and conditional agents

The live workspace uses the existing SQLite database. Create an action with its intended result, completion criterion, scope, next action and reason. Select its subject, owner, status, priority, sprint and existing dependencies. Dependencies require an explanation. Open a card to see its decisions, evidence, outcomes and revisions, or to add a comment. Sprint dates and status use structured controls.

The browser saves through a small loopback API. Every save checks the current episode version. A concurrent update leaves the draft visible and offers **Reload saved version**. Moving to Done cannot manufacture a successful outcome. The same domain checks apply to browser, MCP and CLI records. Offline HTML remains an inspectable snapshot.

## Completion and recovery

Work details show the recorded result separately from the current outcome review. The next step identifies an active check to wait for, changed evidence to reassess, findings to inspect, or a current result ready for Done. A retained passing report can become stale; waiting does not refresh its evidence. These explanations preserve the existing completion gates and do not repeat implementation.

MCP argument validation reports missing, unexpected and wrongly typed fields with `execution: not_started`. Correct the listed fields and resubmit the rejected request. This says nothing about earlier calls with uncertain effects. Completion rejections return `next_step` with the relevant record or review reference; `wait_command` is supplied only for an active check. Wait expiry leaves the same check running. Read current status before reporting that a review is pending.

## Agent responsibilities

| Agent | Mechanical trigger | Judgment it supplies |
| --- | --- | --- |
| Outcome | A planned decision receives a complete outcome through MCP, or Stop observes one in the bound work. | Does actual evidence support each completion criterion, including conditions and exceptions? |
| Intent | Startup after resume/compaction, a new prompt or Stop observes changed governing evidence for the bound work. | Does the current plan conflict with the agreed intent? A changed file alone does not prove a conflict. |
| Recovery | Those same continuation boundaries observe an unconfirmed execution receipt. | What side effects are observed, what completion remains unknown, and what evidence is needed before another attempt? |

Recovery takes precedence over intent, and intent takes precedence over outcome. A fresh startup does not launch an outcome check. Work must have an explicit plan and a decision bound to the host session. Cancelled work does not launch a review. Ordinary acknowledgements and each individual tool call do not create another agent.

These are separate, short-lived host CLI processes with bundled role instructions, not a permanent agent team. Codex runs in its read-only sandbox. Claude exposes only Read, Glob and Grep. Reviewers cannot edit plans, accept lessons, execute a repair, approve a retry or grant permission. Nested hooks, connectors and delegation are disabled. The main assistant retains responsibility for implementation and the user's current instructions.

## Control and recovery

Open a card's **Agent checks** panel to run a selected check, cancel a running check and inspect criterion-level findings. Earlier checks remain readable. Proposed lessons carry their basis, conditions and exceptions; they are not automatically accepted into memory. The Needs attention view supports explicit human lesson reviews and project-requirement approval.

The same work and evidence reuse a check. A new explicit request can retry a failed or cancelled review. Only one check runs per project; a competing request returns a visible conflict. Reviews use a five-minute execution deadline; the user can explicitly select up to fifteen minutes. Status distinguishes the worker heartbeat from child output, reports the last observed phase, inspection counts, output bytes, host errors and remaining execution time. A worker without a heartbeat for 30 seconds appears interrupted. Child silence is observable but does not establish a lost connection or trigger an automatic retry. No automatic retry assumes that interrupted implementation did not execute.

Codex Interrupt cancels checks tied to that session. Claude has no equivalent Interrupt hook; use **Cancel check**, or inspect the expired-worker state after a process failure. Stop first checks recording completeness and intervenes at most once for missing records in a user prompt. A subsequent reviewer can run without adding another Stop intervention for that prompt. When there is no recording gap, Stop intervenes once for a review check. Subsequent Stop events do not add more review feedback. The host can finish its reply while a check is still running; the board's Done gate continues to enforce the result. Use the CLI wait command when the current task needs the result before replying.

```sh
project-memory review --episode EPISODE_ID --role outcome
project-memory review --wait CHECK_ID --wait-seconds 60
project-memory review --cancel CHECK_ID
project-memory review --episode EPISODE_ID --role recovery --retry
project-memory review --episode EPISODE_ID --role outcome --retry --max-seconds 900
```

Waiting defaults to 60 seconds and returns `wait_expired: true` with the current state if the review is still active. It never extends the execution deadline or cancels the reviewer. `--wait --max-seconds N` remains a supported alias for `--wait-seconds N`; conflicting wait limits are rejected. `--max-seconds` on a new review controls execution, not waiting. A retry reviews current evidence, so it is not an identical-input replay.

A timed-out or cancelled run retains any valid report and provider usage already received, but cannot approve work. Missing usage remains unavailable. `execution_deadline`, `host_exit`, `host_error`, `invalid_report`, `worker_error` and `cancelled` distinguish termination causes from an agent verdict of `changes_required`. Status reports observed host-error events and stderr volume, not an inferred transport diagnosis. Raw evidence and logs stay under the private `.memory/agent-runs/` directory.

The complete serialized review report has a 16,000-character limit. Reviewers receive a 12,000-character drafting target and shorter evidence-field limits as the number of required checks grows. Every criterion and constraint must still appear exactly once, with conditions and exceptions preserved. Oversized reports remain invalid; the worker does not truncate them or automatically repeat the review.

MCP still exposes three tools. Use `memory_get` with `view: "schema", id: "agent_check"` for the review request shape; `id: "review"` retains the existing code-review record schema. Retrieve a bounded review list with `view: "reviews", id: EPISODE_ID`, then read a full check by its ID.

New completion claims require a current passing outcome check when reviews are enabled. Enabling the feature does not retroactively invalidate earlier completed work. Code, governing requirements or supporting evidence changes make a check stale while retaining its original verdict. Use `memory_write progress` with `episode_id`, `expected_version`, `actor` and a `payload` containing `reason` and `state` or `next_action` for status changes. It preserves scope, autonomy, dependencies, links and governing evidence; unchanged outcome evidence retains its signature. Use `plan` for an intentional scope revision. Do not rewrite scope as a delivery update or add the review gate to product acceptance criteria. Current checks conservatively hash the whole non-ignored project; unrelated changes can therefore require another review. There is no human override for a failed agent verdict in this beta.

## Install this development version

The README installs published beta 8. To work from source instead, run these commands from a checked-out copy of the repository:

```sh
uv tool install --force .
project-memory setup --project /absolute/path/to/your/project --client codex --trust
```

Use `--client claude --trust` for Claude Code. Setup enables conditional reviewer calls through the installed host account, which must already work and be authenticated. The latest selected Codex/Claude setup owns the reviewer host. Generic MCP setup creates no reviewer on a fresh project; existing reviewer configuration remains in the database. There is no model service or API key managed by Project Memory.

Setup preserves existing data and opens the workspace. Existing project requirements remain unchanged. Open a new host task to load its updated tools and verified hook configuration. Run `project-memory doctor --project /absolute/path/to/your/project` and `project-memory view --project /absolute/path/to/your/project` to verify or reopen it. Add `--no-view` to setup for a headless environment. Read [setup and lifecycle](setup.md) before importing an existing database.

## Cost and boundaries

SQLite remains authoritative, with one additional table for review runs and immutable execution receipts. The Python runtime has no third-party dependencies. The optional local server exits after ten minutes without requests. It polls for changes while the browser is visible and retains drafts independently of refresh.

Review packets preserve full intent and criteria. A manifest lists separate complete sources, large records, prior same-role checks and execution evidence for selective reading; they are not silently truncated. Full receipt archives remain separate. Review context includes mechanical counts, reported failures, unconfirmed calls and reconciliation evidence, with individual completed receipts available on demand. This bounds the initial packet, not the complete model input or aggregate provider usage. It does not enforce a 10K-token cap. Report actual provider counts and latency separately from initial packet characters.

This release adds conditional independent checking and interactive planning. It does not start unattended implementation from the board, guarantee detection of unrecorded intent, prove lower daily correction rates or establish superiority over competing systems. Agent judgments can be wrong. Cancelled and failed checks remain visible rather than being removed from the measured results.

## Recording completeness

`memory_get coverage` returns a paged session list. Add `session_id` to inspect unassessed prompt receipts, unassigned tool activity, missing outcomes and capture gaps. The live workspace displays these checks automatically. It distinguishes a connected viewer from a failed host capture; a working HTTP connection does not prove that every host event reached SQLite.

Hooks retain prompt hashes and sizes. An explicit `checkpoint` records the agent's interpretation, links the observed request IDs to the current plan, and preserves its conditions and exceptions. It does not approve document instructions or establish that every condition in the conversation was understood. Bundle a checkpoint into an existing plan or record write to avoid an extra write. Standalone work checkpoints include the current `plan_id`; a concurrent plan revision requires a fresh read. Read `memory_get schema` with `id: "checkpoint"` for the fields. A checkpoint result reports the remaining issues, so a confirmation read is normally unnecessary.

The reviewer receives numbered task acceptance entries for the episode criterion and explicitly assessed request conditions. The complete scope always applies as a separate constraint. Every project requirement remains visible as a constraint, with an explicit applicability decision, reason, evidence and result. Unrelated historical deliverables do not become new task acceptance criteria. A report must address every task and constraint ID exactly once; a pass cannot leave applicable or uncertain constraints unresolved. Existing reports remain readable, while checks made under the earlier acceptance contract may become stale against the new snapshot contract. Missing proof must remain unknown. The report, original failure and repair remain in the same decision history; proposed lessons still need explicit acceptance.

When SQLite cannot capture an event, the hook reports failure to the host and writes a separate atomic file for each failed call in a private `.capture-errors` directory beside the database. Concurrent sessions cannot overwrite each other's failures. Health reports the pending count and affected sessions even without a database revision. Recovery commits each gap as an immutable receipt before removing its file; interrupted cleanup can replay safely. Existing `.capture-error.json` files are recovered too. An explicit checkpoint must acknowledge every inspected gap; recovery does not reconstruct missing execution or infer its result. If both the database and directory are unwritable, only the host error channel can report the failure. No unavailable storage mechanism can prove complete capture.

On macOS and Linux, viewer reads reuse a project content hash while its file list, identity, size and change timestamps remain unchanged. Review requests and completion checks still hash fresh content. This avoids repeatedly reading unchanged file bodies during polling; it does not remove the conservative whole-project freshness rule or the cost of listing and checking file metadata. Windows retains fresh content reads because its Python file metadata cannot reliably detect an in-place write that restores the original modification time.

See [testing and limitations](evidence.md) for host checks and evaluation requirements.
