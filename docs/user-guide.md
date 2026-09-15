# Use Project Memory

Set up Project Memory once in each project using the [installation instructions](../README.md#install). Setup opens the workspace in your browser. Start a new assistant session after setup so it loads the connection and hooks.

There is no separate HTML installation and no frontend to rebuild after each task. Your assistant and the workspace use the same project records.

## Work with your assistant

Explain the result you want, the constraints and what would count as complete. For example:

> Keep both supported import formats. Record the plan, investigate the failure and show the outcome on the work board.

The assistant records the plan, relevant evidence, decisions and outcomes. You can keep working in the conversation and use the workspace to follow progress. Tell the assistant when priorities change; it should preserve the earlier choice and record what changed and why.

You still decide whether to approve proposed requirements or lessons. Capturing a document does not approve its contents. Hooks record observed activity; the assistant must explain its meaning and assess outcomes. A connected viewer does not prove that this recording is complete.

## Follow work in the workspace

| To do this | Open this |
| --- | --- |
| Review blocked work, proposed lessons and recording gaps | **Needs attention**. Open the relevant record, then review its evidence. |
| Select a reusable method | **Skills**. Import a skill or inspect a discovered project skill, then select it for existing work. |
| Understand relationships or plan a solution | **Project map**. Select work, then choose Relationships, Workflow or Architecture. |
| Follow progress, blockers and sprint work | **Work board**. Open a card for its plan, decisions, outcomes and next action. |
| Understand why an approach was chosen | **Decisions**. Open the record for evidence, alternatives, uncertainty and revisions. |
| Read captured project documents | **Documents**. Edit the original file when its contents need changing. |
| See evidence that may need checking | **Evidence drift**. Ask the assistant to inspect the current source before reusing it. |
| Understand missing records | The **Recording checks** notice, when present. Ask the assistant to assess the listed gaps. |

The workspace also lets you create actions and sprints, revise plans and add comments. Saving a plan does not itself start an assistant or authorise external work. These interactive controls require beta 5 or newer; earlier viewers are read-only.

## Reopen the workspace

Ask your assistant to **open Project Memory**, or run this in the project folder:

```sh
project-memory view
```

If you used the published one-command setup without installing the CLI permanently, use the same package version:

```sh
uvx project-memory-mcp@0.5.0b9 view
```

For a development checkout, use the command from its [development installation instructions](workspace-agents.md#install-this-development-version). Opening `memory_module/viewer.html` directly opens an internal template, not your records.

## Understand updates

| What you see | What it means and what to do |
| --- | --- |
| **Live** | The viewer is connected and checks for saved changes every two seconds while visible. It displays what has been recorded. |
| **Updates are unavailable** | The local service is unreachable. Run the opening command again. The viewer retains its last loaded display. |
| **Snapshot** | You opened an offline export. It does not update or save changes. Open the live workspace for current records. |
| **Open Project Memory** when opening the source template | Use the opening command. Changing browser settings is unnecessary. |

The local viewer stops after ten minutes without requests. Reopening it preserves your records. You may bookmark its local address, but the opening command is the recovery path if the service has stopped or an upgrade changes the address.

Previously selected Markdown files refresh at session start and stop. A file edited during a task may not yet appear in memory; ask the assistant to refresh it. New or moved files need to be selected. If recorded work is missing, ask the assistant to check recording and the connection with `project-memory doctor`; refreshing the page cannot create missing decisions or outcomes.

If a background refresh fails, the workspace shows **Update failed** and keeps the warning until the visible data reloads successfully. It retries automatically while open. A working connection alone does not clear a failed detail or history update.

Project records stay in the local database. Keep the live address private. Use an explicit offline export when you need a portable copy, and check its contents before sharing it. [Setup and lifecycle](setup.md#live-viewer-and-offline-exports) covers exports, backups and upgrades.

## Overview, skills and diagrams

Beta 9 includes these controls.

The viewer opens on **Overview**, showing current work, ready actions, recent decisions and items that need attention. Use **Work**, **Decisions**, **Knowledge** and **Activity** in the sidebar to reach their related views. For example, **Knowledge → Documents** opens captured documents, and **Work → Project map** opens diagrams.

Open a work item to see its objective, next action, completion criterion and history. Choose **List** on the board for a compact alternative. Decisions compare the expected consequence with the outcome recorded for that choice; **Earlier decision** and **Later revision** follow recorded changes. A later success does not replace an earlier failed result.

Use **Expand** for longer reading, or follow a document's **On this page** outline. **Read original text** retains the exact captured document. Record views offer **Summary** and **Table** formats. Conditions, exceptions and review states remain in the full records. These views update from saved records without rebuilding the frontend.

**Skills** discovers project folders under `.agents/skills`, `.claude/skills` and `.codex/skills`. Import a folder, ZIP or `SKILL.md` to keep a project copy. A folder or ZIP preserves supporting files. Imported packages live in the project's SQLite database; they are not installed globally or activated in your assistant automatically. Select a skill from its inspector, choose the work and explain why it applies. The assistant reads the selected instructions when needed and reports actual use with evidence. Selection alone does not prove use.

You can replace an imported skill by selecting its existing version in the import form. Earlier versions and selections remain available. Changed or missing local files appear on affected selections without rebuilding the page. Open the selected skill to read the original package. Release a selection when it no longer applies.

**Project map** has three views. Relationships displays recorded evidence, choices and revisions; expand a node to follow another part of the history. Workflow describes process steps and handoffs. Architecture describes components and their connections. Add nodes, optionally link existing project records, then choose a relationship type and explain the connection. Proposed, confirmed and retired states remain explicit. A drawn dependency does not change the work board's prerequisites or start an agent.

Click a node to open its details. Drag nodes or use arrow keys while a node has keyboard focus; use the zoom and Fit controls for larger diagrams. Positions are saved in that browser. The node and relationship records remain in the project database, with earlier versions preserved. Offline exports can display the included diagrams and recorded relationships but cannot save edits.

**Needs attention** provides access to proposed lessons and complete requirement revisions. Read the conditions and exceptions before accepting a lesson. Acceptance is rejected when its evidence is stale. A concurrent edit retains your draft; use Reload current version to review the latest data before submitting a new change.
