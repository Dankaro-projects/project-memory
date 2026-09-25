# Agent checks and delegated work

Project Memory can start short host processes that read the project and report, and it can delegate a work item to a host that changes files in a separate git worktree. Both use a command line host that you installed and signed in to, so both consume that account's usage. Project Memory manages no model service and holds no API key.

Setup with `--client codex` or `--client claude` records that host. A project can configure several hosts. Generic MCP setup configures no host, so neither checks nor delegated work are available there.

## Hosts and their roles

Four hosts have a profile: Codex, Claude Code, Grok and OpenCode. A profile states the command line of each role, how the structured answer is read, how usage and limit messages are read, and the roles the host may take.

| Host | Checks and work reviews | Delegated work | Isolation of a run |
| --- | --- | --- | --- |
| Codex | Yes | Yes | Ephemeral session, hooks, plugins, memories and web search switched off, every configured MCP server disabled, read only sandbox for reviews and workspace write sandbox for work. |
| Claude Code | Yes | Yes | No setting sources, hooks disabled, an empty MCP configuration, reading tools only for reviews, and shell commands inside the operating system sandbox for work. |
| Grok | Yes | Only after a passing probe of its installed version | The compatibility variables switch off Claude Code and Cursor skills, rules, agents, MCP servers and hooks; each run has its own Grok home that links to your sign in and ignores your skills; memory, subagents and web search are off; only listed tools are allowed and every MCP tool is refused. |
| OpenCode | Yes | No | An empty global configuration, a generated configuration that denies edit, shell and web tools and defines no MCP server, and no project configuration. OpenCode offers no sandbox. |

Grok cannot receive the hive server on its command line, so work of a swarm is routed only to Codex or Claude Code. Grok 1.0 has no switch for your own skills, so each Grok run gets its own `GROK_HOME` in its run folder. That folder holds a link to `~/.grok/auth.json`, never a copy, and a configuration that ignores every skill under your home folder and switches memory off. The skills in `~/.grok/skills` and `~/.agents/skills` and the configuration, MCP servers and hooks of `~/.grok` are therefore not loaded. Grok still reads AGENTS.md project rules, and in a repository `.grok` and `.agents/skills`. If a link cannot be made, as on Windows without the right to create links, the run keeps the default home and the probe reports what stays visible. When Grok renews its sign in during a run, it may write the new sign in into the run folder instead of `~/.grok`; sign in again with `grok login` if a later run reports a missing login. A Grok run refuses a folder whose repository holds `.grok` or `.agents/skills`. Every Grok sandbox allows writes to `~/.grok`, so the probe of the work role also tries a write there, and Grok takes no work while that write succeeds. An OpenCode review must end its answer with one fenced JSON block; a missing or invalid block fails the run. See [setup and lifecycle](setup.md) for the probe and the usage command.

## Agent checks

A check is a read only run against the current records and project files.

| Role | Question it answers |
| --- | --- |
| `outcome` | Does the actual evidence support every completion criterion, including its conditions and exceptions? |
| `intent` | Does the current plan conflict with the agreed intent? A changed file alone does not prove a conflict. |
| `recovery` | Which side effects are observed, which completion is still unknown, and which evidence is needed before another attempt? |

Recovery takes precedence over intent, and intent takes precedence over outcome. A check needs an explicit plan and a decision bound to the host session. Cancelled work starts no check. A fresh session start does not start an outcome check, and ordinary acknowledgements or individual tool calls never create one.

When a Done transition is refused only because no current check exists, the write returns that refusal together with the check it just started, so the assistant waits for a result instead of retrying the work.

Checks prefer a host other than the one that did the work, so that the reviewer is independent. With one configured host the same host reviews and the run records that its independence is `same_host`.

Request, wait for and cancel a check from the command line:

```sh
project-memory review --episode EPISODE_ID --role outcome
project-memory review --wait CHECK_ID --wait-seconds 60
project-memory review --cancel CHECK_ID
project-memory review --episode EPISODE_ID --role recovery --retry
project-memory review --episode EPISODE_ID --role outcome --retry --max-seconds 900
```

The execution limit is 30 to 900 seconds and defaults to 300. Waiting defaults to 60 seconds and returns the current state when the check is still running; it never extends the execution limit and never cancels the reviewer. A retry reads current evidence, so it is not a replay of an identical input.

### Facts outside the repository

