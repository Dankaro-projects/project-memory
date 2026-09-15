# Intent, continuation and the work board

A work card is an existing episode. Its objective states the intended result; its criterion states what counts as completion. A work plan adds scope, the next action, readiness, ownership, sprint membership and explained dependencies. Decisions retain their evidence, initial choice, alternatives, uncertainty, expected consequences, actual outcomes and revisions.

The live HTML viewer includes **Work board**. Select a sprint, subject or state, then open a card to inspect intent and decision history. Its timeline links to the full records and their evidence. Search and pagination remain available; column totals cover all matches, while cards are paged. Existing episodes remain visible even if no plan was recorded. The viewer does not infer a plan for them.

The live board accepts structured plans, sprint assignments, dependencies and comments through the same record validation used by MCP. Offline exports remain read-only. An arbitrary card move cannot establish successful completion. Plans, source versions and failed outcomes remain in the history.

## Record work with one operation

Read `memory_get` with `view: schema, id: plan` once when needed. `memory_write plan` creates an episode and its first plan atomically. A repeated request key returns the original result. Supply:

- `title`, `objective`, `criterion` and `subject` for new work.
- `payload` with `state`, `next_action`, `scope`, `autonomy` and `reason`.
- `actor` and explained `evidence` references.

Optional payload fields are `owner` (`agent` or `human`), `priority` (`high`, `normal` or `low`), `sprint_id`, and `depends_on: [{episode_id, reason}]`. Select existing IDs from the board or sprint list. A null sprint ID explicitly removes an assignment. Dependencies may cross subjects when their reason explains the connection; they do not merge the subjects' evidence automatically.

To revise work, provide `episode_id`, `expected_version` and the complete updated payload, actor and evidence. The previous plan remains readable. Conflicting versions require rereading current state. `in_progress` agent work uses the host's `session_id`; another session is directed to inspect its owner before continuing. These checks do not replace the host's execution permissions or enforce an operating-system lock.

The original objective and criterion remain fixed. When the intended result changes, create new work and use optional `links: [{event_id, reason}]` to explain its relation to the earlier plan or decision. Cancel or conclude the earlier work explicitly. Use a dependency only when earlier completion is a real prerequisite.

`memory_write sprint` uses the same create/update envelope. Its payload contains `starts_on`, `ends_on`, `status` and `reason`. Dates use YYYY-MM-DD; status is `planned`, `active` or `closed`. A sprint is an episode with its own goal. Closing a sprint does not hide unfinished work. Reassign unfinished work before continuing it.

## Continue within the recorded intent

Use `memory_get next` with `id` set to the episode ID and the current `session_id`. The bounded response contains the full intended result, criterion and plan, checked issues, and a suggested next step:

| Result | Meaning |
|---|---|
| `continue` | The recorded work is ready or belongs to this session. Continue only within the current user authorisation and recorded scope. |
| `reconcile` | A host call may already have run. Inspect its real effect before deciding whether any retry is appropriate. |
| `inspect_execution` | Execution exists without an assessed outcome. Inspect existing results before following an outdated next action. |
| `review_outcome` | A failed or uncertain outcome needs review before any further attempt. |
| `review` | Evidence, dependencies or recorded readiness need attention. |
| `inspect_owner` | Another session recorded work in progress. Inspect its result before taking over. |
| `propose` | The plan calls for a proposal or a human step. |
| `plan` | The existing episode has no explicit plan. |
| `finalize` | Current evidence establishes completion; update the plan without repeating the action. |
| `stop` | The work is complete or cancelled. |

Without an explicit episode or an active decision bound to this session, `next` returns a paged selection, not a chosen objective. Read complete governing requirements and supporting records before acting. A small character budget fails explicitly rather than dropping scope or exceptions. Episode-specific `memory_context` also keeps the full current plan mandatory.

`autonomy: act` requires current user-origin evidence in the record. This is a consistency check, not authentication: an assistant can misclassify evidence, and a stored statement cannot grant tool permissions. The host must interpret scope against the actual user request. `suggest` preserves the proposal boundary.

