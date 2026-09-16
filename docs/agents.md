# Agent checks and delegated work

Project Memory can start short host processes that read the project and report, and it can delegate a work item to a host that changes files in a separate git worktree. Both use the Codex or Claude Code CLI that you installed and signed in to, so both consume that account's usage. Project Memory manages no model service and holds no API key.

Setup with `--client codex` or `--client claude` records that host. A project can configure both. Generic MCP setup configures no host, so neither checks nor delegated work are available there.

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

## Delegated work

Delegation runs a work item through a host that edits files, then has the result reviewed by another host before you merge it.

Before a run starts, all of the following must hold:

- A host is configured for the project.
- The work item has a current plan whose state is neither done nor cancelled.
- The plan has autonomy `act`, and you granted it by saving the plan in the control panel. A plan that an assistant wrote with autonomy `act` is not a grant.
- The plan lists paths that limit which files may change.
- The project folder is a git repository with at least one commit.
- No uncommitted change matches those paths. Commit or stash them first.

The run then proceeds as follows:

1. Project Memory creates the worktree `.memory/worktrees/<run>` on the branch `pm/<work item>-<run>` from the current commit.
2. The worker receives the objective, the completion criterion, the requirements, the plan scope and paths, the next action, the matching lessons and the checklist. It edits only inside the allowed paths, runs the checks that are relevant to the changed files and reports what it actually ran.
3. The changes are committed on that branch. The diff is stored as evidence and the changed files are compared with the plan paths. A file outside them leaves the run in the state `scope_violation`.
4. When files changed, a work review starts on the other configured host with the diff, the checklist and the constraints: the scope, each requirement and the action and exceptions of each matching lesson.
5. You merge or discard the run in the Agents view.

The execution limit is 60 to 14,400 seconds and defaults to 1,800. At most one active run of each kind exists per work item, and at most two active runs exist per project.

Merging requires a completed run that changed files and a passing work review. If the review is missing or ended without assessing the work, a merge request starts a new review and reports it instead of merging. You can merge without a passing review by supplying an override reason, and only you can do that. Project Memory merges with `git merge --no-ff`; on a conflict it aborts the merge and reports the git message, leaving the branch intact. A discard removes the worktree and the branch and records your reason.

The worker has no web access, must not commit, push, switch branches or change git configuration, and must not edit records or anything under `.memory`. Work that needs outside information is returned as blocked or partial with the missing information named.

## Fallback between hosts

Project Memory reads the run log of each host. A usage limit, a rate limit, an exhausted quota, HTTP 429, a missing login, an authentication failure or HTTP 401 marks that host unavailable, with the time parsed from the message when the message states one. Without a stated time the host counts as unavailable for 60 minutes.

An unavailable host during delegated work reroutes the run once to the other configured host, with the original run recorded as its parent, and removes the abandoned worktree and branch. When no configured host can run, the request fails with the availability of each host instead of waiting. A successful run marks its host available again. Host availability is visible in the Agents view.

## Guards

An accepted lesson with at least one trigger is a guard. Triggers are paths, keywords and a failure type, and you set them when you accept the lesson.

A guard matches a decision when one of the plan paths matches or overlaps its pattern, or when one of its keywords appears as a whole word, ignoring case, in the objective, the completion criterion, the decision, its reason or the plan scope. Every matching guard must appear in the `lessons_considered` list of the decision with `yes` or `no` and a reason. Otherwise the decision is rejected, and the error names the lessons to read. A decision that matches no guard is unaffected.

Project Memory also reports when a failure type recurs after its lesson was accepted, and when a failed outcome has no lesson and no later complete result. Both appear in the Learning view. A recurring guard shows that the lesson did not prevent the failure; it does not decide what to do about it.

## Scope

When a host session works on an item whose plan lists paths, the lifecycle hook checks each edit before the tool runs. An edit whose target falls outside those paths is blocked: the hook records a `ScopeBlocked` receipt, returns the reason to the host and exits with code 2, and the tool does not run. The message names the blocked paths and the allowed patterns and asks for the scope to be extended in the control panel or revised in a plan.

The check covers the file paths of edit tools, the file markers inside a patch, and the write tools of MCP servers, whose target is `mcp:<server>/<tool>`. Add the pattern `mcp:<server>` to the plan paths to allow every write tool of a server. Reading is never blocked. Shell redirection and other indirect writes inside shell commands are not parsed, so the guard does not catch them.

Widening the paths of a work item is not a change of scope, so you can extend them after a block and the work continues. A widening by an actor other than you is listed in the Learning view for you to inspect.

## Evidence and recovery

Every run keeps its input, its structured report, the host output and its errors under the private `.memory/agent-runs/<run>` folder, and a delegated run also keeps its diff there. These files can contain project content and are not deleted automatically.

A timed out or cancelled run keeps any valid report and provider counters it already received, but it cannot approve work. The states `execution_deadline`, `host_exit`, `host_error`, `invalid_report`, `worker_error`, `cancelled` and `host_unavailable` separate a termination from an actual verdict of changes required. A worker without a heartbeat for 30 seconds is shown as interrupted. Silence from a child process is observable but does not establish a lost connection, and nothing is retried automatically.

Reviewer reports must address every numbered criterion and constraint exactly once, and missing proof must be reported as unknown. A reviewer may propose lessons. Those proposals are recorded as proposed lessons and still require your explicit acceptance.

## Boundaries

- A reviewer cannot edit a plan, accept a lesson, repair the work, approve a retry or grant a permission. Codex reviewers run in its read only sandbox and Claude reviewers receive only the reading tools. Nested hooks, connectors and delegation are disabled inside a run.
- A model verdict is an interpretation. A pass does not prove that the work is correct, and an empty findings list is not evidence of correctness.
- Delegated work is limited by the recorded paths and by the worktree, not by an operating system sandbox. The host's own permissions still apply.
- Runs consume your host account's usage. Project Memory reports the counters the host returns and does not estimate cost.
- The time an agent saves, the tokens it uses and the corrections it avoids are not measured. See [testing and limitations](evidence.md).
