# Workspace interface

The workspace uses the Dankaro UI Kit's tokens with a compact application layout. Run `project-memory view` from a configured project. The same presentation works in the live workspace and exported HTML; planning and comments require the live service.

## Design and sources

The kit source is [v0.1.0, commit fddee587](https://github.com/Dankaro-projects/dankaro-ui-kit/tree/fddee587cb00376b0ba2b4d82b6cabd25a1e8188), verified on 14 September 2026. Its token block is retained in `memory_module/viewer.html`. Application controls use those tokens; the kit's promotional layouts and animation helpers are not included. The user requested this kit for Project Memory.

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

[Linear's board and properties panel](https://mobbin.com/screens/a6f3c2e2-cbe0-4559-bda7-54ee9d405692) informed navigation and inspection. [Notion's document page](https://mobbin.com/screens/55a514bb-4e4b-4378-88bd-0226e5329b2a) informed the heading and reading hierarchy. Both references were inspected through Mobbin. No reference screenshots or product assets are bundled.

```text
Navigation | Work board or records | Selected decision or document
           | Filters and actions  | Properties, reasoning, evidence
           | Scrollable columns   | History and back navigation
```

The chosen layout keeps decision history beside the work that produced it. A centered dialog was rejected because it obscured that context. A framework replacement was unnecessary for these interactions.

## Reading and working

- Navigation groups work, knowledge, and learning/history. Project information contains requirements, measurements and export details.
- The board hides empty columns by default; “Show empty columns” restores all states. Counts, filters and pagination retain their existing scope.
- Records open in a side panel. The list remains interactive. Back follows inspected references; Close or Escape returns to the workspace.
- Decision sections present the choice, reasoning, uncertainty, alternatives and expected consequences. Technical identifiers and capture metadata are expandable.
- Document text supports headings, simple pipe tables, lists, quotes, fenced code, emphasis and HTTP(S) links. “Read original text” preserves the exact stored source, including unsupported formatting. Raw HTML is displayed as text. Images and scripts are not loaded from document content.
- Existing forms retain structured status, owner, sprint and dependency selectors. Plan changes, comments, validation and concurrent-edit recovery use the existing API and SQLite records.

This is not a Notion block editor. Editing captured documents still happens in their source files. Nested Markdown, embedded media and full CommonMark parsing are not implemented; original text remains available. Automated Chromium checks do not establish complete assistive-technology compatibility or daily productivity improvements.

## Local verification

Run `node tests/viewer_logic.cjs`, `node tests/browser_check.cjs` and `node tests/workspace_browser.cjs` with Playwright installed as a development tool. `MEMORY_PLAYWRIGHT` selects an existing Playwright installation and `MEMORY_PYTHON` selects the project Python interpreter. The browser checks create temporary databases and local servers; they do not invoke model reviewers.

The [dated results](verification-workspace-ui-2026-09-14.json) include document rendering, original text, keyboard and mobile navigation, existing planning actions, draft recovery and package checks. Chrome extension inspection also used the existing project database. GitHub Actions and model reviews were not run for this interface iteration.
