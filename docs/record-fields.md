# Record fields

All event payloads reject unknown fields. Text must be nonempty; lists and costs have type and size checks. Dates require an explicit time zone and are normalized to UTC.

| Kind | Required payload | Optional payload |
|---|---|---|
| decision | decision, why, expected, reconsider_when | uncertainty, assumptions, alternatives, review_after, follow_up_owner, model, condition, case_id |
| action | action | host_reference |
| action_result | execution_status, summary | duration_ms, artifact |
| outcome | observed, assessment, assessment_reason, severity, attribution | tokens, human_corrections, duration_ms, failure_type, model, completion, context_characters, research_calls, repeated_research, maintenance_ms |
| review | target, revision, summary, findings | None |
| correction | before, after, reason, scope | None |
| research | question, findings, gaps | queries, refresh_reason |
| lesson | when, do, because, exceptions | pattern_type |
| lesson_review | lesson_id, status, reason | None |
| follow_up | review_after, owner, reason | None |
| episode_status | status, reason | None |
| note | text | None |

`assumptions`, `alternatives` and `queries` are lists of strings. Review `findings` is a list of objects with exactly `location`, `issue` and `severity`. An empty findings list represents a completed review without findings, not proof that the code is correct. Record the exact code revision in `revision` and attach the captured code or review output as evidence.

Outcome assessments are `pending`, `unknown`, `good`, `bad`. Severity is `none`, `minor`, `major`, `unknown`; review finding severity excludes `none`. Execution status is `completed`, `failed`, `unknown`. Lesson review status is `accepted`, `rejected`, `retired`. Episode status transitions are active/reopened to settled/abandoned, and settled/abandoned to reopened.

Code reviews, explicit corrections and lesson reviews require evidence. Good/bad outcomes require evidence and a preceding action. Scoped lessons require evidence; legacy general lessons remain compatible. A lesson review must explicitly link its lesson. Hook decisions additionally require a follow-up owner and date.

Sources take `source_key`, `title`, `summary`, `body`, `origin`, optional `review_after`, and `subject` (default general). Origin is `user`, `tool` or `document`. The source key identifies a logical source; versions are assigned automatically and content is hashed. A source key cannot change subject.

Checks validate structure and references, not whether the source proves the claim. Genuine missing information should be described as missing, not filled with invented evidence.

Codex decision capture additionally requires evidence, uncertainty and an alternatives list. Empty alternatives explicitly records that none were considered. Writing corrections submitted through capture belong to writing episodes. All capture triggers require evidence, including research. The low-level API remains compatible with older records.

`completion` is `complete`, `partial`, `blocked` or `abandoned`. `pattern_type` is `practice`, `anti_pattern` or `recovery`. Numeric effort fields are nonnegative integer measurements; omit unmeasured values. `tokens` is caller-reported and must identify its meaning in the evidence. The live evaluator uses Codex's reported cumulative input/output usage and keeps automated rubric corrections separate from human corrections.
# Record wording

New explanatory fields use complete sentences with a named actor or object and a verb. For example, write “This correction applies to claims about features that have not been evaluated.” instead of “Claims about untested features.” A current rule uses present tense; an observed outcome uses the tense of the observation. An expected consequence remains explicitly conditional. Short titles and status values remain labels.

The module validates required fields and their types mechanically. It does not guess whether prose is true, grammatical or acceptable. A reviewer checks meaning, scope, evidence and exceptions explicitly. Original quotations and historical records remain unchanged; a linked correction records any clarification. The viewer displays original wording before corrected wording.