A workflow run, a published artefact or an installed version lies outside the checked folder, and a receipt keeps only the hash and size of a tool result. A check could therefore read such a fact only from the summary the implementer wrote, and it ended uncertain. The `evidence` operation of `memory_write` closes that gap. Name up to 20 receipts of completed tool calls in `receipt_ids` and leave `data` empty. Project Memory reads the output of each call from the transcript of its session and stores it only when the sha256 of that output matches the receipt. A mismatch, or a call whose transcript holds no result, refuses the whole request and stores nothing. The output is stored under the reserved source key `receipt-evidence:<receipt>`, which only Project Memory can write. Cite the returned sources as evidence on the outcome. A check snapshot then carries each such source with a `verification` field that names its receipt.

Receipts still keep only hashes and sizes, and nothing is stored unless it is named as evidence. Only calls that the hooks observed have a receipt: in a Claude Code session this covers shell commands and not calls to MCP tools. Claude Code transcripts are verified through the `toolUseResult` field; for Codex a result is accepted only when one of its transcript fields matches the hash.

Some criteria cannot be confirmed by any machine, such as an approval of wording. A check reports such a criterion as `needs_user`. The work item then shows it under Criteria no machine can confirm in the control panel, and the Stop notice asks the assistant to name it to you in the chat when it reports the work. Each criterion is offered once. Your statement is stored with user origin, and the next outcome check reads it as the evidence for that criterion. A confirmation belongs to the text of its criterion, so it does not carry over when the plan changes that text. A criterion the check reported as `unknown`, because no evidence was available to it, is not offered to you. The Stop notice asks the assistant to run what proves it and store the output with `memory_write evidence`, so the next check can read it. If you still choose to confirm an unknown criterion through `confirm_criteria`, the next check reads it as met on your evidence and is told that no machine found evidence for it. Several criteria across work items can be confirmed with one statement through the workspace operation `confirm_criteria`; you ask for either in the chat, where the assistant records it with `user_action`.

A check of finished work that went stale, or that was never run, runs again by itself. At every session start, each work item with a good and complete outcome whose outcome check is stale or missing gets a new check, up to 5 a day per project. The limit is the setting `check_refresh_daily_limit`. A Done request on an item with a stale check also requests a new check, as it does for a missing one.

Idle work expires. At every session start, an open work item without any new record for 14 days is archived, and so is one that waits on another open item for 7 days. The periods are the settings `expire_idle_days` and `expire_waiting_days`. An archive is a plan revision to cancelled that keeps the earlier plan and names, in its `archived` field, the idle period, the last activity and the state to restore. Nothing is deleted, and the item stays searchable. A parent waits until its open children are closed, and a closed episode is left as it is. The session start names what was archived, and the Archived part of Now lists the ten most recent archived items. To restore an item, ask in the chat: the assistant revises its plan back to the state named in the archived field.

A finished check is reported by the Stop hook once per result. A new turn does not repeat the notice, and a change in the state of the check reports it again.

## Delegated work

Delegation runs a work item through a host that edits files, then has the result reviewed by another host before you merge it.

Before a run starts, all of the following must hold:

- A host is configured for the project.
- The work item has a current plan whose state is neither done nor cancelled.
- The plan has autonomy `act`, and you granted it in the chat, where the assistant records it with `user_action` on your words. A plan that an assistant wrote with autonomy `act` on its own is not a grant.
- The plan lists paths that limit which files may change.
- The project folder is a git repository with at least one commit.
- No uncommitted change matches those paths. Commit or stash them first.

The run then proceeds as follows:

1. Project Memory creates the worktree `.memory/worktrees/<run>` on the branch `pm/<work item>-<run>` from the current commit.
2. The worker receives the objective, the completion criterion, the requirements, the plan scope and paths, the next action, the matching lessons and the checklist. It edits only inside the allowed paths, runs the checks that are relevant to the changed files and reports what it actually ran.
3. The changes are committed on that branch. The diff is stored as evidence and the changed files are compared with the plan paths. A file outside them leaves the run in the state `scope_violation`.
4. When files changed, a work review starts on the other configured host with the diff, the checklist and the constraints: the scope, each requirement and the action and exceptions of each matching lesson.
5. You ask for the merge or the discard of the run in the chat.

The execution limit is 60 to 14,400 seconds and defaults to 1,800. At most one active run of each kind exists per work item, and at most two active runs exist per project.

The phase of the project decides who merges. While the project is in development, the orchestrator may merge a delegated run after a passing work review. Once you record the phase `production` in the chat, where the assistant records it with `user_action` on your words, every merge is a user action: the `merge` operation of MCP is refused with the reason that the work is prepared and waiting, its changes and its work review are recorded, the project files are unchanged, and the merge happens when you ask for it in the chat, recorded with `user_action`. Every `merge` operation in production receives this refusal first, whatever actor it names. Delegated work and cross review still run in production. The session context states the phase to an assistant, so it knows the gate before it tries.

