# Changelog

## 0.6.0b4 (18 September 2026)

The release states its own version. Version 0.6.0b3 was published with the version constant in `memory_module/__init__.py` left at `0.6.0b2`, so the tool reported a version it was not, and the installer, which builds its wheel URL from that constant, pointed at the earlier wheel. The plugin metadata, the wheel URLs in the plugin files and the install commands in the documentation were left behind in the same way. Every version location named in `docs/releasing.md` is raised in this release. No product code differs from 0.6.0b3.

## 0.6.0b3 (18 September 2026)

A check can see the turn that asked for it. The snapshot of a requested check carries the receipts of the requesting session, with their event names, tool names, turn identifiers and times, and the uncommitted state of the checked folder. Before this, a criterion such as whether the requesting turn changed any code could not be answered at all, because the receipts of a check belong to the work under review and no state of the working tree was recorded, and three checks in a row returned such conditions as unknown. The new evidence is attached after the snapshot signature is computed, so reading it never makes a check stale. `project-memory review` accepts `--session`, which names the session that requests the check, and a request without it says that no tool calls of a requesting session are recorded.

A check that runs in a worktree reads its own evidence. A check reads the worktree recorded on its work plan, while the records, sources and receipts of the run belong to the project database folder, and a reviewer may open only the folder it checks. For a work item whose worktree lies outside the project, every file of its own run was therefore unreachable, and a linked worktree holds a `.git` file rather than a readable history. The packet now carries every record and source itself in that case, each cut at twenty thousand characters and the packet at one hundred and fifty thousand, saying how many characters were left out. A requested check also carries the head commit of the checked folder, its ten most recent commits with the paths the newest five changed, and whether the head is reachable from a remote reference. A check whose run folder lies inside the checked folder keeps the packet it had before, and nothing is written into the checked folder.

## 0.6.0b2 (17 September 2026)

Finished sessions become memory. The Claude Code and Codex sessions of a project, including a session started in another folder that works on the project, are read into redacted digests of at most 20,000 characters: the user messages, failed commands, changed files, memory records written and commits, each with its transcript line. A message worded as an approval, a refusal, a correction or a choice that no record followed is flagged with low confidence, and the user confirms or dismisses it in the new Sessions view. `project-memory sessions distill` asks a host for proposed decisions, requirements, lessons, next actions and corrections, and nothing is recorded until the user accepts one. Transcripts are never changed, session content never enters the machine memory, and `project-memory sessions off` switches reading off for a project.

Starting fresh is cheaper and safer. A new session receives a short summary of the three latest earlier sessions, with identifiers and counts and no quoted message. The prompt hook names the context size once it passes 250,000 tokens. `project-memory handoff` lists work whose next action is older than the latest activity, decisions without an outcome, changed documents, Markdown files no record cites and open flags, or reports ready.

Unconfirmed tool calls can be reconciled from the control panel. Now and a blocked work item list each call with what the transcript of its session reports, and one click records the resolution with that entry as evidence. Read-only calls with a transcript result can be resolved together; calls that can change something are always decided one by one.

Agent checks read the worktree of the work. A work plan can record `worktree`, the git worktree where the work is done, and outcome, intent and recovery checks then read that folder instead of the project folder. Before, a check of work committed on a branch in another worktree could not see the code and returned uncertain for every criterion. A missing or foreign worktree is refused, and a check without one reports the branch it read.

The Grok profile follows Grok 1.0. The removed `--no-memory` option is no longer passed, which made every Grok run of 0.6.0b1 fail after an update of Grok; memory stays off through `GROK_MEMORY=0` and the run configuration. Each Grok run has its own Grok home that links to the sign in and ignores the skills of the user, which Grok 1.0 offers no switch for. Token usage is read from the Grok 1.0 result, including cache reads and cache writes. A probe run that a host refuses because of a command line option names that option.

The Now view keeps its first screen: Latest decisions and Recent scope blocks open from buttons in the view head, each card shows at most five items and opens the rest in a drawer, and board columns show ten cards at a time.

