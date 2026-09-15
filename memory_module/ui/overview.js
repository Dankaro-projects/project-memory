async function renderOverview() {
  const version = ++renderVersion;
  let value;
  if (data.live) value = await api("overview");
  else {
    const groups = Object.fromEntries(
      workStates.map((s) => [
        s,
        (data.work || []).filter((w) => w.state === s),
      ]),
    );
    const decisions = data.records
      .filter((r) => r.kind === "decision")
      .sort((a, b) => b.date.localeCompare(a.date));
    const lessons = data.records.filter(
      (r) => r.kind === "lesson" && r.status === "proposed",
    );
    value = {
      work: {
        counts: Object.fromEntries(
          workStates.map((s) => [s, groups[s].length]),
        ),
        groups: Object.fromEntries(
          workStates.map((s) => [s, groups[s].slice(0, 3)]),
        ),
      },
      decisions: { records: decisions.slice(0, 4), total: decisions.length },
      lessons: { records: lessons.slice(0, 3), total: lessons.length },
    };
  }
  if (view !== "overview" || version !== renderVersion) return;
  const root = el("extension-view"),
    focused = root.contains(document.activeElement)
      ? document.activeElement.dataset.focus
      : null;
  if (data.live) {
    const selected = byId.get(selectedId),
      selectedWork = workById.get(selectedId);
    byId.clear();
    workById.clear();
    if (selected) byId.set(selectedId, selected);
    if (selectedWork) workById.set(selectedId, selectedWork);
  }
  const content = document.createElement("div");
  content.className = "project-overview";
  const intro = document.createElement("section");
  intro.className = "overview-intro";
  const title = document.createElement("h2");
  title.textContent = data.live
    ? captureHealth?.project || data.project
    : data.project;
  intro.append(title);
  const baseline = document.createElement("p");
  const requirements = data.live
    ? captureHealth?.requirements || []
    : data.requirements;
  baseline.textContent =
    data.live && captureHealth?.baseline.status === "not_established"
      ? "Project requirements have not been agreed."
      : requirements.length + " recorded requirements";
  intro.append(
    baseline,
    actionButton("Read project requirements", () => setView("direction")),
  );
  if (!data.live) {
    const scope = document.createElement("p");
    scope.className = "snapshot-scope";
    scope.textContent =
      "Snapshot · " +
      (Object.entries(data.scope)
        .filter(([, v]) => v)
        .map(([k, v]) => labels(k) + ": " + v)
        .join(" · ") || "Exported project records") +
      " · " +
      dateLabel(data.exported_at);
    intro.append(scope);
  }
  content.append(intro);
  const columns = document.createElement("div");
  columns.className = "overview-columns";
  content.append(columns);
  const main = document.createElement("div"),
    aside = document.createElement("aside");
  aside.className = "overview-attention";
  aside.setAttribute("aria-label", "Needs attention");
  columns.append(main, aside);
  const section = (parent, title, count, open) => {
    const group = document.createElement("section");
    group.className = "overview-section";
    const head = document.createElement("div");
    head.className = "section-heading";
    const h = document.createElement("h3");
    h.textContent = title;
    if (count !== undefined) {
      const n = document.createElement("span");
      n.className = "section-count";
      n.textContent = count;
      h.append(n);
    }
    head.append(h);
    if (open) head.append(actionButton("View all", open));
    group.append(head);
    parent.append(group);
    return group;
  };
  const showWork = (state) => {
    el("sprint").value = "";
    setView("board", { query: "", subject: "", status: state });
  };
  const workRow = (work) => {
    workById.set(work.id, work);
    const row = actionButton(work.title, () => openWork(work.id));
    row.className = "overview-work";
    row.dataset.focus = work.id;
    row.setAttribute("aria-label", "Open work " + work.title);
    const title = document.createElement("strong");
    title.textContent = work.title;
    const next = document.createElement("span");
    next.className = "work-next";
    next.textContent = work.plan?.next_action || work.intent;
    row.replaceChildren(statusBadge(work.state), title, next);
    if (work.issues.length) {
      const reason = document.createElement("span");
      reason.className = "attention-reason";
      reason.textContent = work.issues.map((i) => i.reason).join(" ");
      row.append(reason);
    }
    return row;
  };
  for (const [state, title] of [
    ["in_progress", "In progress"],
    ["ready", "Ready to start"],
  ]) {
    const group = section(main, title, value.work.counts[state], () =>
      showWork(state),
    );
    for (const work of value.work.groups[state]) group.append(workRow(work));
    if (!value.work.counts[state]) {
      const p = document.createElement("p");
      p.className = "section-empty";
      p.textContent =
        state === "in_progress"
          ? "No work is recorded as in progress."
          : "No actions are ready to start.";
      group.append(p);
    }
  }
  const recent = section(main, "Recent decisions", value.decisions.total, () =>
    setView("decisions", {
      query: "",
      subject: "",
      episode: "",
      status: "",
      from: "",
      to: "",
    }),
  );
  for (const record of value.decisions.records) {
    byId.set(record.id, record);
    const card = recordSummary(record);
    card.querySelector("button").dataset.focus = record.id;
    recent.append(card);
  }
  if (!value.decisions.total) {
    const p = document.createElement("p");
    p.className = "section-empty";
    p.textContent = "Recorded decisions and their outcomes will appear here.";
    recent.append(p);
  }
  const attention = section(aside, "Needs attention", undefined, () =>
    setView("attention"),
  );
  for (const state of ["blocked", "review"]) {
    for (const work of value.work.groups[state])
      attention.append(workRow(work));
    if (value.work.counts[state] > value.work.groups[state].length)
      attention.append(
        actionButton("View all " + labels(state).toLowerCase() + " work", () =>
          showWork(state),
        ),
      );
  }
  if (!value.work.counts.blocked && !value.work.counts.review) {
    const p = document.createElement("p");
    p.className = "section-empty";
    p.textContent = "No work is recorded as blocked or awaiting review.";
    attention.append(p);
  }
  if (value.lessons.total) {
    const lessons = section(
      aside,
      "Lessons awaiting review",
      value.lessons.total,
      () =>
        setView("lessons", {
          query: "",
          subject: "",
          episode: "",
          status: "proposed",
          from: "",
          to: "",
        }),
    );
    for (const record of value.lessons.records) {
      byId.set(record.id, record);
      const button = actionButton(record.title, () => open(record.id));
      button.dataset.focus = record.id;
      button.className = "overview-lesson";
      lessons.append(button);
    }
  }
  if (
    data.live &&
    (captureHealth?.recording_checks?.sessions.some(
      (s) => s.status === "needs_attention",
    ) ||
      captureHealth?.capture?.failure)
  ) {
    const checks = section(aside, "Recording checks");
    if (captureHealth.capture.failure) {
      const p = document.createElement("p");
      p.textContent = "Host capture failed. Some activity may be missing.";
      checks.append(p);
    }
    for (const session of captureHealth.recording_checks.sessions.filter(
      (s) => s.status === "needs_attention",
    ))
      checks.append(
        actionButton(session.issues.map((i) => labels(i.type)).join(", "), () =>
          showCoverage(session.session_id),
        ),
      );
  }
  const planning = section(aside, "Plan work");
  planning.append(actionButton("Open work board", () => setView("board")));
  if (data.live)
    planning.append(actionButton("New action", () => editWork("plan")));
  root.replaceChildren(content);
  if (focused)
    root.querySelector('[data-focus="' + CSS.escape(focused) + '"]')?.focus();
}