Each recorded phase carries a seal that chains it to the version before it. A phase row written into the database in another way fails that check, and the project is then treated as in production until you record the phase again in the chat, so a changed record can only tighten the gate. A process that runs as your own user can still read the database and the files, so the gate guards against a mistaken or unattended merge by an assistant, not against a process that sets out to bypass it.

Merging requires a completed run that changed files and a passing work review. If the review is missing or ended without assessing the work, a merge request starts a new review and reports it instead of merging. You can merge without a passing review by giving an override reason in the chat, and only you can do that. Project Memory merges with `git merge --no-ff`; on a conflict it aborts the merge and reports the git message, leaving the branch intact. A discard removes the worktree and the branch and records your reason.

The worker has no web access, must not commit, push, switch branches or change git configuration, and must not edit records or anything under `.memory`. Work that needs outside information is returned as blocked or partial with the missing information named.

## Fallback between hosts

Project Memory reads the run log of each host. A usage limit, a rate limit, an exhausted quota, HTTP 429, a missing login, an authentication failure or HTTP 401 marks that host unavailable, with the time parsed from the message when the message states one. Without a stated time the host counts as unavailable for 60 minutes.

An unavailable host during delegated work reroutes the run once to another configured host that may work, with the original run recorded as its parent, and removes the abandoned worktree and branch. When no configured host can run, the request fails with the availability of each host instead of waiting. A successful run marks its host available again. Host availability is visible in the Agents view.

## Routing by headroom

Every check, delegated run, work review and reroute chooses its host by headroom, using the usage ledger of this machine. A host is constrained when this project marked it unavailable, when its latest reported use is 85 percent or more in a window whose reset time has not passed, or when it hit a limit whose reset time has not passed. A limit hit without a reset time counts for 60 minutes. Codex reports its used percentage in its session logs. Claude Code, Grok and OpenCode report none, so for them only limit hits and marked unavailability constrain.

The preferred host is kept unless it is constrained. The preferred host of delegated work is the host you selected, then the configured work host, then the first configured host. Otherwise the installed and available host with the most headroom is chosen, in this order:

1. The lowest reported percentage. A host that reports no percentage counts as 50 percent here, so it follows a host that reports less and precedes a host that reports more. When Codex reports several limits, its general limit decides, and a limit of a single model does not.
2. The lowest load of the last 5 hours relative to the median 5 hour load of the same host over the last 7 days. A host with no recorded use in the last 5 hours has a load of zero, also when it was never used. A host whose load cannot be measured follows the hosts whose load is measured.
3. The configured order.

When every such host is constrained, the one with the most headroom still runs, and a host with a limit hit comes after a host that is only close to its limit. A host that is not installed or that this project marked unavailable is never chosen. The choice is deterministic: the same ledger, receipts and time give the same host.

A check and a work review keep the rule that the reviewer differs from the host that did the work. The other hosts are routed first, so a constrained other host still reviews before the implementer's host does. Only when no other host is installed and available does the same host review, and the run records `same_host`.

Each run records its decision: the chosen host, the preferred host, the reason (`preferred`, `headroom`, `all_constrained`, `same_host` or `not_installed`), a sentence that states why, and the headroom of every candidate. The decision is kept on the run, outside the snapshot the agent reads, and the Usage view lists the decisions of the recent runs.

## Guards

An accepted lesson with at least one trigger is a guard. Triggers are paths, keywords, a failure type and agent roles, and you set them when you accept the lesson.

A guard matches a decision when one of the plan paths matches or overlaps its pattern, or when one of its keywords appears as a whole word, ignoring case, in the objective, the completion criterion, the decision, its reason or the plan scope. Every matching guard must appear in the `lessons_considered` list of the decision with `yes` or `no` and a reason. Otherwise the decision is rejected, and the error names the lessons to read. A decision that matches no guard is unaffected.

Project Memory also reports when a failure type recurs after its lesson was accepted, and when a failed outcome has no lesson and no later complete result. Both appear in the Learning view. A recurring guard shows that the lesson did not prevent the failure; it does not decide what to do about it.

## Instructions and rules

Every run receives the instructions of its role. The roles are `assistant` for the session that works with you, `worker` for delegated work, and `reviewer` for every check and work review.

The instructions are the base text of the role, followed by the accepted rules of that role:

