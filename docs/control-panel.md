# Control panel

The control panel is a local page served by `project-memory view` from the project database. It replaces the earlier workspace and work board. The panel is read only, live and exported alike: you read in it, and you decide in the chat.

```sh
project-memory view
project-memory view --output review.html --include-bodies --no-open
```

The live service binds to `127.0.0.1`, uses a random capability in its address, checks the request host and origin, and reads through a read only connection. The server refuses every write. The address and its credential stay in the private `.memory/viewer.json` file. Treat that address as a secret and do not share it.

## Deciding in the chat

The panel takes no decision. Where a decision belongs to you, the page says in a blue callout what to ask the assistant: accept or reject a lesson, confirm or dismiss a session flag, approve the requirements, change the phase, merge or discard delegated work, confirm a criterion, set the check of a focused problem, take part in a swarm, or change the base text of a role. The assistant records your decision with the `user_action` operation. It passes the receipt of your prompt and your exact words; Project Memory verifies the words against the hash that the hook captured, refuses a notification or a prompt of another session, stores your words as user evidence that no agent can write, and runs the same action the panel used to run, as you. A merge in production is allowed this way, because you asked for it.

## Layout

The panel is laid out as a Notion workspace in one fixed frame: the window never scrolls, and only the regions inside it scroll. A light sidebar on the left names the project, holds Search and lists the 14 views with an icon each:

| Section | Views |
| --- | --- |
| (no heading) | Now |
| Work | Plan, Work, Decisions |
| Knowledge | Records, Requirements, Learning, Sessions |
| System | Architecture, Dependencies, Agents, Machine, Hive, Usage |

System stays folded until you open it or work in one of its views. Under Now, Recent lists the last five pages you opened in this browser for this project; the list lives in the browser only and is empty in a private window. The sidebar shows no count of things to do, because nothing in the panel waits for you. Its foot names the lifecycle stage of the project with its change action and the state of the live connection: Live, Update failed, or the export time of a snapshot. The button beside the project name folds the sidebar away, and the menu button of the top bar brings it back.

Search, the key `/` and Command K (Control K on Windows and Linux) open quick find, a dialog that lists your recent pages and the views of the panel, and as you type the matching views, work items and records. The arrows move the selection, Enter opens it, and Escape closes the dialog. Its last result searches every record in Records.

A top bar holds the breadcrumbs of the page, the project, the section and the view with its icon, the time of the last change the panel saw, and the Activity button. Activity opens a menu with the work by state, the work in progress and the active agent runs from any view; Escape closes it and returns the focus to its button. Under the top bar each view has a page head: its icon, its title and one sentence that summarises it. Now and Requirements read in a centred column of 1,000 pixels, as a Notion page does; the databases use the full width. In a window lower than 760 pixels the icon is left out and the title is smaller, so the view keeps its height.

Most views are databases. A bar above the rows holds the views of the database as tabs and, on its right, the tools: Filter shows the filter chips under the bar, Sort, Group and Properties open a menu, and Search opens a search field that filters while you type. The state of the tools stays in the address, so a refresh or a copied address shows the same view. A table has a header with the icon and the name of each property, and a click on a header sorts by it. Grouped rows fold under their group heading. The title of a row opens the item, and the count of the rows and the pager stay pinned under the rows.

An item opens in the side peek, a page beside the database, so the list stays in sight and the opening row stays marked. The page leads with the properties of the item, each with its icon, then its content under headings, with callouts for what needs attention and toggles for long parts such as the history. A page with three or more sections carries its outline at the right edge: a dash per section, which names its heading on hover and scrolls to it on a click. The body of the page scrolls and its foot with the actions of the item stays pinned, so the primary action is in reach at a window height of 640 pixels. The bar of the side peek holds Close, Full width, Back and the previous and next row, which J and K reach as well. A link inside the page leads deeper and Back returns from it. Escape closes the side peek and returns the focus to the row that opened it. Full width hides the list and centres the page at the reading width of a Notion page; the same button or Escape brings the list back with its scroll position.

Below 1180 pixels one region shows at a time: the side peek covers the view, which keeps its scroll position, and Full width is not offered. Below 900 pixels the sidebar slides in over the page from the menu button of the top bar. On a phone every table row becomes a card whose properties carry their names, so no column hides behind a sideways scroll.

