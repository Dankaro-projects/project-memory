/*
 * Project Memory control panel: views_knowledge.js.
 *
 * Views: learning, agents, records and requirements. Drawers: record and run. Buttons open the forms of forms.js:
 * lesson_review {lesson_id, status}, merge, discard, cancel_run and request_work_review {run_id}, requirements {}.
 *
 * Endpoints read: learning {offset}, agents {offset}, run {id}, records {view, limit, offset, query, subject, status,
 * episode, from, to, order, related}, record {id, body_offset}, requirements {offset, revision_offset}. A snapshot
 * holds records {view, limit: 100} for each view, so the Records view filters and pages those in the browser.
 */
(() => {
  "use strict";
  const P = Panel;
  const h = P.h;
  const PAGE = 25;
  const SNAPSHOT_PAGE = "100";
  const ACTIVE = ["queued", "running", "cancelling"];
  const RETRY_REVIEW = ["failed", "timed_out", "cancelled", "host_unavailable", "interrupted", "stale"];
  const SUBJECTS = ["general", "code", "writing", "research"];
  const STATUSES = ["recorded", "needs_review", "review_due", "superseded", "proposed", "accepted", "rejected", "retired",
    "current_copy", "file_changed", "file_missing", "observed", "execution_unconfirmed", "current", "historical"];
  const ALL_VIEWS = ["episodes", "events", "sources", "captures", "direction"];

  // Shared helpers.
  const put = (parent, ...nodes) => parent.append(...nodes.flat(Infinity).filter((node) => node !== null && node !== undefined && node !== false));
  const number = (n) => Number(n || 0).toLocaleString("en-GB");
  const isAre = (n) => (n === 1 ? "is" : "are");
  const sentence = (text) => h("p", { class: "sentence" }, text);
  const heading = (text, extra) => h("div", { class: "row kn-heading" }, h("h3", null, text), extra || null);
  const section = (title, extra, ...children) => h("section", { class: "stack" }, heading(title, extra), children);
  const chips = (values, prefix) => (values || []).map((value) => h("span", { class: "chip mono" }, (prefix || "") + value));
  // Every cell names its column, so a narrow screen shows the table as labelled rows instead of a sideways scroll.
  const cell = (label, ...children) => h("td", { dataset: { label } }, children);
  function episodeTitle(id) {
    const found = ((P.health() || {}).episodes || []).find((item) => item.id === id);
    return found ? found.title : id;
  }
  function openButton(label, id, options = {}) {
    const open = (event) => (options.work ? P.openWork(id, event.currentTarget) : P.openRecord(id, event.currentTarget));
    return h("button", { type: "button", class: options.class || "quiet kn-link", dataset: { key: (options.prefix || "open-") + id }, on: { click: open } }, label);
  }
  function actionButton(label, key, open, tone) {
    return h("button", { type: "button", class: ["small", tone], dataset: { key }, on: { click: (event) => open(event.currentTarget) } }, label);
  }
  function valueNode(value, name) {
    if (value === null || value === undefined || value === "") return "Not recorded";
    if (Array.isArray(value)) return value.length ? h("ul", { class: "kn-plain" }, value.map((item) => h("li", null, valueNode(item)))) : "None recorded";
    if (typeof value === "object") return h("dl", { class: "kv kn-nested" }, Object.entries(value).map(([key, item]) => [h("dt", null, P.words(key)), h("dd", null, valueNode(item, key))]));
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (name && /(_at|_on|^date)$/.test(name)) return P.date(value);
    return h("span", { class: "kn-text" }, String(value));
  }
  const kv = (entries) => h("dl", { class: "kv" }, entries.filter(([, value]) => value !== undefined).map(([label, value]) => [h("dt", null, label), h("dd", null, value instanceof Node ? value : valueNode(value))]));
  function pager(info, prefix, go) {
    const { offset, count, total, more, limit } = info;
    if (!count && !offset) return null;
    const end = offset + count;
    const text = typeof total === "number" ? "Showing " + number(offset + 1) + " to " + number(end) + " of " + number(total) + "."
      : "Showing " + number(offset + 1) + " to " + number(end) + ".";
    return h("nav", { class: "row kn-pager", "aria-label": "Pages" }, h("span", { class: "muted" }, text),
      h("button", { type: "button", class: "small", disabled: offset <= 0, dataset: { key: prefix + "-previous" }, on: { click: () => go(Math.max(0, offset - limit)) } }, "Previous"),
      h("button", { type: "button", class: "small", disabled: !more, dataset: { key: prefix + "-next" }, on: { click: () => go(end) } }, "Next"));
  }
  // A section that loads on its own, so a missing snapshot key does not hide the rest of the view.
  const failed = (error) => (error && error.notIncluded ? h("p", { class: "muted" }, "This part is not included in this snapshot.") : P.errorState(error));
  // Learning.
  function lessonText(lesson) {
    return [h("p", null, h("strong", null, lesson.do || "No action is recorded.")),
      h("p", { class: "muted" }, "When " + lower(lesson.when)),
      lesson.because ? h("p", { class: "muted" }, "Because " + lower(lesson.because)) : null,
      lesson.exceptions ? h("p", { class: "muted" }, "Exceptions: " + lesson.exceptions) : null];
  }
  const lower = (text) => (text ? text.charAt(0).toLowerCase() + text.slice(1) : "no condition is recorded.");
  function triggerChips(item) {
    const parts = [...chips(item.paths, "Path "), ...chips(item.keywords, "Keyword "), ...(item.failure_type ? chips([item.failure_type], "Failure type ") : [])];
    return parts.length ? h("div", { class: "row" }, parts) : h("p", { class: "muted" }, "No triggers are recorded for this guard.");
  }
  function lessonReview(lesson, status, trigger) {
    P.openForm("lesson_review", { lesson_id: lesson.id, status }, trigger);
  }
  function guardCard(guard, recurrence, ctx) {
    const count = guard.recurrences || 0;
    const lesson = { ...guard, id: guard.lesson_id };
    return h("article", { class: "card kn-guard", dataset: { tone: count ? "blocked" : "guarded" } },
      h("h3", null, h("span", null, P.words(guard.pattern_type || "guard")), count ? P.badge("blocked", "Recurred " + P.count(count, "time")) : P.badge("guarded", "Active guard")),
      lessonText(guard), triggerChips(guard),
      count ? h("div", { class: "notice", dataset: { tone: "blocked" } },
        h("p", null, "The failure type " + (guard.failure_type || "of this guard") + " was recorded " + P.count(count, "time") + " after this lesson was accepted on " + P.date(guard.accepted_at) + "."),
        h("ul", { class: "kn-plain" }, ((recurrence || {}).outcomes || []).map((outcome) => h("li", null, openButton(outcome.observed || outcome.id, outcome.id, { prefix: "recurrence-" }), " ", h("span", { class: "muted" }, P.date(outcome.created_at)))))) : null,
      h("div", { class: "row" }, openButton("Open the lesson", guard.lesson_id, { class: "small", prefix: "guard-" }),
        ctx.canEdit ? actionButton("Retire", "retire-" + guard.lesson_id, (trigger) => lessonReview(lesson, "retired", trigger)) : null));
  }
  function proposedCard(lesson, ctx) {
    return h("article", { class: "card proposed" },
      h("h3", null, h("span", null, P.words(lesson.pattern_type || "lesson")), P.badge("proposed")),
      lessonText(lesson),
      h("p", { class: "muted" }, "Proposed by " + (lesson.actor || "an agent") + " on " + P.date(lesson.created_at) + "."),
      h("div", { class: "row" }, h("span", { class: "muted" }, P.term("work_item") + ":"), openButton(lesson.episode_title || lesson.episode_id, lesson.episode_id, { work: true, prefix: "lesson-work-" })),
      lesson.evidence && lesson.evidence.length ? h("ul", { class: "kn-plain" }, lesson.evidence.map((ref) => h("li", null, openButton(ref.title || ref.source_id, ref.source_id, { prefix: "lesson-evidence-" + lesson.id + "-" }), h("span", { class: "muted" }, " " + ref.reason)))) : null,
      h("div", { class: "row" }, openButton("Open the lesson", lesson.id, { class: "small", prefix: "proposed-" }),
        ctx.canEdit ? [actionButton("Accept", "accept-" + lesson.id, (trigger) => lessonReview(lesson, "accepted", trigger), "primary"),
          actionButton("Reject", "reject-" + lesson.id, (trigger) => lessonReview(lesson, "rejected", trigger)),
          actionButton("Retire", "retire-" + lesson.id, (trigger) => lessonReview(lesson, "retired", trigger), "quiet")] : null));
  }
  P.registerView("learning", {
    title: "Learning",
    async render(container, params, ctx) {
      const offset = Number(params.lesson_offset) || 0;
      const data = await P.get("learning", offset ? { offset: String(offset) } : {});
      const proposed = data.proposed_lessons || { lessons: [], total: 0 };
      const recurring = data.recurrences || [];
      const failures = data.failures_without_lesson || [];
      const ineffective = (data.guards || []).filter((guard) => guard.recurrences > 0).length;
      put(container, sentence([P.count(data.guards_total || 0, "accepted guard") + " " + isAre(data.guards_total || 0) + " active",
        ineffective ? ", and " + P.count(ineffective, "guard") + " recorded a recurrence" : "", ". ",
        P.count(proposed.total, "proposed lesson") + " " + (proposed.total === 1 ? "awaits" : "await") + " your decision."].join("")));
      const byLesson = Object.fromEntries(recurring.map((entry) => [entry.lesson_id, entry]));
      const guards = [...(data.guards || [])].sort((a, b) => (b.recurrences || 0) - (a.recurrences || 0));
      put(container, section("Accepted guards", h("span", { class: "muted" }, P.count(data.guards_total || 0, "guard")),
        guards.length ? h("div", { class: "grid" }, guards.map((guard) => guardCard(guard, byLesson[guard.lesson_id], ctx))) : P.empty("No lesson has been accepted as a guard yet."),
        data.guards_more ? h("p", { class: "muted" }, "Only the first " + guards.length + " guards are shown.") : null));
      put(container, section("Proposed lessons", h("span", { class: "muted" }, P.count(proposed.total, "lesson")),
        proposed.lessons.length ? h("div", { class: "grid" }, proposed.lessons.map((lesson) => proposedCard(lesson, ctx))) : P.empty("No proposed lesson awaits a decision."),
        pager({ offset, count: proposed.lessons.length, total: proposed.total, more: proposed.more, limit: PAGE }, "lessons",
          (next) => P.go("learning", { ...params, lesson_offset: next ? String(next) : "" }))));
      put(container, section("Failures without lessons", h("span", { class: "muted" }, P.count(failures.length, "failure")),
        failures.length ? h("ul", { class: "list" }, failures.map((item) => h("li", { class: "stack kn-item" },
          h("div", { class: "row" }, openButton(item.title || item.episode_id, item.episode_id, { work: true, prefix: "failure-work-" }), P.badge("failed", P.words(item.severity) + " severity")),
          h("p", null, item.observed), h("div", { class: "row" }, item.failure_type ? chips([item.failure_type], "Failure type ") : null,
            h("span", { class: "muted" }, P.date(item.created_at)), openButton("Open the outcome", item.outcome_id, { class: "small", prefix: "failure-" }))))) : P.empty("Every failed outcome has a lesson or a later complete result.")));
      const changes = data.scope_changes || [];
      put(container, section("Scope widened by agents", h("span", { class: "muted" }, P.count(changes.length, "change")),
        changes.length ? h("ul", { class: "list" }, changes.map((item) => h("li", { class: "stack kn-item" },
          h("div", { class: "row" }, openButton(episodeTitle(item.episode_id), item.episode_id, { work: true, prefix: "scope-work-" }), h("span", { class: "muted" }, P.date(item.created_at))),
          h("p", null, "The actor " + item.actor + " added " + P.count(item.added.length, "path") + " to the allowed paths."),
          h("div", { class: "row" }, chips(item.added)), item.reason ? h("p", { class: "muted" }, "Reason: " + item.reason) : null,
          h("div", null, openButton("Open the plan revision", item.plan_id, { class: "small", prefix: "scope-plan-" }))))) : P.empty("No agent has added allowed paths to a work item.")));
      const signals = (data.signals || {}).signals || [];
      put(container, section("Signals", h("span", { class: "muted" }, P.count(signals.length, "signal")),
        (data.signals || {}).note ? h("p", { class: "muted" }, data.signals.note) : null,
        signals.length ? h("ul", { class: "list" }, signals.map((signal, index) => h("li", { class: "stack kn-item" },
          h("div", { class: "row" }, h("strong", null, P.words(signal.type)), signal.failure_type ? chips([signal.failure_type], "Failure type ") : null),
          signal.type === "repeated_failure" ? h("p", null, "The failure was recorded " + P.count(signal.failure_count, "time") + " in " + P.count(signal.assessed, "assessed outcome") + ", of which " + number(signal.successful) + " succeeded.") : null,
          h("p", { class: "muted" }, signal.reason),
          h("div", { class: "row" }, (signal.record_ids || []).map((id, position) => openButton("Record " + (position + 1), id, { class: "small", prefix: "signal-" + index + "-" })))))) : P.empty("No signal is recorded.")));
    },
  });

  // Agents.
  const hostName = (host) => P.words(host);
  // These three columns describe delegated work. For an agent check they stay empty, and a sentence says so once.
  function mergeLabel(run) {
    if (run.role !== "work") return null;
    if (run.merge) return P.badge(run.merge.state);
    if (run.state === "completed" && run.changed_files) return P.badge("review", "Awaiting a decision");
    return "Not merged";
  }
  function reviewLabel(run) {
    if (run.role !== "work") return null;
    return run.review ? P.badge(run.review.state) : "No review";
  }
  const canMerge = (run) => run.role === "work" && run.state === "completed" && run.changed_files > 0 && !run.merge && !(run.review && ACTIVE.includes(run.review.state));
  const canDiscard = (run) => run.role === "work" && !run.merge && !ACTIVE.includes(run.state) && !(run.review && ACTIVE.includes(run.review.state));
  const canRetryReview = (run) => run.role === "work" && run.state === "completed" && !run.merge && (!run.review || RETRY_REVIEW.includes(run.review.state));
  function runActions(run, ctx) {
    if (!ctx.canEdit) return null;
    const buttons = [
      canMerge(run) ? actionButton("Merge", "merge-" + run.id, (trigger) => P.openForm("merge", { run_id: run.id }, trigger), "primary") : null,
      canRetryReview(run) ? actionButton("Request review", "review-" + run.id, (trigger) => P.openForm("request_work_review", { run_id: run.id }, trigger)) : null,
      ACTIVE.includes(run.state) ? actionButton("Cancel", "cancel-" + run.id, (trigger) => P.openForm("cancel_run", { run_id: run.id }, trigger), "danger") : null,
      canDiscard(run) ? actionButton("Discard", "discard-" + run.id, (trigger) => P.openForm("discard", { run_id: run.id }, trigger), "danger") : null,
    ].filter(Boolean);
    return buttons.length ? h("div", { class: "row" }, buttons) : null;
  }
  function hostCard(host) {
    const ready = host.installed && host.available;
    return h("article", { class: "card" },
      h("h3", null, hostName(host.host), P.badge(ready ? "available" : "unavailable", ready ? "Can run work" : "Cannot run work")),
      h("div", { class: "row" }, P.badge(host.installed ? "available" : "missing", host.installed ? "Installed" : "Not installed"),
        P.badge(host.available ? "available" : "unavailable", host.available ? "Available" : "Unavailable")),
      !host.available ? h("p", null, host.until ? "The host is unavailable until " + P.date(host.until) + "." : "The host is unavailable. No end time is recorded.") : null,
      host.reason ? h("p", { class: "muted" }, "Reason: " + host.reason) : null,
      !host.installed ? h("p", { class: "muted" }, "The program of this host was not found on this computer.") : null);
  }
  P.registerView("agents", {
    title: "Agents",
    async render(container, params, ctx) {
      const offset = Number(params.offset) || 0;
      const data = await P.get("agents", offset ? { offset: String(offset) } : {});
      const hosts = data.hosts || [];
      const runs = (data.runs || {}).runs || [];
      const ready = hosts.filter((host) => host.installed && host.available).length;
      const awaiting = runs.filter((run) => run.role === "work" && run.state === "completed" && run.changed_files && !run.merge).length;
      const active = data.active || [];
      put(container, sentence((data.configured ? number(ready) + " of " + P.count(hosts.length, "configured host") + " can run work now. " : "No agent host is configured for this project. ") +
        (active.length ? P.count(active.length, "agent run") + " " + isAre(active.length) + " active. " : "No agent run is active. ") +
        (awaiting ? P.count(awaiting, "delegated run") + " " + (awaiting === 1 ? "awaits" : "await") + " a merge decision." : "")));
      if (!data.configured) put(container, h("div", { class: "notice" }, h("p", null, "Configure an agent host for this project before you delegate work or request checks.")));
      if (hosts.length) put(container, section("Hosts", null, h("div", { class: "grid" }, hosts.map(hostCard))));
      if ((data.attention || []).length) {
        put(container, section("Follow ups that need attention", null, h("ul", { class: "list" }, data.attention.map((item) => h("li", { class: "stack kn-item" },
          h("p", null, item.reason), h("div", { class: "row" }, h("span", { class: "muted" }, P.date(item.created_at)),
            item.run_id ? actionButton("Open the run", "attention-" + item.id, (trigger) => P.openDrawer("run", { id: item.run_id }, trigger)) : null))))));
      }
      const columns = ["Run", "Host", "State", P.term("work_item"), "Changed files", "Review", "Merge", "Started"];
      const table = h("table", { class: "data kn-table" }, h("thead", null, h("tr", null, columns.map((name) => h("th", { scope: "col" }, name)))),
        h("tbody", null, runs.map((run) => h("tr", null,
          cell("Run", h("button", { type: "button", class: "quiet kn-link", dataset: { key: "run-" + run.id }, on: { click: (event) => P.openDrawer("run", { id: run.id }, event.currentTarget) } }, P.words(run.role))),
          cell("Host", hostName(run.host)), cell("State", P.badge(run.state)),
          cell(P.term("work_item"), openButton(episodeTitle(run.episode_id), run.episode_id, { work: true, prefix: "run-work-" + run.id + "-" })),
          cell("Changed files", run.role === "work" ? number(run.changed_files) : null), cell("Review", reviewLabel(run)), cell("Merge", mergeLabel(run)),
          h("td", { class: "kn-nowrap", dataset: { label: "Started" } }, P.date(run.created_at))))));
      put(container, section("Runs", null, runs.length ? h("div", { class: "table-wrap" }, table) : P.empty("No agent run is recorded."),
        runs.length ? h("p", { class: "muted" }, "The changed files, review and merge columns describe delegated work. They stay empty for an agent check.") : null,
        pager({ offset, count: runs.length, more: (data.runs || {}).more, limit: 20 }, "runs", (next) => P.go("agents", { ...params, offset: next ? String(next) : "" }))));
    },
  });

  function diffFiles(text) {
    const files = [];
    let current = null;
    for (const line of String(text || "").split("\n")) {
      if (line.startsWith("diff --git ")) {
        const found = line.match(/ b\/(.+)$/);
        current = { path: found ? found[1] : line.slice(11), added: 0, removed: 0 };
        files.push(current);
      } else if (current && line.startsWith("+") && !line.startsWith("+++")) current.added++;
      else if (current && line.startsWith("-") && !line.startsWith("---")) current.removed++;
    }
    return files;
  }
  async function diffSummary(id) {
    const holder = h("section", { class: "stack" }, heading("Diff summary", openButton("Open the diff", id, { class: "small", prefix: "diff-" })));
    try {
      const { record } = await P.get("record", { id });
      const detail = record.detail || {};
      if (typeof detail.body !== "string") { put(holder, h("p", { class: "muted" }, "The text of the diff is not included in this snapshot.")); return holder; }
      const files = diffFiles(detail.body);
      put(holder, files.length ? h("div", { class: "table-wrap" }, h("table", { class: "data kn-table" },
        h("thead", null, h("tr", null, ["File", "Added lines", "Removed lines"].map((name) => h("th", { scope: "col" }, name)))),
        h("tbody", null, files.map((file) => h("tr", null, h("td", { class: "mono", dataset: { label: "File" } }, file.path),
          cell("Added lines", number(file.added)), cell("Removed lines", number(file.removed))))))) : P.empty("The diff names no changed file."),
      detail.body_more ? h("p", { class: "muted" }, "The summary covers the first part of the diff only.") : null);
    } catch (error) {
      put(holder, failed(error));
    }
    return holder;
  }
  P.registerDrawer("run", {
    async render(body, params, ctx) {
      const { run } = await P.get("run", { id: params.id });
      const report = run.report || {};
      ctx.setKind("Agent run");
      ctx.setTitle(P.words(run.role) + " run on " + hostName(run.host));
      put(body, h("div", { class: "row" }, P.badge(run.state), run.role === "work" ? mergeLabel(run) : null, h("span", { class: "muted" }, P.date(run.created_at))),
        h("p", { class: "mono muted" }, run.id), run.summary ? h("p", null, run.summary) : null, runActions(run, ctx),
        run.error ? h("div", { class: "notice", dataset: { tone: "blocked" } }, h("p", null, run.error)) : null,
        kv([[P.term("work_item"), openButton(episodeTitle(run.episode_id), run.episode_id, { work: true, prefix: "drawer-work-" })],
          ["Role", P.words(run.role)], ["Host", hostName(run.host)], ["Updated", P.date(run.updated_at)],
          ["Allowed paths", run.paths && run.paths.length ? h("div", { class: "row" }, chips(run.paths)) : "None recorded"],
          ["Review", run.role === "work" ? reviewLabel(run) : undefined],
          ["Branch", run.branch || undefined], ["Workspace", run.workspace || undefined],
          ["Parent run", run.parent_run ? actionButton("Open the delegated run", "parent-" + run.parent_run, (trigger) => P.openDrawer("run", { id: run.parent_run }, trigger)) : undefined],
          ["Commit", (run.metrics || {}).commit || undefined]]));
      const checks = report.checks_run || report.checks || [];
      put(body, section("Report checks", null, checks.length ? h("ul", { class: "list" }, checks.map((check) => h("li", null, valueNode(check)))) : P.empty("The report lists no checks.")));
      const findings = report.findings || [];
      if (report.verdict || findings.length || run.role !== "work") {
        put(body, section("Findings", report.verdict ? P.badge(report.verdict, "Verdict: " + P.words(report.verdict)) : null,
          findings.length ? h("ul", { class: "list" }, findings.map((finding) => h("li", null, valueNode(finding)))) : P.empty("The report records no findings.")));
      }
      const other = Object.fromEntries(Object.entries(report).filter(([key]) => !["summary", "checks_run", "checks", "findings", "verdict", "lesson_proposals"].includes(key)));
      if (Object.keys(other).length) put(body, section("Other report fields", null, kv(Object.entries(other).map(([key, value]) => [P.words(key), value]))));
      if (run.diff_source) put(body, await diffSummary(run.diff_source));
      if ((run.reviews || []).length) {
        put(body, section("Work reviews", null, h("ul", { class: "list" }, run.reviews.map((review) => h("li", { class: "row" }, P.badge(review.state), hostName(review.host), h("span", { class: "muted" }, P.date(review.created_at)),
          actionButton("Open the review", "review-run-" + review.id, (trigger) => P.openDrawer("run", { id: review.id }, trigger)))))));
      }
    },
  });

  // Records.
  const recordViews = () => [["all", "All record kinds"], ["episodes", P.term("work_items")], ["decisions", "Decisions"], ["pending", "Pending decisions"],
    ["drift", "Records to recheck"], ["documents", "Documents"], ["sources", "Sources"], ["direction", "Requirement versions"], ["research", "Research"],
    ["corrections", "Corrections"], ["lessons", "Lessons"], ["patterns", "Patterns"], ["events", "All events"], ["captures", "Host captures"]];
  const FILTERS = ["query", "subject", "status", "episode", "from", "to", "order"];
  function localPage(base, filters, offset, limit) {
    const query = (filters.query || "").toLowerCase();
    let rows = (base.records || []).filter((record) => (!query || record.id === filters.query || String(record.title).toLowerCase().includes(query) || JSON.stringify(record.detail || {}).toLowerCase().includes(query))
      && (!filters.subject || record.subject === filters.subject) && (!filters.status || record.status === filters.status)
      && (!filters.episode || record.episode_id === filters.episode) && (!filters.from || String(record.date) >= filters.from)
      && (!filters.to || String(record.date).slice(0, 10) <= filters.to));
    const order = filters.order || "newest";
    rows = rows.sort((a, b) => (order === "title" ? String(a.title).localeCompare(String(b.title)) : order === "oldest" ? String(a.date).localeCompare(String(b.date)) : String(b.date).localeCompare(String(a.date))));
    return { records: rows.slice(offset, offset + limit), total: rows.length, offset, limit, more: offset + limit < rows.length, partial: Boolean(base.more) };
  }
  async function loadRecords(view, filters, offset, limit) {
    if (!P.live) return localPage(await P.get("records", { view, limit: SNAPSHOT_PAGE }), filters, offset, limit);
    return P.get("records", { view, ...filters, limit: String(limit), offset: offset ? String(offset) : "" });
  }
  function openRecordAny(record, trigger) {
    if (record.kind === "episode") P.openWork(record.id, trigger);
    else P.openRecord(record.id, trigger);
  }
  function recordTable(records) {
    return h("div", { class: "table-wrap" }, h("table", { class: "data kn-table" },
      h("thead", null, h("tr", null, ["Title", "Kind", "Status", "Subject", P.term("work_item"), "Date"].map((name) => h("th", { scope: "col" }, name)))),
      h("tbody", null, records.map((record) => h("tr", null,
        cell("Title", h("button", { type: "button", class: "quiet kn-link", dataset: { key: "record-" + record.id }, on: { click: (event) => openRecordAny(record, event.currentTarget) } }, record.title || record.id)),
        cell("Kind", record.kind === "episode" ? P.term("work_item") : P.words(record.kind)), cell("Status", P.badge(record.status)), cell("Subject", P.words(record.subject)),
        cell(P.term("work_item"), record.episode_id && record.episode_id !== record.id ? episodeTitle(record.episode_id) : null),
        h("td", { class: "kn-nowrap", dataset: { label: "Date" } }, record.date ? P.date(record.date) : "Not recorded"))))));
  }
  function recordFilters(params, view) {
    const episodes = ((P.health() || {}).episodes || []).map((item) => [item.id, item.title]);
    const form = h("form", { class: "toolbar kn-filters", role: "search", "aria-label": "Record filters" },
      P.field("Kind", P.select("view", recordViews(), view, { id: "records-view" })),
      P.field("Search", P.input("query", params.query, { id: "records-query", type: "search" })),
      P.field("Subject", P.select("subject", [["", "Any subject"], ...SUBJECTS], params.subject || "", { id: "records-subject" })),
      P.field("Status", P.select("status", [["", "Any status"], ...STATUSES], params.status || "", { id: "records-status" })),
      P.field(P.term("work_item"), P.select("episode", [["", "Any " + P.term("work_item").toLowerCase()], ...episodes], params.episode || "", { id: "records-episode" })),
      P.field("From", P.input("from", params.from, { id: "records-from", type: "date" })),
      P.field("To", P.input("to", params.to, { id: "records-to", type: "date" })),
      P.field("Order", P.select("order", [["newest", "Newest first"], ["oldest", "Oldest first"], ["title", "Title"]], params.order || "newest", { id: "records-order" })),
      h("div", { class: "row" }, h("button", { type: "submit", class: "primary", id: "records-apply" }, "Apply filters"),
        h("button", { type: "button", class: "quiet", id: "records-clear", on: { click: () => P.go("records", {}) } }, "Clear")));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const values = P.formValues(form);
      if (values.order === "newest") values.order = "";
      if (values.view === "all") values.view = "";
      P.go("records", values);
    });
    const active = FILTERS.some((name) => params[name]) || view !== "all";
    return h("details", { class: "kn-filter-box", id: "records-filters", open: active || window.innerWidth >= 640 },
      h("summary", null, active ? "Filters are applied" : "Filters"), form);
  }
  P.registerView("records", {
    title: "Records",
    async render(container, params) {
      const view = params.view || "all";
      const filters = Object.fromEntries(FILTERS.filter((name) => params[name]).map((name) => [name, params[name]]));
      const label = Object.fromEntries(recordViews())[view] || P.words(view);
      put(container, recordFilters(params, view));
      if (!P.live) put(container, h("p", { class: "muted" }, "This snapshot filters the first " + SNAPSHOT_PAGE + " records of each kind in the browser."));
      if (view === "all") {
        const groups = await Promise.all(ALL_VIEWS.map((name) => loadRecords(name, filters, 0, 5).then((page) => [name, page], (error) => [name, null, error])));
        const total = groups.reduce((sum, [, page]) => sum + (page ? page.total : 0), 0);
        put(container, sentence(P.count(total, "record") + " " + (total === 1 ? "matches" : "match") + (Object.keys(filters).length ? " these filters." : " across all kinds.")));
        for (const [name, page, error] of groups) {
          const title = Object.fromEntries(recordViews())[name];
          if (error) { put(container, section(title, null, failed(error))); continue; }
          if (!page.total) continue;
          put(container, section(title, h("span", { class: "muted" }, P.count(page.total, "record")), recordTable(page.records),
            page.total > page.records.length ? h("div", null, h("button", { type: "button", class: "small", dataset: { key: "records-all-" + name }, on: { click: () => P.go("records", { ...filters, view: name }) } }, "Show all " + number(page.total) + " " + title.toLowerCase())) : null));
        }
        return;
      }
      const offset = Number(params.offset) || 0;
      const page = await loadRecords(view, filters, offset, PAGE);
      put(container, sentence(P.count(page.total, "record") + " in " + label.toLowerCase() + " " + (page.total === 1 ? "matches" : "match") + (Object.keys(filters).length ? " these filters." : ".")));
      if (page.partial) put(container, h("p", { class: "muted" }, "This kind holds more records than the snapshot includes."));
      put(container, page.records.length ? recordTable(page.records) : P.empty("No records match these filters."),
        pager({ offset, count: page.records.length, total: page.total, more: page.more, limit: PAGE }, "records", (next) => P.go("records", { ...params, offset: next ? String(next) : "" })));
    },
  });

  // Record drawer: fields, evidence, reverse references and paged source text with an outline.
  function outline(text) {
    const found = [];
    let fenced = false;
    for (const line of String(text).split("\n")) {
      if (line.startsWith("```")) fenced = !fenced;
      else if (!fenced) { const match = line.match(/^(#{1,6})\s+(.+)$/); if (match) found.push({ level: match[1].length, text: match[2] }); }
    }
    return found;
  }
  function sourceText(id, first) {
    const holder = h("section", { class: "stack" });
    const state = { offset: Number(first.body_offset) || 0, original: false, detail: first };
    const draw = () => {
      const detail = state.detail;
      const body = detail.body;
      const headings = outline(body);
      const content = state.original ? h("pre", { class: "source-text" }, body) : P.markdown(body);
      const end = state.offset + body.length;
      const toggle = h("button", { type: "button", class: "small", dataset: { key: "source-toggle" }, on: { click: () => { state.original = !state.original; draw(); } } },
        state.original ? "Show formatted text" : "Show original text");
      const go = async (offset) => {
        try {
          const { record } = await P.get("record", offset ? { id, body_offset: String(offset) } : { id });
          Object.assign(state, { offset, detail: record.detail });
          draw();
          holder.querySelector("h3").focus();
        } catch (error) {
          put(holder, failed(error));
        }
      };
      const parts = h("div", { class: "row kn-pager" }, h("span", { class: "muted" }, "Characters " + number(state.offset + 1) + " to " + number(end) + (detail.body_more ? ". More text follows." : ".")),
        h("button", { type: "button", class: "small", disabled: state.offset <= 0, dataset: { key: "source-previous" }, on: { click: () => go(Math.max(0, state.offset - 12000)) } }, "Previous part"),
        h("button", { type: "button", class: "small", disabled: !detail.body_more, dataset: { key: "source-next" }, on: { click: () => go(detail.next_offset) } }, "Next part"));
      const nav = headings.length && !state.original ? h("nav", { class: "kn-outline", "aria-label": "Document outline" }, h("h4", null, "Outline"),
        h("ul", { class: "kn-plain" }, headings.map((item, index) => h("li", { dataset: { level: String(item.level) } },
          h("button", { type: "button", class: "quiet kn-link", dataset: { key: "outline-" + index }, on: { click: () => {
            const target = content.querySelectorAll("h1, h2, h3, h4, h5, h6")[index];
            if (target) { target.tabIndex = -1; target.scrollIntoView({ block: "start" }); target.focus({ preventScroll: true }); }
          } } }, item.text))))) : null;
      holder.replaceChildren(); put(holder, h("div", { class: "row kn-heading" }, h("h3", { tabindex: "-1" }, "Content"), toggle), parts, nav, content);
    };
    draw();
    return holder;
  }
  function referenceList(page) {
    return page.records.length ? h("ul", { class: "list" }, page.records.map((record) => h("li", { class: "row" },
      h("button", { type: "button", class: "quiet kn-link", dataset: { key: "related-" + record.id }, on: { click: (event) => openRecordAny(record, event.currentTarget) } }, record.title || record.id),
      h("span", { class: "muted" }, P.words(record.kind)), P.badge(record.status)))) : P.empty("No other record refers to this record.");
  }
  P.registerDrawer("record", {
    async render(body, params, ctx) {
      const [{ record }, related] = await Promise.all([P.get("record", { id: params.id }),
        P.get("records", { related: params.id, limit: String(PAGE) }).catch((error) => error)]);
      const detail = record.detail || {};
      const payload = detail.payload || {};
      ctx.setTitle(record.title || record.id);
      ctx.setKind(record.kind === "episode" ? P.term("work_item") : P.words(record.kind));
      put(body, h("div", { class: "row" }, P.badge(record.status), h("span", { class: "chip" }, P.words(record.subject)), h("span", { class: "muted" }, P.date(record.date))),
        h("p", { class: "mono muted" }, record.id));
      if (record.kind === "lesson" && ctx.canEdit) {
        const lesson = { ...payload, id: record.id, episode_id: record.episode_id };
        const status = detail.lesson_status || record.status;
        put(body, h("div", { class: "row" }, status === "proposed" ? [actionButton("Accept", "drawer-accept", (trigger) => lessonReview(lesson, "accepted", trigger), "primary"),
          actionButton("Reject", "drawer-reject", (trigger) => lessonReview(lesson, "rejected", trigger))] : null,
        status === "accepted" ? actionButton("Retire", "drawer-retire", (trigger) => lessonReview(lesson, "retired", trigger)) : null));
      }
      if (record.episode_id && record.episode_id !== record.id) {
        put(body, h("p", null, P.term("work_item") + ": ", openButton(record.episode_title || episodeTitle(record.episode_id), record.episode_id, { work: true, prefix: "drawer-episode-" })));
      }
      const fields = record.kind === "episode" ? { objective: detail.objective, criterion: detail.criterion, task_type: detail.task_type, status: detail.status, version: detail.version }
        : record.kind === "source" ? { summary: detail.summary, origin: detail.origin, version: detail.version, checked_at: detail.checked_at, review_after: detail.review_after, ...(detail.document ? { path: detail.document.path, format: detail.document.format, authority: detail.document.authority } : {}) }
          : record.kind === "project_revision" ? { requirements: detail.requirements, reason: detail.reason, actor: detail.actor }
            : record.kind === "host_receipt" ? { event_name: detail.event_name, tool_name: detail.tool_name, session_id: detail.session_id, ...(detail.payload || {}) }
              : { ...payload, actor: detail.actor };
      const shown = Object.entries(fields).filter(([, value]) => value !== undefined && value !== null && value !== "");
      if (shown.length) put(body, h("dl", { class: "kv" }, shown.map(([name, value]) => [h("dt", null, P.words(name)), h("dd", null, valueNode(value, name))])));
      if ((detail.review_reasons || []).length) put(body, h("div", { class: "notice", dataset: { tone: "review" } }, h("p", null, "This record needs review."), valueNode(detail.review_reasons)));
      if (record.outcome) {
        const assessment = (record.outcome.detail.payload || {}).assessment;
        put(body, section("Latest outcome", P.badge(assessment === "bad" ? "failed" : assessment === "good" ? "good" : "mixed", "Assessment: " + P.words(assessment)),
          h("p", null, openButton(record.outcome.title, record.outcome.id, { prefix: "drawer-outcome-" }))));
      }
      const chain = [["Replaces", detail.supersedes], ["Replaced by", detail.replaced_by], ["Decision", detail.decision_id]].filter(([, id]) => id);
      if (chain.length) put(body, h("dl", { class: "kv" }, chain.map(([label, id]) => [h("dt", null, label), h("dd", null, openButton(id, id, { prefix: "drawer-chain-" }))])));
      if (record.kind === "source") {
        if (typeof detail.body === "string") put(body, sourceText(record.id, detail));
        else put(body, section("Content", null, h("p", { class: "muted" }, "The source text is not included in this snapshot.")));
      }
      const evidence = detail.evidence || [];
      put(body, section("Evidence", h("span", { class: "muted" }, P.count(evidence.length, "reference")), evidence.length ? h("ul", { class: "list" }, evidence.map((ref) => h("li", { class: "stack kn-item" },
        h("div", { class: "row" }, openButton(ref.title || ref.source_id, ref.source_id, { prefix: "evidence-" }), ref.status ? P.badge(ref.status) : null, ref.origin ? h("span", { class: "muted" }, P.words(ref.origin)) : null),
        ref.reason ? h("p", { class: "muted" }, ref.reason) : null))) : P.empty("No evidence is attached to this record.")));
      if ((detail.links || []).length) {
        put(body, section("Earlier records", null, h("ul", { class: "list" }, detail.links.map((ref) => h("li", { class: "stack kn-item" },
          openButton(ref.event_id, ref.event_id, { prefix: "link-" }), ref.reason ? h("p", { class: "muted" }, ref.reason) : null)))));
      }
      put(body, section("Referenced by", related instanceof Error ? null : h("span", { class: "muted" }, P.count(related.total, "record")),
        related instanceof Error ? failed(related) : referenceList(related),
        !(related instanceof Error) && related.more ? h("p", { class: "muted" }, "Only the first " + related.records.length + " references are shown.") : null));
    },
  });

  // Requirements.
  P.registerView("requirements", {
    title: "Requirements",
    async render(container, params, ctx) {
      const offset = Number(params.offset) || 0;
      const revisionOffset = Number(params.revision_offset) || 0;
      const query = {};
      if (offset) query.offset = String(offset);
      if (revisionOffset) query.revision_offset = String(revisionOffset);
      const data = await P.get("requirements", query);
      const items = data.items || { items: [], total: 0 };
      const current = data.current || {};
      const established = items.status !== "not_established";
      put(container, h("div", { class: "view-head" }, sentence(established
        ? "Version " + current.version + " of the project requirements is current, with " + P.count(items.total, "requirement") + "."
        : "The project requirements are not established yet. Review them to record an approved version."),
      ctx.canEdit ? h("button", { type: "button", class: "primary", id: "review-requirements", on: { click: (event) => P.openForm("requirements", {}, event.currentTarget) } }, "Review requirements") : null));
      put(container, section("Current requirements", P.badge(established ? items.status : "review", established ? P.words(items.status) : "Not established"),
        kv([["Version", String(items.version)], ["Reason", current.reason || "Not recorded"], ["Approved by", current.actor || "Not recorded"],
          ["Approved on", current.created_at ? P.date(current.created_at) : "Not recorded"]]),
        h("ol", { class: "kn-requirements", start: String(offset + 1) }, items.items.map((item) => h("li", null, item.text))),
        pager({ offset, count: items.items.length, total: items.total, more: items.more, limit: PAGE }, "requirements", (next) => P.go("requirements", { ...params, offset: next ? String(next) : "" })),
        h("p", { class: "muted" }, "The recorded requirements govern decisions. They do not prove that the project covers every need.")));
      put(container, section("Approval evidence", h("span", { class: "muted" }, P.count(items.evidence_total || 0, "reference")),
        (items.evidence || []).length ? h("ul", { class: "list" }, items.evidence.map((ref, index) => h("li", { class: "stack kn-item" },
          openButton(ref.title || "Approval source " + (offset + index + 1), ref.source_id, { prefix: "requirement-evidence-" }), ref.reason ? h("p", { class: "muted" }, ref.reason) : null)))
          : P.empty("No approval evidence is recorded for this version.")));
      const revisions = data.revisions || [];
      put(container, section("History", null, h("div", { class: "table-wrap" }, h("table", { class: "data kn-table" },
        h("thead", null, h("tr", null, ["Version", "Requirements", "Evidence", "Approved by", "Date", "Reason"].map((name) => h("th", { scope: "col" }, name)))),
        h("tbody", null, revisions.map((revision) => h("tr", null,
          cell("Version", openButton("Version " + revision.version, "direction_" + revision.version, { prefix: "revision-" })),
          cell("Requirements", number(revision.requirement_count)), cell("Evidence", number(revision.evidence_count)), cell("Approved by", revision.actor || "Not recorded"),
          h("td", { class: "kn-nowrap", dataset: { label: "Date" } }, revision.created_at ? P.date(revision.created_at) : "Not recorded"),
          cell("Reason", revision.reason || (revision.version === current.version ? current.reason : "") || "Not recorded")))))),
      pager({ offset: revisionOffset, count: revisions.length, more: data.more, limit: 10 }, "revisions", (next) => P.go("requirements", { ...params, revision_offset: next ? String(next) : "" }))));
    },
  });
})();
