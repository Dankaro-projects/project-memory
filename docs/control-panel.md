# Control panel

The control panel is a local page served by `project-memory view` from the project database. It replaces the earlier workspace and work board. The same page renders an offline export, where the editing controls are absent.

```sh
project-memory view
project-memory view --output review.html --include-bodies --no-open
```

The live service binds to `127.0.0.1`, uses a random capability in its address, checks the request host and origin, and reads through a read only connection. Writes need the same origin, a JSON content type and the session token. The address and its credential stay in the private `.memory/viewer.json` file. Treat that address as a secret and do not share it.

## Layout

A navigation rail on the left groups the fourteen views in four sections. Below 900 pixels it collapses into a top menu. The top bar carries the project name, the live state, the phase of the project and a search box. Records open in a drawer on the right, which Escape closes and which returns focus to the control that opened it. The page stays usable down to 320 pixels; only graphs, tables and code blocks scroll inside their own container.

| Section | Views |
| --- | --- |
| Delivery | Now, Plan, Work |
| Structure | Architecture, Dependencies |
| Oversight | Decisions, Learning, Agents, Machine, Hive, Usage, Sessions |
| Reference | Records, Requirements |

## The phase of the project

The top bar states the phase under the label Lifecycle, so it is not confused with the phases of a plan: development or production. The phase decides who merges delegated work. In development the orchestrator may merge a run after a passing work review. In production every merge is a user action in the Agents view, and a merge over MCP is refused with the reason that the work is prepared and waiting.

Selecting the phase opens a form that asks for the new phase and a reason. The reason is required. The phase is recorded as a new version, so every earlier phase and its reason stay readable, and only you can record it: a write from an agent to the phase is refused. A change of phase also updates the phase of this project in the registry of the machine memory.

A control panel that was started from inside an assistant session, where Claude Code or Codex set their environment variables, does not merge delegated work in production and does not move the project back to development. Start the panel from your own terminal with `project-memory view` for those actions. Inside an assistant session, `project-memory view` asks the browser to open the panel and does not print its address, because the address carries the access key of the panel.

Labels follow the project template. An engagement calls components "Stakeholders and workstreams", an automation calls them "Systems and workflows", and a product calls them "Components".

## Now

Now opens with one sentence that states the position, for example "1 work item is in progress, 2 are blocked and 1 needs review." Below it are cards for work in progress, blocked work, the attention list, running agents, recent decisions with their outcome badges, and recent scope blocks.

While a templated project has no approved requirements, a kickoff checklist appears first with the open questions, the research still needed, the starter documents that are not yet filled and the phases. Each entry carries the action that advances it.

The attention list names the reason for every entry and links to the record behind it:

| Entry | Meaning |
| --- | --- |
| Edit blocked | An edit fell outside the recorded paths of a work item. Allow the paths or keep the block. |
| Blocked | A work item is blocked, with the first recorded issue. |
| Awaiting merge | Delegated work finished and waits for your merge decision. |
| Needs review | A work item cannot continue or finish until it is reviewed. |
| Lessons to accept | Proposed lessons await your acceptance or rejection. |
| Failure without a lesson | A failed outcome has no lesson and no later complete result. |
| Repeated failure | A failure type recurred after its lesson was accepted. |
| Agent follow up | An agent run needs a decision or could not complete a follow up step. |
| Recording gap, Recording failed | Host events are missing or could not be written. |

## Plan

Plan shows the hierarchy of phases, epics, stories, tasks, research items, deliverables and workflows, built from the parent of each work item. Every node carries a state badge and a progress roll up of its descendants. A timeline strip shows the phases in order. The detail of an item lists its acceptance criteria. "Add item" creates a new item with its type and its parent.

## Work

Work offers a board with one column per state and a sortable list, with filters for subject, state, sprint and free text. Empty columns are hidden until you show them. The filters and the selected format stay in the address, so a refresh keeps them.

A work item opens in the drawer with the intended result, the completion criterion, the next step and its reason, the plan scope and the allowed paths, the dependencies and their reasons, the recorded issues, the agent checks and delegated runs, a compact lineage graph and the paged history. Its actions are Edit plan, Allow paths, Delegate, Request check and Comment.