The panel follows the colour scheme of the computer, light or dark. Colours are tokens with a light and a dark value, and the status pills use the property colours of Notion. The contrast of every text colour on its background reaches 4.5 to 1 in the light theme, and every status pill reaches it in both themes; the test of the stylesheet measures them.

## The phase of the project

The foot of the sidebar states the phase under the label Lifecycle, so it is not confused with the phases of a plan: development or production. The phase decides who merges delegated work. In development the orchestrator may merge a run after a passing work review. In production every merge is a user action in the Agents view, and a merge over MCP is refused with the reason that the work is prepared and waiting.

Ask the assistant in the chat to change the phase, with your reason. The reason is required. The phase is recorded as a new version, so every earlier phase and its reason stay readable, and only you can record it: a write from an agent to the phase is refused. A change of phase also updates the phase of this project in the registry of the machine memory.

Inside an assistant session, `project-memory view` asks the browser to open the panel and does not print its address, because the address carries the access key of the panel.

Labels follow the project template. An engagement calls components "Stakeholders and workstreams", an automation calls them "Systems and workflows", and a product calls them "Components".

## Now

Now is the home page of the project and a digest of its work. Its sentence states how many work items are in progress, blocked and in review. Under it, a table for each place of the work lists the items with their state, their next step and their type:

| Part | Content |
| --- | --- |
| In progress | The work items in progress. |
| Paused | The blocked items and the items in review, with what each waits for: another item, a check or a review. |
| Ready to start | The work items that can start. |
| Done | The finished items, folded behind a toggle that shows the ten most recent. |
| Archived | The idle items that the expiry rule archived, folded behind a toggle that shows the ten most recent, with the state each returns to, its days without activity and its last activity. The part appears only when an item is archived. To restore one, ask in the chat. |
| Latest decisions | The recent decisions with their outcome, and a link to Decisions. |

A row opens its work item in the side peek. When the project follows a template, the kickoff checklist stands above the tables as a callout with the next step, until every kickoff step is done. When tool calls started without a recorded result, Needs reconciliation stands above the tables with the result that the transcript suggests for each call; ask the assistant in the chat to record the results.

Now asks nothing of you. Lessons, session flags and session proposals are read in Learning and Sessions, where each opens as a page that says how to decide it in the chat. J and K move to the next and the previous row while a page is open.

## Plan

Plan is a database of the plan tree with two views. Outline is a table of the phases, epics, stories, tasks, research items, deliverables and workflows, built from the parent of each work item: a row nests its sub-items under a fold, as Notion sub-items do, and carries its state, type, progress, number of acceptance criteria and owner. The first two levels start open. Phases is a gallery of the phases in order, each card with its state and its progress. Filter narrows the outline by type and state, which opens every fold, and Search narrows it by title. The count and the progress of the plan stay under the rows. A row or a card opens the work item in the side peek. The panel creates no work item: new work is planned in the chat.

## Work

Work is a database of the work items with two views. Table is the default: one row per work item with its title, state, type, priority, issues, next step, subject, sprint and creation date. Sort orders the rows by any property, Group groups them by state, type, priority, subject or sprint, Properties hides the columns you do not need, Filter narrows by subject, state, sprint and type, and Search reads the title, the intent and the plan. Without a sort of yours the rows follow the order of the states. Board is one column per state, tinted in the colour of the state, with ten cards at a time and Show more; empty columns are hidden until you show them, and the board keeps its columns beside an open side peek and scrolls sideways inside its own region.

A work item opens in the side peek as a page. Its properties come first: state, type, subject, priority, owner, the item it is part of, sprint, creation date and version. A callout states the next step and its reason, in red when the item is blocked. Headed sections follow: the intended result, the completion criterion, the acceptance criteria, the issues, the criteria that wait for your confirmation, the scope with its autonomy, next action, reason and allowed paths, the dependencies and their reasons, the agent checks and delegated runs, and a compact lineage graph with a list alternative. The paged history is a toggle that stays closed until you open it. Sections without content are named in one sentence. When the next step is another page, such as an unfinished prerequisite or a check, the foot holds the button that opens it. A plan, its state and priority, its allowed paths, a delegation, a check or a comment are asked for in the chat.

