# Use Project Memory

Set up Project Memory once in each project using the [installation instructions](../README.md#install-and-connect). Setup opens the workspace in your browser. Start a new assistant session after setup so it loads the connection and hooks.

There is no separate HTML installation and no frontend to rebuild after each task. Your assistant and the workspace use the same project records.

## Work with your assistant

Explain the result you want, the constraints and what would count as complete. For example:

> Keep both supported import formats. Record the plan, investigate the failure and show the outcome on the work board.

The assistant records the plan, relevant evidence, decisions and outcomes. You can keep working in the conversation and use the workspace to follow progress. Tell the assistant when priorities change; it should preserve the earlier choice and record what changed and why.

You still decide whether to approve proposed requirements or lessons. Capturing a document does not approve its contents. Hooks record observed activity; the assistant must explain its meaning and assess outcomes. A connected viewer does not prove that this recording is complete.

## Follow work in the workspace

| To do this | Open this |
| --- | --- |
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
uvx project-memory-mcp@0.5.0b8 view
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

Project records stay in the local database. Keep the live address private. Use an explicit offline export when you need a portable copy, and check its contents before sharing it. [Setup and lifecycle](setup.md#live-viewer-and-offline-exports) covers exports, backups and upgrades.
