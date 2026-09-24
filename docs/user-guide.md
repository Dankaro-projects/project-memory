# User guide

This guide follows one project from the first conversation to delivery. Install Project Memory once per project using the [installation instructions](../README.md#install), or create a new project with `project-memory init`. Start a new assistant session after setup so that the client loads the tools and the hooks.

You do not install a separate interface and you never rebuild a frontend. The assistant and the control panel use the same records.

## Create the project

```sh
project-memory init ~/projects/lead-intake --template automation --client claude
```

Choose `product` for a software product, `engagement` for a consulting engagement and `automation` for a workflow automation. The command creates the folder, initialises git, writes the starter documents, connects the client and records the phases of the template as work items. Repeat it safely: an existing document is never overwritten and an existing phase is never duplicated.

To add a template to a project that already uses Project Memory, run the same command against that folder. To work without a template, use `project-memory setup` and plan the work items yourself.

## Answer the kickoff questions

Ask the assistant to start the kickoff. It reads `memory_get kickoff`, which returns the template, the phases, the open questions, the research still needed and the starter documents that are still empty.

The assistant asks you the open questions, for example:

> Which process should be automated, and how does it run today? What starts it, and how often does it run? Which systems and accounts does it use?

It records your answers as a note that names the questions it answers, fills the starter documents with those answers and captures each document as evidence. It should not invent facts about your project. Where an answer is unknown, it records a research work item instead of a guess.

When the questions are answered and the documents are filled, the assistant proposes requirements. Read them, then approve them explicitly. Approval is a separate act with its own reason and evidence, and only you perform it. The kickoff checklist disappears from the Now view once the requirements baseline exists.

## Work day to day

Explain the result you want, the constraints and what would count as complete. For example:

> Keep both supported import formats. Record the plan, investigate the failure and show the outcome in the control panel.

The assistant records a work item with its objective, its completion criterion, the next action, the scope and the files it may change. During the work it records the decisions it takes with their evidence, their alternatives, their uncertainty and the consequence it expects. After acting it records what actually happened, including failures. When priorities change, tell it: the earlier choice stays in the history and the change is recorded with its reason.

You decide the things that need a person:

| Your decision | Where |
| --- | --- |
| Approve or revise the requirements | Requirements view, or the assistant asks you |
| Accept, reject or retire a proposed lesson | Learning view |
| Widen the allowed paths of a work item | Work detail, Allow paths |
| Grant autonomy for delegated work | Work detail, Edit plan |
| Merge or discard delegated work | Agents view |
| Confirm or dismiss a flagged direction, accept or reject a distilled proposal | Sessions view |

Capturing a document does not approve its contents. A recorded plan does not authorise work. A hook records that something happened; only the assistant can record what it meant.

## Follow the work in the control panel

Run `project-memory view` in the project folder, or ask the assistant to open Project Memory. Start on **Now**: it states the project position in one sentence and lists the work in progress, the paused work, the work ready to start and the finished work, each item with its next step. Every row opens the work item behind it as a page beside the list.

Use **Plan** for the outline of phases and items, **Work** for the table and the board of work items, and **Learning** for the lessons and their guards. The panel is read only. When a page says that a decision is yours, tell the assistant in the chat: it records your decision with your exact words, which Project Memory checks against the prompt you sent. The [control panel guide](control-panel.md) describes every view.

## Keep the records honest

- Read the conditions and the exceptions of a proposed lesson before you accept it. An accepted lesson with a trigger becomes a guard, and later decisions must consider it explicitly.
- When an edit is blocked because it falls outside the recorded paths, decide whether the scope should grow. Use Allow paths to extend it with a reason, or leave the block in place and ask for a plan revision.
- When a work item is blocked, open it and read the issues. A blocked chain is highlighted in the Dependencies view.
- A failed outcome stays visible after a later success. Do not rewrite an earlier record to match a newer interpretation.

## Start a fresh session

A long session costs more with every prompt, because the whole context is read again. When the context of a session passes 250,000 tokens, the prompt hook says so once, and again after each further 50,000 tokens. Change the threshold with `project-memory sessions on --hint-tokens 400000`.

Before you start a fresh session, run:

```sh
project-memory handoff
```

It reports `ready`, or it lists what is not yet recorded: work in progress whose next action is older than the latest activity of the session, decisions without an outcome, captured documents that changed, Markdown files the session changed that no record cites, and flagged directions that wait for you. Ask the assistant to record the gaps, then start the new session.

A new session receives a short summary of the three latest earlier sessions of the project: the digest ids, the dates, the counts, the latest commit and the work items each session wrote to. The summary never quotes a message; the assistant reads a digest with `memory_get record` when it needs the content. To turn what a session decided into records, run `project-memory sessions distill` and decide the proposals in the Sessions view.

## Reopen the panel and read a snapshot

```sh
project-memory view
```

If you used the published command without installing the CLI permanently, use the same version:

```sh
uvx project-memory-mcp@0.6.0b14 view
```

Opening `memory_module/viewer.html` directly shows an internal template with the launch command, not your records.

| What you see | What it means |
| --- | --- |
| **Live** | The panel is connected and checks for saved changes every second while the tab is visible. |
| **Update failed** | A refresh did not arrive. The panel keeps the last loaded content and retries automatically. |
| **Updates are unavailable** | The local service has stopped. Run the opening command again. |
| **Snapshot from a time** | You opened an offline export. It does not update and it cannot save changes. |

The local service stops after ten minutes without a request, and reopening it preserves every record. For a portable copy:

```sh
project-memory view --output review.html --include-bodies --no-open
```

An existing file is protected unless you add `--replace`. Check the contents of an export before you share it, because it contains the project evidence.

## Read the records in Obsidian

The control panel answers the question of the day: what is in progress, what is paused and what comes next. To read across time instead, write the records as a folder of Markdown notes and open them in Obsidian:

```sh
project-memory export --obsidian ~/Documents/Vault
```

The command writes one note per record into the folder `Project Memory` inside that vault, and a later run reuses the folder without the option. A work item is a folder that holds its own decisions, outcomes and plans; sources, lessons and requirement revisions sit in shared folders beside it. Each note carries the record identifier, the kind, the state, the subject and the date in its frontmatter, and links to its work item and its evidence as wikilinks, so the graph, the backlinks and the search of Obsidian work on your project history.

| Option | What it does |
| --- | --- |
| `--include-receipts` | Adds a note for every host receipt. They are tool observations, so they are left out by default. |
| `--full` | Rewrites every note instead of only the records that changed. |
| `--hook off` | Stops the session stop hook from refreshing the vault. `--hook on` starts it again. |

The export runs in one direction. The database stays the source of truth, and a note edited inside the managed folder is lost on the next run. Keep a thought of your own in a file outside that folder and capture it with `memory_write document`, which stores it as evidence with a version.

The export owns the folder `Project Memory` and writes or removes nothing outside it. Inside it, only a file that carries the marker of this project in its frontmatter is replaced or removed; any other file is kept and named in the report. A folder that already holds files without that marker refuses the run, so a mistyped path cannot overwrite an unrelated folder.

After a session that wrote records, the session stop hook starts the refresh as a separate process and does not wait for it, because a full run does not fit the hook budget.

## When something is missing

Previously captured Markdown files refresh at session start and at session stop. A file that changed during a task may not be captured yet; ask the assistant to refresh it with `memory_write sync`, or run `project-memory sync`. New or moved files need to be selected explicitly, because the hooks do not crawl the project.

If recorded work seems to be missing, run `project-memory doctor`. It checks the database, starts the installed MCP process, and reports the configured clients, the observed hook events and the unconfirmed tool calls. Reloading the page cannot create a decision or an outcome that was never recorded.

If a host event could not be written, Project Memory keeps a failure file beside the database and reports the count. The next session start recovers those events as recording gaps, which the assistant must inspect and acknowledge explicitly.