When the latest outcome check reports a criterion that no machine can confirm, the work item lists it under Criteria no machine can confirm, with no count. Confirm it in the chat when you choose to: your statement is recorded with user origin, and the next outcome check reads it as the evidence for that criterion. Each criterion is offered once.

A criterion the check reported as unknown, because it found no evidence either way, is not offered: the missing evidence is for the assistant to attach, and the Stop notice asks it to. A criterion the check found met or unmet is never offered. A confirmation does not finish a work item. Request a new outcome check; Done still needs that check to pass. When you say in the chat that a work item is finished, the assistant can close it on your words instead.

Moving a card cannot manufacture a result. Done requires an evidenced good outcome with complete completion, current supporting evidence, no unresolved execution and, where agent checks are configured, a current passing outcome check.

## Decisions

Decisions is a database with three views: All decisions, Needs review and Current, which hides the decisions that a revision replaced. Its table carries the outcome, the review marker, the status, the work item, who recorded the decision and its date, and it sorts, groups by outcome, review, status or work item, and hides properties. Filter narrows the table by outcome and Search reads the decision text. A decision opens in the side peek as a page: its properties, a callout with the reasons it needs review, then the decision, its reason, when to reconsider it and its uncertainty under headings, the expected consequence beside the observed outcome, the alternatives, the lessons considered and the evidence. A lineage view connects the requirement, the work item, the plan, the decision, the action and the outcome, and a toggle switches between the graph and a readable list. Each outcome belongs to the exact decision that was recorded, so an earlier failure stays visible after a successful revision.

## Learning

Learning has six views with their counts: Proposed lessons, Guards, Failures without a lesson, Instructions, Scope changes and Signals. The first four are rows; Scope changes and Signals keep their cards in a scrolling region, because each card links to several records. A proposed lesson opens the decision pane described under Now. Accepting a lesson in the chat can set its triggers: paths, keywords, a failure type and the agent roles. The triggers you set on acceptance replace the triggers of the lesson, and a field you leave empty stays empty.

A guard row names its recurrence count and its triggers, and the guard pane holds the lesson text, the triggers and every counted recurrence, with a callout that says how to reassess one in the chat and Open the lesson in the foot, so a guard that keeps failing to prevent its failure is visible. Scope changes lists the path widenings made by an actor other than you, and Signals the records whose evidence needs attention.

### Instructions

The Instructions tab is one row per agent role, assistant, worker and reviewer, with its accepted rules, the rules in the prompt and the characters used. The role opens in the pane with what that role receives, and a callout says how to change its base text in the chat:

| Part | Meaning |
| --- | --- |
| Base text | The instructions in force for the role, either the file shipped with Project Memory or the version you saved in this project. |
| Character budget | How much of the budget for rules this prompt uses. The budget is 600 characters for the assistant, 1,200 for the worker and 900 for the reviewer. |
| Rules in force | The accepted rules composed into the prompt now, each with the number of runs that carried it, the verdicts of those runs, the recurrences of its failure type before and after acceptance, and its state of effective, unproven or ineffective. |
| Rules that wait for a matching run | Accepted rules of the role whose paths, keywords or failure type match only some runs. They are composed into the runs they match. |
| Rules left out | Accepted rules that the prompt could not carry, with the reason: the limit of eight rules for one role, the character budget, a rule that is longer than the whole budget of its role, or a rule the prompt already carries among its constraints. Nothing is dropped in silence. |

A lesson becomes a rule when you name one to three roles as you accept it. A lesson without a role stays a guard: it is reported when a decision matches it and it is not composed into any prompt.

A change you give in the chat saves a new version of the base text of that role. Every earlier version stays, so returning to an earlier text means saving that text again. Only you can save it: the source key of the base text is reserved, so a write from an agent is refused and only a version whose author is you is in force. The rules are not edited here, because they come from lessons that you accepted.

The counts are counts only. No model judges a rule, pending and uncertain runs stay out of the ratio, and a rule with few runs is reported as unproven with its denominators rather than as an improvement.

## Sessions

