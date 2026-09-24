# Control panel

The control panel is a local page served by `project-memory view` from the project database. It replaces the earlier workspace and work board. The same page renders an offline export, where the editing controls are absent.

```sh
project-memory view
project-memory view --output review.html --include-bodies --no-open
```

The live service binds to `127.0.0.1`, uses a random capability in its address, checks the request host and origin, and reads through a read only connection. Writes need the same origin, a JSON content type and the session token. The address and its credential stay in the private `.memory/viewer.json` file. Treat that address as a secret and do not share it.

## Layout

The panel is one fixed frame: the window never scrolls, and only the panes inside it scroll. A navy rail on the left holds the 14 views with an icon each, in three groups and a fold:

| Group | Views |
| --- | --- |
| Work | Now, Plan, Work |
| Decide | Sessions, Learning, Decisions |
| Look up | Records, Requirements |
| More views | Architecture, Dependencies, Agents, Machine, Hive, Usage |

More views stays folded until you open it or work in one of its views. Sessions, Learning, Agents and Machine show a count where something waits for you, and the folded More views shows the sum of its views. The counts add up the kinds of the same response that Now shows, so the rail and Now cannot disagree. The foot of the rail names the project, the lifecycle stage of the project with its change action, and the state of the live connection: Live, Update failed, or the export time of a snapshot.

One header row above the view holds the title of the view, its summary in one sentence, the search box and the Project activity button. The key `/` moves the focus to the search box, and Enter opens Records with the query. Project activity opens a panel with the work by state, the work in progress and the active agent runs from any view; Escape closes it and returns the focus to its button.

The view scrolls inside its own pane. Most views are list panes: a head with the tabs or the filters and a foot with the count or the actions stay in place, and only the rows scroll between them. A row is 60 pixels high and opens its item in the detail pane beside the list, so the list stays in sight and the opening row stays marked. The body of the detail pane scrolls and its foot with the actions of the item stays pinned, so the primary action is in reach at a window height of 640 pixels however long the item is. J opens the next row and K the previous one. A link inside the pane leads deeper and Back returns from it. Escape closes the pane and returns the focus to the row that opened it. Full width, a toggle in the bar of the pane, hides the list so a long document reads at the width of both panes; Show the list or Escape brings the list back with its scroll position.

Below 1180 pixels one pane shows at a time: the detail pane covers the view, which keeps its scroll position, and the Full width toggle is not offered. Below 900 pixels the rail becomes a menu behind a button in the header row. On a phone the panes run edge to edge, and every table becomes labelled rows.

Three limits follow from the frame. A long item scrolls inside the pane and never scrolls the window. The rail scrolls inside itself when the window is lower than the rail needs: measured at a width of 1600 pixels, 575 pixels with More views folded and 784 pixels with More views open. A snapshot holds no session messages, so its flags and proposals in Now stay one row that names their number.

## The phase of the project

The foot of the rail states the phase under the label Lifecycle, so it is not confused with the phases of a plan: development or production. The phase decides who merges delegated work. In development the orchestrator may merge a run after a passing work review. In production every merge is a user action in the Agents view, and a merge over MCP is refused with the reason that the work is prepared and waiting.

Selecting the phase opens a form that asks for the new phase and a reason. The reason is required. The phase is recorded as a new version, so every earlier phase and its reason stay readable, and only you can record it: a write from an agent to the phase is refused. A change of phase also updates the phase of this project in the registry of the machine memory.

A control panel that was started from inside an assistant session, where Claude Code or Codex set their environment variables, does not merge delegated work in production and does not move the project back to development. Start the panel from your own terminal with `project-memory view` for those actions. Inside an assistant session, `project-memory view` asks the browser to open the panel and does not print its address, because the address carries the access key of the panel.

Labels follow the project template. An engagement calls components "Stakeholders and workstreams", an automation calls them "Systems and workflows", and a product calls them "Components".

## Now

Now lists every kind of decision that waits for you. Its summary states the total, for example "12 items wait for you." One tab per kind carries the true count of that kind, and the rows of the selected kind fill the list. A row names the item and why it waits, and opens it in the pane: a work item opens with the next step of the server pinned in the foot, and a lesson, a session flag or a session proposal opens the decision pane. The kinds are:

