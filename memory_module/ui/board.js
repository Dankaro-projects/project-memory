async function loadSprints(reset = false) {
  if (data.live) {
    const result = await api("sprints", {
      offset: reset ? 0 : sprintOffset,
      limit: 25,
    });
    sprintRows = reset ? result.sprints : [...sprintRows, ...result.sprints];
    sprintOffset = (reset ? 0 : sprintOffset) + result.sprints.length;
    el("more-sprints").hidden = !result.more;
  }
  const chosen = el("sprint").value;
  if (
    data.live &&
    chosen &&
    chosen !== "unassigned" &&
    !sprintRows.some((s) => s.id === chosen)
  ) {
    const selected = await api("sprints", { episode: chosen, limit: 1 });
    sprintRows.push(...selected.sprints);
  }
  el("sprint").replaceChildren();
  option("sprint", "", "All sprints");
  option("sprint", "unassigned", "Unassigned work");
  for (const sprint of sprintRows)
    option(
      "sprint",
      sprint.id,
      sprint.title +
        (sprint.schedule ? " · " + labels(sprint.schedule.status) : ""),
    );
  if (chosen && ![...el("sprint").options].some((o) => o.value === chosen))
    option("sprint", chosen, "Selected sprint");
  el("sprint").value = chosen;
  el("edit-sprint").disabled = !sprintRows.some((s) => s.id === chosen);
}
async function renderBoard() {
  const request = ++renderVersion,
    size = Number(el("page-size").value),
    filter = {
      limit: size,
      offset: (page - 1) * size,
      sprint_id: el("sprint").value,
      subject: el("subject").value,
      query: el("query").value,
      state: el("status").value,
    };
  let result;
  if (data.live) {
    result = await api("board", filter);
    if (request !== renderVersion) return;
  } else {
    const all = (data.work || []).filter(
      (r) =>
        (!filter.subject || r.subject === filter.subject) &&
        (!filter.sprint_id ||
          (filter.sprint_id === "unassigned"
            ? !r.plan?.sprint_id
            : r.plan?.sprint_id === filter.sprint_id)) &&
        (!filter.query ||
          JSON.stringify(r).toLowerCase().includes(filter.query.toLowerCase())),
    );
    const counts = Object.fromEntries(
      workStates.map((state) => [
        state,
        all.filter((r) => r.state === state).length,
      ]),
    );
    const rows = all.filter((r) => !filter.state || r.state === filter.state),
      slice = pageRecords(rows, page, size);
    page = slice.page;
    result = { cards: slice.records, counts, total: rows.length };
  }
  const pages = Math.max(1, Math.ceil(result.total / size));
  if (page > pages) {
    page = pages;
    return renderBoard();
  }
  const selected = workById.get(selectedId);
  workById.clear();
  for (const card of result.cards) workById.set(card.id, card);
  if (selected) workById.set(selected.id, selected);
  const focusedCard = el("board-grid").contains(document.activeElement)
    ? document.activeElement.dataset.episode
    : null;
  el("board-grid").replaceChildren();
  for (const state of workStates) {
    if (filter.state && filter.state !== state) continue;
    if (
      !filter.state &&
      !el("show-empty").checked &&
      !result.counts[state] &&
      (result.total || state !== "backlog")
    )
      continue;
    const column = document.createElement("section");
    column.className = "work-column";
    column.dataset.state = state;
    column.setAttribute("aria-label", labels(state));
    const h = document.createElement("h2");
    h.textContent = labels(state);
    const count = document.createElement("span");
    count.textContent = result.counts[state] || 0;
    h.append(count);
    column.append(h);
    const cards = result.cards.filter((r) => r.state === state);
    for (const card of cards) {
      const button = document.createElement("button");
      button.className = "work-card";
      button.dataset.state = card.state;
      button.dataset.episode = card.id;
      button.setAttribute("aria-label", "Open work " + card.title);
      const meta = document.createElement("span");
      meta.className = "work-meta";
      meta.textContent =
        labels(card.subject) +
        " · " +
        labels(card.plan ? card.plan.owner || "agent" : "Unassigned") +
        (card.plan?.priority === "high" ? " · High priority" : "");
      const title = document.createElement("strong");
      title.textContent = card.title;
      button.append(meta, title);
      if (card.plan?.next_action) {
        const next = document.createElement("p");
        next.textContent = card.plan.next_action;
        button.append(next);
      }
      const foot = document.createElement("div");
      foot.className = "card-foot";
      if (card.issues.length) {
        const issue = document.createElement("span");
        issue.className = "badge";
        issue.dataset.state = "needs_review";
        issue.textContent = card.plan
          ? card.issues.length +
            " " +
            (card.issues.length === 1 ? "issue" : "issues")
          : "Needs a plan";
        issue.title = card.issues.map((i) => i.reason).join("\n");
        foot.append(issue);
      }
      const lineage = document.createElement("span");
      lineage.className = "lineage-label";
      lineage.textContent = card.decision_id
        ? "Decision history"
        : "View action";
      foot.append(lineage);
      button.append(foot);
      button.addEventListener("click", () => openWork(card.id));
      column.append(button);
    }
    if (!cards.length) {
      const empty = document.createElement("p");
      empty.className = "column-empty";
      empty.textContent = result.counts[state]
        ? "No cards on this page."
        : result.total
          ? "No actions."
          : data.live
            ? "Create an action to start planning."
            : "No actions in this snapshot.";
      column.append(empty);
    }
    el("board-grid").append(column);
  }
  if (focusedCard) {
    const current = [...el("board-grid").querySelectorAll(".work-card")].find(
      (card) => card.dataset.episode === focusedCard,
    );
    (current || document.querySelector("[data-view=board]")).focus();
  }
  const sprint = sprintRows.find((r) => r.id === filter.sprint_id);
  el("sprint-summary").replaceChildren();
  if (sprint) {
    const title = document.createElement("strong");
    title.textContent = sprint.title;
    const p = document.createElement("p");
    p.textContent = sprint.intent;
    el("sprint-summary").append(title, p);
    if (sprint.schedule) {
      const dates = document.createElement("span");
      dates.textContent =
        sprint.schedule.starts_on +
        " – " +
        sprint.schedule.ends_on +
        " · " +
        labels(sprint.schedule.status);
      el("sprint-summary").append(dates);
    }
  }
  el("count").textContent = result.total
    ? `${(page - 1) * size + 1}–${Math.min(page * size, result.total)} of ${result.total} work items`
    : "No work matches these filters.";
  el("page").textContent = `Page ${page} of ${pages}`;
  el("previous").disabled = page === 1;
  el("next").disabled = page === pages;
  el("pending-note").hidden = true;
}
async function openWork(id, refresh = false) {
  extensionDetail = false;
  selectDetail(id, refresh);
  coverageSession = null;
  selectedId = id;
  workDetail = true;
  const scroll = el("detail").scrollTop;
  try {
    if (data.live) {
      const result = await api("board", { episode: id, limit: 1 });
      if (selectedId !== id) return;
      if (result.cards[0]) workById.set(id, result.cards[0]);
    }
    const work = workById.get(id);
    if (!work) return;
    el("detail-title").textContent = work.title;
    const body = el("detail-body");
    body.replaceChildren();
    metadata(body, { ...work, kind: "Work item" });
    if (data.live) {
      const actions = document.createElement("div");
      actions.className = "work-actions";
      for (const [label, fn] of [
        ["Edit plan", () => editWork("plan", work)],
        ["Add comment", () => editWork("comment", work)],
      ]) {
        const button = document.createElement("button");
        button.textContent = label;
        button.addEventListener("click", fn);
        actions.append(button);
      }
      body.append(actions);
    }
    const intent = document.createElement("section");
    intent.className = "intent-panel";
    const h = document.createElement("h3");
    h.textContent = "Intended result";
    const p = document.createElement("p");
    p.textContent = work.intent;
    intent.append(h, p);
    body.append(intent);
    const dl = document.createElement("dl");
    dl.className = "work-reading";
    body.append(dl);
    if (work.completion_next && work.state !== "done") {
      field(dl, "Next step", work.completion_next.reason);
      const read = work.completion_next.read_with;
      if (read?.view === "record")
        dl.lastElementChild.append(actionButton(
          work.completion_next.action === "refresh_evidence" ? "Inspect evidence" : "Read outcome",
          () => open(read.id),
        ));
      else if (read?.view === "reviews")
        dl.lastElementChild.append(actionButton("Inspect review", () => {
          const checks = body.querySelector(".agent-checks");
          if (checks) { checks.open = true; checks.scrollIntoView({block: "start"}); }
        }));
    }
    if (work.plan?.next_action)
      field(dl, work.completion_next ? "Planned next action" : "next_action", work.plan.next_action);
    field(dl, "done_when", work.done_when);
    const properties = document.createElement("dl");
    properties.className = "work-properties";
    body.append(properties);
    field(properties, "status", labels(work.state));
    if (work.outcome)
      field(properties, "Recorded result", labels(work.outcome.assessment) + " · " + labels(work.outcome.completion || "Unmeasured"));
    if (work.agent_check)
      field(properties, "Outcome review", labels(work.agent_check.state));
    if (work.recorded_state !== work.state)
      field(properties, "Recorded state", labels(work.recorded_state));
    if (work.plan) {
      for (const key of ["scope", "autonomy", "owner", "reason"])
        if (work.plan[key])
          field(
            ["autonomy", "owner"].includes(key) ? properties : dl,
            key === "scope" ? "Work scope" : key,
            work.plan[key],
          );
      const planButton = document.createElement("button");
      planButton.textContent = "Read work plan and evidence";
      planButton.addEventListener("click", () => open(work.plan.id));
      body.append(planButton);
    }
    if (work.issues.length) field(dl, "What needs attention", work.issues);
    if (data.live) {
      await showChecks(body, work);
      await showWorkSkills(body, work);
      body.append(
        actionButton("View project map", () => openProjectMap(work.id)),
      );
    }
    for (const ref of work.plan?.depends_on || []) {
      const button = document.createElement("button");
      button.textContent = "Inspect dependency";
      button.addEventListener("click", () => openWork(ref.episode_id));
      const p = document.createElement("p");
      p.append(button, document.createTextNode(" " + ref.reason));
      body.append(p);
    }
    const section = document.createElement("section");
    section.className = "lineage";
    const heading = document.createElement("h3");
    heading.textContent = "Activity and decisions";
    section.append(heading);
    const list = document.createElement("ol");
    list.className = "timeline";
    section.append(list);
    body.append(section);
    const more = document.createElement("button");
    more.textContent = "Load more history";
    section.append(more);
    let offset = 0;
    const add = (record) => {
      byId.set(record.id, record);
      list.append(historyEntry(record));
    };
    const target = refresh ? workHistoryShown.get(id) || 10 : 10;
    let hasMore = true;
    const load = async () => {
      more.disabled = true;
      try {
        const result = await api("records", {
          view: "events",
          episode: id,
          order: "oldest",
          limit: 10,
          offset,
        });
        if (selectedId !== id || !workDetail) return;
        for (const record of result.records) add(record);
        offset += result.records.length;
        workHistoryShown.set(id, offset);
        hasMore = result.more;
        more.hidden = !result.more;
        if (!offset) {
          const p = document.createElement("p");
          p.textContent = "No decisions or actions have been recorded.";
          section.append(p);
        }
      } catch (error) {
        hasMore = false;
        disconnected(error);
      } finally {
        more.disabled = false;
      }
    };
    if (data.live) {
      do {
        await load();
      } while (hasMore && offset < target && selectedId === id && workDetail);
    } else {
      for (const record of data.records
        .filter((r) => r.episode_id === id && r.kind !== "episode")
        .sort((a, b) => (a.detail.seq || 0) - (b.detail.seq || 0)))
        add(record);
      more.hidden = true;
    }
    more.addEventListener("click", load);
    if (selectedId !== id || !workDetail) return;
    showDetail(refresh, scroll);
  } catch (error) {
    disconnected(error);
  }
}
el("sprint").addEventListener("input", () => {
  page = 1;
  el("edit-sprint").disabled = !sprintRows.some(
    (s) => s.id === el("sprint").value,
  );
  render();
});
el("more-sprints").addEventListener("click", () =>
  loadSprints().catch(disconnected),
);
for (const button of document.querySelectorAll("[data-view=board]"))
  button.addEventListener("click", () =>
    loadSprints(true)
      .then(() => render())
      .catch(disconnected),
  );
loadSprints().catch(disconnected);
