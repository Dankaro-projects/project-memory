const readingViews = new Set([
  "episodes",
  "decisions",
  "documents",
  "sources",
  "direction",
  "research",
  "corrections",
  "lessons",
  "patterns",
  "drift",
  "pending",
]);
const presentations = new Map();
const decisionHistoryShown = new Map();
function presentationFor(section) {
  return (
    presentations.get(section) ||
    (section === "board"
      ? "board"
      : readingViews.has(section)
        ? "reading"
        : "table")
  );
}
function setPresentationVisibility() {
  const format = presentationFor(view),
    reading = readingViews.has(view) && format === "reading";
  el("reading-view").hidden = !reading;
  el("record-table").hidden =
    reading ||
    ["overview", "board", "map", "skills", "attention"].includes(view);
  el("board-grid").classList.toggle(
    "work-list",
    view === "board" && format === "list",
  );
  el("board-options").hidden = view !== "board" || format !== "board";
}
function statusBadge(value, text) {
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.dataset.state = value;
  badge.textContent = text || labels(value);
  return badge;
}
function recordedOutcome(record) {
  if (data.live) return record.outcome;
  return (
    data.records
      .filter((r) => r.kind === "outcome" && r.detail.decision_id === record.id)
      .sort((a, b) => (b.detail.seq || 0) - (a.detail.seq || 0))[0] || null
  );
}
function recordSummary(record) {
  const article = document.createElement("article"),
    payload = record.detail.payload || record.detail;
  article.className = "record-summary";
  article.dataset.record = record.id;
  const meta = document.createElement("div");
  meta.className = "summary-meta";
  meta.append(statusBadge(record.status));
  const context = document.createElement("span");
  context.textContent =
    labels(record.subject) +
    (record.date ? " · " + dateLabel(record.date) : "");
  meta.append(context);
  const title = document.createElement("h3"),
    button = document.createElement("button");
  button.className = "summary-title";
  button.dataset.record = record.id;
  button.setAttribute("aria-label", "Open " + record.title);
  button.textContent = record.episode_title || record.title;
  button.addEventListener("click", () => open(record.id).catch(disconnected));
  title.append(button);
  article.append(meta, title);
  const excerpt = (label, text) => {
    if (!text) return;
    const p = document.createElement("p");
    p.className = "summary-excerpt";
    if (label) {
      const strong = document.createElement("strong");
      strong.textContent = label + " ";
      p.append(strong);
    }
    p.append(document.createTextNode(text));
    article.append(p);
  };
  if (record.kind === "decision") {
    if (record.episode_title) excerpt("", payload.decision);
    excerpt("Reason:", payload.why);
    const outcome = recordedOutcome(record);
    if (outcome) {
      const line = document.createElement("div");
      line.className = "summary-outcome";
      line.append(statusBadge(outcome.detail.payload.assessment));
      if (outcome.status === "needs_review")
        line.append(statusBadge(outcome.status, "Outcome needs review"));
      article.append(line);
      excerpt("Observed:", outcome.detail.payload.observed);
    } else
      excerpt(
        "Outcome:",
        data.live
          ? "No outcome is recorded here."
          : "No outcome is included in this export.",
      );
  } else if (record.kind === "lesson") {
    excerpt("Applies when:", payload.when);
    excerpt("Exceptions:", payload.exceptions);
  } else if (record.kind === "correction") {
    excerpt("Original:", payload.before);
    excerpt("Correction:", payload.after);
  } else if (record.kind === "project_revision") {
    excerpt("", payload.requirements?.[0]);
    excerpt("", payload.requirements?.length + " recorded requirements");
  } else
    excerpt(
      "",
      payload.objective || payload.summary || payload.question || payload.scope,
    );
  return article;
}
function drawReading(records) {
  const root = el("reading-view"),
    focused = root.contains(document.activeElement)
      ? document.activeElement.dataset.record
      : null;
  root.replaceChildren();
  root.className = ["documents", "sources", "lessons", "patterns"].includes(
    view,
  )
    ? "reading-grid"
    : "reading-list";
  for (const record of records) root.append(recordSummary(record));
  if (!records.length) {
    const p = document.createElement("p");
    p.className = "reading-empty";
    p.textContent = "No records match these filters.";
    root.append(p);
  }
  if (focused)
    root
      .querySelector('[data-record="' + CSS.escape(focused) + '"] button')
      ?.focus();
}
function historyEntry(record, selected = false) {
  const item = document.createElement("li");
  item.className = "history-entry";
  item.dataset.kind = record.kind;
  if (selected) item.classList.add("history-selected");
  const meta = document.createElement("div");
  meta.className = "summary-meta";
  meta.append(statusBadge(record.status, labels(record.kind)));
  const time = document.createElement("time");
  time.dateTime = record.date;
  time.textContent = dateLabel(record.date);
  meta.append(time);
  if (record.kind === "outcome")
    meta.append(statusBadge(record.detail.payload.assessment));
  item.append(meta);
  reference(item, record.id, selected ? "You are reading this decision." : "");
  return item;
}
async function decisionPage(record, body, refresh, scroll) {
  const id = record.id,
    detail = record.detail,
    payload = detail.payload;
  const title = record.episode_title || byId.get(record.episode_id)?.title;
  if (title) el("detail-title").textContent = title;
  const actions = document.createElement("div");
  actions.className = "work-actions";
  if (record.episode_id)
    actions.append(
      actionButton("Open work", () => openWork(record.episode_id)),
    );
  if (detail.supersedes)
    actions.append(
      actionButton("Earlier decision", () => open(detail.supersedes)),
    );
  if (detail.replaced_by)
    actions.append(
      actionButton("Later revision", () => open(detail.replaced_by)),
    );
  body.append(actions);
  const main = document.createElement("dl");
  main.className = "record-content decision-content";
  field(main, "Selected approach", payload.decision);
  field(main, "why", payload.why);
  body.append(main);
  const comparison = document.createElement("section");
  comparison.className = "consequence-comparison";
  comparison.setAttribute("aria-label", "Expected and observed consequences");
  const expected = document.createElement("dl");
  field(expected, "Expected consequence", payload.expected);
  comparison.append(expected);
  const observed = document.createElement("dl"),
    outcome = recordedOutcome(record);
  if (outcome) {
    byId.set(outcome.id, outcome);
    const value = outcome.detail.payload;
    field(observed, "Observed consequence", value.observed);
    observed.append(statusBadge(value.assessment));
    if (value.completion)
      observed.append(statusBadge(value.completion, labels(value.completion)));
    if (outcome.status === "needs_review")
      observed.append(
        statusBadge(outcome.status, "Outcome evidence needs review"),
      );
    reference(observed, outcome.id, "Read the assessment and its evidence.");
  } else
    field(
      observed,
      "Observed consequence",
      data.live
        ? "No outcome has been recorded for this decision."
        : "No outcome is included for this decision in this export.",
    );
  comparison.append(observed);
  body.append(comparison);
  const conditions = document.createElement("dl");
  conditions.className = "record-content";
  for (const key of ["uncertainty", "alternatives", "reconsider_when"])
    field(conditions, key, payload[key]);
  for (const [key, value] of Object.entries(payload))
    if (
      ![
        "decision",
        "why",
        "expected",
        "uncertainty",
        "alternatives",
        "reconsider_when",
        "project_revision",
        "work_plan_id",
      ].includes(key)
    )
      field(conditions, key, value);
  if (detail.review_reasons?.length)
    field(conditions, "Why this needs review", detail.review_reasons);
  if (pending.has(id)) field(conditions, "Follow-up", pending.get(id));
  body.append(conditions);
  const evidence = document.createElement("section");
  evidence.className = "decision-evidence references";
  const h = document.createElement("h3");
  h.textContent = "Evidence for this decision";
  evidence.append(h);
  for (const ref of detail.evidence || [])
    reference(
      evidence,
      ref.source_id,
      ref.reason + (ref.status ? " · " + labels(ref.status) : ""),
    );
  for (const ref of detail.links || [])
    reference(evidence, ref.event_id, ref.reason);
  reference(
    evidence,
    "direction_" + (payload.project_revision ?? 0),
    "Requirements recorded with this choice.",
  );
  if (payload.work_plan_id)
    reference(
      evidence,
      payload.work_plan_id,
      "Work plan recorded with this choice.",
    );
  body.append(evidence);
  const history = document.createElement("section");
  history.className = "lineage";
  const heading = document.createElement("h3");
  heading.textContent = "Decision history";
  history.append(heading);
  const list = document.createElement("ol");
  list.className = "timeline";
  history.append(list);
  body.append(history);
  if (detail.supersedes) {
    const previous = document.createElement("li");
    previous.className = "history-entry";
    reference(
      previous,
      detail.supersedes,
      "Earlier choice and its recorded outcome.",
    );
    list.append(previous);
  }
  list.append(historyEntry(record, true));
  let offset = 0,
    more = true;
  const target = refresh ? decisionHistoryShown.get(id) || 10 : 10;
  const load = async () => {
    button.disabled = true;
    try {
      const result = data.live
        ? await api("records", { related: id, offset, limit: 10 })
        : {
            records: data.records
              .filter(
                (r) =>
                  r.detail.decision_id === id ||
                  r.detail.supersedes === id ||
                  (r.detail.links || []).some((l) => l.event_id === id),
              )
              .sort(
                (a, b) =>
                  a.date.localeCompare(b.date) ||
                  (a.detail.seq || 0) - (b.detail.seq || 0),
              ),
            more: false,
          };
      if (selectedId !== id) return;
      for (const entry of result.records) {
        byId.set(entry.id, entry);
        list.append(historyEntry(entry));
      }
      offset += result.records.length;
      more = result.more;
      decisionHistoryShown.set(id, offset);
      button.hidden = !more;
    } catch (error) {
      more = false;
      disconnected(error);
    } finally {
      button.disabled = false;
    }
  };
  const button = actionButton("Load more decision history", load);
  history.append(button);
  showDetail(refresh, scroll);
  do {
    await load();
  } while (more && offset < target && selectedId === id);
  if (selectedId !== id) return;
  const technical = document.createElement("details");
  technical.className = "technical";
  const summary = document.createElement("summary");
  summary.textContent = "Record details";
  technical.append(summary);
  const dl = document.createElement("dl");
  technical.append(dl);
  for (const key of [
    "id",
    "actor",
    "created_at",
    "seq",
    "episode_id",
    "request_key",
    "version",
    "session_id",
    "turn_id",
  ])
    if (detail[key] != null) field(dl, key, detail[key]);
  body.append(technical);
  if (refresh) el("detail").scrollTop = scroll;
}
function documentOutline(body) {
  body.querySelector(".document-outline")?.remove();
  const headings = [
    ...body.querySelectorAll(
      '[data-field="body"] .document :is(h1,h2,h3,h4,h5,h6)',
    ),
  ];
  if (headings.length < 2) return;
  const outline = document.createElement("details");
  outline.className = "document-outline";
  outline.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "On this page";
  outline.append(summary);
  const nav = document.createElement("nav");
  nav.setAttribute("aria-label", "Document sections");
  outline.append(nav);
  for (const heading of headings) {
    const button = actionButton(heading.textContent, () => {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
      heading.scrollIntoView({ block: "start" });
    });
    button.dataset.level = heading.tagName;
    nav.append(button);
  }
  body.querySelector('[data-field="body"]')?.before(outline);
}
