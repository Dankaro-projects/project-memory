/*
 * Project Memory control panel: views_knowledge.js registers the Learning, Agents, Machine, Records and Requirements
 * views and the "record", "run", "guard" and "instructions" panes. Buttons open the forms of forms.js: lesson_review
 * {lesson_id, status}, instructions {role}, merge, discard, cancel_run and request_work_review {run_id}, requirements {},
 * promotion {promotion_id, status}, machine_rule {rule_id}, and reassess {decision_id, outcome_id} next to each counted
 * recurrence. Learning and Sessions are tabs of rows in the frame; a lesson, a flag and a proposal are decided in the
 * "decide" pane of views_work.js. A snapshot holds records {view, limit: 100} for each view, so the Records view filters
 * and pages those in the browser. A snapshot carries no machine response, because the machine memory stays on the
 * computer that holds it, and no sessions response. Agents and Machine are tabs of rows or of scrolling regions; a run
 * opens the "run" pane. The Hive view lists the swarms as rows, and a row opens the "swarm" pane with the timeline; it
 * opens hive_post {swarm_id, move, target}, hive_close {swarm_id} and hive_purge {}. The Usage view reads usage {} in one
 * scrolling region and offers no action. The Sessions view reads sessions {} and opens session_flag {flag_ids, status}.
 */
