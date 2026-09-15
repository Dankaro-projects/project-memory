# Workspace interface

The workspace uses the Dankaro UI Kit's tokens with a compact application layout. Run `project-memory view` from a configured project. The same presentation works in the live workspace and exported HTML; planning and comments require the live service.

## Design and sources

The kit source is [v0.1.0, commit fddee587](https://github.com/Dankaro-projects/dankaro-ui-kit/tree/fddee587cb00376b0ba2b4d82b6cabd25a1e8188), verified on 14 September 2026. Its token block is retained in `memory_module/ui/workspace.css`. Application controls use those tokens; the kit's promotional layouts and animation helpers are not included.

| Role | Choice |
| --- | --- |
| Primary text | Dankaro ink, `#111111` |
| Actions and selection | Dankaro indigo, `#4457e6` |
| Navigation and columns | Dankaro surface, `#f7f5f1` |
| Document canvas | White, `#ffffff` |
| Supporting text | Application addition, `#65625d` |
| Headings and body | Manrope, 700 and 400; system fallback for other character sets |
| Technical values | The kit's system monospace stack |
| Spacing and corners | The kit's 4px spacing scale and 8px control radius |

Manrope Latin fonts come from `@fontsource/manrope` 5.3.0. Their SIL Open Font License is included in the HTML template. Font files remain separate in source and are embedded when the viewer is rendered, so there is no font CDN, extra request or frontend build step.

The viewer opens on an overview of current work, ready actions, recent decisions and items that need attention. Five primary sections contain the existing views: Overview, Work, Decisions, Knowledge and Activity. Each section exposes its own tabs and relevant filters. Switching record views preserves their filters during the session.

## Source structure

`memory_module/viewer.html` is the shared shell. Files in `memory_module/ui` separate record rendering, reading layouts, the overview, API access, live updates, navigation, the board, forms, reviews, approvals, skills and the map. They are ordered classic JavaScript units with shared workspace state. Python assembles them with CSS and fonts into the same self-contained page for live use and exports. Users install the package once; recording work does not require a frontend build. There is no JavaScript runtime dependency, CDN or separate asset server.

## Reading and working

- Needs attention groups blocked work, outcome reviews, proposed lessons and observed recording gaps. Requirement revisions and lesson acceptance are explicit user actions with reasons and preserved history.
- Skills lists project-local and imported packages. A selection links an exact package version to work; a use report remains a separate evidence-backed statement.
- Project map opens around selected work. Relationships follow recorded evidence and revisions. Workflow and Architecture contain explicitly authored nodes and explained relationships. Their coordinates stay in browser storage; their meaning and revisions stay in SQLite.
- Dependencies lists declared packages from project `package.json`, `pyproject.toml` and `requirements*.txt` files, with version declarations, groups and manifest paths. Search and selectors filter the inventory. Counts describe declarations across manifests, not unique installed packages. Refresh rereads local manifests; exports retain their captured inventory. Invalid manifests and unsupported requirements directives remain visible. No package manager, install script or network lookup runs.
- Recorded architecture dependencies show active `uses` and `depends_on` connections, their purpose, evidence and confirmation status. External services appear when explicitly recorded in an architecture map. Package names alone do not establish purpose, deployment, transitive versions or a decision that introduced them.
- Purpose and decisions displays complete dependency sections from captured Markdown, including nested exceptions, through a document selector. The source status and original document remain accessible. These are stored explanations, not inferred package roles; later file changes are flagged. Offline exports include documentation only within their source selection.
- The overview shows one work status at a time, with up to three work rows, and the latest decision for up to three distinct work items. Status selectors switch the visible queue; counts and footer links expose full lists. The first visit selects in-progress work, otherwise blocked, review or ready work. The selected status survives refresh. Empty statuses use one compact empty state.
- Work rows preview the saved next action and a short completion-guidance label. Opening a row reveals the complete plan, reasons and evidence. Decision rows preview the latest choice and its own outcome; prior revisions remain in the decision reader and full history. Lessons and recording checks use compact review links. No generated summaries or inferred approval are introduced. The live overview uses a bounded response; deriving work states still examines project work records.
- The overview header provides the work board, requirements and new-action entry points. Work and decisions use balanced panels on desktop and stack on narrow screens. Preview text can truncate; complete authored text remains in the existing readers. Exports derive the latest decision available for each work item within the selected snapshot scope.
- The board offers Board and List formats. It hides empty columns by default; “Show empty columns” restores all states. Counts, filters and pagination retain their existing scope. Work details put the objective, next action and completion criterion before properties and history.
- Records use readable summaries by default, with a Table option for detailed scanning. Events and host capture retain tables. Lessons show their conditions and exceptions; corrections show the original and corrected wording.
- Records open in a side panel. Expand provides a wider reading space. Back follows inspected references; Close or Escape returns to the workspace.
- Decision pages compare expected and observed consequences, then show uncertainty, alternatives, evidence and paged history. Each outcome belongs to its exact recorded decision. An earlier failure remains visible after a successful revision. Missing or stale outcomes remain explicit. Technical identifiers and capture metadata are expandable.
- Document text supports headings, simple pipe tables, lists, quotes, fenced code, emphasis and HTTP(S) links. “Read original text” preserves the exact stored source, including unsupported formatting. Raw HTML is displayed as text. Images and scripts are not loaded from document content.
- Documents with multiple headings include an outline that moves to the selected section. Reading layouts preserve the original text and require no additional source processing or model calls.
- Existing forms retain structured status, owner, sprint and dependency selectors. Plan changes, comments, validation and concurrent-edit recovery use the existing API and SQLite records.

This is not a Notion block editor. Editing captured documents still happens in their source files. Nested Markdown, embedded media and full CommonMark parsing are not implemented; original text remains available. Automated Chromium checks do not establish complete assistive-technology compatibility or daily productivity improvements.

## Local verification

`node tests/browser/dependencies_browser.cjs` checks dependency counts, search, group and manifest selectors, linked architecture and evidence, live refresh, mobile layout and preserved offline versions through the real local server.

Run `node tests/browser/viewer_logic.cjs`, `node tests/browser/browser_check.cjs`, `node tests/browser/workspace_browser.cjs`, `node tests/browser/knowledge_browser.cjs` and `node tests/browser/usability_browser.cjs` with Playwright installed as a development tool. `MEMORY_PLAYWRIGHT` selects an existing Playwright installation and `MEMORY_PYTHON` selects the project Python interpreter. The browser checks create temporary databases and local servers; they do not invoke model reviewers.

The knowledge browser case covers product and consulting work through the real local API. It imports an inert ZIP, selects skills, checks local resource drift and preserved originals, approves requirements and a lesson, creates architecture and workflow diagrams, follows evidence links, and rejects a concurrent edit. Outputs belong in ignored `.memory` directories. These are scripted workflow checks, not measurements of model judgement or long-term productivity.

The usability case uses a failed decision and its successful revision, blocked work and a document with an explicit exception. It checks outcome attribution, revision navigation, document reading, planning, table access, live recovery, evidence drift and mobile widths. It reports scripted click counts and timings for two fixed journeys. To compare an earlier runtime on the same fixture, set `MEMORY_UX_RUNTIME` to that source checkout and `MEMORY_UX_BASELINE=1`. These measurements do not establish human task times, model-token savings or long-term usability.