Moving a card cannot manufacture a result. Done requires an evidenced good outcome with complete completion, current supporting evidence, no unresolved execution and, where agent checks are configured, a current passing outcome check.

## Architecture

Architecture draws the structure of the project as a graph. Nodes are sized by the number of lines and coloured by state. Three layers can be shown together, and a layer that holds nothing is not offered:

- **Code**: one node per directory that contains source files, from Python, JavaScript, TypeScript and Dart imports, with declared packages as separate nodes behind a toggle.
- **Workflows**: one node per exported n8n workflow in the project, with the services its nodes and credentials name, and a call edge to another exported workflow.
- **Authored**: systems, services, workflows, integrations, datasets, stakeholders, workstreams, deliverables and processes that somebody described rather than extracted. A proposed item has a dashed border and a Confirm button, because an item an agent authored stays proposed until you confirm it.

Selecting a node shows its files, the work items attached through plan paths, the matching lessons, its explicit links and its flags. "Open files" switches to the file level of one component, where files carry no work state, and a Back control returns to the component level. For an exported workflow the file level shows its nodes and connections with the triggers marked. The view states that imports are read statically and that dynamic loading is not detected. Credential names and identifiers from an export are shown; secrets are never read.

## Dependencies

Dependencies has two tabs. The work graph draws the dependency edges between work items, coloured by state, and marks the blocked chains in red. The package tab lists the declared packages with their ecosystem, requirement, group and manifest, together with the manifest issues and the flags for a package that is declared but not imported or imported but not declared. The package tab appears only in a project that declares packages. No package manager runs and no network lookup is made.

## Decisions

Decisions lists the recorded decisions with their outcome badges and a marker for a decision that needs review. The detail compares the expected consequence with the observed outcome, then shows the alternatives, the uncertainty, the lessons considered and the evidence. A lineage view connects the requirement, the work item, the plan, the decision, the action and the outcome, and a toggle switches between the graph and a readable list. Each outcome belongs to the exact decision that was recorded, so an earlier failure stays visible after a successful revision.

## Learning

Learning opens with the Instructions section, then the accepted guards with their triggers and their recurrence counts, so that a guard that keeps failing to prevent its failure is visible. Below them are the proposed lessons with Accept, Reject and Retire. Accepting a lesson can set its triggers: paths, keywords, a failure type and the agent roles. The triggers you set on acceptance replace the triggers of the lesson, and a field you leave empty stays empty. The view also lists failures without a lesson, the path widenings made by an actor other than you, and the records whose evidence needs attention.

### Instructions

The Instructions section holds one panel per agent role: assistant, worker and reviewer. Each panel states what that role receives:

| Part | Meaning |
| --- | --- |
| Base text | The instructions in force for the role, either the file shipped with Project Memory or the version you saved in this project. |
| Character budget | How much of the budget for rules this prompt uses. The budget is 600 characters for the assistant, 1,200 for the worker and 900 for the reviewer. |
| Rules in force | The accepted rules composed into the prompt now, each with the number of runs that carried it, the verdicts of those runs, the recurrences of its failure type before and after acceptance, and its state of effective, unproven or ineffective. |
| Rules that wait for a matching run | Accepted rules of the role whose paths, keywords or failure type match only some runs. They are composed into the runs they match. |
| Rules left out | Accepted rules that the prompt could not carry, with the reason: the limit of eight rules for one role, the character budget, a rule that is longer than the whole budget of its role, or a rule the prompt already carries among its constraints. Nothing is dropped in silence. |

A lesson becomes a rule when you name one to three roles as you accept it. A lesson without a role stays a guard: it is reported when a decision matches it and it is not composed into any prompt.

"Edit the base text" saves a new version of the base text of that role. Every earlier version stays, so returning to an earlier text means saving that text again. Only you can save it: the source key of the base text is reserved, so a write from an agent is refused and only a version whose author is you is in force. The rules are not edited here, because they come from lessons that you accepted.

The counts are counts only. No model judges a rule, pending and uncertain runs stay out of the ratio, and a rule with few runs is reported as unproven with its denominators rather than as an improvement.

## Agents

