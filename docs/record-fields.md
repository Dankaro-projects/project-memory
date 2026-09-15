# Record fields

All event payloads reject unknown fields. Text must be nonempty; lists and costs have type and size checks. Timestamps require an explicit time zone and are normalized to UTC; sprint calendar dates use YYYY-MM-DD.

| Kind | Required payload | Optional payload |
|---|---|---|
| decision | decision, why, expected, reconsider_when | uncertainty, assumptions, alternatives, review_after, follow_up_owner, model, condition, case_id, lessons_considered |
| action | action | host_reference |
| action_result | execution_status, summary | duration_ms, artifact |
| outcome | observed, assessment, assessment_reason, severity, attribution | tokens, human_corrections, duration_ms, failure_type, model, completion, context_characters, research_calls, repeated_research, maintenance_ms |
| review | target, revision, summary, findings | None |
| correction | before, after, reason, scope | None |
| research | question, findings, gaps | queries, refresh_reason |
| lesson | when, do, because, exceptions | pattern_type, paths, keywords, failure_type |
| lesson_review | lesson_id, status, reason | paths, keywords, failure_type |
| follow_up | review_after, owner, reason | None |
| episode_status | status, reason | None |
| note | text | kickoff_answers |
| work_plan | state, next_action, scope, autonomy, reason | sprint_id, depends_on, owner, priority, session_id, paths, item_type, acceptance, parent_id |
| sprint | starts_on, ends_on, status, reason | None |

## Paths, lesson triggers and guards

A work plan `paths` field is a list of 1 to 100 unique patterns, each at most 500 characters. A pattern is a relative POSIX path without a `..` segment, or an absolute path. `*` matches characters within one path segment, `**` matches across segments and `?` matches one character. A pattern without glob characters matches that path and everything beneath it, and `.` matches the whole project. Relative patterns are compared with paths relative to the project root.

A lesson can carry triggers: `paths` (a list of at most 100 patterns), `keywords` (a list of at most 30 items, each at most 100 characters) and `failure_type` (text). A lesson review can carry the same three fields. When an accepted review carries any of them, the review's trigger fields replace all of the lesson's triggers; a trigger field that the review omits is then empty. An accepted lesson with at least one trigger is a guard.

A guard matches a decision when a plan path matches or overlaps a guard pattern, or when a keyword appears as a whole word, ignoring case, in the episode objective, the success criterion, the decision, its reason or the plan scope. A decision must list every matching guard in `lessons_considered`: a list of at most 50 objects with exactly `lesson_id`, `applies` (`yes` or `no`) and `reason`. Each entry must reference a lesson record. Otherwise the decision is rejected with the matching lessons and a next step that names the first lesson to read. Decisions that match no guard are not affected.

When a host session has work in progress with `paths`, or has a bound decision whose plan has `paths`, the hook blocks edit tools whose targets fall outside those patterns. The block is recorded as a `ScopeBlocked` host receipt and the tool call is not recorded as started. The hook does not parse shell redirection or other indirect writes inside shell commands; it reads edit tool paths and patch markers only.

A change to `scope`, `autonomy` or `depends_on` is a change of scope: a plan revision that changes any of these fields cannot move work to Done directly, and a decision recorded under the earlier plan needs review. A change to `paths` alone is not a change of scope, so the user can widen the allowed paths after a scope block and the work continues. Path additions by an actor other than the user are listed for the user to inspect.

The scope check also covers write tools of MCP servers that change something outside the project files, such as a workflow in an n8n instance or a file in a document service. A tool of this kind is a write when its name contains a verb such as create, update, delete, publish or execute, and its target is `mcp:<server>/<tool>`. Add the pattern `mcp:<server>` to the plan paths to allow every write tool of that server, or `mcp:<server>/<tool>` to allow one tool.

## Work items, components and templates

The same work item fields serve a software product, a consulting engagement and a workflow automation. `item_type` is one of `phase`, `epic`, `story`, `task`, `research`, `deliverable` or `workflow`; a plan without it is a `task`. `acceptance` is a list of 1 to 30 unique acceptance criteria, and each criterion is a complete sentence that ends with a full stop, a question mark or an exclamation mark. `parent_id` names an existing work item that is not a sprint. A parent chain cannot contain a cycle. The graph derives a `part_of` edge from the latest plan of each item, and the plan hierarchy counts the states of the descendants of each item.