Sessions reads the finished Claude Code and Codex sessions of this project. Collection runs when a session starts, for a few recent files within a short time limit, and in full with `project-memory sessions collect`. A session belongs to the project when its working folder is the project, when its identifier appears in the host receipts of the project, or when its tool calls name the project folder. The view has three tabs, Flags, Proposals and Digests, and the summary states the true number of open flags.

| Tab | Content |
| --- | --- |
| Flags | A message worded as an approval, a refusal, a correction or a choice, or an answer to a question, that no plan, decision, progress, requirement or lesson record followed within ten turns or one hour. Each flag has low confidence. A row opens the flag as a page: confirm it in the chat when a record is missing and dismiss it otherwise, with an optional reason. Several flags can be dismissed together with one reason; one refused flag refuses the whole batch. Both decisions are kept, and the view reports how many decided flags were confirmed. |
| Proposals | What a host proposed from one digest after `project-memory sessions distill`: a decision, a requirement, a lesson, a next action or a correction, with its confidence and the transcript lines it rests on. A row opens the proposal as a page. Accepted in the chat, it is recorded in the work item you name; rejected, it records nothing. |
| Digests | One row per session with its last activity, messages, changed files, failed commands and open flags. The row opens its digest record. |

A digest is a versioned source of at most 20,000 characters. It holds the user messages, each cut to 1,000 characters, the failed commands with their exit codes, the changed files, the memory records written and the commits made in the time of the session, and it names the transcript line of each item. Credentials are redacted before anything is stored. Transcripts are never changed, and session content never enters the machine memory. `project-memory sessions off` switches reading off for the project.

An accepted lesson becomes a proposed lesson, which you then accept in Learning as any other. An accepted decision, requirement, next action or correction becomes a note of the selected work item that cites the digest, because those records carry fields and approvals that a digest cannot supply.

## Records

Records is a database with one view for each kind of record. All, Work items, Decisions, Documents, Sources, Lessons and All events stand as tabs, and the other kinds wait in the menu at the end of the tabs. Filter shows chips for the subject, the status, the work item and the two dates, with Clear filters, and Sort orders the rows by date, newest or oldest first, or by title. The filters apply on change, and typing in Search keeps the focus while the rows update. The table carries the title, state, kind, subject, work item and date of each record; a work item shows its board state, the same word as in Plan and Work. All is one table grouped by kind, with the five latest records of each kind and Show all beside the heading of a group. The pager stays under the rows, and the count of the matches stands in the sentence of the page.

A row opens the record in the side peek as a page: its state, kind, subject, work item, date and identifier as properties, the short fields as further properties and the long ones as headed text, then its evidence, the records that refer to it, paged source bodies and a toggle that shows the exact captured text. Captured documents render a limited Markdown subset: headings, simple tables, lists, quotes, fenced code, emphasis and links to http and https addresses. Raw HTML is shown as text, and no image or script is loaded from a document. Full width centres a long document at the reading width of a page.

## Requirements

Requirements reads as one page: its status, version, approver, date and reason as properties, a callout that the recorded requirements govern decisions without proving that the project covers every need, the numbered requirements, the evidence of the approval and a table of the earlier versions. A callout says how to approve or revise the requirements in the chat. A revision still needs an explicit approval with a reason and evidence.

## Architecture

Architecture draws the structure of the project as a graph that takes the height of the frame, under a head with the template label, the layer tabs and the filters, beside a side column that scrolls on its own with the selected item, the blocked chains, the item list, the legend and the notes. Below 1100 pixels the graph keeps a height of 320 pixels and the layout scrolls as one region. Nodes are sized by the number of lines and coloured by state. Three layers can be shown together, and a layer that holds nothing is not offered:

- **Code**: one node per directory that contains source files, from Python, JavaScript, TypeScript and Dart imports, with declared packages as separate nodes behind a toggle.
- **Workflows**: one node per exported n8n workflow in the project, with the services its nodes and credentials name, and a call edge to another exported workflow.
- **Authored**: systems, services, workflows, integrations, datasets, stakeholders, workstreams, deliverables and processes that somebody described rather than extracted. A proposed item has a dashed border and a callout, because an item an agent authored stays proposed until you confirm it in the chat.

