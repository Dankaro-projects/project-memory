# Project Memory

Project Memory keeps the plan, the evidence, the decisions, the outcomes and the accepted lessons of one project in a local SQLite database. Your assistant reads and writes those records through three MCP tools. You follow the same records in a local control panel, and you decide what is approved, accepted and merged.

It works for a software product, a consulting engagement and a workflow automation built in n8n. Work items can be code, documents, deliverables or exported workflows.

**Public beta.** Python 3.11 or newer is required. The runtime uses only the Python standard library. There is no telemetry, no hosted database and no account. Agent checks and delegated work run through the Codex or Claude Code CLI that you already have installed, and they consume that account's usage.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run this inside the project you want to remember:

```sh
uvx project-memory-mcp@0.6.0b7 setup --client codex --trust
```

For Claude Code, replace `--client codex` with `--client claude`. Setup preserves existing records and settings, connects the MCP server and the lifecycle hooks, and opens the control panel. `--trust` approves the project integration in that client; omit it to approve the connection yourself. Start a new assistant session afterwards. Add `--no-view` for a headless installation.

For a permanent command:

```sh
uv tool install project-memory-mcp==0.6.0b7
project-memory doctor
project-memory view
```

To install the published wheel directly from GitHub instead of PyPI:

```sh
uvx --from https://github.com/Dankaro-projects/project-memory/releases/download/v0.6.0b7/project_memory_mcp-0.6.0b7-py3-none-any.whl project-memory setup --client claude --trust
```

Other local MCP clients use `setup --client mcp` and the stdio server. [Setup and lifecycle](docs/setup.md) covers client configuration, the plugins, upgrades, backups and removal.

## Start a project

A new project starts from one of three templates:

```sh
project-memory init ~/projects/pricing-tool --template product --client codex
```

| Template | For | Starter documents | Phases |
| --- | --- | --- | --- |
| `product` | A software product | `docs/brief.md`, `docs/research.md`, `docs/architecture.md`, `docs/stories.md` | Goals and brief, Research, Requirements approval, Architecture, User stories and acceptance, Build, Review and release |
| `engagement` | A consulting engagement | `engagement/brief.md`, `engagement/stakeholders.md`, `engagement/research.md`, `engagement/findings.md`, `deliverables/README.md` | Scope and brief, Stakeholders and hypotheses, Research and evidence, Analysis and synthesis, Recommendations and deliverables, Client review, Handover |
| `automation` | A workflow automation, with n8n exports in `workflows/` | `automation/process.md`, `automation/systems.md`, `automation/solution-design.md`, `workflows/README.md` | Process discovery, Systems and credentials inventory, Solution design, Workflow stories and test data, Build workflows, Test with sample data, Deployment and handover |

`init` creates the folder, initialises git unless you pass `--no-git`, writes any starter document that does not exist yet, connects the clients you name and records each phase as a work item. Running it again creates nothing a second time and never overwrites a document you have edited.

## The kickoff conversation

Each template carries seven kickoff questions. Your assistant reads them with `memory_get kickoff` and asks you, for example what problem the product solves, who the stakeholders are, or which systems the process uses. It records your answers with `memory_write answer_kickoff`, fills the starter documents from those answers and proposes requirements. Only you approve the requirements baseline. Until that approval exists, the control panel opens on a kickoff checklist that shows the open questions, the research still needed, the starter documents that are still empty and the phases.

## The everyday loop

Tell the assistant the result you want, the constraints and what would count as complete. It then records a work item with its objective, its completion criterion, the next action and the files it may change, continues from that record in later sessions, and records each decision with its evidence, alternatives, uncertainty and expected consequence. After acting, it records what actually happened. A failure stays visible after a later success.

You approve requirements, accept or reject proposed lessons, widen the allowed paths, and decide what is merged. Capturing a document does not approve its contents, and a plan does not authorise work.

## The control panel

Run `project-memory view` in the project, or ask the assistant to open Project Memory. The panel reads the same database and refreshes while it is open.

| View | Content |
| --- | --- |
| Now | One sentence on the project position, the kickoff checklist, work in progress, blocked work, the attention list, running agents and recent decisions |
| Plan | The hierarchy of phases, epics, stories, research items, deliverables and workflows, with progress roll ups and a phase timeline |
| Work | A board and a list of work items, and a detail with the plan, the allowed paths, dependencies, agent runs, lineage and history |
| Architecture | Components from source code, exported n8n workflows and authored items such as systems, stakeholders and deliverables |
| Dependencies | The work dependency graph with blocked chains, and the declared package table |
| Decisions | Decisions with their outcomes, and the lineage from requirement to outcome |
| Learning | Accepted guards and their recurrences, lessons awaiting your acceptance, failures without a lesson and scope widenings |
| Agents | Host availability, agent checks, delegated runs, and the merge or discard decision |
| Records | Search across every record kind, captured documents and host receipts |
| Requirements | The current requirements, their version and their approval evidence |

`project-memory view --output review.html --include-bodies --no-open` writes an offline snapshot instead. See the [control panel guide](docs/control-panel.md).

## Agent checks and delegated work

When Codex or Claude Code is configured, Project Memory can start short, separate host processes. An agent check reads the records and the project and reports on an outcome, on the current intent or on an unconfirmed execution. Delegated work runs a work item in its own git worktree, limited to the paths in its plan, and a second host reviews the resulting diff before you merge it. If one host reports a usage limit or a rate limit, the run reroutes once to the other configured host.

These runs use your installed Codex or Claude Code account and consume that account's usage. A reviewer cannot edit plans, accept lessons or merge anything. See [agent checks and delegated work](docs/agents.md).

## Guards and scope

An accepted lesson with a trigger becomes a guard. When a guard matches the paths or the wording of a work item, a decision must list that lesson in `lessons_considered` with an explicit yes or no and a reason, otherwise the decision is rejected. When a work item has allowed paths, the lifecycle hooks block an edit outside them, record the block and ask you to extend the scope in the control panel. The [record fields](docs/record-fields.md) document defines the pattern rules.

## Honest boundaries

- A recorded plan is not permission. Autonomy and scope are advisory records; your host's own permissions and your current instructions decide what may run.
- The scope guard reads the file paths of edit tools, patch markers and the targets of MCP write tools. It does not parse shell redirection or other indirect writes inside shell commands.
- Hooks record events mechanically. Only the assistant can record what those events meant, and a connected panel does not prove that the recording is complete.
- An agent verdict is an interpretation. A passing check does not prove that the work is correct, and an empty findings list is not proof either.
- Architecture is read statically from imports, manifests and exported workflow files. Dynamic loading is not detected.
- Bounded retrieval limits the characters a call returns. It cannot control the complete model input of the host.
- Daily productivity, token usage, correction rates and human task time are not measured, and this project makes no claim about them. [Testing and limitations](docs/evidence.md) states what is verified and what is not.

## Documentation

- [User guide](docs/user-guide.md), from the first conversation to delivery
- [Control panel](docs/control-panel.md) and [agent checks and delegated work](docs/agents.md)
- [Setup and lifecycle](docs/setup.md) and [record fields](docs/record-fields.md)
- [Testing and limitations](docs/evidence.md), [contributing](CONTRIBUTING.md), [security](SECURITY.md) and [releasing](docs/releasing.md)

Packages are published on [PyPI](https://pypi.org/project/project-memory-mcp/) and in [GitHub Releases](https://github.com/Dankaro-projects/project-memory/releases).