The usage ledger separates fresh input, cache writes, cache reads, output and reasoning, and labels fresh work as fresh input plus cache writes plus output. Cache reads are never added into one total, routing by headroom compares fresh work, each Claude message counts once, and per session statistics report turns, the largest context and the cache read share. The machine memory folder and its database files are readable by their owner only, and doctor reports a wider mode.

## 0.6.0b1 (17 September 2026)

Agents improve between runs. Accepted rules can target a role, and the instructions for the assistant, the worker and the reviewer are composed from a base text the user edits in the control panel plus the matching rules, within a fixed budget per role, with every omission reported. The panel shows how often each rule was used and whether its failure recurred. A failure stays counted until the user reassesses it, because an agent cannot clear its own failure by recording a later outcome.

A project is in development or in production. In development the orchestrator merges delegated work after a passing review; in production the merge belongs to the user in the control panel, and a panel started from inside an assistant session refuses it. A machine memory above the project memories keeps a registry of projects and rules that apply across them; a project may propose a rule and only the user accepts it, after mechanical checks refuse project names, paths, identifiers, addresses and host names.

A focused problem lets several attempts work on one problem, one after another or in parallel. Each attempt declares its own hypothesis, attempts alternate between hosts, and a check that only the user sets decides the result in a clean checkout of the attempt's commit. The first passing attempt goes to cross review and the usual merge rules. The check for this feature was written before its implementation, which Codex then wrote in a delegated run.

The hive is a separate database where agents that work together record typed entries such as hypotheses, observations, challenges, conclusions and patterns. Entries are validated mechanically, near duplicates and unsupported claims are refused with a correction, agents can be kept blind to others' positions until they state their own, and confirmed patterns become lesson proposals. Delegated workers receive only the hive server. The panel shows each swarm as a live timeline where the user can post.

Codex, Claude, Grok and OpenCode are described by host profiles. Grok and OpenCode may review; Grok may work only after a probe for its installed version, and OpenCode not before a container runner exists. A usage ledger in the machine memory counts tokens, cost and Codex limit windows from delegated runs and from local session logs, storing counts only. Host choice prefers the host with the most headroom. `project-memory usage` and `project-memory host probe` are new, and the panel gains Usage, Focus and Hive views.

Known limits in this release: the operating system sandboxes of the hosts limit writes but not reads, so a worker can read files outside its worktree; Grok still reads AGENTS.md and its own user skills; the MCP tool list has almost no room left under its size limit.

The same project model now serves a software product, a consulting engagement and a workflow automation. `project-memory init` creates a project from one of three templates, with starter documents, phase work items and kickoff questions. `memory_get kickoff` reports the open questions, the research still needed and the documents that are not yet filled, and `answer_kickoff` records the answers the user gives. Work items carry a type, acceptance criteria and a parent, so phases, epics, stories, research items, deliverables and workflows form one hierarchy. Interface text describes work items, deliverables and components rather than assuming that work is code.

The workspace is replaced by a control panel with ten views: Now, Plan, Work, Architecture, Dependencies, Decisions, Learning, Agents, Records and Requirements. The panel and the MCP tools now read through one API layer, so both show the same bounded responses, and an offline export embeds those responses instead of a separate rendering. Labels follow the project template.

Architecture is extracted from Python, JavaScript, TypeScript and Dart imports, from package manifests and from exported n8n workflow files, and it can be combined with authored components such as systems, stakeholders and deliverables. An item that an agent authors stays proposed until the user confirms it. A typed link table records lineage, work dependencies and explicit relationships between records, components and packages.

An accepted lesson with a trigger becomes a guard. A decision must list every matching guard with an explicit applicability and a reason, otherwise it is rejected. A work plan can list the paths it may change; the lifecycle hooks then block an edit outside them, record the block and leave the tool unrun. The user widens the paths in the control panel. Recurring failure types and failed outcomes without a lesson are reported.

Delegated work runs a work item in its own git worktree on its own branch, limited to the recorded paths, and another host reviews the diff before the user merges it. A usage limit, a rate limit or a missing login marks a host unavailable and reroutes the run once to the other configured host. A merge needs a passing work review, or an explicit override by the user. Setup supports several clients in one project, and agent checks prefer a host other than the one that did the work.