| Kind | Meaning |
| --- | --- |
| Edit blocked | An edit fell outside the recorded paths of a work item. Allow the paths or keep the block. |
| Blocked | A work item is blocked, with the first recorded issue. |
| Awaiting merge | Delegated work finished and waits for your merge decision. |
| Needs review | A work item cannot continue or finish until it is reviewed. |
| Lessons to accept | Proposed lessons await your acceptance or rejection. |
| Failure without a lesson | A failed outcome has no lesson and no later complete result. |
| Repeated failure | A failure type recurred after its lesson was accepted. |
| Agent follow up | An agent run needs a decision or could not complete a follow up step. |
| Session flags, Session proposals | Directions and proposals read from finished sessions wait for a decision. |
| Machine rules | An agent proposed a rule for the machine memory. |
| Too many rules, Rule without effect | A role holds more rules than its prompt carries, or an accepted rule did not prevent its failure. |
| Recording gap, Recording failed | Host events are missing or could not be written. |

The decision pane decides the item in place. A lesson needs a reason and keeps the triggers it was proposed with; Accept, Reject and Retire are in the foot, and Retire opens the review form. A flag takes an optional reason and no dialog; Confirm it when a record is missing and Dismiss it when nothing is missing. A proposal opens its form. The first letter of each decision works as a key while the focus is not in a field, and after each decision the next row opens by itself, so a run of decisions needs no return to the list. The shown flags can also be dismissed together with one reason.

Four tabs follow the kinds. Needs reconciliation lists the tool calls whose result the transcript holds, with one button that reconciles the call as the transcript suggests and a form for another result. Kickoff appears while a templated project has no approved requirements, with the open questions, the research still needed, the starter documents not yet filled and the phases, each with the action that advances it. Latest decisions shows the recent decisions with their outcome badges, and Scope blocks the recent blocked edits.

The `now` response carries the first 20 rows of each kind, so a snapshot shows every tab with its rows, and the live panel pages further rows.

## Plan

Plan shows the hierarchy of phases, epics, stories, tasks, research items, deliverables and workflows, built from the parent of each work item, as a tree under a head with the filters for type, state and text. Every node carries a state badge and a progress roll up of its descendants, and a timeline strip shows the phases in order. The tree scrolls under the head; the foot holds the progress of the plan and Add item, which creates a new item with its type and its parent. A node opens the work item in the pane.

## Work

Work is a board with one column per state or a list, chosen by a switch in the head beside the filters for subject, state, sprint and free text. Below 640 pixels the filters stay folded until you open them. Empty columns are hidden until you show them. The board keeps its columns beside an open pane and scrolls sideways inside its own region. The list has one row per work item with its state, type, subject, priority, issues and creation date, ordered by the Sort by select and the Descending order check. The filters, the format and the sort stay in the address, so a refresh keeps them. The foot counts the shown items.

A work item opens in the pane with the next step and its reason, the issues and the criteria that wait for your confirmation first, then the intended result, the completion criterion, the plan scope and the allowed paths, the dependencies and their reasons, the agent checks and delegated runs, a compact lineage graph with a list alternative, and the paged history, which stays closed until you open it. Sections without content are named in one sentence. Two selects change the state and the priority with a stated reason. The foot pins the next step and the actions Edit plan, Allow paths, Delegate, Request check and Comment; Edit plan is shown once when it is the next step. The delegation form asks for missing paths and the permission to act and saves them in the plan before it delegates, and Delegate is disabled with its reason for finished work and for a project without an agent host.

When the latest outcome check reports a criterion that no machine can confirm, the work item lists it under Needs your confirmation. Confirm writes your statement with user origin, and the next outcome check reads it as the evidence for that criterion. Each criterion is offered once.

Now has a Criteria to confirm tab with one row per work item. Confirm criteria in its foot opens one form that lists every such criterion, grouped by work item, with the reviewer's reason beside it. It offers the criteria the check reported as needing you. A criterion the check reported as unknown, because it found no evidence either way, is not offered: the missing evidence is for the assistant to attach, and the Stop notice asks it to. A criterion the check found met or unmet is never offered. Nothing is selected in advance. One statement is recorded for every selected criterion, in one step: if one of them is no longer open, nothing is recorded. A confirmation does not finish a work item. Request a new outcome check of each item; Done still needs that check to pass.

Moving a card cannot manufacture a result. Done requires an evidenced good outcome with complete completion, current supporting evidence, no unresolved execution and, where agent checks are configured, a current passing outcome check.

## Decisions

Decisions keeps its filters in a head above the rows: a search, the outcome, decisions that need review and hiding replaced decisions. Each row carries the outcome badge, the status and the review marker, and the foot counts the shown decisions. A decision opens in the pane, which compares the expected consequence with the observed outcome, then shows the alternatives, the uncertainty, the lessons considered and the evidence. A lineage view connects the requirement, the work item, the plan, the decision, the action and the outcome, and a toggle switches between the graph and a readable list. Each outcome belongs to the exact decision that was recorded, so an earlier failure stays visible after a successful revision.

## Learning