An authored component describes a part of the project that is not extracted from code, such as a system, a stakeholder, a workstream, a workflow or a deliverable. It is stored as a versioned source with the key `component:<slug>` and a JSON body with `title`, `kind`, `description`, `status` and `path`. Kinds are `system`, `component`, `service`, `workflow`, `integration`, `dataset`, `stakeholder`, `workstream`, `deliverable` and `process`. Status is `proposed`, `confirmed` or `retired`. Only `workspace-user` can set the status `confirmed` or change a confirmed component, so a component that an agent authors stays proposed until the user confirms it. Links between components use the link types `uses`, `produces`, `part_of`, `owns` and `informs`, in addition to the earlier link types.

A project created from a template stores the setting `project_template`. A `note` can carry `kickoff_answers`, a list of 1 to 30 unique kickoff question ids, which records that the note answers those questions.

`assumptions`, `alternatives` and `queries` are lists of strings. Review `findings` is a list of objects with exactly `location`, `issue` and `severity`. An empty findings list represents a completed review without findings, not proof that the reviewed work is correct. A review can be recorded in any subject: it can examine code, a report, a deck, a workflow or another deliverable. Record the exact version that was reviewed in `revision`, such as a code revision or a named draft, use `location` for a file and line, a section or a slide, and attach the captured work or review output as evidence.

Use the atomic `plan` and `sprint` write operations to create or intentionally revise plans; they preserve the earlier version using the supplied episode version. Use `progress` for routine work state or next-action changes while retaining scope, dependencies, links and evidence. Work dependencies are `[{episode_id, reason}]`. The [work board guide](work-board.md) defines states and continuation rules. Decisions automatically retain their current project revision and, when present, `work_plan_id`. These references do not grant permission or accept the plan's interpretation as fact.

Outcome assessments are `pending`, `unknown`, `good`, `bad`. Severity is `none`, `minor`, `major`, `unknown`; review finding severity excludes `none`. Execution status is `completed`, `failed`, `unknown`. Lesson review status is `accepted`, `rejected`, `retired`. Episode status transitions are active/reopened to settled/abandoned, and settled/abandoned to reopened.

Code reviews, explicit corrections and lesson reviews require evidence. Good/bad outcomes require evidence and a preceding action. Scoped lessons require evidence; legacy general lessons remain compatible. A lesson review must explicitly link its lesson. Hook decisions additionally require a follow-up owner and date.

Sources take `source_key`, `title`, `summary`, `body`, `origin`, optional `review_after`, and `subject` (default general). Origin is `user`, `tool` or `document`. The source key identifies a logical source; versions are assigned automatically and content is hashed. A source key cannot change subject.

Checks validate structure and references, not whether the source proves the claim. Genuine missing information should be described as missing, not filled with invented evidence.

Codex decision capture additionally requires evidence, uncertainty and an alternatives list. Empty alternatives explicitly records that none were considered. Writing corrections submitted through capture belong to writing episodes. All capture triggers require evidence, including research. The low-level API remains compatible with older records.

`completion` is `complete`, `partial`, `blocked` or `abandoned`. `pattern_type` is `practice`, `anti_pattern` or `recovery`. Numeric effort fields are nonnegative integer measurements; omit unmeasured values. `tokens` is caller-reported and must identify its meaning in the evidence. The live evaluator uses Codex's reported cumulative input/output usage and keeps automated rubric corrections separate from human corrections.
# Record wording

New explanatory fields use complete sentences with a named actor or object and a verb. For example, write “This correction applies to claims about features that have not been evaluated.” instead of “Claims about untested features.” A current rule uses present tense; an observed outcome uses the tense of the observation. An expected consequence remains explicitly conditional. Short titles and status values remain labels.

The module validates required fields and their types mechanically. It does not guess whether prose is true, grammatical or acceptable. A reviewer checks meaning, scope, evidence and exceptions explicitly. Original quotations and historical records remain unchanged; a linked correction records any clarification. The viewer displays original wording before corrected wording.
