let overviewState = null;

async function renderOverview() {
  const version = ++renderVersion;
  let value;
  if (data.live) value = await api("overview");
  else {
    const groups = Object.fromEntries(workStates.map(s => [s, (data.work || []).filter(w => w.state === s)]));
    const latest = new Map();
    for (const record of data.records.filter(r => r.kind === "decision").sort((a, b) => b.date.localeCompare(a.date) || (b.detail.seq || 0) - (a.detail.seq || 0))) {
      const key = record.episode_id || record.id;
      if (!latest.has(key)) latest.set(key, record);
    }
    const lessons = data.records.filter(r => r.kind === "lesson" && r.status === "proposed");
    value = {
      work: {
        counts: Object.fromEntries(workStates.map(s => [s, groups[s].length])),
        groups: Object.fromEntries(workStates.map(s => [s, groups[s].slice(0, 3)])),
      },
      decisions: { records: [...latest.values()].slice(0, 3), total: latest.size },
      lessons: { records: lessons.slice(0, 3), total: lessons.length },
    };
  }
  if (view !== "overview" || version !== renderVersion) return;
  const root = el("extension-view");
  const focused = root.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
  if (data.live) {
    const selected = byId.get(selectedId), selectedWork = workById.get(selectedId);
    byId.clear(); workById.clear();
    if (selected) byId.set(selectedId, selected);
    if (selectedWork) workById.set(selectedId, selectedWork);
  }
  const content = document.createElement("div");
  content.className = "project-overview";
  content.dataset.recordingSummary = String(data.live && !captureHealth?.recording_checks?.more);
  const control = (key, title, handler) => {
    const button = actionButton(title, handler);
    button.dataset.focus = key;
    return button;
  };
  const intro = document.createElement("header"); intro.className = "overview-intro";
  const context = document.createElement("div"); context.className = "overview-context";
  const title = document.createElement("h2");
  title.textContent = data.live ? captureHealth?.project || data.project : data.project;
  const requirements = data.live ? captureHealth?.requirements || [] : data.requirements;
  const requirement = control("requirements",
    data.live && captureHealth?.baseline.status === "not_established" ? "Review project requirements"
      : requirements.length + " project requirement" + (requirements.length === 1 ? "" : "s"),
    () => setView("direction"),
  );
  requirement.className = "overview-requirements";
  requirement.setAttribute("aria-label", "Read project requirements");
  context.append(title, requirement);
  const actions = document.createElement("div"); actions.className = "overview-actions";
  actions.append(control("work-board", "Open work board", () => setView("board")));
  if (data.live) {
    const create = control("new-action", "New action", () => editWork("plan")); create.className = "primary"; actions.append(create);
  }
  intro.append(context, actions); content.append(intro);
  if (!data.live) {
    const scope = document.createElement("p"); scope.className = "snapshot-scope";
    scope.textContent = "Snapshot · " + (Object.entries(data.scope).filter(([, v]) => v).map(([k, v]) => labels(k) + ": " + v).join(" · ") || "Exported project records") + " · " + dateLabel(data.exported_at);
    content.append(scope);
  }
  const columns = document.createElement("div"); columns.className = "overview-columns";
  const workPanel = document.createElement("section"), decisions = document.createElement("section");
  workPanel.className = "overview-panel overview-work-panel";
  decisions.className = "overview-panel overview-decisions";
  const heading = (parent, text, note) => {
    const head = document.createElement("div"); head.className = "overview-panel-heading";
    const h = document.createElement("h3"); h.textContent = text; head.append(h);
    if (note) { const p = document.createElement("p"); p.textContent = note; head.append(p); }
    parent.append(head);
  };
  const attentionCount = value.work.counts.blocked + value.work.counts.review;
  heading(workPanel, "Work", attentionCount ? attentionCount + " item" + (attentionCount === 1 ? " needs" : "s need") + " attention" : "Your current work and next actions");
  heading(decisions, "Latest decisions", "The latest choice for each work item");
  columns.append(workPanel, decisions); content.append(columns);
  const showWork = state => {
    el("sprint").value = "";
    setView("board", { query: "", subject: "", status: state });
  };
  const statusNames = { in_progress: "In progress", ready: "Ready", blocked: "Blocked", review: "In review" };
  const tabs = document.createElement("nav"); tabs.className = "overview-status"; tabs.setAttribute("aria-label", "Work by status");
  const list = document.createElement("div"); list.id = "overview-work-list"; list.className = "overview-work-list";
  const footer = document.createElement("div"); footer.className = "overview-panel-footer";
  workPanel.append(tabs, list, footer);
  const workRow = work => {
    workById.set(work.id, work);
    const row = actionButton(work.title, () => openWork(work.id)); row.className = "overview-work";
    row.dataset.state = work.state; row.dataset.focus = work.id; row.setAttribute("aria-label", "Open work " + work.title);
    const mark = document.createElement("span"); mark.className = "overview-status-mark"; mark.setAttribute("aria-hidden", "true");
    const text = document.createElement("span"); text.className = "overview-work-text";
    const title = document.createElement("strong"); title.textContent = work.title;
    const next = document.createElement("span"); next.className = "work-next"; next.textContent = work.plan?.next_action || work.intent;
    text.append(title, next);
    if (work.completion_next || work.issues.length) {
      const guidance = document.createElement("span"); guidance.className = "overview-guidance";
      const step = work.completion_next; guidance.dataset.action = step?.action || "resolve";
      guidance.textContent = step
        ? ({ finalize: "Ready to finish", reconcile: "Check execution", refresh_evidence: "Review evidence", wait_review: "Review running", assess_coverage: "Review recording gaps" }[step.action] || labels(step.action))
        : "Review blockers";
      text.append(guidance);
    }
    const arrow = document.createElement("span"); arrow.className = "overview-row-arrow"; arrow.textContent = "›"; arrow.setAttribute("aria-hidden", "true");
    row.replaceChildren(mark, text, arrow); return row;
  };
  const selectWork = state => {
    overviewState = state;
    for (const button of tabs.children) button.setAttribute("aria-pressed", String(button.dataset.state === state));
    list.replaceChildren(...value.work.groups[state].map(workRow)); list.setAttribute("aria-label", statusNames[state] + " work");
    if (!value.work.counts[state]) {
      const empty = document.createElement("div"); empty.className = "overview-empty";
      const h = document.createElement("strong"); h.textContent = { in_progress: "No work in progress", ready: "No work ready to start", blocked: "No blocked work", review: "No work awaiting review" }[state];
      const p = document.createElement("p"); p.textContent = "Choose another status or open the work board.";
      empty.append(h, p); list.append(empty);
    }
    const count = document.createElement("span");
    count.textContent = value.work.counts[state] ? "Showing " + value.work.groups[state].length + " of " + value.work.counts[state] : "0 items";
    footer.replaceChildren(count, control("all-work", "View all " + statusNames[state].toLowerCase() + " work", () => showWork(state)));
  };
  for (const [state, label] of Object.entries(statusNames)) {
    const button = actionButton(label, () => selectWork(state)); button.dataset.state = state; button.dataset.focus = "status-" + state;
    button.setAttribute("aria-controls", list.id); button.setAttribute("aria-label", label + ": " + value.work.counts[state]);
    const count = document.createElement("span"); count.textContent = value.work.counts[state]; button.append(count); tabs.append(button);
  }
  selectWork(overviewState || ["in_progress", "blocked", "review", "ready"].find(s => value.work.counts[s]) || "in_progress");
  const decisionList = document.createElement("div"); decisionList.className = "overview-decision-list"; decisions.append(decisionList);
  for (const record of value.decisions.records) {
    byId.set(record.id, record);
    const button = actionButton(record.title, () => open(record.id)); button.className = "overview-decision"; button.dataset.focus = record.id;
    button.setAttribute("aria-label", "Open " + record.title);
    const title = document.createElement("strong"); title.textContent = record.episode_title || record.title;
    const choice = document.createElement("span"); choice.className = "overview-choice"; choice.textContent = record.detail.payload?.decision || record.title;
    const meta = document.createElement("span"); meta.className = "overview-decision-meta";
    const outcome = recordedOutcome(record);
    if (outcome) meta.append(statusBadge(outcome.detail.payload.assessment));
    else { const missing = document.createElement("span"); missing.textContent = data.live ? "No outcome yet" : "No outcome in export"; meta.append(missing); }
    if (record.status === "needs_review" || outcome?.status === "needs_review") meta.append(statusBadge("needs_review", "Evidence needs review"));
    const date = document.createElement("time"); date.dateTime = record.date;
    date.textContent = record.date ? new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(new Date(record.date)) : "";
    meta.append(date); button.replaceChildren(title, choice, meta); decisionList.append(button);
  }
  if (!value.decisions.records.length) {
    const empty = document.createElement("div"); empty.className = "overview-empty";
    const h = document.createElement("strong"); h.textContent = "No decisions yet";
    const p = document.createElement("p"); p.textContent = "Recorded choices and outcomes will appear here.";
    empty.append(h, p); decisionList.append(empty);
  }
  const decisionFooter = document.createElement("div"); decisionFooter.className = "overview-panel-footer";
  const shown = document.createElement("span"); shown.textContent = "Showing " + value.decisions.records.length + " of " + value.decisions.total + " work items";
  decisionFooter.append(shown, control("decision-history", "View decision history", () => setView("decisions", { query: "", subject: "", episode: "", status: "", from: "", to: "" })));
  decisions.append(decisionFooter);
  const links = document.createElement("nav"); links.className = "overview-followups"; links.setAttribute("aria-label", "Project reviews");
  if (value.lessons.total) links.append(control("lessons", value.lessons.total + " lesson" + (value.lessons.total === 1 ? "" : "s") + " awaiting review", () => setView("lessons", { query: "", subject: "", episode: "", status: "proposed", from: "", to: "" })));
  const checks = data.live ? captureHealth?.recording_checks?.sessions.filter(s => s.status === "needs_attention") || [] : [];
  if (checks.length || captureHealth?.capture?.failure) links.append(control("recording-checks", captureHealth?.capture?.failure ? "Capture needs attention" : checks.length + " recording check" + (checks.length === 1 ? "" : "s"), () => checks.length === 1 ? showCoverage(checks[0].session_id) : setView("attention")));
  links.append(control("attention", "Open attention list", () => setView("attention")));
  content.append(links); root.replaceChildren(content);
  if (focused) (root.querySelector('[data-focus="' + CSS.escape(focused) + '"]') || tabs.querySelector('[aria-pressed="true"]'))?.focus();
}