Skills, project maps, the separate dependency module and the separate review log module are removed, together with their views and operations. Distribution is now GitHub Releases and PyPI only. The documentation is rewritten for a first time reader: `docs/control-panel.md` replaces the workspace and work board documents, `docs/agents.md` replaces the workspace agents document, and the removed channels are gone.

No productivity, token or correction rate is claimed or measured.

## 0.5.0b9 — 15 September 2026

The viewer opens on a project overview with current work, attention items and recent decisions. Five primary sections expose contextual views and filters. Work supports Board and List formats; record summaries retain a Table option. Expanded reading, document outlines and decision pages make the evidence easier to follow. Expected and observed consequences remain tied to the exact decision, including earlier failures, later revisions and stale evidence. No data migration, model call or runtime dependency is added.

Capture failures remain separate across sessions and recover once after interrupted cleanup, including legacy failure files. File-access errors return an MCP tool error without closing the connection. The viewer keeps failed detail and history refreshes visible and retries them. Unchanged viewer requests reuse content hashes; review creation and completion still check fresh files. Direction retrieval pages complete versioned requirements and approval evidence instead of duplicating them in its overview. Continuation guidance uses progress updates that preserve scope and evidence.

Agent reviews separate task acceptance from project constraints, with explicit applicability and preserved exceptions. Evidence manifests support selective reads. The wait command now honours its own limit. Review status preserves child activity, timeout diagnostics and available partial reports and usage without granting approval. A progress operation updates status without rewriting scope or its evidence.

The workspace adds project skills, explicit human requirement and lesson reviews, a Needs attention view, and a project map with recorded relationships, workflows and architecture. Skill selections preserve exact package versions, including supporting files; changed local files appear without a rebuild. Diagram changes preserve their reasons and earlier versions. Import does not activate instructions or execute scripts, and a skill-use report is distinct from a selection.

Frontend responsibilities now live in separate source files assembled into the existing self-contained viewer. The package still requires no runtime dependencies or frontend build command. Bundle validation checks the complete viewer assets. New browser cases cover product and consulting work, evidence links, skill drift and conflict recovery.

## 0.5.0b8 — 14 September 2026

Board refreshes preserve keyboard focus on the same action. A delayed-response browser case reproduces the focus loss and verifies the correction. Beta 7 was withheld after the browser gate exposed this race; all six operating-system and Python combinations passed.

Runtime subprocesses use UTF-8 explicitly, including on Windows where isolated Python ignores the parent UTF-8 environment setting. Beta 6 was withheld after the Windows installed-wheel gate failed. This release includes its import-isolation correction.

Installed launchers, diagnostics, live viewers and review workers now load the selected runtime even when a project contains an older `memory_module` package. An upgrade of the original prototype exposed this conflict after beta 5 publication. The installed-wheel check now includes a conflicting project package. Existing data and project files remain unchanged.

## 0.5.0b5 — 14 September 2026

Opening the internal HTML template now shows the workspace launch command instead of unstyled controls. Project information explains live updates and offline exports, and the [user guide](docs/user-guide.md) covers everyday use and recovery.

Host checks now expose unassessed requests, unassigned activity, missing outcomes and capture gaps. Intent checkpoints can accompany a plan or record write. Stop intervenes once, and an explicitly assessed unknown result remains visible without another intervention. Reviewer reports must cover every numbered criterion. The live workspace displays recording gaps and capture failures without a rebuild.

The live viewer becomes an interactive project workspace. Users can create actions and sprints, select dependencies, revise plans and add comments while retaining the complete decision history. Concurrent edits preserve the draft and require a current version before saving. Offline exports remain available.

Codex and Claude setup enable conditional read-only outcome, intent and recovery agents through the installed host CLI. One durable SQLite table records requests, cancellation, explicit retries and immutable results. New completion claims require a current passing outcome check; existing historical completion remains intact. No runtime dependency or separate orchestration service is added.

