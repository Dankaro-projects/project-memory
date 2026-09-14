# Interactive workspace and conditional agents

The live workspace uses the existing SQLite database. Create an action with its intended result, completion criterion, scope, next action and reason. Select its subject, owner, status, priority, sprint and existing dependencies. Dependencies require an explanation. Open a card to see its decisions, evidence, outcomes and revisions, or to add a comment. Sprint dates and status use structured controls.

The browser saves through a small loopback API. Every save checks the current episode version. A concurrent update leaves the draft visible and offers **Reload saved version**. Moving to Done cannot manufacture a successful outcome. The same domain checks apply to browser, MCP and CLI records. Offline HTML remains an inspectable snapshot.

## Agent responsibilities

| Agent | Mechanical trigger | Judgment it supplies |
| --- | --- | --- |
| Outcome | A planned decision receives a complete outcome through MCP, or Stop observes one in the bound work. | Does actual evidence support each completion criterion, including conditions and exceptions? |
| Intent | Startup after resume/compaction, a new prompt or Stop observes changed governing evidence for the bound work. | Does the current plan conflict with the agreed intent? A changed file alone does not prove a conflict. |
| Recovery | Those same continuation boundaries observe an unconfirmed execution receipt. | What side effects are observed, what completion remains unknown, and what evidence is needed before another attempt? |

Recovery takes precedence over intent, and intent takes precedence over outcome. A fresh startup does not launch an outcome check. Work must have an explicit plan and a decision bound to the host session. Cancelled work does not launch a review. Ordinary acknowledgements and each individual tool call do not create another agent.

These are separate, short-lived host CLI processes with bundled role instructions, not a permanent agent team. Codex runs in its read-only sandbox. Claude exposes only Read, Glob and Grep. Reviewers cannot edit plans, accept lessons, execute a repair, approve a retry or grant permission. Nested hooks, connectors and delegation are disabled. The main assistant retains responsibility for implementation and the user's current instructions.

## Control and recovery

Open a card's **Agent checks** panel to run a selected check, cancel a running check and inspect criterion-level findings. Earlier checks remain readable. Proposed lessons carry their basis, conditions and exceptions; they are not automatically accepted into memory. The UI does not currently provide lesson acceptance or project-requirement approval; those existing explicit MCP operations remain available.

The same work and evidence reuse a check. A new explicit request can retry a failed or cancelled review. Only one check runs per project; a competing request returns a visible conflict. Workers report activity, use a five-minute default deadline. The user can select up to fifteen minutes for a larger check and preserve diagnostic files. A worker without activity for 30 seconds appears interrupted. No automatic retry assumes that interrupted implementation did not execute.

Codex Interrupt cancels checks tied to that session. Claude has no equivalent Interrupt hook; use **Cancel check**, or inspect the expired-worker state after a process failure. Stop first checks recording completeness and intervenes at most once for missing records in a user prompt. A subsequent reviewer can run without adding another Stop intervention for that prompt. When there is no recording gap, Stop intervenes once for a review check. Subsequent Stop events do not add more review feedback. The host can finish its reply while a check is still running; the board's Done gate continues to enforce the result. Use the CLI wait command when the current task needs the result before replying.

```sh
project-memory review --episode EPISODE_ID --role outcome
project-memory review --wait CHECK_ID
project-memory review --cancel CHECK_ID
project-memory review --episode EPISODE_ID --role recovery --retry
project-memory review --episode EPISODE_ID --role outcome --retry --max-seconds 900
```

MCP still exposes three tools. Use `memory_get` with `view: "schema", id: "agent_check"` for the review request shape; `id: "review"` retains the existing code-review record schema. Retrieve a bounded review list with `view: "reviews", id: EPISODE_ID`, then read a full check by its ID.

New completion claims require a current passing outcome check when reviews are enabled. Enabling the feature does not retroactively invalidate earlier completed work. Code, governing requirements or supporting evidence changes make a check stale while retaining its original verdict. Progress-only edits retain the governing evidence and do not invalidate a pass. Current checks conservatively hash the whole non-ignored project; unrelated changes can therefore require another review. There is no human override for a failed agent verdict in this beta.

## Install this development version

The README installs published beta 5. To work from source instead, run these commands from a checked-out copy of the repository:

```sh
uv tool install --force .
project-memory setup --project /absolute/path/to/your/project --client codex --trust
```

Use `--client claude --trust` for Claude Code. Setup enables conditional reviewer calls through the installed host account, which must already work and be authenticated. The latest selected Codex/Claude setup owns the reviewer host. Generic MCP setup creates no reviewer on a fresh project; existing reviewer configuration remains in the database. There is no model service or API key managed by Project Memory.

Setup preserves existing data and opens the workspace. Existing project requirements remain unchanged. Open a new host task to load its updated tools and verified hook configuration. Run `project-memory doctor --project /absolute/path/to/your/project` and `project-memory view --project /absolute/path/to/your/project` to verify or reopen it. Add `--no-view` to setup for a headless environment. Read [setup and lifecycle](setup.md) before importing an existing database.

## Cost and boundaries

SQLite remains authoritative, with one additional table for review runs and immutable execution receipts. The Python runtime has no third-party dependencies. The optional local server exits after ten minutes without requests. It polls for changes while the browser is visible and retains drafts independently of refresh.

Review packets preserve full intent and criteria. Large supporting records move into a local file that the reviewer can read; they are not silently truncated. Full receipt archives remain separate. Review context includes mechanical counts, reported failures, unconfirmed calls and reconciliation evidence, with individual completed receipts available on demand. This bounds the initial packet, not the complete model input or aggregate provider usage. It does not enforce a 10K-token cap. The [evaluation](workspace-agents-evidence-2026-09-14.md) reports actual provider counts and latency separately from initial packet characters.

This release adds conditional independent checking and interactive planning. It does not start unattended implementation from the board, guarantee detection of unrecorded intent, prove lower daily correction rates or establish superiority over competing systems. Agent judgments can be wrong. Cancelled and failed checks remain visible rather than being removed from the measured results.

## Recording completeness

`memory_get coverage` returns a paged session list. Add `session_id` to inspect unassessed prompt receipts, unassigned tool activity, missing outcomes and capture gaps. The live workspace displays these checks automatically. It distinguishes a connected viewer from a failed host capture; a working HTTP connection does not prove that every host event reached SQLite.

Hooks retain prompt hashes and sizes. An explicit `checkpoint` records the agent's interpretation, links the observed request IDs to the current plan, and preserves its conditions and exceptions. It does not approve document instructions or establish that every condition in the conversation was understood. Bundle a checkpoint into an existing plan or record write to avoid an extra write. Standalone work checkpoints include the current `plan_id`; a concurrent plan revision requires a fresh read. Read `memory_get schema` with `id: "checkpoint"` for the fields. A checkpoint result reports the remaining issues, so a confirmation read is normally unnecessary.

The reviewer receives numbered checklist entries for the criterion, scope, governing requirements and explicitly assessed request conditions. A report must address every ID exactly once. Missing proof must remain unknown. The report, original failure and repair remain in the same decision history; proposed lessons still need explicit acceptance.

When SQLite cannot capture an event, the hook reports failure to the host and attempts to write a small local `.capture-error.json` sidecar. The viewer notices it even without a database revision. On successful recovery, the gap becomes an immutable receipt; an explicit checkpoint must acknowledge the inspected interval. If both the database and directory are unwritable, only the host error channel can report the failure. No unavailable storage mechanism can prove complete capture.

See [the completeness evaluation](completeness-evidence-2026-09-14.md) for the baseline, actual host runs, costs and remaining limits.