Selecting a node shows its files, the work items attached through plan paths, the matching lessons, its explicit links and its flags. "Open files" switches to the file level of one component, where files carry no work state, and a Back control returns to the component level. For an exported workflow the file level shows its nodes and connections with the triggers marked. The view states that imports are read statically and that dynamic loading is not detected. Credential names and identifiers from an export are shown; secrets are never read.

## Dependencies

Dependencies has two tabs and the same graph layout as Architecture. The work graph draws the dependency edges between work items, coloured by state, and marks the blocked chains in red. The package tab lists the declared packages with their ecosystem, requirement, group and manifest, together with the manifest issues and the flags for a package that is declared but not imported or imported but not declared. The package tab appears only in a project that declares packages. No package manager runs and no network lookup is made.

## Agents

Agents has the views Runs, Hosts and Follow ups. Runs is a table with one row per run: its role and host, its state, its review state, its merge state, its work item, its changed files and its start; the review, the merge and the changed files describe delegated work and stay empty for an agent check, which changes no files. A row opens the run pane with its report, its criterion level findings and its diff summary, and a callout that says how to merge, discard, cancel or review delegated work again in the chat. Hosts shows one card per configured host with whether it is installed, whether it is available, until when it is unavailable and the reason. Follow ups lists the runs that need a decision or could not complete a follow up step.

## Machine

Machine reads the memory of this computer, which sits above the projects on it. Its tabs are regions under the isolation notice, and its proposal and rule cards take at least 480 pixels of the region:

| Tab | Content |
| --- | --- |
| Proposals | The promotions an agent proposed from this project. A callout says how to accept or decline one in the chat, where you can correct its text before it is written. Decided proposals follow below. |
| Rules in force | Each promoted rule with its trigger words, its roles, the basis written at promotion and how many projects promoted it. You retire a rule in the chat. |
| Retired rules | The rules you retired. They reach no prompt and stay readable. |
| Projects | The registry: the path, the template, the phase and the dates of every project on this computer. |

The view states the isolation rule in place: effectiveness stays in each project, the adoption count reports how many projects promoted a rule, and no outcome is combined across projects. The registry stays on this computer, it is not part of an export, and no agent reads it. A snapshot carries no machine response at all, so the view says so instead of showing an empty list.

A rule in force is not rewritten. Retire it and promote the corrected text, so the history of both stays readable. Only you write to the machine memory: an agent can propose a promotion and read the rules that were accepted.

## Hive

Hive is the shared record of agents that work on one problem together. Each swarm is a row with its state, its kind, its entry count, its blind phase and its agents. The row opens the swarm in the pane: the purpose, the agents with their phase, and the timeline of entries as a conversation, where answers and replies sit under their target and a challenge or a support is a labelled link. Two selects filter the timeline by move and by agent, and the address carries the swarm and both filters, so a copied address opens the same reading. An open swarm says in a callout how to answer a question, ask one, post an observation or close the swarm in the chat. Closed swarms are purged in the chat as well.

## Usage

Usage reads the usage ledger of this machine in one scrolling region: a note on how to collect usage and probe a host, one card per host with its windows, its cost, its limit state and its latest probe, and the routing decisions of the recent runs of the project. The ledger stays on the computer that holds it, so a snapshot carries no usage.

## Refreshing and saving

The panel polls the health endpoint one second after the previous poll ends while the tab is visible, so a change shows within two seconds, and it redraws only when the database revision changes. A live update keeps the selected view and its filters, the marked row, the scroll position of the view and of the pane, the keyboard focus and the text that you typed into a field, A failed update shows Update failed at the foot of the sidebar and an alert, and both clear on the next successful poll.

The panel sends no write. A decision that the assistant records with `user_action` carries its own request key, so a retry does not create a second record, and the panel shows the change within two seconds.

## Offline export

An export embeds the same responses that the live panel requests, so the views read from the file and make no network request. The scope options are a work item, a subject and a date range, with `--include-bodies` for source text, `--replace` to overwrite an existing file and a record limit that fails explicitly instead of silently truncating. A scoped export omits the project wide views, opens on the first view of the sidebar that it holds, and marks the views that were not exported. A snapshot holds no sessions, machine or usage response, and its Records view filters and pages the embedded records in the browser. A full snapshot of a large project needs the `html` command with `--max-records`, because `view --output` stops at its lower record limit. An export does not update.