Learning has six tabs with their counts: Proposed lessons, Guards, Failures without a lesson, Instructions, Scope changes and Signals. The first four are rows; Scope changes and Signals keep their cards in a scrolling region, because each card links to several records. A proposed lesson opens the decision pane described under Now. Accepting a lesson can set its triggers: paths, keywords, a failure type and the agent roles. The triggers you set on acceptance replace the triggers of the lesson, and a field you leave empty stays empty.

A guard row names its recurrence count and its triggers, and the guard pane holds the lesson text, the triggers and every counted recurrence with its Reassess action, with Open the lesson and Retire in the foot, so a guard that keeps failing to prevent its failure is visible. Scope changes lists the path widenings made by an actor other than you, and Signals the records whose evidence needs attention.

### Instructions

The Instructions tab is one row per agent role, assistant, worker and reviewer, with its accepted rules, the rules in the prompt and the characters used. The role opens in the pane with what that role receives, and Edit the base text pinned in the foot:

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

## Sessions

Sessions reads the finished Claude Code and Codex sessions of this project. Collection runs when a session starts, for a few recent files within a short time limit, and in full with `project-memory sessions collect`. A session belongs to the project when its working folder is the project, when its identifier appears in the host receipts of the project, or when its tool calls name the project folder. The view has three tabs, Flags, Proposals and Digests, and the summary states the true number of open flags.

| Tab | Content |
| --- | --- |
| Flags | A message worded as an approval, a refusal, a correction or a choice, or an answer to a question, that no plan, decision, progress, requirement or lesson record followed within ten turns or one hour. Each flag has low confidence. A row opens the decision pane: Confirm it when a record is missing and Dismiss it otherwise, with an optional reason and no dialog, and the next flag opens by itself. The shown flags are dismissed together from the foot of the list with one optional reason; one refused flag refuses the whole batch. Both decisions are kept, and the view reports how many decided flags were confirmed. |
| Proposals | What a host proposed from one digest after `project-memory sessions distill`: a decision, a requirement, a lesson, a next action or a correction, with its confidence and the transcript lines it rests on. A row opens the decision pane, and Accept records it in the work item you select; reject records nothing. |
| Digests | One row per session with its last activity, messages, changed files, failed commands and open flags. The row opens its digest record. |

A digest is a versioned source of at most 20,000 characters. It holds the user messages, each cut to 1,000 characters, the failed commands with their exit codes, the changed files, the memory records written and the commits made in the time of the session, and it names the transcript line of each item. Credentials are redacted before anything is stored. Transcripts are never changed, and session content never enters the machine memory. `project-memory sessions off` switches reading off for the project.

An accepted lesson becomes a proposed lesson, which you then accept in Learning as any other. An accepted decision, requirement, next action or correction becomes a note of the selected work item that cites the digest, because those records carry fields and approvals that a digest cannot supply.

## Records

Records is rows in the frame. The kind, the search and the subject share one row of a head that stays in place, and the status, the work item, the two dates and the order wait in a fold behind More filters, which opens on its own when one of them is applied; below 640 pixels the whole box stays folded until you open it. The filters apply on change, typing keeps the focus while the results update, and Clear resets them. Each row shows the state, the kind, the subject, the work item and the date in one line; a work item shows its board state, the same word as in Plan and Work. All record kinds lists every kind as a group of its first five rows with Show all. The rows scroll under the head, the pager stays in the foot, and the count of the matches stands beside the title.

A row opens the record in the pane with its evidence, the records that refer to it, paged source bodies and a toggle that shows the exact captured text. Captured documents render a limited Markdown subset: headings, simple tables, lists, quotes, fenced code, emphasis and links to http and https addresses. Raw HTML is shown as text, and no image or script is loaded from a document. Full width hides the list so a long document reads at the width of both panes.

## Requirements

Requirements reads the current requirement text with its version, the evidence of its approval and the earlier revisions in one scrolling region, and Review requirements stays in the foot. The review form proposes a revision, which still needs an explicit approval with a reason and evidence.

## Architecture

Architecture draws the structure of the project as a graph that takes the height of the frame, under a head with the template label, the layer tabs and the filters, beside a side column that scrolls on its own with the selected item, the blocked chains, the item list, the legend and the notes. Below 1100 pixels the graph keeps a height of 320 pixels and the layout scrolls as one region. Nodes are sized by the number of lines and coloured by state. Three layers can be shown together, and a layer that holds nothing is not offered:

- **Code**: one node per directory that contains source files, from Python, JavaScript, TypeScript and Dart imports, with declared packages as separate nodes behind a toggle.
- **Workflows**: one node per exported n8n workflow in the project, with the services its nodes and credentials name, and a call edge to another exported workflow.
- **Authored**: systems, services, workflows, integrations, datasets, stakeholders, workstreams, deliverables and processes that somebody described rather than extracted. A proposed item has a dashed border and a Confirm button, because an item an agent authored stays proposed until you confirm it.