(() => {
  "use strict";
  const P = Panel, { h, put, button } = P;
  const PAGE = 25, SNAPSHOT_PAGE = "100", ACTIVE = ["queued", "running", "cancelling"];
  const RETRY_REVIEW = ["failed", "timed_out", "cancelled", "host_unavailable", "interrupted", "stale"];
  const SUBJECTS = ["general", "code", "writing", "research"];
  const STATUSES = ["recorded", "needs_review", "review_due", "superseded", "proposed", "accepted", "rejected", "retired",
    "current_copy", "file_changed", "file_missing", "observed", "execution_unconfirmed", "current", "historical"];
  const ALL_VIEWS = ["episodes", "events", "sources", "captures", "direction"];

  // Shared helpers.
  const number = (n) => Number(n || 0).toLocaleString("en-GB");
  const isAre = (n) => (n === 1 ? "is" : "are");
  const purpose = (text) => h("p", { class: "muted" }, text);
  // A section reads as a heading of a Notion page, with its count or its control beside the heading.
  const heading = (text, extra) => h("div", { class: "block-head" }, P.heading(2, text), extra || null);
  const section = (title, extra, ...children) => h("section", { class: "stack" }, heading(title, extra), children);
  const counted = (n, noun) => h("span", { class: "muted" }, P.count(n, noun));
  const chips = (values, prefix) => (values || []).map((value) => h("span", { class: "chip mono" }, (prefix || "") + value));
  // Every cell names its column, so a narrow screen shows the table as labelled rows instead of a sideways scroll.
  const cell = (label, ...children) => h("td", { dataset: { label } }, children);
  const dateCell = (label, value) => h("td", { class: "kn-nowrap", dataset: { label } }, value ? P.date(value) : "Not recorded");
  const table = (columns, rows) => h("div", { class: "table-wrap" }, h("table", { class: "data kn-table" },
    h("thead", null, h("tr", null, columns.map((name) => h("th", { scope: "col" }, name)))), h("tbody", null, rows)));
  const episodeTitle = (id) => (((P.health() || {}).episodes || []).find((item) => item.id === id) || { title: id }).title;
  const listOf = (list, render, message) => (list.length ? h("ul", { class: "list" }, list.map((item, index) => h("li", { class: "stack kn-item" }, render(item, index))))
    : P.empty(message));
  const grid = (list, render, message) => (list.length ? h("div", { class: "grid" }, list.map(render)) : P.empty(message));
  // A scrolling region of the frame for cards, tables and text that are no rows.
  const region = (...children) => h("div", { class: "list-pane" }, h("div", { class: "pane-rows pane-body", dataset: { scroll: "rows" } }, children));
  const openButton = (label, id, options = {}) => button(label, (options.prefix || "open-") + id,
    (trigger) => (options.work ? P.openWork(id, trigger) : P.openRecord(id, trigger)), options.class || "quiet kn-link");
  // A link shows the identifier of its record until the title is read.
  function titledButton(id, options = {}) {
    const node = openButton(options.label || id, id, options);
    P.get("record", { id }).then(({ record }) => { if (record && record.title) node.textContent = record.title; }, () => {});
    return node;
  }
  const actionButton = (label, key, open, tone) => button(label, key, open, ["small", tone]);
  const openForm = (name, context) => (trigger) => P.openForm(name, context, trigger);
  const openRun = (id) => (trigger) => P.openPane("run", { id }, trigger);
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
    const text = "Showing " + number(offset + 1) + " to " + number(end) + (typeof total === "number" ? " of " + number(total) : "") + ".";
    return h("nav", { class: "row kn-pager", "aria-label": "Pages" }, h("span", { class: "muted" }, text),
      button("Previous", prefix + "-previous", () => go(Math.max(0, offset - limit)), "small", { disabled: offset <= 0 }),
      button("Next", prefix + "-next", () => go(end), "small", { disabled: !more }));
  }
  // A section that loads on its own, so a missing snapshot key does not hide the rest of the view.
  const failed = (error) => (error && error.notIncluded ? h("p", { class: "muted" }, "This part is not included in this snapshot.") : P.errorState(error));

  // Learning: one tab for each part of the response. A proposed lesson is decided in the shared "decide" pane of
  // views_work.js, a guard and the instructions of a role open the panes of this file, and a failure opens its outcome.
  function lessonText(lesson) {
    return [h("p", null, h("strong", null, lesson.do || "No action is recorded.")),
      h("p", { class: "muted" }, "When " + lower(lesson.when)),
      lesson.because ? h("p", { class: "muted" }, "Because " + lower(lesson.because)) : null,
      lesson.exceptions ? h("p", { class: "muted" }, "Exceptions: " + lesson.exceptions) : null];
  }
  const lower = (text) => (text ? text.charAt(0).toLowerCase() + text.slice(1) : "no condition is recorded.");
  // Guards name their paths and rules name their roles; both name their keywords and failure type.
  const triggerParts = (item, first) => [...(item[first] || []).map((value) => (first === "paths" ? "Path " : "Role ") + value), ...(item.keywords || []).map((value) => "Keyword " + value),
    ...(item.failure_type ? ["Failure type " + item.failure_type] : [])];
  function triggerChips(item, first, empty) {
    const parts = triggerParts(item, first).map((part) => h("span", { class: "chip mono" }, part));
    return parts.length ? h("div", { class: "row" }, parts) : empty;
  }
  const lessonReview = (lesson, status) => openForm("lesson_review", { lesson_id: lesson.id, status });
  // The guard pane: the lesson, its triggers, and every counted recurrence with its Reassess action.
  P.registerPane("guard", { async render(body, params, ctx) {
      const data = await P.get("learning"), guard = (data.guards || []).find((entry) => entry.lesson_id === params.id);
      ctx.setKind("Guard");
      if (!guard) { ctx.setTitle("This guard is not listed"); put(body, h("p", { class: "muted" }, "Select another row of the list.")); return; }
      const count = guard.recurrences || 0, recurrence = (data.recurrences || []).find((entry) => entry.lesson_id === params.id) || {};
      ctx.setTitle(guard.do || "No action is recorded.");
      put(body, h("div", { class: "row" }, h("span", { class: "chip" }, P.words(guard.pattern_type || "guard")), count ? P.badge("blocked", "Recurred " + P.count(count, "time")) : P.badge("guarded", "Active guard"),
        h("span", { class: "muted" }, "Accepted " + P.date(guard.accepted_at))),
      lessonText(guard).slice(1), triggerChips(guard, "paths", h("p", { class: "muted" }, "No triggers are recorded for this guard.")),
      count ? h("div", { class: "notice", dataset: { tone: "blocked" } },
        h("p", null, "The failure type " + (guard.failure_type || "of this guard") + " was recorded " + P.count(count, "time") + " after this lesson was accepted on " + P.date(guard.accepted_at) + "."),
        h("ul", { class: "kn-plain" }, (recurrence.outcomes || []).map((outcome) => h("li", null, openButton(outcome.observed || outcome.id, outcome.id, { prefix: "recurrence-" }), " ", h("span", { class: "muted" }, P.date(outcome.created_at)), " ",
          P.formButton("Reassess", "reassess", { decision_id: outcome.decision_id, outcome_id: outcome.id }, { class: "small" }))))) : null);
      put(ctx.foot, h("div", { class: "row" }, openButton("Open the lesson", guard.lesson_id, { class: "small", prefix: "guard-" }),
        ctx.canEdit ? actionButton("Retire", "retire-" + guard.lesson_id, lessonReview({ id: guard.lesson_id }, "retired")) : null));
  } });
  // Instructions: the text each agent role receives, the rules composed into it and the rules left out.
  const RULE_STATE = { effective: "ready", unproven: "backlog", ineffective: "blocked" };
  const VERDICTS = ["pass", "changes_required", "uncertain", "pending"];
  const has = (list, id) => (list || []).some((entry) => (entry.lesson_id || entry) === id);
  // A rule beyond the effectiveness limit carries its identifier and its reason only, so no count is invented for it.
  function ruleEntry(rule, role, reason) {
    const counts = rule.verdicts || {};
    return h("li", { class: "stack kn-item kn-rule" },
      h("div", { class: "row" }, openButton(rule.do || rule.lesson_id, rule.lesson_id, { prefix: "rule-" + role + "-" }),
        rule.state ? P.badge(RULE_STATE[rule.state] || "neutral", P.words(rule.state)) : null),
      rule.when ? h("p", { class: "muted" }, "When " + lower(rule.when)) : null,
      reason ? h("p", { class: "muted" }, reason) : null,
      rule.state ? h("div", { class: "row" }, h("span", { class: "chip" }, P.count(rule.runs || 0, "run") + " composed it"),
        VERDICTS.filter((name) => counts[name]).map((name) => h("span", { class: "chip" }, P.words(name) + ": " + counts[name])),
        h("span", { class: "chip" }, "Recurrences before " + (rule.recurrences_before || 0) + ", after " + (rule.recurrences_after || 0))) : null,
      rule.note ? h("p", { class: "muted" }, rule.note) : null);
  }
  // The instructions pane of one role: its budget, its base text and its rules. The base text is edited in a dialog.
  P.registerPane("instructions", { async render(body, params, ctx) {
      const data = await P.get("learning"), value = data.instructions || {}, role = params.role, item = (value.roles || {})[role];
      ctx.setKind("Instructions");
      ctx.setTitle(P.words(role) + " role");
      if (!item) { put(body, h("p", { class: "muted" }, "No instruction text is available for this role.")); return; }
      const rules = data.effectiveness || [], accepted = (value.counts || {})[role] || 0, max = value.max_rules || 0;
      const omitted = item.omitted || [], mine = rules.filter((rule) => (rule.roles || []).indexOf(role) >= 0);
      const found = (id) => mine.find((rule) => rule.lesson_id === id) || { lesson_id: id };
      const composed = (item.rule_ids || []).map(found);
      const waiting = mine.filter((rule) => !has(item.rule_ids, rule.lesson_id) && !has(omitted, rule.lesson_id));
      const saved = String(item.base_source || "").indexOf("instructions-base:") === 0;
      put(body, h("div", { class: "stack kn-role", dataset: { key: "instructions-" + role } },
        h("div", { class: "row" }, P.badge("guarded", P.count(accepted, "accepted rule")), h("span", { class: "muted" }, "This prompt carries " + P.count(composed.length, "rule") + " and uses " + number(item.used || 0) + " of "
          + number(item.budget || 0) + " characters. The whole text is " + number(item.characters || 0) + " characters.")),
        P.progress(item.budget ? (item.used || 0) / item.budget : 0, "Character budget of the " + role + " rules"),
        waiting.length ? h("p", { class: "muted" }, P.count(waiting.length, "further accepted rule") + " " + isAre(waiting.length) + " composed only into a run that matches the triggers.") : null,
        accepted > max ? h("p", { class: "muted" }, "This role has more than " + P.count(max, "accepted rule") + ", so one prompt cannot carry all of them.") : null,
        h("h4", null, "Base text"),
        h("p", { class: "muted" }, saved ? "You saved version " + item.base_version + " of this text in this project."
          : item.base_source === "none" ? "No base text is shipped for this role."
            : "The shipped file " + item.base_source + " is in force. No project version is saved."),
        h("pre", { class: "source-text kn-base", dataset: { key: "base-" + role } }, item.base || "No base text is recorded."),
        h("h4", null, "Rules in force"),
        composed.length ? h("ul", { class: "list" }, composed.map((rule) => ruleEntry(rule, role))) : P.empty("No rule is composed into this prompt."),
        waiting.length ? [h("h4", null, "Rules that wait for a matching run"), h("ul", { class: "list" }, waiting.map((rule) => ruleEntry(rule, role)))] : null,
        omitted.length ? [h("h4", null, "Rules left out"), h("ul", { class: "list" }, omitted.map((entry) => ruleEntry(found(entry.lesson_id), role, entry.reason)))] : null));
      put(ctx.foot, P.formButton("Edit the base text", "instructions", { role }, { class: "small" }));
  } });
  const LEARNING_TABS = [["proposed", "Proposed lessons", "review"], ["guards", "Guards", "guarded"], ["failures", "Failures without a lesson", "blocked"],
    ["instructions", "Instructions", null], ["scope", "Scope changes", null], ["signals", "Signals", null]];
  P.registerView("learning", { title: "Learning", async render(container, params, ctx) {
      const offset = Number(params.lesson_offset) || 0, data = await P.get("learning", offset ? { offset: String(offset) } : {});
      const proposed = data.proposed_lessons || { lessons: [], total: 0 }, failures = data.failures_without_lesson || [];
      const ineffective = (data.guards || []).filter((guard) => guard.recurrences > 0).length;
      // A rule can be judged ineffective by the verdicts of its runs alone, with no recurrence recorded.
      const weak = (data.effectiveness || []).filter((rule) => rule.state === "ineffective").length;
      const total = data.guards_total || 0;
      ctx.setSummary([P.count(total, "guard") + " " + isAre(total) + " active", ineffective ? P.count(ineffective, "guard") + " recorded a recurrence" : "",
        weak ? P.count(weak, "rule") + " " + isAre(weak) + " ineffective" : "", P.count(proposed.total, "lesson") + " wait" + (proposed.total === 1 ? "s" : "")].filter(Boolean).join(", ").replace(/, ([^,]+)$/, " and $1") + ".");
      const guards = [...(data.guards || [])].sort((a, b) => (b.recurrences || 0) - (a.recurrences || 0));
      const value = data.instructions || {}, roles = Object.keys(value.roles || {}), changes = data.scope_changes || [], signals = (data.signals || {}).signals || [];
      const counts = { proposed: proposed.total, guards: data.guards_total || 0, failures: failures.length, instructions: roles.length, scope: changes.length, signals: signals.length };
      const tabs = LEARNING_TABS.map(([id, label, tone]) => ({ id, label, count: counts[id], tone: counts[id] ? tone : null }));
      // A link from Now names its tab as section, which the tabs replaced.
      const tab = tabs.find((entry) => entry.id === (params.tab || params.section)) || tabs[0];
      container.classList.add("list-view");
      put(container, P.viewTabs("The parts of Learning", "learning-tab-", tabs, tab, (id) => P.go("learning", { tab: id }), ctx));
      const list = (rows, note, empty, foot) => P.listPane({ title: tab.label, count: tab.count, tone: tab.tone, note, rows, empty, foot });
      const turn = (to) => () => P.go("learning", { tab: "proposed", lesson_offset: to ? String(to) : "" });
      if (tab.id === "proposed") put(container, list(proposed.lessons.map((lesson) => P.paneRow("lesson-row-" + lesson.id, "learning", lesson.do || "No action is recorded.",
        P.words(lesson.pattern_type || "lesson") + ", proposed by " + (lesson.actor || "an agent") + " on " + P.date(lesson.created_at) + ". From: " + (lesson.episode_title || lesson.episode_id),
        (trigger) => P.openPane("decide", { kind: "lessons_to_accept", id: lesson.id, from: lesson.episode_title }, trigger))),
      "A lesson is a rule learned from a failure. A row decides the lesson in the pane, and an accepted lesson with triggers is a guard, which reminds agents when their work matches it.",
      "No proposed lesson awaits a decision.", [h("span", null, proposed.lessons.length ? "Showing " + (offset + 1) + " to " + (offset + proposed.lessons.length) + " of " + proposed.total + "." : ""),
        P.live && (offset || proposed.more) ? h("span", { class: "row" }, offset ? button("Previous " + PAGE, "lessons-previous", turn(Math.max(0, offset - PAGE)), "small") : null,
          proposed.more ? button("Next " + PAGE, "lessons-next", turn(offset + PAGE), "small") : null) : null]));
      else if (tab.id === "guards") put(container, list(guards.map((guard) => P.paneRow("guard-row-" + guard.lesson_id, "learning", guard.do || "No action is recorded.",
        (guard.recurrences ? "Recurred " + P.count(guard.recurrences, "time") : "Active guard") + ". " + (triggerParts(guard, "paths").join(", ") || "No triggers are recorded") + ".",
        (trigger) => P.openPane("guard", { id: guard.lesson_id }, trigger))),
      "A guard that recorded a recurrence is listed first, with its recurrences and their Reassess action in the pane." + (data.guards_more ? " Only the first " + guards.length + " guards are shown." : ""),
      "No lesson has been accepted as a guard yet."));
      else if (tab.id === "failures") put(container, list(failures.map((item) => P.paneRow("failure-row-" + item.outcome_id, "decisions", item.observed || item.title,
        P.words(item.severity) + " severity" + (item.failure_type ? ", failure type " + item.failure_type : "") + ". " + (item.title || item.episode_id) + ", " + P.date(item.created_at),
        (trigger) => P.openRecord(item.outcome_id, trigger))),
      "A failed outcome that no lesson followed. A row opens the outcome with its " + P.term("work_item").toLowerCase() + ".", "Every failed outcome has a lesson or a later complete result."));
      else if (tab.id === "instructions") put(container, list(roles.map((role) => { const item = value.roles[role];
        return P.paneRow("instructions-row-" + role, "agents", P.words(role) + " role", P.count((value.counts || {})[role] || 0, "accepted rule") + ", " + (item.rule_ids || []).length + " in this prompt, "
          + number(item.used || 0) + " of " + number(item.budget || 0) + " characters used.", (trigger) => P.openPane("instructions", { role }, trigger)); }),
      value.note, "No instruction text is available in this snapshot."));
      else if (tab.id === "scope") put(container, region(listOf(changes, (item) => [h("div", { class: "row" }, openButton(episodeTitle(item.episode_id), item.episode_id, { work: true, prefix: "scope-work-" }),
        h("span", { class: "muted" }, P.date(item.created_at))), h("p", null, "The actor " + item.actor + " added " + P.count(item.added.length, "path") + " to the allowed paths."),
      h("div", { class: "row" }, chips(item.added)), item.reason ? h("p", { class: "muted" }, "Reason: " + item.reason) : null,
      h("div", null, openButton("Open the plan revision", item.plan_id, { class: "small", prefix: "scope-plan-" }))], "No agent has added allowed paths to a " + P.term("work_item").toLowerCase() + ".")));
      else put(container, region((data.signals || {}).note ? h("p", { class: "muted" }, data.signals.note) : null,
        listOf(signals, (signal, index) => [h("div", { class: "row" }, h("strong", null, P.words(signal.type)), signal.failure_type ? chips([signal.failure_type], "Failure type ") : null),
          signal.type === "repeated_failure" ? h("p", null, "The failure was recorded " + P.count(signal.failure_count, "time") + " in " + P.count(signal.assessed, "assessed outcome") + ", of which " + number(signal.successful) + " succeeded.") : null,
          h("p", { class: "muted" }, signal.reason),
          h("div", { class: "row" }, (signal.record_ids || []).map((id, position) => titledButton(id, { label: "Record " + (position + 1), class: "small", prefix: "signal-" + index + "-" })))], "No signal is recorded.")));
  } });

  // Agents.
  // These three columns describe delegated work. For an agent check they stay empty, and a sentence says so once.
  const mergeLabel = (run) => (run.role !== "work" ? null : run.merge ? P.badge(run.merge.state)
    : run.state === "completed" && run.changed_files ? P.badge("review", "Awaiting a decision") : "Not merged");
  const reviewLabel = (run) => (run.role !== "work" ? null : run.review ? P.badge(run.review.state) : "No review");
  const canMerge = (run) => run.role === "work" && run.state === "completed" && run.changed_files > 0 && !run.merge && !(run.review && ACTIVE.includes(run.review.state));
  const canDiscard = (run) => run.role === "work" && !run.merge && !ACTIVE.includes(run.state) && !(run.review && ACTIVE.includes(run.review.state));
  const canRetryReview = (run) => run.role === "work" && run.state === "completed" && !run.merge && (!run.review || RETRY_REVIEW.includes(run.review.state));
  function runActions(run, ctx) {
    if (!ctx.canEdit) return null;
    const context = { run_id: run.id };
    const buttons = [canMerge(run) ? actionButton("Merge", "merge-" + run.id, openForm("merge", context), "primary") : null,
      canRetryReview(run) ? actionButton("Request review", "review-" + run.id, openForm("request_work_review", context)) : null,
      ACTIVE.includes(run.state) ? actionButton("Cancel", "cancel-" + run.id, openForm("cancel_run", context), "danger") : null,
      canDiscard(run) ? actionButton("Discard", "discard-" + run.id, openForm("discard", context), "danger") : null].filter(Boolean);
    return buttons.length ? h("div", { class: "row" }, buttons) : null;
  }
  function hostCard(host) {
    const ready = host.installed && host.available;
    return h("article", { class: "card" },
      h("h3", null, P.words(host.host), P.badge(ready ? "available" : "unavailable", ready ? "Can run work" : "Cannot run work")),
      h("div", { class: "row" }, P.badge(host.installed ? "available" : "missing", host.installed ? "Installed" : "Not installed"),
        P.badge(host.available ? "available" : "unavailable", host.available ? "Available" : "Unavailable")),
      !host.available ? h("p", null, host.until ? "The host is unavailable until " + P.date(host.until) + "." : "The host is unavailable. No end time is recorded.") : null,
      host.reason ? h("p", { class: "muted" }, "Reason: " + host.reason) : null,
      !host.installed ? h("p", { class: "muted" }, "The program of this host was not found on this computer.") : null);
  }
  // The runs are rows that open the run pane, and the hosts and the follow ups are regions under their own tab.
  const AGENT_TABS = [["runs", "Runs", null], ["hosts", "Hosts", null], ["attention", "Follow ups", "review"]];
  P.registerView("agents", { title: "Agents", async render(container, params, ctx) {
      const offset = Number(params.offset) || 0, data = await P.get("agents", offset ? { offset: String(offset) } : {});
      const hosts = data.hosts || [], runs = (data.runs || {}).runs || [], active = data.active || [], attention = data.attention || [];
      const ready = hosts.filter((host) => host.installed && host.available).length;
      const awaiting = runs.filter((run) => run.role === "work" && run.state === "completed" && run.changed_files && !run.merge).length;
      ctx.setSummary((data.configured ? number(ready) + " of " + P.count(hosts.length, "configured host") + " can run work now. " : "No agent host is configured for this project. ") +
        (active.length ? P.count(active.length, "agent run") + " " + isAre(active.length) + " active. " : "No agent run is active. ") +
        (awaiting ? P.count(awaiting, "delegated run") + " " + (awaiting === 1 ? "awaits" : "await") + " a merge decision." : ""));
      const counts = { runs: null, hosts: hosts.length, attention: attention.length };
      const tabs = AGENT_TABS.map(([id, label, tone]) => ({ id, label, count: counts[id], tone: counts[id] ? tone : null }));
      const tab = tabs.find((entry) => entry.id === params.tab) || tabs[0];
      container.classList.add("list-view");
      put(container, P.viewTabs("The parts of Agents", "agents-tab-", tabs, tab, (id) => P.go("agents", { tab: id }), ctx));
      // Runs are a table: the review and the merge describe delegated work and stay empty for an agent check.
      if (tab.id === "runs") put(container, h("p", { class: "muted db-note" }, "A row opens the run with its report, its diff summary and its actions. Review, Merge and Changed files describe delegated work and stay empty for an agent check."),
        h("section", { class: "list-pane" }, P.dbTable({ id: "runs", rows: runs, rowKey: (run) => run.id, onOpen: (run, t) => openRun(run.id)(t), keepOrder: true, empty: "No agent run is recorded.",
          properties: [{ key: "title", label: "Run", type: "title", width: 240, sortable: false, rowIcon: () => "agents", get: (run) => P.words(run.role) + " run on " + P.words(run.host) },
            { key: "state", label: "State", type: "status", width: 130, sortable: false },
            { key: "review", label: "Review", type: "custom", icon: "check", width: 130, sortable: false, render: (run) => (run.role === "work" && run.review ? P.badge(run.review.state) : null) },
            { key: "merge", label: "Merge", type: "custom", icon: "relation", width: 170, sortable: false, render: (run) => (run.role === "work" ? mergeLabel(run) : null) },
            { key: "work", label: P.term("work_item"), type: "text", icon: "relation", width: 220, sortable: false, get: (run) => episodeTitle(run.episode_id) },
            { key: "changed", label: "Changed files", type: "number", width: 124, sortable: false, get: (run) => (run.role === "work" ? String(run.changed_files || 0) : "") },
            { key: "created_at", label: "Started", type: "date", width: 128, sortable: false }],
          foot: pager({ offset, count: runs.length, more: (data.runs || {}).more, limit: 20 }, "runs", (next) => P.go("agents", { ...params, offset: next ? String(next) : "" })) })));
      else if (tab.id === "hosts") put(container, region(data.configured ? null : h("div", { class: "notice" }, h("p", null, "Configure an agent host for this project before you delegate work or request checks.")),
        grid(hosts, hostCard, "No agent host is configured for this project.")));
      else put(container, region(listOf(attention, (item) => [h("p", null, item.reason),
        h("div", { class: "row" }, h("span", { class: "muted" }, P.date(item.created_at)), item.run_id ? actionButton("Open the run", "attention-" + item.id, openRun(item.run_id)) : null)], "No follow up needs attention.")));
  } });

  function diffFiles(text) {
    const files = [];
    for (const line of String(text || "").split("\n")) {
      const current = files[files.length - 1];
      if (line.startsWith("diff --git ")) files.push({ path: (line.match(/ b\/(.+)$/) || [null, line.slice(11)])[1], added: 0, removed: 0 });
      else if (current && line.startsWith("+") && !line.startsWith("+++")) current.added++;
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
      put(holder, files.length ? table(["File", "Added lines", "Removed lines"], files.map((file) => h("tr", null, h("td", { class: "mono", dataset: { label: "File" } }, file.path),
        cell("Added lines", number(file.added)), cell("Removed lines", number(file.removed))))) : P.empty("The diff names no changed file."),
      detail.body_more ? h("p", { class: "muted" }, "The summary covers the first part of the diff only.") : null);
    } catch (error) {
      put(holder, failed(error));
    }
    return holder;
  }
  P.registerPane("run", { async render(body, params, ctx) {
      const { run } = await P.get("run", { id: params.id }), report = run.report || {};
      ctx.setKind("Agent run");
      ctx.setTitle(P.words(run.role) + " run on " + P.words(run.host));
      put(ctx.foot, runActions(run, ctx));
      put(body, h("div", { class: "row" }, P.badge(run.state), run.role === "work" ? mergeLabel(run) : null, h("span", { class: "muted" }, P.date(run.created_at))),
        h("p", { class: "mono muted" }, run.id), run.summary ? h("p", null, run.summary) : null,
        run.error ? h("div", { class: "notice", dataset: { tone: "blocked" } }, h("p", null, run.error)) : null,
        kv([[P.term("work_item"), openButton(episodeTitle(run.episode_id), run.episode_id, { work: true, prefix: "pane-work-" })],
          ["Role", P.words(run.role)], ["Host", P.words(run.host)], ["Updated", P.date(run.updated_at)],
          ["Allowed paths", run.paths && run.paths.length ? h("div", { class: "row" }, chips(run.paths)) : "None recorded"],
          ["Review", run.role === "work" ? reviewLabel(run) : undefined],
          ["Branch", run.branch || undefined], ["Workspace", run.workspace || undefined],
          ["Parent run", run.parent_run ? actionButton("Open the delegated run", "parent-" + run.parent_run, openRun(run.parent_run)) : undefined],
          ["Commit", (run.metrics || {}).commit || undefined]]));
      const checks = report.checks_run || report.checks || [];
      put(body, section("Report checks", null, checks.length ? h("ul", { class: "list" }, checks.map((check) => h("li", null, valueNode(check)))) : P.empty("The report lists no checks.")));
      const findings = report.findings || [];
      if (report.verdict || findings.length || run.role !== "work") put(body, section("Findings", report.verdict ? P.badge(report.verdict, "Verdict: " + P.words(report.verdict)) : null,
        findings.length ? h("ul", { class: "list" }, findings.map((finding) => h("li", null, valueNode(finding)))) : P.empty("The report records no findings.")));
      const other = Object.fromEntries(Object.entries(report).filter(([key]) => !["summary", "checks_run", "checks", "findings", "verdict", "lesson_proposals"].includes(key)));
      if (Object.keys(other).length) put(body, section("Other report fields", null, kv(Object.entries(other).map(([key, value]) => [P.words(key), value]))));
      if (run.diff_source) put(body, await diffSummary(run.diff_source));
      if ((run.reviews || []).length) put(body, section("Work reviews", null, h("ul", { class: "list" }, run.reviews.map((review) => h("li", { class: "row" }, P.badge(review.state),
        P.words(review.host), h("span", { class: "muted" }, P.date(review.created_at)), actionButton("Open the review", "review-run-" + review.id, openRun(review.id)))))));
  } });

  // Records: the kind, the search and the subject sit in one row of the head, the other five filters wait in a fold,
  // the rows of the chosen kind scroll under them, the pager stays in the foot, and a row opens the record beside the list.
  const recordViews = () => [["all", "All record kinds"], ["episodes", P.term("work_items")], ["decisions", "Decisions"], ["pending", "Pending decisions"],
    ["drift", "Records to recheck"], ["documents", "Documents"], ["sources", "Sources"], ["direction", "Requirement versions"], ["research", "Research"],
    ["corrections", "Corrections"], ["lessons", "Lessons"], ["patterns", "Patterns"], ["events", "All events"], ["captures", "Host captures"]];
  const FILTERS = ["query", "subject", "status", "episode", "from", "to", "order"];
  const RECORD_ICONS = { episode: "work", decision: "decisions", lesson: "learning", project_revision: "requirements" };
  // The filter box and its fold keep their open state across renders, so a live update does not fold them again.
  const folds = new Map();
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
  const openRecordAny = (record, trigger) => (record.kind === "episode" ? P.openWork(record.id, trigger) : P.openRecord(record.id, trigger));
  // Records is a database: each kind of record is a view, the filters are chips under the bar, and every kind shows as
  // a table whose title opens the record beside it. All record kinds is a table grouped by kind with five rows each.
  const MAIN_VIEWS = new Set(["all", "episodes", "decisions", "documents", "sources", "lessons", "events"]);
  const VIEW_ICONS = { all: "table", episodes: "work", decisions: "decisions", documents: "page", sources: "records", lessons: "learning", events: "timeline",
    pending: "decisions", drift: "alert", direction: "requirements", research: "search", corrections: "flag", patterns: "learning", captures: "agents" };
  const KIND_OF = { episodes: "Work items", events: "Events", sources: "Sources", captures: "Host captures", direction: "Requirement versions" };
  const recordProperties = () => [
    { key: "title", label: "Title", type: "title", width: 360, sortable: true, get: (record) => record.title || record.id,
      rowIcon: (record) => RECORD_ICONS[record.kind] || "page" },
    { key: "state", label: "State", type: "status", width: 150, sortable: false, get: (record) => record.state || record.status },
    { key: "kind", label: "Kind", type: "select", width: 132, sortable: false, labelOf: (record) => (record.kind === "episode" ? P.term("work_item") : P.words(record.kind)) },
    { key: "subject", label: "Subject", type: "select", width: 112, sortable: false },
    { key: "work", label: P.term("work_item"), type: "text", icon: "relation", width: 240, sortable: false,
      get: (record) => (record.episode_id && record.episode_id !== record.id ? record.episode_title || episodeTitle(record.episode_id) : "") },
    { key: "date", label: "Date", type: "date", width: 128, sortable: true },
  ];
  function recordFilters(params, update) {
    const episodes = ((P.health() || {}).episodes || []).map((item) => [item.id, item.title]);
    const dateField = (name, label) => P.field(label, h("input", { type: "date", id: "records-" + name, name, value: params[name] || "",
      on: { change: (event) => update(name, event.currentTarget.value) } }));
    return P.filterRow(
      P.filterField("select", "records-subject", "Subject", params.subject || "", (value) => update("subject", value), [["", "Any subject"], ...SUBJECTS.map((name) => [name, P.words(name)])]),
      P.filterField("select", "records-status", "Status", params.status || "", (value) => update("status", value), [["", "Any status"], ...STATUSES.map((name) => [name, P.words(name)])]),
      P.filterField("select", "records-episode", P.term("work_item"), params.episode || "", (value) => update("episode", value), [["", "Any " + P.term("work_item").toLowerCase()], ...episodes]),
      dateField("from", "From"), dateField("to", "To"),
      h("button", { type: "button", class: "quiet small", id: "records-clear", on: { click: () => P.go("records", params.view ? { view: params.view } : {}) } }, "Clear filters"));
  }
  P.registerView("records", { title: "Records", async render(container, params, ctx) {
      const view = params.view || "all", labels = Object.fromEntries(recordViews()), label = labels[view] || P.words(view);
      const filters = Object.fromEntries(FILTERS.filter((name) => params[name]).map((name) => [name, params[name]]));
      const filtered = Object.keys(filters).some((name) => name !== "order"), notes = [];
      if (!P.live) notes.push("This snapshot filters the first " + SNAPSHOT_PAGE + " records of each kind in the browser.");
      // A filter keeps the view in place: the route changes and the view renders again with the focus and the caret kept.
      const go = (next) => { P.setParams(Object.fromEntries(Object.entries({ ...params, offset: "", ...next }).filter(([, value]) => value))); P.refresh(); };
      const update = (name, value) => go({ [name]: value });
      const active = ["subject", "status", "episode", "from", "to"].filter((name) => params[name]).length;
      const open = folds.has("records-filters") ? folds.get("records-filters") : active > 0;
      const order = params.order || "newest", sort = order === "title" ? { key: "title", dir: "asc" } : { key: "date", dir: order === "oldest" ? "asc" : "desc" };
      const setSort = (key, dir) => update("order", key === "title" ? "title" : dir === "asc" ? "oldest" : "");
      container.classList.add("list-view");
      put(container, h("div", { class: "db-controls" }, P.dbBar({ id: "records", label: "Record kinds", view,
        views: recordViews().map(([id, text]) => ({ id, label: id === "all" ? "All" : text, icon: VIEW_ICONS[id], more: !MAIN_VIEWS.has(id) })),
        onView: (id) => P.go("records", { ...filters, view: id === "all" ? "" : id }),
        filter: { active, open, onToggle: () => { folds.set("records-filters", !open); P.refresh(); } },
        sort: { options: [["date", "Date"], ["title", "Title"]], key: sort.key, dir: sort.dir, quiet: !params.order, onChange: setSort },
        search: { id: "records-query", value: params.query || "", placeholder: "Search records", onInput: (value) => update("query", value) } }),
      open ? recordFilters(params, update) : null));
      const properties = recordProperties(), note = notes.join(" ");
      if (view === "all") {
        const groups = await Promise.all(ALL_VIEWS.map((name) => loadRecords(name, filters, 0, 5).then((page) => [name, page], (error) => [name, null, error])));
        const total = groups.reduce((sum, [, page]) => sum + (page ? page.total : 0), 0);
        ctx.setSummary(P.count(total, "record") + " " + (total === 1 ? "matches" : "match") + (filtered ? " these filters." : " across all kinds."));
        const pages = new Map(groups.filter(([, page]) => page && page.total).map(([name, page]) => [name, page]));
        const rows = [...pages].flatMap(([name, page]) => page.records.map((record) => ({ ...record, group: name })));
        const errors = groups.filter(([, , error]) => error).map(([name, , error]) => h("div", { class: "stack" }, h("strong", null, labels[name]), failed(error)));
        put(container, h("section", { class: "list-pane" }, errors, P.dbTable({ id: "records", rows, rowKey: (record) => record.id, onOpen: openRecordAny,
          properties: [...properties, { key: "group", label: "Kind", type: "select", hidden: true, order: ALL_VIEWS, labelOf: (record) => KIND_OF[record.group] || labels[record.group] }],
          hidden: ["group"], group: "group", sort: null, empty: "No records match these filters.", foot: [note, "Each kind shows its five latest records."].filter(Boolean).join(" "),
          groupExtra: (name) => { const page = pages.get(name); return page && page.total > page.records.length
            ? button("Show all " + number(page.total), "records-all-" + name, () => P.go("records", { ...filters, view: name }), "quiet small") : null; } })));
        return;
      }
      const offset = Number(params.offset) || 0, page = await loadRecords(view, filters, offset, PAGE);
      ctx.setSummary(P.count(page.total, "record") + " in " + label.toLowerCase() + " " + (page.total === 1 ? "matches" : "match") + (filtered ? " these filters." : "."));
      if (page.partial) notes.push("This kind holds more records than the snapshot includes.");
      put(container, h("section", { class: "list-pane" }, P.dbTable({ id: "records", rows: page.records, rowKey: (record) => record.id, onOpen: openRecordAny, properties,
        sort, onSort: setSort, empty: "No records match these filters.", keepOrder: true,
        foot: [notes.length ? h("span", null, notes.join(" ")) : null,
          pager({ offset, count: page.records.length, total: page.total, more: page.more, limit: PAGE }, "records", (next) => update("offset", next ? String(next) : ""))] })));
  } });

  // Record pane: fields, evidence, reverse references and paged source text with an outline.
  function outline(text) {
    const found = [];
    let fenced = false;
    for (const line of String(text).split("\n")) {
      if (line.startsWith("```")) fenced = !fenced;
      const match = !fenced && line.match(/^(#{1,6})\s+(.+)$/);
      if (match) found.push({ level: match[1].length, text: match[2] });
    }
    return found;
  }
  function sourceText(id, first) {
    const holder = h("section", { class: "stack" });
    const state = { offset: Number(first.body_offset) || 0, original: false, detail: first };
    const draw = () => {
      const detail = state.detail, body = detail.body, headings = outline(body);
      const content = state.original ? h("pre", { class: "source-text" }, body) : P.markdown(body);
      const end = state.offset + body.length;
      const toggle = button(state.original ? "Show formatted text" : "Show original text", "source-toggle", () => { state.original = !state.original; draw(); }, "small");
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
        button("Previous part", "source-previous", () => go(Math.max(0, state.offset - 12000)), "small", { disabled: state.offset <= 0 }),
        button("Next part", "source-next", () => go(detail.next_offset), "small", { disabled: !detail.body_more }));
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
      button(record.title || record.id, "related-" + record.id, (trigger) => openRecordAny(record, trigger), "quiet kn-link"),
      h("span", { class: "muted" }, P.words(record.kind)), P.badge(record.state || record.status)))) : P.empty("No other record refers to this record.");
  }
  P.registerPane("record", { async render(body, params, ctx) {
      const [{ record }, related] = await Promise.all([P.get("record", { id: params.id }),
        P.get("records", { related: params.id, limit: String(PAGE) }).catch((error) => error)]);
      const detail = record.detail || {}, payload = detail.payload || {};
      ctx.setTitle(record.title || record.id);
      ctx.setKind(record.kind === "episode" ? P.term("work_item") : P.words(record.kind));
      const work = record.episode_id && record.episode_id !== record.id
        ? openButton([P.icon("page"), record.episode_title || episodeTitle(record.episode_id)], record.episode_id, { work: true, prefix: "pane-episode-", class: "link-button" }) : null;
      put(body, P.props([["status", "State", P.badge(record.state || record.status)],
        ["select", "Kind", P.chip(record.kind === "episode" ? P.term("work_item") : P.words(record.kind))], ["select", "Subject", P.chip(P.words(record.subject))],
        ["relation", P.term("work_item"), work], ["date", "Date", P.date(record.date)], ["number", "Identifier", h("span", { class: "mono muted" }, record.id)]]));
      if (record.kind === "lesson" && ctx.canEdit) {
        const lesson = { ...payload, id: record.id, episode_id: record.episode_id };
        const status = detail.lesson_status || record.status;
        if (status === "proposed") put(ctx.foot, h("div", { class: "row" }, actionButton("Accept", "pane-accept", lessonReview(lesson, "accepted"), "primary"),
          actionButton("Reject", "pane-reject", lessonReview(lesson, "rejected"))));
        if (status === "accepted") put(ctx.foot, h("div", { class: "row" }, actionButton("Retire", "pane-retire", lessonReview(lesson, "retired"))));
      }
      const fields = record.kind === "episode" ? { objective: detail.objective, criterion: detail.criterion, task_type: detail.task_type, status: detail.status, version: detail.version }
        : record.kind === "source" ? { summary: detail.summary, origin: detail.origin, version: detail.version, checked_at: detail.checked_at, review_after: detail.review_after, ...(detail.document ? { path: detail.document.path, format: detail.document.format, authority: detail.document.authority } : {}) }
          : record.kind === "project_revision" ? { requirements: detail.requirements, reason: detail.reason, actor: detail.actor }
            : record.kind === "host_receipt" ? { event_name: detail.event_name, tool_name: detail.tool_name, session_id: detail.session_id, ...(detail.payload || {}) }
              : { ...payload, actor: detail.actor };
      const shown = Object.entries(fields).filter(([, value]) => value !== undefined && value !== null && value !== "");
      // Short values are properties of the page, and long text is its body under a heading of its own.
      const long = ([, value]) => typeof value === "string" && value.length > 90;
      const short = shown.filter((entry) => !long(entry)), text = shown.filter(long);
      if ((detail.review_reasons || []).length) put(body, P.callout("alert", [h("strong", null, "This record needs review."), valueNode(detail.review_reasons)], "review"));
      if (short.length) put(body, P.props(short.map(([name, value]) => ["text", P.words(name), valueNode(value, name)])));
      if (text.length) put(body, P.divider(), text.map(([name, value]) => h("section", { class: "stack" }, P.heading(3, P.words(name)), h("p", { class: "kn-text" }, value))));
      if (record.outcome) {
        const assessment = (record.outcome.detail.payload || {}).assessment;
        put(body, section("Latest outcome", P.badge(assessment === "bad" ? "failed" : assessment === "good" ? "good" : "mixed", "Assessment: " + P.words(assessment)),
          h("p", null, openButton(record.outcome.title, record.outcome.id, { prefix: "pane-outcome-" }))));
      }
      const chain = [["Replaces", detail.supersedes], ["Replaced by", detail.replaced_by], ["Decision", detail.decision_id]].filter(([, id]) => id);
      if (chain.length) put(body, P.props(chain.map(([label, id]) => ["relation", label, titledButton(id, { prefix: "pane-chain-", class: "link-button" })])));
      if (record.kind === "source") put(body, typeof detail.body === "string" ? sourceText(record.id, detail)
        : section("Content", null, h("p", { class: "muted" }, "The source text is not included in this snapshot.")));
      const evidence = detail.evidence || [];
      put(body, section("Evidence", counted(evidence.length, "reference"), listOf(evidence, (ref) => [h("div", { class: "row" }, openButton(ref.title || ref.source_id, ref.source_id, { prefix: "evidence-" }),
        ref.status ? P.badge(ref.status) : null, ref.origin ? h("span", { class: "muted" }, P.words(ref.origin)) : null), ref.reason ? h("p", { class: "muted" }, ref.reason) : null],
      "No evidence is attached to this record.")));
      if ((detail.links || []).length) put(body, section("Earlier records", null, listOf(detail.links, (ref) => [titledButton(ref.event_id, { prefix: "link-" }),
        ref.reason ? h("p", { class: "muted" }, ref.reason) : null])));
      put(body, section("Referenced by", related instanceof Error ? null : counted(related.total, "record"),
        related instanceof Error ? failed(related) : referenceList(related),
        !(related instanceof Error) && related.more ? h("p", { class: "muted" }, "Only the first " + related.records.length + " references are shown.") : null));
  } });

  // Requirements: the current text, its version, the approval evidence and the earlier revisions read in one scrolling
  // region of the frame, and Review requirements stays in the foot. The approval keeps its dialog with its reason.
  P.registerView("requirements", { title: "Requirements", async render(container, params, ctx) {
      const offset = Number(params.offset) || 0, revisionOffset = Number(params.revision_offset) || 0;
      const data = await P.get("requirements", { offset: offset ? String(offset) : "", revision_offset: revisionOffset ? String(revisionOffset) : "" });
      const items = data.items || { items: [], total: 0 }, current = data.current || {}, revisions = data.revisions || [];
      const established = items.status !== "not_established";
      ctx.setSummary(established ? "Version " + current.version + " is current with " + P.count(items.total, "requirement") + "."
        : "The requirements are not established yet and wait for your review.");
      // The requirements read as one Notion page: its properties, a callout, the numbered text and its history.
      const history = revisions.map((revision) => ({ ...revision, id: "direction_" + revision.version, title: "Version " + revision.version,
        reason_text: revision.reason || (revision.version === current.version ? current.reason : "") || "" }));
      container.classList.add("list-view");
      put(container, h("section", { class: "list-pane" }, h("div", { class: "pane-rows pane-body doc", dataset: { scroll: "requirements" } },
        P.props([["status", "Status", P.badge(established ? items.status : "review", established ? P.words(items.status) : "Not established")],
          ["number", "Version", String(items.version)], ["person", "Approved by", current.actor], ["date", "Approved on", current.created_at ? P.date(current.created_at) : null],
          ["text", "Reason", current.reason]]),
        P.callout("info", h("p", null, "The recorded requirements govern decisions. They do not prove that the project covers every need.")),
        P.divider(),
        h("div", { class: "block-head" }, P.heading(2, "Requirements"), counted(items.total, "requirement")),
        items.items.length ? h("ol", { class: "kn-requirements", start: String(offset + 1) }, items.items.map((item) => h("li", null, item.text))) : P.empty("No requirement is recorded yet."),
        pager({ offset, count: items.items.length, total: items.total, more: items.more, limit: PAGE }, "requirements", (next) => P.go("requirements", { ...params, offset: next ? String(next) : "" })),
        section("Approval evidence", counted(items.evidence_total || 0, "reference"),
          listOf(items.evidence || [], (ref, index) => [openButton([P.icon("page"), ref.title || "Approval source " + (offset + index + 1)], ref.source_id, { prefix: "requirement-evidence-", class: "link-button" }),
            ref.reason ? h("p", { class: "muted" }, ref.reason) : null], "No approval evidence is recorded for this version.")),
        section("History", null, h("div", { class: "db-inline" }, P.dbTable({ id: "revisions", rows: history, rowKey: (revision) => revision.version, keepOrder: true,
          onOpen: (revision, t) => P.openRecord(revision.id, t), empty: "No earlier version is recorded.",
          properties: [{ key: "title", label: "Version", type: "title", width: 150, sortable: false, rowIcon: () => "requirements" },
            { key: "requirement_count", label: "Requirements", type: "number", width: 130, sortable: false }, { key: "evidence_count", label: "Evidence", type: "number", width: 110, sortable: false },
            { key: "actor", label: "Approved by", type: "text", icon: "person", width: 140, sortable: false }, { key: "created_at", label: "Date", type: "date", width: 130, sortable: false },
            { key: "reason_text", label: "Reason", type: "text", width: 320, sortable: false }],
          foot: pager({ offset: revisionOffset, count: revisions.length, more: data.more, limit: 10 }, "revisions", (next) => P.go("requirements", { ...params, revision_offset: next ? String(next) : "" })) })))),
        ctx.canEdit ? h("div", { class: "pane-foot" }, h("span", null, established ? P.count(items.total, "requirement") + " in version " + items.version + "." : "No version is approved yet."),
          h("button", { type: "button", class: "primary small", id: "review-requirements", on: { click: (event) => P.openForm("requirements", {}, event.currentTarget) } }, "Review requirements")) : null));
  } });

  // Machine: the rules the user promoted out of single projects, the projects on this computer and the proposals
  // this project recorded. The machine memory is read here and is written only by an action of the user.
  const ruleFacts = (rule) => kv([["When", rule.when], ["Do", rule.do], ["Because", rule.because], ["Exceptions", rule.exceptions]]);
  const ruleTriggers = (rule) => triggerChips(rule, "roles", null);
  function machineRule(rule, ctx) {
    const retired = rule.status !== "accepted";
    return h("article", { class: "card", dataset: { key: "machine-rule-" + rule.rule_id, tone: retired ? "done" : "guarded" } },
      h("h3", null, h("span", null, rule.do || "No action is recorded."), P.badge(rule.status)),
      ruleFacts(rule), ruleTriggers(rule),
      h("p", { class: "muted" }, P.count(rule.adopted_by || 0, "project") + " promoted this rule. It was recorded on " + P.date(rule.recorded_at) + "."),
      rule.basis ? h("p", { class: "muted" }, "Basis: " + rule.basis) : null,
      ctx.canEdit && !retired ? h("div", { class: "row" }, actionButton("Retire", "retire-rule-" + rule.rule_id,
        openForm("machine_rule", { rule_id: rule.rule_id }))) : null);
  }
  function promotionCard(item, ctx) {
    const open = (status) => openForm("promotion", { promotion_id: item.id, status });
    const waiting = item.state === "proposed";
    return h("article", { class: ["card", waiting ? "proposed" : null], dataset: { key: "promotion-" + item.id } },
      h("h3", null, h("span", null, (item.rule || {}).do || "No action is recorded."), P.badge(item.state)),
      ruleFacts(item.rule || {}), ruleTriggers(item.rule || {}),
      h("p", { class: "muted" }, "Proposed by " + (item.actor || "an agent") + " on " + P.date(item.proposed_at) + "."),
      item.basis ? h("p", { class: "muted" }, "Basis: " + item.basis) : null,
      item.reason ? h("p", { class: "muted" }, "Your reason: " + item.reason) : null,
      ctx.canEdit && waiting ? h("div", { class: "row" },
        actionButton("Accept", "accept-promotion-" + item.id, open("accepted"), "primary"),
        actionButton("Decline", "decline-promotion-" + item.id, open("declined"))) : null);
  }
  function registryTable(projects, here) {
    return table(["Project", "Template", "Lifecycle", "First seen", "Updated"], projects.map((item) => h("tr", { dataset: { key: "machine-project-" + item.id } },
      cell("Project", h("span", { class: "mono" }, item.path), item.path === here ? [" ", P.badge("current", "This project")] : null),
      cell("Template", item.template ? P.words(item.template) : "Not recorded"), cell("Lifecycle", P.badge(item.phase === "production" ? "review" : "in_progress", P.words(item.phase))),
      dateCell("First seen", item.first_seen), dateCell("Updated", item.updated_at))));
  }
  // One tab for each part of the machine memory. Every tab scrolls inside its region under the isolation notice.
  const MACHINE_TABS = [["proposals", "Proposals", "review"], ["rules", "Rules in force", "guarded"], ["retired", "Retired rules", null], ["projects", "Projects", null]];
  P.registerView("machine", { title: "Machine", async render(container, params, ctx) {
      let data;
      try {
        data = await P.get("machine");
      } catch (error) {
        put(container, error && error.notIncluded
          ? P.empty("The machine memory stays on the computer that holds it, so a snapshot carries no rule and no registry.")
          : failed(error));
        return;
      }
      const rules = data.rules || [], promotions = data.promotions || [], retired = data.retired || [], projects = data.projects || [];
      const waiting = promotions.filter((item) => item.state === "proposed"), decided = promotions.filter((item) => item.state !== "proposed");
      ctx.setSummary((data.exists ? P.count(data.rules_total || 0, "rule") + " " + isAre(data.rules_total || 0) + " in force on " + data.machine + ", promoted from "
        + P.count(data.projects_total || 0, "project") + ". " : "No machine memory exists on this computer yet. ")
        + P.count(waiting.length, "proposal") + " from this project " + (waiting.length === 1 ? "awaits" : "await") + " your decision.");
      const counts = { proposals: waiting.length, rules: rules.length, retired: retired.length, projects: data.projects_total || 0 };
      const tabs = MACHINE_TABS.map(([id, label, tone]) => ({ id, label, count: counts[id], tone: counts[id] ? tone : null }));
      const tab = tabs.find((entry) => entry.id === params.tab) || tabs[0];
      container.classList.add("list-view");
      put(container, P.viewTabs("The parts of Machine", "machine-tab-", tabs, tab, (id) => P.go("machine", { tab: id }), ctx));
      const notice = h("div", { class: "notice", dataset: { key: "machine-isolation" } },
        h("p", null, "The machine memory holds the rules that you promoted from single projects, so that they reach every project on this computer. " + data.note),
        data.error ? h("p", null, "The machine memory could not be read: " + data.error) : null, h("p", { class: "muted" }, "Database: " + data.database));
      if (tab.id === "proposals") put(container, region(notice, purpose("A proposal stays in this project until you accept it. Correct its text in the acceptance form when a word belongs to this project alone."),
        grid(waiting, (item) => promotionCard(item, ctx), "No proposal awaits your decision. An agent proposes a rule with the promote_rule action."),
        decided.length ? section("Decided proposals", counted(decided.length, "proposal"), grid(decided, (item) => promotionCard(item, ctx))) : null));
      else if (tab.id === "rules") put(container, region(notice, purpose("A rule in force is not rewritten. Retire it and promote the corrected text when it needs a change."),
        grid(rules, (rule) => machineRule(rule, ctx), "No rule is promoted to this machine yet.")));
      else if (tab.id === "retired") put(container, region(notice, grid(retired, (rule) => machineRule(rule, ctx), "No rule of this machine is retired.")));
      else put(container, region(notice, purpose("The registry stays on this computer. It is not part of an export and no agent reads it."),
        projects.length ? registryTable(projects, data.project_path) : P.empty("No project is recorded in the registry yet.")));
  } });
  // Hive: the shared working record of agents that work together. The list shows every swarm, and a swarm opens as a
  // conversation timeline in which replies and answers sit under the entry they reply to. Challenges, supports and
  // citations are labelled links to their entry. The move and agent filters live in the route, so a new revision keeps them.
  const HIVE_MOVES = ["orient", "hypothesis", "observation", "challenge", "support", "question", "answer", "conclusion", "pattern", "checkpoint"];
  const HOSTS = { codex: ["in_progress", "Codex"], claude: ["review", "Claude"], "workspace-user": ["guarded", "User"], session: ["neutral", "Session"] };
  const NESTED = ["replies_to", "answers"];
  const LINKS_OUT = { replies_to: "Replies to", answers: "Answers", challenges: "Challenges", supports: "Supports", cites: "Cites" };
  const LINKS_IN = { challenges: "Challenged by", supports: "Supported by", cites: "Cited by" };
  // Beyond this depth a reply follows its target at the same depth, so a long exchange stays readable on a phone.
  const THREAD_DEPTH = 2;
  const hostTone = (host) => (HOSTS[host] || ["neutral"])[0];
  const hostBadge = (host) => h("span", { class: "badge", dataset: { tone: hostTone(host), state: "host-" + host } }, (HOSTS[host] || [null, P.words(host)])[1]);
  const swarmBadge = (swarm) => P.badge(swarm.state === "open" ? "active" : "done", P.words(swarm.state));
  function addressee(value) {
    if (value === "user") return "Addressed to the user";
    if (value === "all") return "Addressed to every agent";
    const [kind, name] = value.split(/:(.*)/s);
    return kind === "role" ? "Addressed to the " + name + " role" : "Addressed to agent " + name;
  }
  function basisChip(basis) {
    if (basis.kind !== "command") return h("span", { class: "chip mono" }, P.words(basis.kind) + " " + basis.value);
    if (!basis.verified) return h("span", { class: "chip mono", dataset: { tone: "review", state: "unverified-command" } }, "Unverified command " + basis.value);
    return h("span", { class: "chip mono", dataset: { tone: basis.exit_code === 0 ? "ready" : "blocked", state: "verified-command" } },
      "Verified command " + basis.value + ", exit code " + basis.exit_code);
  }
  // A link moves the reader to its entry. An entry hidden by a filter is named in a message instead.
  function entryLink(label, id, byId, owner) {
    const other = byId.get(id);
    return button(label + " " + id + (other ? " by " + other.agent : ""), "hive-link-" + owner.id + "-" + label + "-" + id, () => {
      const node = document.getElementById("hive-" + id);
      if (!node) return P.toast("The entry " + id + " is hidden by the filters.");
      node.scrollIntoView({ block: "center" });
      node.focus({ preventScroll: true });
    }, "small quiet");
  }
  function hiveEntry(entry, view, children) {
    const { byId, swarm, ctx, revealing } = view, data = entry.data || {};
    const outgoing = entry.links_out.filter((link) => LINKS_OUT[link.relation]), incoming = entry.links_in.filter((link) => LINKS_IN[link.relation]);
    const answered = entry.links_in.some((link) => link.relation === "answers");
    const canAnswer = entry.move === "question" && !answered && ctx.canEdit && swarm.state === "open" && entry.host !== "workspace-user";
    return h("article", { class: "card", id: "hive-" + entry.id, tabindex: "-1", dataset: { key: "hive-entry-" + entry.id, move: entry.move, host: entry.host || "unknown" },
      style: { borderLeft: "3px solid " + P.colors[hostTone(entry.host)] } },
    h("div", { class: "row" }, hostBadge(entry.host), h("strong", null, entry.agent), P.chip(P.words(entry.move)),
      entry.confidence ? P.chip(P.words(entry.confidence) + " confidence") : null,
      entry.confirmed ? P.badge("confirmed", "Confirmed") : null, entry.disputed ? P.badge("blocked", "Disputed") : null,
      entry.move === "question" ? P.badge(answered ? "ready" : "review", answered ? "Answered" : "Open question") : null,
      revealing.has(entry.id) ? P.chip("Ends the blind phase of " + entry.agent) : null,
      h("span", { class: "muted" }, entry.id + ", " + P.date(entry.created_at))),
    entry.move === "checkpoint" ? kv([["Done", data.done], ["Belief", data.belief], ["Open questions", data.open_questions], ["Next step", data.next_step]])
      : h("p", null, entry.claim),
    entry.detail ? P.markdown(entry.detail) : null,
    entry.addressed_to ? h("p", { class: "muted" }, addressee(entry.addressed_to) + ".") : null,
    entry.bases.length ? h("div", { class: "row" }, entry.bases.map(basisChip)) : null,
    outgoing.length || incoming.length ? h("div", { class: "row" }, outgoing.map((link) => entryLink(LINKS_OUT[link.relation], link.to, byId, entry)),
      incoming.map((link) => entryLink(LINKS_IN[link.relation], link.from, byId, entry))) : null,
    entry.confirmed ? h("p", { class: "muted" }, "Confirmed: " + entry.confirmed) : null,
    entry.disputed ? h("p", { class: "muted" }, "Disputed: " + entry.disputed) : null,
    canAnswer ? h("div", { class: "row" }, P.formButton("Answer", "hive_post", { swarm_id: swarm.id, move: "answer", target: entry.id }, { class: "small" })) : null,
    children.length ? h("div", { class: "hive-thread" }, children) : null);
  }
  function timeline(entries, view) {
    const shown = new Set(entries.map((entry) => entry.id)), replies = new Map(), roots = [];
    for (const entry of entries) {
      const parent = entry.links_out.find((link) => NESTED.includes(link.relation) && shown.has(link.to));
      if (!parent) { roots.push(entry); continue; }
      if (!replies.has(parent.to)) replies.set(parent.to, []);
      replies.get(parent.to).push(entry);
    }
    const thread = (entry, depth) => {
      const below = replies.get(entry.id) || [];
      if (depth < THREAD_DEPTH) return [hiveEntry(entry, view, below.flatMap((reply) => thread(reply, depth + 1)))];
      return [hiveEntry(entry, view, []), ...below.flatMap((reply) => thread(reply, depth))];
    };
    return h("div", { class: "stack", dataset: { key: "hive-timeline" } }, roots.flatMap((entry) => thread(entry, 0)));
  }
  function agentRow(agent, swarm) {
    const blind = agent.phase === "blind";
    const note = !swarm.blind ? "This swarm has no blind phase." : agent.host === "workspace-user" ? "The user sees every entry."
      : blind ? "It has not posted its hypothesis, so the hypotheses, conclusions and patterns of other agents stay hidden from it."
        : "Its view opened on " + P.date(agent.revealed_at) + ", when it posted its hypothesis.";
    return h("div", { class: "stack", dataset: { key: "hive-agent-" + agent.agent_id, phase: agent.phase } },
      h("div", { class: "row" }, hostBadge(agent.host), h("strong", null, agent.agent_id), P.chip(P.words(agent.role) + " role"),
        h("span", { class: "badge", dataset: { tone: blind ? "review" : "ready", state: agent.phase } }, blind ? "Blind phase" : "Open phase"),
        h("span", { class: "muted" }, P.count(agent.entries, "entry", "entries"))),
      h("p", { class: "muted" }, note));
  }
  // The filters live here and in the address of Hive as swarm, move and agent, so a live update and a copied address keep them.
  const hiveFilter = { id: null, move: "", agent: "" };
  const syncHive = () => { if (P.route().name === "hive") P.setParams({ ...P.route().params, swarm: hiveFilter.id, move: hiveFilter.move, agent: hiveFilter.agent }); };
  P.registerPane("swarm", { async render(body, params, ctx) {
      const data = await P.get("hive", { id: params.id });
      const { swarm, agents } = data, open = swarm.state === "open";
      if (hiveFilter.id !== params.id) { const { name, params: r } = P.route(), same = name === "hive" && r.swarm === params.id; Object.assign(hiveFilter, { id: params.id, move: same && r.move || "", agent: same && r.agent || "" }); }
      syncHive();
      ctx.setKind("Swarm");
      ctx.setTitle(swarm.title);
      const byId = new Map(data.entries.map((entry) => [entry.id, entry]));
      const entries = data.entries.filter((entry) => (!hiveFilter.move || entry.move === hiveFilter.move) && (!hiveFilter.agent || entry.agent === hiveFilter.agent));
      const revealing = new Set();
      if (swarm.blind) {
        const seen = new Set();
        for (const entry of data.entries) if (entry.move === "hypothesis" && !seen.has(entry.agent)) { seen.add(entry.agent); revealing.add(entry.id); }
      }
      const filter = (name) => (value) => { hiveFilter[name] = value; syncHive(); P.refresh(); };
      const moves = {};
      for (const entry of data.entries) moves[entry.move] = (moves[entry.move] || 0) + 1;
      const context = { swarm_id: swarm.id };
      put(body, h("p", { dataset: { key: "hive-state" } }, `The swarm is ${open ? "open" : "closed"} with ${P.count(data.total, "entry", "entries")} from ${P.count(agents.length, "agent")}.`),
        h("p", null, swarm.purpose),
        h("div", { class: "row" }, swarmBadge(swarm), P.chip(P.words(swarm.kind) + " swarm"), P.chip("Opened " + P.date(swarm.opened_at)),
          swarm.closed_at ? P.chip("Closed " + P.date(swarm.closed_at)) : null, swarm.blind ? P.chip("Blind phase first") : null,
          swarm.episode_id ? button("Open the work item", "hive-work", (trigger) => P.openWork(swarm.episode_id, trigger), "small quiet") : null),
        swarm.summary ? h("p", { class: "muted" }, "Summary: " + swarm.summary) : null,
        Object.keys(moves).length ? h("p", { class: "muted" }, "Entries by move: " + Object.entries(moves).map(([move, n]) => P.lower(P.words(move)) + " " + n).join(", ") + ".") : null,
        section("Agents", counted(agents.length, "agent"), listOf(agents, (agent) => agentRow(agent, swarm), "No agent has joined this swarm yet.")),
        section("Timeline", h("span", { class: "muted" }, P.count(entries.length, "entry", "entries")),
          h("div", { class: "toolbar" },
            P.filterField("select", "hive-move", "Move", hiveFilter.move, filter("move"), [["", "All moves"], ...HIVE_MOVES.map((move) => [move, P.words(move)])]),
            P.filterField("select", "hive-agent", "Agent", hiveFilter.agent, filter("agent"), [["", "All agents"], ...agents.map((agent) => [agent.agent_id, agent.agent_id])])),
          entries.length ? timeline(entries, { byId, swarm, ctx, revealing })
            : P.empty(data.total ? "No entry matches the filters." : "No entry is recorded in this swarm yet."),
          data.more ? h("p", { class: "muted" }, `The first ${P.count(data.entries.length, "entry", "entries")} of ${data.total} are shown.`) : null));
      put(ctx.foot, ctx.canEdit && open ? h("div", { class: "row", dataset: { key: "hive-actions" } },
        P.formButton("Ask a question", "hive_post", { ...context, move: "question" }, { class: "small" }),
        P.formButton("Post an observation", "hive_post", { ...context, move: "observation" }, { class: "small" }),
        P.formButton("Close the swarm", "hive_close", context, { class: "small" })) : null);
  } });
  // The swarms are rows, an address that names a swarm opens its pane, and the purge of closed swarms stays in the foot.
  P.registerView("hive", { title: "Hive", async render(container, params, ctx) {
      const data = await P.get("hive", { limit: "50" });
      const swarms = data.swarms || [], opened = swarms.filter((swarm) => swarm.state === "open").length;
      const swarmPane = (id, trigger) => P.openPane("swarm", { id, route: ["swarm", "move", "agent"] }, trigger);
      ctx.onShown(() => { const row = params.swarm && container.querySelector('[data-key="hive-swarm-' + params.swarm + '"]');
        if (row && (hiveFilter.id !== params.swarm || document.getElementById("detail").hidden)) swarmPane(params.swarm, row); });
      ctx.setSummary(data.total ? `${P.count(data.total, "swarm")} ${isAre(data.total)} recorded, and ${opened} ${isAre(opened)} open.`
        : "No swarm is recorded yet. A swarm opens when agents start to work together on one problem.");
      const closed = ctx.canEdit && swarms.some((swarm) => swarm.state !== "open");
      container.classList.add("list-view");
      put(container, P.listPane({ title: "Swarms", count: data.total || 0, note: "The hive is the shared record of agents that work on one problem together. Each group of agents is a swarm, and a row opens its entries as a conversation.",
        rows: swarms.map((swarm) => P.paneRow("hive-swarm-" + swarm.id, "hive", swarm.title, h("span", { class: "row" }, swarmBadge(swarm), P.chip(P.words(swarm.kind) + " swarm"),
          P.chip(P.count(swarm.entries, "entry", "entries")), swarm.blind ? P.chip("Blind phase first") : null, h("span", { class: "muted" }, swarm.agents.map((agent) => agent.agent_id).join(", "))),
        (trigger) => swarmPane(swarm.id, trigger))),
        empty: "No swarm is recorded yet.", foot: [h("span", null, data.total > swarms.length ? `The ${swarms.length} newest swarms are shown.` : ""),
          !closed ? null : (P.health() || {}).assistant_started
            ? h("span", { dataset: { key: "hive-purge-refused" } }, "This control panel was started from inside an assistant session, so it does not purge swarms. Start the control panel from your own terminal with project-memory view to purge closed swarms.")
            : P.formButton("Purge closed swarms", "hive_purge", {}, { class: "small danger" })] }));
  } });

  // Usage: the usage ledger of this machine for each host, the latest probe of each host and the routing decisions of
  // the recent runs of this project. The ledger stays on the computer that holds it, so a snapshot carries no usage.
  const HOST_NAMES = { codex: "Codex", claude: "Claude", grok: "Grok", opencode: "OpenCode" };
  const hostName = (host) => HOST_NAMES[host] || P.words(host);
  const WINDOWS = [["last_5_hours", "Last 5 hours"], ["today", "Today"], ["last_7_days", "Last 7 days"]];
  const ROUTES = { preferred: "Preferred host", headroom: "Most headroom", all_constrained: "Every host constrained", same_host: "Worker host reviews", not_installed: "No host installed" };
  function limitText(item) {
    const parts = (item.limits || []).filter((limit) => !limit.expired).map((limit) => limit.used_percent + " percent of the "
      + (limit.window_minutes ? number(limit.window_minutes) + " minute " : "") + "window " + limit.limit_id + (limit.resets_at ? ", which resets at " + P.date(limit.resets_at) : ""));
    const hit = item.limit_hit;
    if (hit && hit.active) parts.push("A limit was hit (" + P.lower(P.words(hit.reason)) + ")" + (hit.until ? " and resets at " + P.date(hit.until) : ""));
    return parts.length ? parts.join("; ") + "." : (item.measured || {}).limit_state ? "Every reported limit window has reset." : "Not reported.";
  }
  function costText(cost, measured) {
    const amounts = Object.entries(cost || {}).map(([currency, amount]) => amount.toFixed(2) + " " + currency);
    return amounts.length ? amounts.join(", ") : measured ? "0.00 USD" : "Unavailable";
  }
  // Section 17.1: fresh work (fresh input, cache writes and output) and cache reads are shown apart and never added up.
  function windowText(value) {
    if (!value) return "Unavailable";
    const parts = [number(value.fresh_work_tokens) + " tokens of fresh work", number(value.cache_read_tokens) + " tokens of cache reads"];
    if (value.unseparated_tokens) parts.push(number(value.unseparated_tokens) + " tokens not separated by kind");
    return parts.join(", ");
  }
  function sessionText(found) {
    const top = ((found || {}).shown || [])[0];
    return top ? `${found.total} sessions in the last 7 days. The largest context was ${number(top.largest_context_tokens)} tokens over ${top.turns} turns, `
      + (top.cache_read_share == null ? "with no input recorded." : `and ${Math.round(top.cache_read_share * 100)} percent of its input was read from the cache.`) : "No session is recorded.";
  }
  const probeText = (probe) => (probe ? (probe.passed ? "Passed" : "Failed") + " for version " + (probe.version || "unknown") + " on " + P.date(probe.probed_at)
    + ". Roles: " + probe.roles.join(" and ") + "." : "No probe is recorded on this machine.");
  function usageCard(item, data) {
    const room = item.headroom || {}, measured = item.measured || {}, windows = item.windows || {};
    return h("article", { class: "card", dataset: { key: "usage-host-" + item.host, constrained: String(Boolean(room.constrained)) } },
      h("h3", null, h("span", null, hostName(item.host)), P.badge(room.constrained ? "review" : "ready", room.constrained ? "Constrained" : "Not constrained")),
      (data.configured_hosts || []).includes(item.host) ? h("div", { class: "row" }, P.chip("Configured for this project")) : null,
      kv([...WINDOWS.map(([name, label]) => [label, measured.tokens ? windowText(windows[name]) : "Unavailable"]), ["Sessions", sessionText(item.sessions)],
        ["Cost in the last 7 days", costText((windows.last_7_days || {}).cost, measured.cost)], ["Limit state", limitText(item)],
        ["Fresh work in the last 5 hours", room.relative_load == null ? "Not measured" : room.relative_load + " times the median of the last 7 days"],
        ["Probe", probeText((data.probes || {})[item.host])]]),
      h("div", { class: "row" }, Object.entries(measured).map(([name, yes]) => P.badge(yes ? "ready" : "backlog", P.words(name) + (yes ? " measured" : " unavailable")))),
      (item.unavailable || []).length ? h("ul", { class: "kn-plain muted" }, item.unavailable.map((text) => h("li", null, text))) : null);
  }
  P.registerView("usage", { title: "Usage", async render(container, params, ctx) {
      let data;
      try {
        data = await P.get("usage");
      } catch (error) {
        put(container, error && error.notIncluded ? P.empty("The usage ledger stays on the computer that holds it, so a snapshot carries no usage.") : failed(error));
        return;
      }
      const items = data.hosts || [], routing = data.routing || [], constrained = items.filter((item) => (item.headroom || {}).constrained).length;
      ctx.setSummary(data.ledger ? "Usage was measured at " + P.date(data.measured_at) + ". " + P.count(constrained, "host") + " " + isAre(constrained) + " constrained."
        : "No usage is recorded on this machine yet.");
      container.classList.add("list-view");
      put(container, region(h("div", { class: "notice", dataset: { key: "usage-note" } }, h("p", null, data.note),
        data.error ? h("p", null, "The usage ledger could not be read: " + data.error) : null,
        h("p", { class: "muted" }, "Run project-memory usage to collect the latest usage. Run project-memory host probe with a host name in your own terminal to check which roles it may take. The probe runs the host and spends tokens.")),
      section("Hosts", counted(items.length, "host"), grid(items, (item) => usageCard(item, data), "No host is known.")),
      section("Routing decisions of recent runs", counted(routing.length, "decision"), routing.length
        ? table(["Run", "Chosen host", "Preferred host", "Reason", "Decided"], routing.map((item) => h("tr", { dataset: { key: "routing-" + item.id } },
          cell("Run", button(P.words(item.role), "routing-run-" + item.id, openRun(item.id), "quiet kn-link")), cell("Chosen host", hostName(item.host)),
          cell("Preferred host", hostName(item.preferred)), cell("Reason", P.chip(ROUTES[item.reason] || P.words(item.reason)), " ", h("span", null, item.sentence)),
          dateCell("Decided", item.decided_at || item.created_at))))
        : P.empty("No run of this project records a routing decision yet."))));
  } });

  // Sessions: tabs for the flagged directions that no record followed, the distilled proposals and the digests of
  // finished sessions. A flag and a proposal are decided in the shared "decide" pane of views_work.js, and a digest opens
  // its record. The shown flags are dismissed together with session_flag {flag_ids, status}.
  const SESSION_TABS = [["flags", "Flags", "review"], ["proposals", "Proposals", "review"], ["digests", "Digests", null]];
  P.registerView("sessions", { title: "Sessions", async render(container, params, ctx) {
      let data;
      try {
        data = await P.get("sessions");
      } catch (error) {
        put(container, P.errorState(error));
        return;
      }
      const counts = data.counts || {}, digests = data.digests || [], flags = data.flags || [], proposals = data.proposals || [];
      const decided = (counts.confirmed || 0) + (counts.dismissed || 0), open = Math.max(counts.open || 0, flags.length);
      ctx.setSummary(data.reading ? P.count(digests.length, "session digest") + " " + isAre(digests.length) + " shown, with "
        + P.count(open, "open flag") + " and " + P.count(proposals.length, "pending proposal") + "."
        : "Session reading is switched off for this project. Run project-memory sessions on to switch it on.");
      const items = { flags, proposals, digests };
      const tabs = SESSION_TABS.map(([id, label, tone]) => ({ id, label, count: id === "flags" ? open : items[id].length, tone: items[id].length ? tone : null }));
      const tab = tabs.find((entry) => entry.id === params.tab) || tabs[0];
      container.classList.add("list-view");
      put(container, P.viewTabs("The parts of Sessions", "sessions-tab-", tabs, tab, (id) => P.go("sessions", { tab: id }), ctx));
      const decide = (kind, id) => (trigger) => P.openPane("decide", { kind, id }, trigger);
      const rows = tab.id === "flags" ? flags.map((flag) => P.paneRow("session-row-" + flag.id, "sessions", "“" + flag.excerpt + "”",
        P.words(flag.category) + ", " + flag.session_key + ", line " + flag.line + ", " + P.date(flag.at), decide("session_flags", flag.id)))
        : tab.id === "proposals" ? proposals.map((item) => P.paneRow("session-row-" + item.id, "sessions", item.text,
          P.words(item.slot) + ", " + P.lower(P.words(item.confidence)) + " confidence, " + item.pointers.file, decide("session_proposals", item.id)))
          : digests.map((item) => P.paneRow("session-digest-" + item.session_key, "records", item.session_key, "Last activity " + P.date(item.last_at) + ". " + P.count(item.messages, "message") + ", "
            + P.count(item.files, "file") + ", " + P.count(item.failures, "failed command") + ", " + P.count(item.open_flags, "open flag") + ".", (trigger) => P.openRecord(item.source_id, trigger)));
      const notes = { flags: data.note, proposals: "A proposal from an earlier session becomes a record only when you accept it. Run project-memory sessions distill to ask a host for proposals.",
        digests: "A row opens the digest record of the session. Run project-memory handoff before a fresh session." };
      const empties = { flags: "No flag waits for a decision.", proposals: "No proposal is pending.", digests: "No session of this project is collected yet." };
      put(container, P.listPane({ title: tab.label, count: tab.count, tone: tab.tone, note: notes[tab.id], rows, empty: empties[tab.id],
        foot: tab.id !== "flags" ? null : [h("span", { dataset: { key: "sessions-note" } }, (open > flags.length ? "The newest " + flags.length + " of " + open + " open flags are shown. " : "")
          + (decided ? "Of " + decided + " decided flags, " + counts.confirmed + " were confirmed." : "No flag is decided yet.")),
        ctx.canEdit && flags.length > 1 ? P.formButton("Dismiss the " + flags.length + " shown flags", "session_flag", { flag_ids: flags.map((flag) => flag.id), status: "dismissed" }, { class: "small" }) : null] }));
  } });
})();
