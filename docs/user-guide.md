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

Capturing a document does not approve its contents. A recorded plan does not authorise work. A hook records that something happened; only the assistant can record what it meant.

## Follow the work in the control panel

Run `project-memory view` in the project folder, or ask the assistant to open Project Memory. Start on **Now**: it states the project position in one sentence, lists the work in progress and the blocked work, and shows an attention list of the items that need you. Every entry opens the record or the work item behind it.

Use **Plan** for the hierarchy of phases and items, **Work** for the board and the work detail, and **Learning** for the lessons that await your acceptance. The [control panel guide](control-panel.md) describes every view.

## Keep the records honest

- Read the conditions and the exceptions of a proposed lesson before you accept it. An accepted lesson with a trigger becomes a guard, and later decisions must consider it explicitly.
- When an edit is blocked because it falls outside the recorded paths, decide whether the scope should grow. Use Allow paths to extend it with a reason, or leave the block in place and ask for a plan revision.
- When a work item is blocked, open it and read the issues. A blocked chain is highlighted in the Dependencies view.
- A failed outcome stays visible after a later success. Do not rewrite an earlier record to match a newer interpretation.

## Reopen the panel and read a snapshot

```sh
project-memory view
```

If you used the published command without installing the CLI permanently, use the same version:

```sh
uvx project-memory-mcp@0.6.0b1 view
```

Opening `memory_module/viewer.html` directly shows an internal template with the launch command, not your records.

| What you see | What it means |
| --- | --- |
| **Live** | The panel is connected and checks for saved changes every two seconds while the tab is visible. |
| **Update failed** | A refresh did not arrive. The panel keeps the last loaded content and retries automatically. |
| **Updates are unavailable** | The local service has stopped. Run the opening command again. |
| **Snapshot from a time** | You opened an offline export. It does not update and it cannot save changes. |

The local service stops after ten minutes without a request, and reopening it preserves every record. For a portable copy:

```sh
project-memory view --output review.html --include-bodies --no-open
```

An existing file is protected unless you add `--replace`. Check the contents of an export before you share it, because it contains the project evidence.

## When something is missing

Previously captured Markdown files refresh at session start and at session stop. A file that changed during a task may not be captured yet; ask the assistant to refresh it with `memory_write sync`, or run `project-memory sync`. New or moved files need to be selected explicitly, because the hooks do not crawl the project.

If recorded work seems to be missing, run `project-memory doctor`. It checks the database, starts the installed MCP process, and reports the configured clients, the observed hook events and the unconfirmed tool calls. Reloading the page cannot create a decision or an outcome that was never recorded.

If a host event could not be written, Project Memory keeps a failure file beside the database and reports the count. The next session start recovers those events as recording gaps, which the assistant must inspect and acknowledge explicitly.
