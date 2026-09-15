const sectionViews = {
  overview: ["overview", "attention"],
  work: ["board", "episodes", "pending", "map"],
  decisions: ["decisions", "drift"],
  knowledge: [
    "documents",
    "sources",
    "direction",
    "research",
    "corrections",
    "lessons",
    "patterns",
    "skills",
  ],
  activity: ["events", "captures"],
};
const savedFilters = new Map();
const filterFields = [
  "query",
  "subject",
  "status",
  "episode",
  "from",
  "to",
  "order",
];
function setView(value, selectedFilters = {}) {
  const changed = value !== view;
  savedFilters.set(
    view,
    Object.fromEntries(filterFields.map((id) => [id, el(id).value])),
  );
  view = value;
  page = 1;
  setBoardVisibility();
  const filters = { ...savedFilters.get(view), ...selectedFilters };
  for (const id of filterFields)
    el(id).value = filters[id] || (id === "order" ? "newest" : "");
  for (const b of document.querySelectorAll("[data-view]"))
    b.setAttribute("aria-pressed", String(b.dataset.view === view));
  document.body.classList.remove("nav-open");
  el("menu-toggle").setAttribute("aria-expanded", "false");
  if (changed && view === "board" && data.live)
    loadSprints(true).catch(disconnected);
  render();
}
for (const b of document.querySelectorAll("[data-section]"))
  b.addEventListener("click", () =>
    setView(sectionViews[b.dataset.section][0]),
  );
for (const b of document.querySelectorAll("[data-view]"))
  b.addEventListener("click", () => setView(b.dataset.view));
for (const id of [
  "query",
  "subject",
  "status",
  "episode",
  "from",
  "to",
  "order",
  "page-size",
])
  el(id).addEventListener("input", () => {
    page = 1;
    render();
  });
el("clear").addEventListener("click", () => {
  for (const id of [
    "query",
    "subject",
    "status",
    "episode",
    "from",
    "to",
    "sprint",
  ])
    el(id).value = "";
  page = 1;
  render();
});
el("previous").addEventListener("click", () => {
  page--;
  render();
});
el("next").addEventListener("click", () => {
  page++;
  render();
});
el("close").addEventListener("click", () => el("detail").close());
el("detail").addEventListener("close", () => {
  selectedId = null;
  extensionDetail = false;
  workDetail = false;
  coverageSession = null;
  detailHistory.length = 0;
  document.body.classList.remove("inspector-open", "reader-expanded");
  el("detail-expand").textContent = "Expand";
  el("detail-expand").setAttribute("aria-pressed", "false");
  if (detailTrigger?.isConnected) detailTrigger.focus();
  else {
    const current = [...document.querySelectorAll("button[aria-label]")].find(
      (b) => b.getAttribute("aria-label") === detailTriggerLabel,
    );
    (current || document.querySelector('[data-view="' + view + '"]'))?.focus();
  }
});
el("detail-back").addEventListener("click", async () => {
  const previous = detailHistory.pop();
  if (!previous) return;
  restoringDetail = true;
  try {
    if (previous.work) await openWork(previous.id);
    else await open(previous.id);
  } finally {
    restoringDetail = false;
    el("detail-back").hidden = !detailHistory.length;
  }
});
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    el("detail").open &&
    !el("editor").open &&
    !el("about").open
  ) {
    event.preventDefault();
    el("detail").close();
  }
  if (event.key === "Escape") {
    document.body.classList.remove("nav-open");
    el("menu-toggle").setAttribute("aria-expanded", "false");
  }
});
el("menu-toggle").addEventListener("click", () => {
  const shown = document.body.classList.toggle("nav-open");
  el("menu-toggle").setAttribute("aria-expanded", String(shown));
});
el("about-open").addEventListener("click", () => el("about").showModal());
el("about-close").addEventListener("click", () => el("about").close());
el("show-empty").addEventListener("change", () => render());
const icons = {
  board: "M2 3h12v10H2z M6 3v10 M10 3v10",
  episodes: "M3 2h10v12H3z M5 5h6 M5 8h6 M5 11h3",
  pending: "M8 2v7 M5 6l3 3 3-3 M3 11v3h10v-3",
  decisions: "M8 2v5 M3 14v-4h10v4 M8 7v3 M5 4l3-2 3 2",
  documents: "M3 1h7l3 3v11H3z M10 1v4h3 M5 8h6 M5 11h4",
  sources: "M6 5H4a3 3 0 0 0 0 6h3 M10 5h2a3 3 0 0 1 0 6H9 M5 8h6",
  direction: "M3 14V2 M3 2h10l-2 3 2 3H3",
  research: "M10.5 10.5L14 14 M11 6a5 5 0 1 1-10 0a5 5 0 1 1 10 0",
  corrections: "M3 3h10 M8 3v10 M5 13h6",
  lessons: "M2 3l6 2 6-2v10l-6 2-6-2z M8 5v10",
  patterns: "M2 3h3v3H2z M11 3h3v3h-3z M6.5 11h3v3h-3z M3.5 6v2h9V6 M8 8v3",
  drift: "M1 9h3l2-5 4 9 2-5h3",
  events: "M3 3h11 M3 8h11 M3 13h11",
  skills: "M3 2h10v12H3z M5 5h6 M5 8h6 M5 11h3",
  map: "M1 1h5v5H1z M10 10h5v5h-5z M3 6v6h7",
  attention: "M8 1L15 14H1z M8 5v4 M8 11v1",
  captures: "M5 3L1 8l4 5 M11 3l4 5-4 5",
};
icons.overview = "M2 7l6-5 6 5 M3 6v8h10V6 M6 14V9h4v5";
for (const button of document.querySelectorAll("[data-section]")) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("class", "nav-icon");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute(
    "d",
    icons[
      { work: "board", knowledge: "documents", activity: "events" }[
        button.dataset.section
      ] || button.dataset.section
    ],
  );
  svg.append(path);
  button.prepend(svg);
}