Selecting a node shows its files, the work items attached through plan paths, the matching lessons, its explicit links and its flags. "Open files" switches to the file level of one component, where files carry no work state, and a Back control returns to the component level. For an exported workflow the file level shows its nodes and connections with the triggers marked. The view states that imports are read statically and that dynamic loading is not detected. Credential names and identifiers from an export are shown; secrets are never read.

## Dependencies

Dependencies has two tabs and the same graph layout as Architecture. The work graph draws the dependency edges between work items, coloured by state, and marks the blocked chains in red. The package tab lists the declared packages with their ecosystem, requirement, group and manifest, together with the manifest issues and the flags for a package that is declared but not imported or imported but not declared. The package tab appears only in a project that declares packages. No package manager runs and no network lookup is made.

## Agents

Agents has the tabs Runs, Hosts and Follow ups. Runs is one row per run with its role, host, state, work item, changed files, review state and merge state; the delegation parts stay empty for an agent check, which changes no files. A row opens the run pane with its report, its criterion level findings and its diff summary, and Merge, Request review, Cancel and Discard in the foot where they apply. Hosts shows one card per configured host with whether it is installed, whether it is available, until when it is unavailable and the reason. Follow ups lists the runs that need a decision or could not complete a follow up step.

## Machine

Machine reads the memory of this computer, which sits above the projects on it. Its tabs are regions under the isolation notice, and its proposal and rule cards take at least 480 pixels of the region:

| Tab | Content |
| --- | --- |
| Proposals | The promotions an agent proposed from this project, with Accept and Decline. The acceptance form opens the text of the rule for correction before it is written. Decided proposals follow below. |
| Rules in force | Each promoted rule with its trigger words, its roles, the basis written at promotion, how many projects promoted it and Retire. |
| Retired rules | The rules you retired. They reach no prompt and stay readable. |
| Projects | The registry: the path, the template, the phase and the dates of every project on this computer. |

The view states the isolation rule in place: effectiveness stays in each project, the adoption count reports how many projects promoted a rule, and no outcome is combined across projects. The registry stays on this computer, it is not part of an export, and no agent reads it. A snapshot carries no machine response at all, so the view says so instead of showing an empty list.

A rule in force is not rewritten. Retire it and promote the corrected text, so the history of both stays readable. Only you write to the machine memory: an agent can propose a promotion and read the rules that were accepted.

## Hive

Hive is the shared record of agents that work on one problem together. Each swarm is a row with its state, its kind, its entry count, its blind phase and its agents. The row opens the swarm in the pane: the purpose, the agents with their phase, and the timeline of entries as a conversation, where answers and replies sit under their target and a challenge or a support is a labelled link. Two selects filter the timeline by move and by agent, and the address carries the swarm and both filters, so a copied address opens the same reading. Ask a question, Post an observation and Close the swarm stay in the foot of the pane. The purge of closed swarms sits in the foot of the list, with its refusal when the panel was started from inside an assistant session.

## Usage

Usage reads the usage ledger of this machine in one scrolling region: a note on how to collect usage and probe a host, one card per host with its windows, its cost, its limit state and its latest probe, and the routing decisions of the recent runs of the project. The ledger stays on the computer that holds it, so a snapshot carries no usage.

## Refreshing and saving

The panel polls the health endpoint one second after the previous poll ends while the tab is visible, so a change shows within two seconds, and it redraws only when the database revision changes. A live update keeps the selected view and its filters, the marked row, the scroll position of the view and of the pane, the keyboard focus and the text that you typed into a field, and an open form is never redrawn under you. A failed update shows Update failed at the foot of the rail and an alert, and both clear on the next successful poll.

Every save carries the session token and a request key created when the form opened, so submitting the same form twice does not create a second record. A save also carries the version you read. When somebody else changed the record first, the panel keeps your draft and offers to reload the saved version. Server messages appear in the alert area of the form.

## Offline export

An export embeds the same responses that the live panel requests, so the views read from the file and make no network request. The scope options are a work item, a subject and a date range, with `--include-bodies` for source text, `--replace` to overwrite an existing file and a record limit that fails explicitly instead of silently truncating. A scoped export omits the project wide views, opens on the first view of the rail that it holds, and marks the views that were not exported. A snapshot holds no sessions, machine or usage response, and its Records view filters and pages the embedded records in the browser. A full snapshot of a large project needs the `html` command with `--max-records`, because `view --output` stops at its lower record limit. An export cannot save changes and does not update.