Agents shows one card per configured host with whether it is installed, whether it is available, until when it is unavailable and the reason. The runs table lists the role, the host, the state, the work item, the number of changed files, the review state and the merge state. A run opens with its report, its criterion level findings, its diff summary and the buttons that apply: Merge, Discard and Cancel. The delegation columns stay empty for an agent check, which changes no files.

## Machine

Machine reads the memory of this computer, which sits above the projects on it. The view lists:

| Part | Content |
| --- | --- |
| Proposals from this project | The promotions an agent proposed here, with Accept and Decline. The acceptance form opens the text of the rule for correction before it is written. |
| Rules in force | Each promoted rule with its trigger words, its roles, the basis written at promotion, how many projects promoted it and Retire. |
| Retired rules | The rules you retired. They reach no prompt and stay readable. |
| Projects on this machine | The registry: the path, the template, the phase and the dates of every project on this computer. |

The view states the isolation rule in place: effectiveness stays in each project, the adoption count reports how many projects promoted a rule, and no outcome is combined across projects. The registry stays on this computer, it is not part of an export, and no agent reads it. A snapshot carries no machine response at all, so the view says so instead of showing an empty list.

A rule in force is not rewritten. Retire it and promote the corrected text, so the history of both stays readable. Only you write to the machine memory: an agent can propose a promotion and read the rules that were accepted.

## Sessions

Sessions reads the finished Claude Code and Codex sessions of this project. Collection runs when a session starts, for a few recent files within a short time limit, and in full with `project-memory sessions collect`. A session belongs to the project when its working folder is the project, when its identifier appears in the host receipts of the project, or when its tool calls name the project folder.

| Part | Content |
| --- | --- |
| Flagged directions | A message worded as an approval, a refusal, a correction or a choice, or an answer to a question, that no plan, decision, progress, requirement or lesson record followed within ten turns or one hour. Each flag has low confidence. Confirm it when a record is missing and dismiss it otherwise. Both decisions are kept, and the view reports how many decided flags were confirmed. |
| Proposals | What a host proposed from one digest after `project-memory sessions distill`: a decision, a requirement, a lesson, a next action or a correction, with its confidence and the transcript lines it rests on. Accept records it in the work item you select; reject records nothing. |
| Session digests | One row per session with its last activity, messages, changed files, failed commands and open flags. The session opens its digest record. |

A digest is a versioned source of at most 20,000 characters. It holds the user messages, each cut to 1,000 characters, the failed commands with their exit codes, the changed files, the memory records written and the commits made in the time of the session, and it names the transcript line of each item. Credentials are redacted before anything is stored. Transcripts are never changed, and session content never enters the machine memory. `project-memory sessions off` switches reading off for the project.

An accepted lesson becomes a proposed lesson, which you then accept in Learning as any other. An accepted decision, requirement, next action or correction becomes a note of the selected work item that cites the digest, because those records carry fields and approvals that a digest cannot supply.

## Records

Records searches across every kind with the existing filters for subject, status, work item, dates and order, and pages the result. A record opens in the drawer with its evidence, the records that refer to it, paged source bodies and a toggle that shows the exact captured text. Captured documents render a limited Markdown subset: headings, simple tables, lists, quotes, fenced code, emphasis and links to http and https addresses. Raw HTML is shown as text, and no image or script is loaded from a document.

## Requirements

Requirements lists the current requirement text with its version and the evidence of its approval, together with the earlier revisions. The review form proposes a revision, which still needs an explicit approval with a reason and evidence.

## Refreshing and saving

The panel polls the health endpoint one second after the previous poll ends while the tab is visible, so a change shows within two seconds, and it redraws only when the database revision changes. Filters, the selected view, an open drawer, the scroll position and keyboard focus survive a refresh, and an open form is never redrawn under you.

Every save carries the session token and a request key created when the form opened, so submitting the same form twice does not create a second record. A save also carries the version you read. When somebody else changed the record first, the panel keeps your draft and offers to reload the saved version. Server messages appear in the alert area of the form.

## Offline export

An export embeds the same responses that the live panel requests, so the views read from the file and make no network request. The scope options are a work item, a subject and a date range, with `--include-bodies` for source text, `--replace` to overwrite an existing file and a record limit that fails explicitly instead of silently truncating. A scoped export omits the project wide views, because they would list records outside the scope, and the panel states which views were not exported. An export cannot save changes and does not update.