const sourceStates = [
  "current_copy",
  "superseded",
  "review_due",
  "needs_review",
  "file_changed",
  "file_missing",
  "file_unreadable",
];
const recordStates = ["recorded", "needs_review", "replaced"];
const viewStatuses = {
  decisions: recordStates,
  corrections: recordStates,
  research: recordStates,
  sources: sourceStates,
  documents: sourceStates,
  episodes: ["active", "reopened", "settled", "abandoned"],
  lessons: [
    "proposed",
    "accepted",
    "rejected",
    "retired",
    "needs_review",
    "replaced",
  ],
  direction: ["current", "historical", "needs_review"],
  pending: [
    "execution_unconfirmed",
    "not_started",
    "consequence_pending",
    "consequence_unknown",
  ],
  captures: ["observed", "execution_unconfirmed"],
  drift: sourceStates.filter((s) => s !== "current_copy"),
};
function setBoardVisibility() {
  const board = view === "board",
    overview = view === "overview";
  const extension = ["map", "skills", "attention", "overview"].includes(view);
  const section = Object.keys(sectionViews).find((key) =>
    sectionViews[key].includes(view),
  );
  for (const nav of document.querySelectorAll("[data-section]"))
    nav.setAttribute("aria-pressed", String(nav.dataset.section === section));
  for (const tabs of document.querySelectorAll("[data-views]"))
    tabs.hidden = tabs.dataset.views !== section;
  for (const button of document.querySelectorAll("[data-view]"))
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  el("extension-view").hidden = !extension;
  document.querySelector(".filters").hidden = [
    "map",
    "attention",
    "overview",
  ].includes(view);
  document.querySelector(".pager").hidden = [
    "map",
    "attention",
    "overview",
  ].includes(view);
  el("knowledge-actions").hidden = !(view === "direction" && data.live);
  for (const id of ["query", "subject", "status"])
    el(id).parentElement.hidden =
      (extension && (view !== "skills" || id !== "query")) ||
      (view === "direction" && id === "subject");
  el("board").hidden = !board;
  el("board-controls").hidden = !board;
  const title =
    document.querySelector('[data-view="' + view + '"]')?.textContent ||
    labels(view);
  el("view-title").textContent = title;
  el("page-title").textContent = title;
  for (const id of ["episode", "from", "to", "order"])
    el(id).parentElement.hidden =
      board ||
      extension ||
      (id === "episode" &&
        ["sources", "documents", "direction"].includes(view));
  const status = el("status").value;
  el("status").replaceChildren();
  option("status", "", "All statuses");
  const statuses = board
    ? workStates
    : viewStatuses[view === "patterns" ? "lessons" : view] || [
        ...new Set([
          ...recordStates,
          "proposed",
          "accepted",
          "rejected",
          "retired",
        ]),
      ];
  for (const value of statuses) option("status", value, labels(value));
  el("status").value = statuses.includes(status) ? status : "";
  const formats = board
    ? [
        ["board", "Board"],
        ["list", "List"],
      ]
    : [
        ["reading", "Summary"],
        ["table", "Table"],
      ];
  el("presentation").replaceChildren();
  for (const [value, label] of formats) option("presentation", value, label);
  el("presentation").value = presentationFor(view);
  el("presentation-controls").hidden =
    extension || (!board && !readingViews.has(view));
  setPresentationVisibility();
}
el("presentation").addEventListener("change", () => {
  presentations.set(view, el("presentation").value);
  setPresentationVisibility();
  render();
});
el("detail-expand").addEventListener("click", () => {
  const expanded = document.body.classList.toggle("reader-expanded");
  el("detail-expand").textContent = expanded ? "Collapse" : "Expand";
  el("detail-expand").setAttribute("aria-pressed", String(expanded));
});