Startup and continuation hooks point to the next-work view for an active planned decision. In the development version, verified hooks conditionally start a read-only intent, outcome or recovery agent. They do not execute planned implementation work, schedule future sessions, accept lessons or declare success. New work in a project with agent checks enabled also requires a current passing outcome check. Done requires an evidenced good outcome with `completion: complete`, current supporting evidence, and no unresolved execution or consequence. Changed evidence can put a previously completed card back into Review without overwriting its historical state. Failed outcomes remain visible in the timeline and existing metrics.

## Storage and compatibility

Plans and sprints use existing event and episode tables. No runtime dependency or task service is added. On the first plan write to an older version-2 database, the transaction extends its event-type validation trigger; all other checks and existing rows remain intact. Read-only operations do not change the schema. Keep a normal SQLite backup before upgrading; older releases cannot author the new record kinds.

MCP board and sprint pages fit whole cards within the requested budget and return `next_offset`; use it with the same filters to retrieve more. A single oversized card fails explicitly. Checked state and totals scan matching work. Large histories need measurement before claiming flat latency. The agent remains responsible for recording significant changes and interpreting intent. This iteration does not establish unattended execution, lower daily reminder rates or long-term resistance to semantic drift.

## Design references

The board uses familiar status columns, a sprint selector and direct card inspection, informed by the inspected [ClickUp sprint board](https://mobbin.com/screens/7e98b037-9d01-46b5-bd5b-50cb02ed249a), [Height project board](https://mobbin.com/screens/913e271b-5104-454a-96aa-56b7c255c167) and [Jira work board](https://mobbin.com/screens/8fadb82e-c19c-4892-a2c5-978804acdd28). Its intent panel and decision history expose this product's evidence rather than adding another task manager.

See [workspace and agent controls](workspace-agents.md) for the interactive workflow, exact triggers and current limits.

## Project skills and maps (development version)

`memory_get next` reports the count of selected skills and a bounded read pointer. Use `memory_get` with `view: "skill_selections"` and the work ID to inspect versions, selection reasons and freshness. Use `view: "skills"` to discover project methods; the catalog contains metadata only. `view: "skill"` with a skill ID lists its files, and an explicit `file` reads a bounded text slice. Read complete applicable instructions and referenced conditions before applying a method. Preserve the returned `revision` across pages and restart the read if it changes. Binary files return their size, not a text interpretation.

`memory_write skill_import` stores base64 file entries or a base64 ZIP with `expected_version: 0`. A replacement requires the current version and its exact `replace_skill_id`. Imports support up to 100 files and 1 MB of decoded content; archives also have a 1 MB compressed limit. Symlinks, hidden files and escaping paths are rejected. Nothing is extracted or executed. `SKILL.md` requires a lowercase hyphenated name and a description. The dependency-free reader supports plain, quoted and folded/literal block scalars for name, description, license and compatibility; YAML anchors, tags and complex values for these fields are unsupported. Other frontmatter and all original bytes remain in the package. This is not a global skill installer or a complete YAML implementation.

`memory_write skill_selection` records `selected`, `released` or `reported_use` for work. Pass the exact skill revision, current work version, reason, actor and explained evidence. Local selections preserve a complete package snapshot. Reported use is an agent claim linked to evidence; capture does not infer that every instruction ran correctly. A removed local skill can still be read from its selection snapshot and released.

`memory_get relationships` expands recorded connections around an ID, with at most 100 nodes, depth 3 and 200 edges. The default neighbourhood is smaller. Truncation is explicit and nodes can be expanded separately. `memory_get map` reads authored workflow or architecture pages for a work ID; assemble all pages from the same version before revising a diagram. Each page preserves whole nodes and relationships. Node and edge counts state the complete diagram size.

`memory_write map` saves a complete diagram using the current work and map versions, a change reason and evidence. Read its schema for the supported node and relationship types. References must identify existing project records. Assistant-created or changed items cannot be confirmed on the user's behalf. The user confirms them in the workspace. Diagrams support 100 nodes and 200 relationships per work item; split larger designs across related work. Maps annotate the design and process; board dependencies remain separately enforced prerequisites.

Skills and diagrams reuse versioned sources and ordinary evidence-linked notes. They add no database tables, event types, background model calls or runtime packages. The three existing subject boundaries remain intact.
