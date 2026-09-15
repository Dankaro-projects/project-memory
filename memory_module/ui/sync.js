let coverageSession = null;
let captureHealth = null;
async function showCoverage(session, refresh = false) {
  const scroll = el("detail").scrollTop;
  coverageSession = session;
  try {
    const result = await api("coverage", { session_id: session });
    if (coverageSession !== session) return;
    selectedId = null;
    workDetail = false;
    el("detail-title").textContent = "Recording checks";
    el("detail-kind").textContent = "Host session";
    const body = el("detail-body");
    body.replaceChildren();
    const dl = document.createElement("dl");
    body.append(dl);
    field(
      dl,
      "Recording state",
      result.issues.length
        ? "Some records remain unresolved."
        : "The observed activity has an explicit assessment.",
    );
    for (const issue of result.issues)
      field(dl, labels(issue.type), issue.reason);
    field(dl, "Assessment", result.meaning);
    const refs = document.createElement("div");
    refs.className = "references";
    body.append(refs);
    const append = (value) => {
      for (const prompt of value.pending_prompts)
        reference(refs, prompt.id, "Inspect the unassessed request receipt");
      for (const gap of value.capture_gaps)
        reference(refs, gap, "Inspect the capture gap");
      for (const item of value.unconfirmed)
        reference(refs, item.id, "Inspect the uncertain tool result");
      for (const item of value.open_decisions)
        reference(
          refs,
          item.decision_id,
          "Inspect the decision without an outcome",
        );
    };
    append(result);
    if (result.more) {
      const more = document.createElement("button");
      more.textContent = "Show more records";
      let offset = result.next_offset;
      body.append(more);
      more.addEventListener("click", async () => {
        more.disabled = true;
        try {
          const page = await api("coverage", { session_id: session, offset });
          if (coverageSession !== session) return;
          append(page);
          offset = page.next_offset;
          more.hidden = !page.more;
        } catch (error) {
          disconnected(error);
        } finally {
          more.disabled = false;
        }
      });
    }
    showDetail(refresh, scroll);
  } catch (error) {
    disconnected(error);
  }
}
function updateHealth(value) {
  captureHealth = value;
  csrf = value.csrf;
  reviewHost = value.review_host;
  el("workspace-actions").hidden = !value.interactive;
  const recording = el("recording-status");
  recording.replaceChildren();
  const checks = (value.recording_checks?.sessions || []).filter(
    (s) => s.status === "needs_attention",
  );
  recording.hidden = !checks.length && !value.capture?.failure;
  if (value.capture?.failure)
    recording.append(
      document.createTextNode(
        "Host capture failed. Some activity may be missing. ",
      ),
    );
  for (const session of checks) {
    const button = document.createElement("button");
    button.textContent =
      "Recording checks: " +
      session.issues.map((i) => labels(i.type)).join(", ");
    button.addEventListener("click", () => showCoverage(session.session_id));
    recording.append(button);
  }
  if (value.recording_checks?.more) {
    recording.hidden = false;
    const more = document.createElement("button");
    more.textContent = "More recording checks";
    more.addEventListener("click", async () => {
      try {
        const page = await api("coverage", {
          offset: value.recording_checks.next_offset,
        });
        updateHealth({ ...value, recording_checks: page });
      } catch (error) {
        disconnected(error);
      }
    });
    recording.append(more);
  }
  el("project").textContent = value.project;
  el("scope").textContent =
    "Project baseline: " +
    labels(value.baseline.status) +
    ". Product coverage has not been assessed.";
  el("requirements").replaceChildren();
  for (const rule of value.requirements) {
    const li = document.createElement("li");
    li.textContent = rule;
    el("requirements").append(li);
  }
  const chosen = el("episode").value;
  el("episode").replaceChildren();
  option("episode", "", "All episodes");
  for (const episode of value.episodes)
    option("episode", episode.id, episode.title);
  if (chosen && ![...el("episode").options].some((o) => o.value === chosen))
    option("episode", chosen, byId.get(chosen)?.title || chosen);
  el("episode").value = chosen;
  el("overview").textContent =
    value.decisions +
    " decisions · " +
    value.assessed +
    " recorded assessments. " +
    Object.entries(value.measurements)
      .map(
        ([key, measure]) =>
          labels(key) +
          ": " +
          (measure.reported_decisions
            ? measure.total +
              " reported across " +
              measure.reported_decisions +
              " decisions"
            : "not measured"),
      )
      .join(" · ");
  if (value.episodes_more)
    el("overview").textContent +=
      " The episode selector shows the latest 1,000 episodes; all episodes remain accessible through the paged Episodes view.";
}
async function poll() {
  if (polling || document.hidden) return;
  polling = true;
  const failuresAtStart = refreshFailures;
  try {
    if (refreshPending) healthTag = null;
    const value = await api("health", {}, true);
    if (value) {
      updateHealth(value);
      if (coverageSession && el("detail").open)
        await showCoverage(coverageSession, true);
      if (view === "board") await loadSprints(true);
      await loadPage();
      if (
        selectedId &&
        el("detail").open &&
        !el("editor").open &&
        !extensionDetail
      ) {
        if (workDetail) await openWork(selectedId, true);
        else await open(selectedId, true);
      }
    }
    if (
      refreshFailures !== failuresAtStart ||
      (refreshPending && el("detail").open && el("editor").open)
    ) {
      healthTag = null;
      return;
    }
    refreshPending = false;
    lastSuccess = new Date().toLocaleTimeString();
    el("detail-connection").hidden = true;
    el("connection").hidden = true;
    el("live-status").textContent = "Live";
    el("live-status").dataset.state = "connected";
    el("live-status").title = "Last checked " + lastSuccess;
  } catch (error) {
    healthTag = null;
    disconnected(error);
  } finally {
    polling = false;
  }
}
function startLive() {
  el("live-status").textContent = "Connecting";
  el("live-status").dataset.state = "snapshot";
  el("snapshot").textContent =
    "Live workspace · Plans, decisions and evidence stay in this project.";
  el("workspace-help").textContent =
    "This workspace updates automatically while open. Plan actions and sprints here or ask your assistant to record them. To reopen it, ask your assistant to open Project Memory or run project-memory view in your project folder.";
  document.querySelector("footer").textContent =
    "This workspace refreshes every two seconds while visible. Capture does not approve requirements or lessons. Run project-memory view to reconnect after the service stops.";
  for (const subject of ["code", "writing", "research", "general"])
    option("subject", subject, labels(subject));
  for (const status of [
    "active",
    "reopened",
    "settled",
    "abandoned",
    "recorded",
    "proposed",
    "accepted",
    "rejected",
    "retired",
    "replaced",
    "historical",
    "current",
    "current_copy",
    "needs_review",
    "review_due",
    "superseded",
    "file_changed",
    "file_missing",
    "file_unreadable",
    "execution_unconfirmed",
    "not_started",
    "consequence_pending",
    "consequence_unknown",
    "observed",
  ])
    option("status", status, labels(status));
  for (const key of ["subject", "episode"])
    if (initialFilters.has(key)) {
      if (key === "episode")
        option(key, initialFilters.get(key), initialFilters.get(key));
      el(key).value = initialFilters.get(key);
    }
  const requestedView = initialFilters.get("view");
  view = initialFilters.has("episode") ? "events" : initialFilters.has("subject") ? "episodes" : "overview";
  if (requestedView && document.querySelector('[data-view="'+CSS.escape(requestedView)+'"]')) view = requestedView;
  setBoardVisibility();
  for (const b of document.querySelectorAll("[data-view]"))
    b.setAttribute("aria-pressed", String(b.dataset.view === view));
  poll();
  setInterval(poll, 2000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
}
