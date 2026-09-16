# Control panel

The control panel is a local page served by `project-memory view` from the project database. It replaces the earlier workspace and work board. The same page renders an offline export, where the editing controls are absent.

```sh
project-memory view
project-memory view --output review.html --include-bodies --no-open
```

The live service binds to `127.0.0.1`, uses a random capability in its address, checks the request host and origin, and reads through a read only connection. Writes need the same origin, a JSON content type and the session token. The address and its credential stay in the private `.memory/viewer.json` file. Treat that address as a secret and do not share it.

## Layout

A navigation rail on the left groups the ten views in four sections. Below 900 pixels it collapses into a top menu. The top bar carries the project name, the live state and a search box. Records open in a drawer on the right, which Escape closes and which returns focus to the control that opened it. The page stays usable down to 320 pixels; only graphs, tables and code blocks scroll inside their own container.

| Section | Views |
| --- | --- |
| Delivery | Now, Plan, Work |
| Structure | Architecture, Dependencies |
| Oversight | Decisions, Learning, Agents |
| Reference | Records, Requirements |

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

Learning shows the accepted guards with their triggers and their recurrence counts, so that a guard that keeps failing to prevent its failure is visible. Below them are the proposed lessons with Accept, Reject and Retire. Accepting a lesson can set its triggers: paths, keywords and a failure type. The triggers you set on acceptance replace the triggers of the lesson, and a field you leave empty stays empty. The view also lists failures without a lesson, the path widenings made by an actor other than you, and the records whose evidence needs attention.

## Agents

Agents shows one card per configured host with whether it is installed, whether it is available, until when it is unavailable and the reason. The runs table lists the role, the host, the state, the work item, the number of changed files, the review state and the merge state. A run opens with its report, its criterion level findings, its diff summary and the buttons that apply: Merge, Discard and Cancel. The delegation columns stay empty for an agent check, which changes no files.

## Records

Records searches across every kind with the existing filters for subject, status, work item, dates and order, and pages the result. A record opens in the drawer with its evidence, the records that refer to it, paged source bodies and a toggle that shows the exact captured text. Captured documents render a limited Markdown subset: headings, simple tables, lists, quotes, fenced code, emphasis and links to http and https addresses. Raw HTML is shown as text, and no image or script is loaded from a document.

## Requirements

Requirements lists the current requirement text with its version and the evidence of its approval, together with the earlier revisions. The review form proposes a revision, which still needs an explicit approval with a reason and evidence.

## Refreshing and saving

The panel polls the health endpoint every two seconds while the tab is visible and redraws only when the database revision changes. Filters, the selected view, an open drawer, the scroll position and keyboard focus survive a refresh, and an open form is never redrawn under you.

Every save carries the session token and a request key created when the form opened, so submitting the same form twice does not create a second record. A save also carries the version you read. When somebody else changed the record first, the panel keeps your draft and offers to reload the saved version. Server messages appear in the alert area of the form.

## Offline export

An export embeds the same responses that the live panel requests, so the views read from the file and make no network request. The scope options are a work item, a subject and a date range, with `--include-bodies` for source text, `--replace` to overwrite an existing file and a record limit that fails explicitly instead of silently truncating. A scoped export omits the project wide views, because they would list records outside the scope, and the panel states which views were not exported. An export cannot save changes and does not update.