Actual host evaluation exposed and repaired Codex configuration errors, repeated Stop feedback, and a browser save that invalidated unchanged evidence. Code changes and expired workers now invalidate live results without requiring another memory write.

## 0.5.0b4 — 14 September 2026

Work intent now connects to an explicit next action, scope, dependencies and sprint. The live and offline viewers add a read-only Kanban board with direct access to decisions, evidence, failures and recovery history.

- Atomic plan and sprint operations reuse existing episodes and append-only events. Version checks preserve concurrent edits and earlier plans.
- Bounded continuation distinguishes ready work, human proposals, dependency and evidence review, uncertain execution, another session's work, and completion that only needs its plan finalised.
- Decisions retain the work plan used at the time. Scope and prerequisite changes trigger review; routine progress changes do not.
- Startup and continuation hooks point to the planned work without starting another objective or claiming tool permissions.
- Setup opens the current viewer after an upgrade instead of reusing an older process.
- Existing records remain intact. The first plan write extends the existing event validation trigger transactionally; no dependency, task database or agent scheduler is added.

The work board and continuation rules of this release were documented separately at the time. Their current form is in the control panel and record field documents.

## 0.5.0b3 — 14 September 2026

First-beta feedback exposed configured but inactive hooks, duplicate cross-host discovery, manual document refresh, an unclear requirements baseline and a static viewer.

- Setup opens an included live, read-only local viewer; offline exports remain available with explicit atomic replacement.
- Codex excludes Claude plugin hooks. Managed Claude hooks suppress duplicate plugin capture, and host receipt/result identities remain separate.
- Doctor checks actual Codex hook discovery and trust. Health distinguishes missing requirements, historical capture, native tool availability and unmeasured costs.
- Session start and stop refresh previously selected Markdown, preserving immutable evidence and explicit approval.
- Oversized context reports a recovery route; complete requirements can be paged and safely reused while unchanged. Omitted evidence stays discoverable.
- Review records explain changed dependencies and requirement revisions. Runtime dependencies remain zero.


## 0.5.0b2

Adds Claude Code as a setup client. `setup --client claude` writes the project MCP server to `.mcp.json` and the lifecycle hooks to `.claude/settings.local.json`; `--trust` pre-approves the server there. Uninstall removes exactly those entries.

The hook command accepts `--host claude`. Claude Code's `PostToolUseFailure` closes the matching tool receipt with the failure retained, and `SessionStart` now returns additional context at startup, resume and after compaction: memory session, active decision and unconfirmed tool calls. Codex receipt identifiers are unchanged; Claude Code receipts record their host and use the prompt identifier or capture time in place of a turn identifier.

`doctor` reports the configured client and expects eight lifecycle events for Claude Code, which has no interrupt hook. Memory tool calls are excluded from receipts under any server name. A source checkout with its own environment installs a `uv run --project` launcher instead of the release wheel.

On Claude Code, each prompt receives memory context only when an unconfirmed tool call or an unresolved decision needs attention, or when the prompt starts with an explicit `[memory:subject]` request; the session start context already carries the standing guidance. Codex prompts are unchanged.

Adds a Claude Code plugin manifest and a repository marketplace named `dankaro`. The plugin shares the Codex plugin's server and skill and adds the lifecycle hooks. The hook command accepts `--project` and exits silently in projects without a Project Memory database, so plugin hooks never block unrelated work.

## 0.5.0b1

First public beta of Project Memory. Adds an installable package, stable CLI, project setup and removal, an installed-process doctor, versioned launch configuration, a Codex plugin and public release checks. Project direction can change through explicit, evidenced revisions while retaining original requirements and flagging earlier dependent decisions for review.

Preserves the local SQLite core, three-tool MCP interface, mechanical Codex capture, interruption reconciliation, Markdown snapshots, bounded retrieval, separate subjects and offline HTML viewer from the private 0.4 evaluation.

The release does not claim universal client compatibility, guaranteed productivity gains or complete model inputs below 10K tokens. Those remain evaluation work.