- The base text is the file shipped with Project Memory, `memory_module/agents/<role>.md`, unless you saved a project version in the chat, where the assistant records it with `user_action` on your words. Only you can save it, every version stays, and the latest version is the one in force.
- A rule is a lesson that you accepted with one or more roles. A lesson with no role is a guard and is never composed into a prompt.
- A prompt carries at most eight rules for one role, within 600 characters for the assistant, 1,200 for the worker and 900 for the reviewer. Rules with the fewest recurrences after acceptance come first, then the most recently accepted. A rule that does not fit is reported with its reason, in the Learning view and in the metrics of the run. A rule that is longer than the budget of its role is reported as left out in every run, because no run can carry it. Nothing is dropped in silence.
- More than eight accepted rules for one role is reported in the Learning view, and so is a rule that the counts judge ineffective.
- A rule with paths, keywords or a failure type is composed only into a run that matches it. A rule with a role and no other trigger is composed into every run of that role.

Each run stores the exact text it received as a source `instructions:<role>` and records the rule identifiers in its metrics, so an outcome can be read against the instructions that produced it. The source keys `instructions-base:<role>` and `instructions:<role>` are reserved: you give the base text in the chat and Project Memory writes the text of a run, so an agent cannot replace the instructions it is judged against.

The assistant receives the base text of its role when a session starts and again after a compaction. It receives the rules of the role when it submits a prompt, and the rules of the files it is about to change before an edit. Each rule is carried once in a session, and a rule that did not fit is named instead of hidden.

A rule of the worker travels with delegated work, and a rule of the reviewer with a check. Each rule reaches a prompt once: a rule the cross review already carries among its constraints is reported as left out rather than written twice, and a rule composed into the instructions of a worker is not repeated in its snapshot.

Write the current text of each role to a folder for reading:

```sh
project-memory instructions --output instructions
```

The command reads the database and writes one Markdown file per role. The database stays the source of truth.

## Promoting a rule to this machine

A rule that holds beyond one project can be promoted to the memory of this machine, which sits above the projects on this computer. Its location is `PROJECT_MEMORY_MACHINE_DB`, or `~/.project-memory/machine.sqlite`, and `project-memory machine init` creates it. The first promotion you accept creates it as well.

An agent proposes a promotion with the `promote_rule` write operation. The proposal is recorded in this project and nothing is written to the machine memory. You accept or decline it in the chat, where the assistant records it with `user_action` on your words, and only an acceptance writes the rule, as `workspace-user`.

Mechanical checks refuse a proposal whose text carries the project name, an absolute path, a record identifier, a document file name of this project, an electronic mail address or a host name. The refusal names the check that matched and never repeats the value. The same checks run again when you accept, so a corrected text you give in the chat is checked as well. They cover every text that reaches the machine memory, including the failure type. The project name is found in its ordinary spellings, such as a joined, hyphenated, underscored or plural form, and a short abbreviation of the name is found as a whole word. Ordinary words of work, such as automation, invoice or review, are not checked alone, so a generic rule may still use them. Record identifiers are found in any letter case and when cut short, and network addresses, local host names, home folder paths, network shares and paths that start at an environment variable are refused as well.

A promoted rule is an ordinary accepted lesson in the machine memory. `guards.compose` adds the machine rules of a role after the project rules, each labelled as machine level, within a sub budget of 200 characters for the assistant, 400 for the worker and 300 for the reviewer. The heading of the machine rules counts against that sub budget. Project rules take precedence when the budget is tight, and an omitted machine rule is reported with its reason like any other omission. The edit and prompt hook of the assistant carries the machine rules of the assistant as well, once in each session, and names an omitted machine rule with `memory_get machine`. The metrics of a run record the machine rules it carried.

When a second project promotes the text of a rule that is already in force with other roles or triggers, the acceptance is refused and names the roles and triggers of the machine rule, so nothing is dropped without notice. Retiring a rule a second time reports the recorded retirement.

Effectiveness stays in each project. The Machine view reports how many projects promoted a rule and the basis written at promotion, and states plainly that no outcome is combined across projects. A rule in force is not rewritten: retire it and promote the corrected text.

An assistant reads the accepted rules with `memory_get machine`, which returns the same read only view without the registry of projects. The command line reads the same content with `project-memory machine rules`. See [setup and lifecycle](setup.md) for the location of the database and the commands that create and read it.

## Rule effectiveness

For each rule, Project Memory counts the runs that composed it, the verdicts of those runs, and the recurrences of its failure type before and after acceptance. A delegated work run reports a result rather than a verdict, so the verdict of the cross review that judged that work counts for the rules the worker received. From these counts it reports a state of effective, unproven or ineffective. A single recurrence decides nothing: the comparison before and after acceptance is used from two recurrences, and a state that rests on small counts says so. The counts appear in the Learning view of the control panel and in the guards view of `memory_get`.

No model judges a rule. Pending and uncertain runs stay out of the ratio, every denominator is reported, and a rule with few runs is reported as unproven together with its counts. A rule whose failure type keeps recurring is returned to you as ineffective rather than staying in force unexamined. Retiring it is your decision.

## Scope

When a host session works on an item whose plan lists paths, the lifecycle hook checks each edit before the tool runs. An edit whose target falls outside those paths is blocked: the hook records a `ScopeBlocked` receipt, returns the reason to the host and exits with code 2, and the tool does not run. The message names the blocked paths and the allowed patterns and asks for the scope to be extended in the chat or revised in a plan.

The check covers the file paths of edit tools, the file markers inside a patch, and the write tools of MCP servers, whose target is `mcp:<server>/<tool>`. Add the pattern `mcp:<server>` to the plan paths to allow every write tool of a server. Reading is never blocked. Shell redirection and other indirect writes inside shell commands are not parsed, so the guard does not catch them.

Widening the paths of a work item is not a change of scope, so you can extend them after a block and the work continues. A widening by an actor other than you is listed in the Learning view for you to inspect.

## Recording work

A piece of work takes two writes: its plan, and one `log` entry when it is done. The entry names the decision, why it was taken, the action, what was observed, the assessment and the completion, and it records them as a decision, an action and an outcome in one step, so every view and check reads it as before. It replaces the current decision of the work item. Fields it leaves out, such as the alternatives or the uncertainty, are recorded as not stated. A decision that is not yet carried out is still a `record` of kind decision.

A plan, record, log or progress write that names the session assesses the open prompts and capture gaps of that session, because the record states what the turn did. A turn that changed something and left no record still needs an assessment. An explicit `checkpoint` stays available to state conditions and exceptions.

A check reads only the sources that the current outcome cites. To prove a criterion that a check reported as unknown, store the command output with the `evidence` operation, record an outcome that supersedes the current one and cites the returned sources, and request the check again with `retry` true. A request for unchanged evidence returns the earlier check and marks it as reused.

When you tell the agent in the chat that a work item is finished, the agent can close it with the `close` operation, even when its check is uncertain. The operation takes the receipt of your prompt and its exact text, compares the text with the hash in the receipt, and refuses a notification or a prompt of another session. It stores your words as evidence that an agent cannot write itself, and the item reaches Done on that evidence.

Only a call that acts outside the repository needs reconciliation when its result is missing: a push, a publication, a network command such as `gh` or `curl`, or a tool of another MCP server that does more than read. A local edit, test or commit that reports no result can be inspected in the repository instead. Calls captured before this rule keep the earlier treatment.

## Evidence and recovery

Every run keeps its input, its structured report, the host output and its errors under the private `.memory/agent-runs/<run>` folder, and a delegated run also keeps its diff there. These files can contain project content and are not deleted automatically.

A timed out or cancelled run keeps any valid report and provider counters it already received, but it cannot approve work. The states `execution_deadline`, `host_exit`, `host_error`, `invalid_report`, `worker_error`, `cancelled` and `host_unavailable` separate a termination from an actual verdict of changes required. A worker without a heartbeat for 30 seconds is shown as interrupted. Silence from a child process is observable but does not establish a lost connection, and nothing is retried automatically.

Reviewer reports must address every numbered criterion and constraint exactly once, and missing proof must be reported as unknown. A criterion that no machine can confirm is reported as `needs_user`, and a passing report cannot contain one. A reviewer may propose lessons. Those proposals are recorded as proposed lessons and still require your explicit acceptance.

## Boundaries

- A reviewer cannot edit a plan, accept a lesson, repair the work, approve a retry or grant a permission. Codex reviewers run in its read only sandbox and Claude reviewers receive only the reading tools. Nested hooks, connectors and delegation are disabled inside a run.
- A model verdict is an interpretation. A pass does not prove that the work is correct, and an empty findings list is not evidence of correctness.
- Delegated work is limited by the recorded paths and by the worktree, not by an operating system sandbox. The host's own permissions still apply.
- Runs consume your host account's usage. Project Memory reports the counters and the cost the host returns and does not estimate cost. `project-memory usage` shows them per host.
- The probe of Grok relies on the report of Grok itself for the MCP servers and skills it can see. The process runner confines writes, not reads, so a worker shell can still read files that your user account can read.
- The time an agent saves, the tokens it uses and the corrections it avoids are not measured. See [testing and limitations](evidence.md).
