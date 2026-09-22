/*
 * Project Memory control panel: views_work.js registers the Now, Plan, Work and Decisions views and the "work" and
 * "decision" panes. Buttons open the forms of forms.js: plan {episode_id} or {parent_id, item_type}, comment,
 * allow_paths {episode_id, paths}, delegate, review {episode_id, role} and answer_kickoff {question_ids}. The panes
 * draw the lineage with Panel.lineageGraph of graphs.js, and runs open the "run" pane of views_knowledge.js. A work item
 * with a focused problem shows its check, its attempts side by side and its report, with the focus_check and
 * focus_start forms {episode_id}. Now opens the "decide" pane {kind, id} for a lesson, a flag or a proposal, and the reconcile {receipt_id, resolution}
 * and reconcile_read_only forms; a blocked work item lists its unconfirmed calls with the same forms.
 */
(() => {
  "use strict";
  const P = Panel, { h, chip, button, put, kv, lower } = P;
  const STATES = ["backlog", "ready", "in_progress", "blocked", "review", "done", "cancelled"];
  const TYPES = ["phase", "epic", "story", "task", "research", "deliverable", "workflow"];
  const CHILD_TYPE = { phase: "epic", epic: "story", story: "task" }, PRIORITY = { high: 0, normal: 1, low: 2 }, REVIEW_STATUS = ["needs_review", "review_due"];
  const remembered = { open: new Map(), history: new Map(), timers: {} };

  // Shared helpers.
  const noun = (n) => lower(P.term(n === 1 ? "work_item" : "work_items"));
  const verb = (n, one, many) => (n === 1 ? one : many);
  const typeLabel = (type) => (type === "deliverable" ? P.term("deliverable") : P.words(type || "task"));
  const toneBadge = (tone, label, state) => h("span", { class: "badge", dataset: { tone, state: state || tone } }, label);
  const heading = (title, count) => h("h3", null, h("span", null, title), typeof count === "number" ? chip(String(count)) : null);
  const listOf = (items, render, message) => (items && items.length
    ? h("ul", { class: "list" }, items.map((item, index) => h("li", null, render(item, index)))) : P.empty(message));
  const section = (title, ...children) => h("section", { class: "stack" }, h("h3", null, title), ...children);
  const textList = (items) => h("ul", null, items.map((item) => h("li", null, typeof item === "string" ? item : JSON.stringify(item))));

  const openDecision = (id, trigger) => P.openPane("decision", { id }, trigger);
  const openRun = (run, trigger) => P.openPane("run", { id: run.id }, trigger);
  // A row that opens something: every child is shown inside one button with a stable key.
  const openItem = (key, handler, ...children) => button(children, key, handler, "item work-item");
  const openable = (id) => /^(episode|event|source|host|direction|check)_/.test(String(id));
  function openNode(node, trigger) {
    const id = String(node.id || "");
    if (node.kind === "decision") return openDecision(id, trigger);
    if (id.startsWith("episode_")) return P.openWork(id, trigger);
    if (id.startsWith("check_")) return openRun({ id, episode_id: node.episode_id }, trigger);
    return P.openRecord(id, trigger);
  }
  const workContext = (card, extra) => ({ episode_id: card.id, expected_version: card.version, card, plan: card.plan, title: card.title, ...extra });

  // An unfinished prerequisite is the ordinary state of a planned item, so it is named rather than marked as a fault.
  function issueInfo(raw) {
    const list = Array.isArray(raw) ? raw : [], total = list.length || Number(raw) || 0;
    if (!total) return null;
    if (list.length && list.every((issue) => issue.type === "dependency")) return { tone: "backlog", text: total === 1 ? "Waits for 1 prerequisite" : "Waits for " + total + " prerequisites" };
    return { tone: list.length ? "blocked" : "review", text: P.count(total, "open issue") };
  }
  function workButton(item, prefix) {
    const plan = item.plan || {}, issue = issueInfo(item.issues), priority = item.priority || plan.priority;
    const next = item.next_action !== undefined ? item.next_action : plan.next_action;
    return openItem(prefix + item.id, (t) => P.openWork(item.id, t),
      h("span", { class: "row" }, P.badge(item.state), chip(typeLabel(item.item_type || plan.item_type || "task")), priority && priority !== "normal" ? chip(P.words(priority) + " priority") : null,
        issue ? chip(issue.text, { dataset: { tone: issue.tone } }) : null),
      h("strong", null, item.title), next ? h("span", { class: "muted" }, next) : null);
  }
  const OUTCOMES = { good: ["ready", "Good outcome"], bad: ["blocked", "Bad outcome"], mixed: ["review", "Mixed outcome"],
    unknown: ["neutral", "Outcome unknown"], pending: ["backlog", "Outcome pending"] };
  const outcomeBadge = (assessment) => (assessment ? toneBadge(...(OUTCOMES[assessment] || ["neutral", "Outcome " + P.words(assessment).toLowerCase()]), "outcome-" + assessment)
    : toneBadge("neutral", "No outcome recorded", "no-outcome"));
  function runButton(run, prefix) {
    const facts = [run.changed_files ? "It changed " + P.count(run.changed_files, "file") + "." : "", run.review ? "Its review state is " + P.words(run.review.state).toLowerCase() + "." : "",
      run.merge ? "The work is " + run.merge.state + "." : "", run.error || ""].filter(Boolean);
    return openItem(prefix + run.id, (t) => openRun(run, t), h("span", { class: "row" }, P.badge(run.state), chip(P.words(run.role)), chip(P.words(run.host))),
      h("strong", null, run.summary || P.words(run.role) + " run"), facts.length ? h("span", { class: "muted" }, facts.join(" ")) : null,
      h("span", { class: "muted" }, P.date(run.updated_at || run.created_at)));
  }
  // Keeps focus on the same control after a local re-render of part of a view.
  function redraw(region, draw) {
    const active = document.activeElement;
    const key = active && region.contains(active) ? active.id || active.dataset.key : null;
    region.replaceChildren();
    put(region, draw());
    const target = key && (region.querySelector("#" + CSS.escape(key)) || region.querySelector('[data-key="' + CSS.escape(key) + '"]'));
    if (target) target.focus({ preventScroll: true });
  }
  // A low risk decision is recorded by its button at once, and a refusal leaves the button in place.
  P.actButton = (label, key, operation, data, done, className) => (P.canEdit() ? button(label, key, async (trigger) => {
    trigger.disabled = true;
    try { P.toast(done(await P.action(operation, data, P.requestKey(operation)))); } catch (error) { trigger.disabled = false; P.toast(error.message, "blocked"); }
  }, className) : null);
  const selectControl = (id, label, options, value, onChange) => P.filterField("select", id, label, value, onChange, options);
  const checkControl = (id, label, checked, onChange) => P.filterField("check", id, label, checked, onChange);
  // The search boxes of this file filter while the reader types, after a short pause.
  const searchControl = (id, label, value, onInput) => P.field(label, P.input(id, value, { id, type: "search", autocomplete: "off", on: { input: (event) => {
    const text = event.currentTarget.value;
    clearTimeout(remembered.timers[id]);
    remembered.timers[id] = setTimeout(() => onInput(text.trim()), 200);
  } } }));

  // graphs.js draws the lineage and keeps its own toggle between the graph and a readable vertical list.
  const lineageBlock = (host, focus, data) => P.lineageGraph(host, focus, { data, compact: true, title: "Lineage", open: openNode });

  // Now.
  function nowSentence(now) {
    const c = now.counts || {};
    if (!now.total) return "No " + noun(2) + " are recorded yet.";
    const progress = c.in_progress || 0, blocked = c.blocked || 0, review = c.review || 0;
    return `${progress} ${noun(progress)} ${verb(progress, "is", "are")} in progress, ${blocked} ${verb(blocked, "is", "are")} blocked and ${review} ${verb(review, "needs", "need")} review.`;
  }
  const DOCUMENTS = { unchanged: ["review", "Not yet filled"], missing: ["blocked", "Missing"], changed: ["ready", "Filled"], unreadable: ["blocked", "Unreadable"] };
  function kickoffStep(step, kickoff) {
    const primary = (label, handler) => button(label, "kickoff-next", handler, "primary");
    if (step.action === "answer_kickoff" && P.canEdit()) return primary("Answer the open questions", (t) => P.openForm("answer_kickoff", { question_ids: step.question_ids, questions: kickoff.questions }, t));
    if (step.action === "fill_documents") return primary("Open the documents", () => P.go("records", { view: "documents" }));
    if (step.action === "approve_requirements") return primary("Review the requirements", () => P.go("requirements"));
    if (step.action === "research" && (step.episode_ids || []).length) return primary("Open the first research item", (t) => P.openWork(step.episode_ids[0], t));
    if (step.action === "continue_phase" && step.episode_id) return primary("Open the phase", (t) => P.openWork(step.episode_id, t));
    return null;
  }
  const BASELINE = { not_established: "The requirements baseline is not established.", current: "The requirements baseline is approved.",
    needs_review: "The requirements baseline needs review, because its approval evidence changed." };
  async function kickoffCard(now) {
    // Approving the requirements is one kickoff step among several, so the checklist stays until every step is done.
    if (!now.kickoff || !now.kickoff.template) return null;
    const kickoff = await P.get("kickoff").catch((error) => ({ error }));
    const card = h("section", { class: "card kickoff", "aria-labelledby": "kickoff-title" }, h("h3", { id: "kickoff-title" }, "Kickoff checklist"));
    if (kickoff.error) return card.appendChild(P.errorState(kickoff.error)) && card;
    if ((kickoff.next_step || {}).action === "kickoff_complete") return null;
    const questions = kickoff.questions || [], step = kickoff.next_step || {};
    const answered = questions.filter((question) => question.answered).length;
    const open = (kickoff.documents || []).filter((doc) => doc.status !== "changed").length, research = (kickoff.research || []).length;
    const checkItem = (done, label, text, action) => h("div", { class: "check-item" }, toneBadge(done ? "ready" : "review", label), h("div", { class: "stack" }, text, action));
    // The full checklist stays one card high until the reader opens it, so the project state keeps the first screen.
    const details = h("details", { class: "kickoff-detail", open: remembered.open.get("kickoff") === true,
      on: { toggle: (event) => remembered.open.set("kickoff", event.currentTarget.open) } }, h("summary", null, "Show the full checklist"),
    h("div", { class: "kickoff-grid" },
        section("Questions", h("p", { class: "muted" }, `${answered} of ${P.count(questions.length, "kickoff question")} ${verb(answered, "is", "are")} answered.`),
          listOf(questions, (question) => checkItem(question.answered, question.answered ? "Answered" : "Open", h("span", null, question.text),
            question.answered ? (question.answered_by ? button("Read the answer", "kickoff-answer-" + question.id, (t) => P.openRecord(question.answered_by, t), "small") : null)
              : P.canEdit() ? button("Answer", "kickoff-question-" + question.id, (t) => P.openForm("answer_kickoff", { question_ids: [question.id], questions, question }, t), "small") : null),
          "The template has no kickoff questions.")),
        section("Research still needed", listOf(kickoff.research, (item) => workButton({ ...item, id: item.episode_id, item_type: "research" }, "kickoff-research-"),
          "No open research item is recorded.")),
        section("Starter documents", listOf(kickoff.documents, (doc) => {
          const [tone, label] = DOCUMENTS[doc.status] || ["neutral", P.words(doc.status)];
          return h("div", { class: "check-item" }, toneBadge(tone, label, doc.status), h("div", { class: "stack" }, h("span", { class: "mono" }, doc.path),
            doc.status === "changed" ? null : button("Find it in the records", "kickoff-doc-" + doc.path, () => P.go("records", { view: "documents", query: doc.path }), "small")));
        }, "The template has no starter documents.")),
        section("Phases", listOf(kickoff.phases, (phase) => (phase.episode_id ? workButton({ ...phase, id: phase.episode_id, item_type: "phase" }, "kickoff-phase-")
          : checkItem(false, "Missing", h("span", null, phase.title + " is not recorded as a work item."))), "The template has no phases."))));
    put(card, h("p", null, `This project uses the ${kickoff.title || P.words(kickoff.template)} template. ` +
      `${BASELINE[(now.kickoff || {}).baseline] || BASELINE.not_established} ` +
      `${answered} of ${P.count(questions.length, "kickoff question")} ${verb(answered, "is", "are")} answered, ` +
      `${P.count(research, "research item")} ${verb(research, "is", "are")} open and ` +
      `${P.count(open, "starter document")} ${verb(open, "is", "are")} not filled.`),
    h("div", { class: "notice" }, h("p", null, h("strong", null, "Next step. "), step.reason || "No next step is recorded."), kickoffStep(step, kickoff)), details);
    return card;
  }
  // One row per kind: tone, label, what the row opens in words (null names the work item) and the action that opens it.
  const openRecordOf = (entry, trigger) => (entry.id && openable(entry.id) ? P.openRecord(entry.id, trigger) : null);
  const openWorkOf = (entry, trigger) => P.openWork(entry.id, trigger), openCaptures = () => P.go("records", { view: "captures" });
  const openRules = () => P.go("learning", { section: "instructions" }), openSessions = () => P.go("sessions");
  const ATTENTION = {
    scope_block: ["blocked", "Edit blocked", null, (entry, trigger) => (entry.episode_id ? P.openWork(entry.episode_id, trigger) : openRecordOf(entry, trigger))],
    blocked_work: ["blocked", "Blocked", null, openWorkOf], work_to_review: ["review", "Needs review", null, openWorkOf],
    awaiting_merge: ["review", "Awaiting merge", "agent run", (entry, trigger) => openRun(entry, trigger)],
    agent_follow_up: ["review", "Agent follow up", "agent run", openRecordOf], guard_recurrence: ["blocked", "Repeated failure", "lesson", openRecordOf],
    failure_without_lesson: ["blocked", "Failure without a lesson", "outcome", openRecordOf],
    capture_failure: ["blocked", "Recording failed", "captures", openCaptures], recording_gap: ["review", "Recording gap", "captures", openCaptures],
    lessons_to_accept: ["review", "Lessons to accept", "proposed lessons", () => P.go("learning", { section: "proposed" })],
    rules_over_cap: ["review", "Too many rules", "instructions", openRules], rule_ineffective: ["review", "Rule without effect", "instructions", openRules],
    session_flags: ["review", "Session flags", "flags", openSessions], session_proposals: ["review", "Session proposals", "proposals", openSessions],
    machine_rules: ["review", "Machine rules", "proposed rules", () => P.go("machine")] };
  const attentionOf = (type) => ATTENTION[type] || ["neutral", P.words(type), "record", openRecordOf];
  const decisionItem = (decision, prefix) => openItem(prefix + decision.id, (t) => openDecision(decision.id, t),
    h("span", { class: "row" }, outcomeBadge(decision.outcome && decision.outcome.assessment), decision.status !== "recorded" ? P.badge(decision.status) : null),
    h("strong", null, decision.title), h("span", { class: "muted" }, [decision.episode_title, P.date(decision.date)].filter(Boolean).join(". ")));
  function scopeBlock(block) {
    const outside = block.still_outside || [];
    const allowed = (block.allowed_patterns || []).join(", ") || "not recorded";
    return h("div", { class: "stack" }, h("span", { class: "row" }, outside.length ? toneBadge("blocked", "Still outside the allowed paths") : toneBadge("ready", "Now allowed"),
      block.host ? chip(P.words(block.host)) : null, h("span", { class: "muted" }, P.date(block.created_at))),
    h("p", null, `An edit to ${(block.blocked || []).join(", ")} was blocked. The allowed paths were ${allowed}.`),
    h("div", { class: "row" }, block.episode_id ? button("Open the " + noun(1), "now-block-" + block.id, (t) => P.openWork(block.episode_id, t), "small") : null,
      outside.length && P.canEdit() && block.episode_id ? button("Allow paths", "now-allow-" + block.id, (t) => P.openForm("allow_paths", { episode_id: block.episode_id, paths: outside, block }, t), "small") : null));
  }
  const allLink = (href, text) => h("a", { href }, text);

  const BOARD_PAGE = 10;
  function unconfirmedItem(item) {
    const found = item.transcript || {}, suggested = found.suggested;
    const text = !found.found ? "No transcript entry was found for this call. Check its effect yourself."
      : found.result === "no_result" ? "The transcript holds the call but no result." : `The transcript reports the call as ${found.result}. ${found.excerpt || ""}`;
    return h("div", { class: "stack", dataset: { key: "unconfirmed-" + item.id } },
      h("span", { class: "row" }, chip(item.tool_name), item.read_only ? chip("Read only") : null, h("span", { class: "muted" }, P.date(item.created_at))),
      item.work_title ? h("span", null, item.work_title) : null, h("p", { class: "muted" }, text),
      P.canEdit() ? h("div", { class: "row" },
        suggested && found.result !== "no_result" ? P.actButton("Record as " + lower(P.words(suggested)), "reconcile-now-" + item.id, "reconcile",
          { receipt_id: item.id, resolution: suggested, reason: "The user accepts the result that the transcript reports." },
          (result) => "The call is recorded as " + lower(P.words(result.resolution)) + ".", "small primary") : null,
        P.formButton(suggested ? "Choose another result" : "Reconcile", "reconcile", { receipt_id: item.id, resolution: suggested, suggested, tool: item.tool_name }, { class: "small" })) : null);
  }
  async function unconfirmedCard(params) {
    const data = await P.get("unconfirmed", params).catch(() => null);
    const items = (data && data.items) || [];
    if (!items.length) return null;
    const bulk = items.filter((item) => item.read_only && (item.transcript || {}).found && item.transcript.result !== "no_result").length;
    return h("section", { class: "card", dataset: { key: "now-unconfirmed", total: String(data.total || items.length) } }, heading("Needs reconciliation", data.total),
      h("p", { class: "muted" }, "These tool calls started without a recorded result, so their work items stay blocked. The suggestion comes from the transcript of the session."),
      bulk && P.canEdit() ? P.formButton(`Resolve ${bulk} read-only ${bulk === 1 ? "call" : "calls"}`, "reconcile_read_only", { count: bulk }, { class: "small" }) : null,
      listOf(items, unconfirmedItem));
  }
  // Now: one tab for each kind that waits with its count, the rows of the selected kind, and a pane that decides the item or
  // opens it. Lessons, flags and proposals are decided in the pane, and the next item opens after each decision.
  const NOW_ICONS = { blocked_work: "work", work_to_review: "records", scope_block: "work", awaiting_merge: "agents", agent_follow_up: "agents",
    lessons_to_accept: "learning", guard_recurrence: "learning", rules_over_cap: "learning", rule_ineffective: "learning", failure_without_lesson: "decisions",
    session_flags: "sessions", session_proposals: "sessions", machine_rules: "machine", capture_failure: "requirements", recording_gap: "requirements" };
  const DECIDED = { lessons_to_accept: ["learning", (data) => (data.proposed_lessons || {}).lessons, (item) => [item.do || item.title || "Proposed lesson", "From: " + (item.episode_title || item.episode_id)],
      "An accepted lesson guides every later agent run that matches it."],
    session_flags: ["sessions", (data) => data.flags, (item) => ["\u201C" + item.excerpt + "\u201D", P.words(item.category) + ", from " + item.session_key],
      "A message of yours that may hold a direction no record followed. Each flag is a low confidence hint."],
    session_proposals: ["sessions", (data) => data.proposals, (item) => [item.text, P.words(item.slot) + ", " + lower(P.words(item.confidence)) + " confidence"],
      "A proposal from an earlier session becomes a record only when you accept it."] };
  const NOW_EXTRA = { unconfirmed: "Needs reconciliation", kickoff: "Kickoff", decisions: "Latest decisions", scope_blocks: "Scope blocks" };
  P.registerView("now", { title: "Now", async render(container, params, ctx) {
      const now = await P.get("now"), waiting = now.attention_count || 0, kinds = now.attention_kinds || [];
      ctx.setSummary(waiting ? `${waiting} ${verb(waiting, "item waits", "items wait")} for you.` : "Nothing waits for you.");
      const [unconfirmed, kickoff] = await Promise.all([unconfirmedCard({}), kickoffCard(now)]);
      const extra = { unconfirmed, kickoff, decisions: () => [listOf(now.latest_decisions, (decision) => decisionItem(decision, "now-decision-"), "No decision is recorded."), allLink("#decisions", "Open all decisions")],
        scope_blocks: () => listOf(now.scope_blocks, scopeBlock, "No edit was blocked for being outside the allowed paths.") };
      const tabs = [...kinds.map((kind) => ({ id: kind.type, label: attentionOf(kind.type)[1], count: kind.count, tone: attentionOf(kind.type)[0], kind })),
        ...Object.keys(NOW_EXTRA).filter((id) => extra[id]).map((id) => ({ id, label: NOW_EXTRA[id], count: id === "unconfirmed" ? Number(unconfirmed.dataset.total) : id === "scope_blocks" ? (now.scope_blocks || []).length : null }))];
      const tab = tabs.find((entry) => entry.id === params.kind) || tabs[0], offset = Number(params.offset) || 0;
      container.classList.add("list-view");
      put(container, h("div", { class: "tabs now-tabs", role: "group", "aria-label": "What waits for you, by kind" }, tabs.map((entry) => button([entry.label,
        entry.count === null ? null : chip(String(entry.count), entry.tone ? { dataset: { tone: entry.tone } } : null)], "now-kind-" + entry.id, () => P.go("now", { kind: entry.id }), null,
      { "aria-pressed": String(entry === tab) }))));
      // The row of tabs scrolls sideways, so the selected tab is brought into sight.
      ctx.onShown(() => container.querySelector('.now-tabs [aria-pressed="true"]').scrollIntoView({ block: "nearest", inline: "nearest" }));
      if (!tab.kind) { put(container, h("div", { class: "list-pane" }, paneBody("rows", typeof extra[tab.id] === "function" ? extra[tab.id]() : extra[tab.id]))); return; }
      // The rows of a decided kind come from the view that owns them, and every other kind pages through the response of Now.
      const type = tab.id, [tone, label, target, open] = attentionOf(type);
      let rows, total = tab.kind.rows, source = DECIDED[type];
      if (source) {
        // A snapshot holds no session messages, so its flags and proposals stay one row that names their number.
        const items = await P.get(source[0]).then(source[1], () => null);
        if (!items) source = null;
        if (items) { total = tab.count; rows = items.map((item) => { const [title, sub] = source[2](item); return P.paneRow("now-row-" + item.id, NOW_ICONS[type], title, sub, (t) => P.openPane("decide", { kind: type, id: item.id, from: item.episode_title }, t)); }); }
      }
      if (!rows) {
        const page = offset && P.live ? (await P.get("now", { attention_type: type, attention_offset: String(offset) })).attention : tab.kind.entries || [];
        rows = page.map((entry, index) => P.paneRow("now-attention-" + type + "-" + (entry.id || index), NOW_ICONS[type] || "records", entry.title || label, entry.detail || entry.reason, (t) => open(entry, t)));
      }
      const from = source ? 0 : offset, turn = (to) => () => P.go("now", { kind: type, offset: to ? String(to) : "" });
      put(container, P.listPane({ title: label, count: tab.count, tone, note: source ? source[3] : target ? "A row opens the " + target + "." : "A row opens the " + noun(1) + " with why it waits and its next step.",
        rows, empty: "Nothing of this kind waits for you.", foot: [h("span", null, rows.length ? `Showing ${from + 1} to ${from + rows.length} of ${Math.max(total, from + rows.length)}.` : ""),
          !source && P.live ? h("span", { class: "row" }, offset ? button("Previous 20", "now-previous", turn(Math.max(0, offset - 20)), "small") : null,
            offset + rows.length < total ? button("Next 20", "now-next", turn(offset + 20), "small") : null) : null] }));
  } });

  // The decision pane of a lesson, a session flag or a session proposal. A lesson needs a reason; a flag does not.
  const DECIDE = { lessons_to_accept: ["Decide a lesson", "Accept", "Reject", "accepted", "rejected"], session_flags: ["Decide a flag", "Confirm", "Dismiss", "confirmed", "dismissed"],
    session_proposals: ["Decide a proposal", "Accept", "Reject", "accepted", "rejected"] };
  // After a decision the next row of the list opens, so a run of decisions needs no return to the list.
  function advance() {
    const rows = [...document.querySelectorAll("#main .pane-row")], index = rows.findIndex((row) => "selected" in row.dataset), next = rows[index + 1] || rows[index - 1];
    if (next) next.click(); else P.closePane();
  }
  P.registerPane("decide", { async render(body, params, ctx) {
      const [kindName, yes, no, yesStatus, noStatus] = DECIDE[params.kind], lessons = params.kind === "lessons_to_accept", flags = params.kind === "session_flags";
      ctx.setKind(kindName);
      const data = lessons ? await P.get("record", { id: params.id }) : await P.get("sessions");
      const item = lessons ? data.record : ((flags ? data.flags : data.proposals) || []).find((entry) => entry.id === params.id);
      if (!item || (lessons && (item.detail.lesson_status || item.status) !== "proposed")) { ctx.setTitle("This item is decided"); put(body, h("p", { class: "muted" }, "Select another row of the list.")); return; }
      const lesson = lessons ? item.detail.payload || {} : null;
      ctx.setTitle(lessons ? lesson.do || item.title : flags ? "\u201C" + item.excerpt + "\u201D" : item.text);
      if (lessons) put(body, h("div", { class: "row" }, toneBadge("guarded", "Proposed by " + (item.detail.actor || "an agent")), chip(P.words(lesson.pattern_type || "lesson")), h("span", { class: "muted" }, P.date(item.date))),
        P.kv([["When", lesson.when], ["Because", lesson.because], ["Exceptions", lesson.exceptions],
          ["Learned in", button(item.episode_title || params.from || item.episode_id, "decide-work", (t) => P.openWork(item.episode_id, t), "small")]]),
        h("p", { class: "muted" }, "An accepted lesson guides every later agent run that matches it. It keeps the triggers it was proposed with; open the full lesson to change them."),
        button("Open the full lesson", "decide-open", (t) => P.openRecord(item.id, t), "small quiet"));
      else if (flags) put(body, h("div", { class: "row" }, chip(P.words(item.category)), toneBadge("backlog", "Low confidence"), h("span", { class: "muted" }, item.session_key + ", line " + item.line + ", " + P.date(item.at))),
        h("p", null, "No decision, requirement or correction was recorded within the hour after this message. Confirm the flag when a record is missing. Dismiss it when nothing is missing."),
        button("Open Sessions", "decide-open", () => P.go("sessions"), "small quiet"));
      else put(body, h("div", { class: "row" }, chip(P.words(item.slot)), chip(P.words(item.confidence) + " confidence"), h("span", { class: "muted" }, item.pointers.file + ", lines " + item.pointers.lines.join(", "))),
        button("Open Sessions", "decide-open", () => P.go("sessions"), "small quiet"));
      if (!ctx.canEdit) return;
      if (!lessons && !flags) { put(ctx.foot, h("div", { class: "actions" }, [[yes, yesStatus, "primary"], [no, noStatus, ""]].map(([text, status, tone]) =>
        button(text, "decide-" + status, (t) => P.openForm("session_proposal", { proposal_id: item.id, status }, t).then((result) => result && advance()), tone)))); return; }
      const reason = P.textarea("reason", "", { id: "decide-reason", rows: "1", "aria-label": "Reason", placeholder: lessons ? "Write the reason for your decision" : "Add a reason (optional)" });
      const decide = (status) => async (trigger) => {
        const text = reason.value.trim();
        if (lessons && !text) { P.toast("Write the reason for your decision first.", "blocked"); reason.focus(); return; }
        trigger.disabled = true;
        try {
          const triggers = lessons && status === "accepted" && ((lesson.paths || []).length || (lesson.keywords || []).length || lesson.failure_type || (lesson.roles || []).length)
            ? { paths: lesson.paths || [], keywords: lesson.keywords || [], ...(lesson.failure_type ? { failure_type: lesson.failure_type } : {}), ...((lesson.roles || []).length ? { roles: lesson.roles } : {}) } : {};
          await P.action(lessons ? "lesson_review" : "session_flag", lessons ? { lesson_id: item.id, expected_version: (await P.get("record", { id: item.episode_id })).record.detail.version, status, reason: text, ...triggers }
            : { flag_id: item.id, status, ...(text ? { reason: text } : {}) }, P.requestKey("decide"));
          P.toast(`The ${lessons ? "lesson" : "flag"} is ${status}.`);
          advance();
        } catch (error) { trigger.disabled = false; P.toast(error.message, "blocked"); }
      };
      put(ctx.foot, reason, h("div", { class: "actions" }, button(yes, "decide-" + yesStatus, decide(yesStatus), "primary", { "data-hotkey": yes[0].toLowerCase() }),
        button(no, "decide-" + noStatus, decide(noStatus), null, { "data-hotkey": no[0].toLowerCase() })),
      h("p", { class: "hint" }, `The next item opens after each decision. Keys: ${yes[0]} ${yes.toLowerCase()}, ${no[0]} ${no.toLowerCase()}, J and K move.`),
      flags && data.flags.length > 1 ? P.formButton("Dismiss the " + data.flags.length + " shown flags", "session_flag", { flag_ids: data.flags.map((flag) => flag.id), status: "dismissed" }, { class: "small quiet" }) : null);
  } });
  // The decision letters never fire while the reader types in a field.
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.target.closest("input, textarea, select, [contenteditable], dialog")) return;
    const target = document.querySelector('#detail .detail-foot [data-hotkey="' + CSS.escape(event.key.toLowerCase()) + '"]');
    if (target && !target.disabled) { event.preventDefault(); target.click(); }
  });

  // Plan.
  const matches = (node, filter) => (!filter.type || node.item_type === filter.type) && (!filter.state || node.state === filter.state)
    && (!filter.query || String(node.title).toLowerCase().includes(filter.query.toLowerCase()));
  const progressText = (node) => {
    const active = node.descendants - (node.rollup.cancelled || 0);
    return active ? `${node.rollup.done} of ${active} done` : "No items below";
  };
  function planNode(node, cards, filter, depth) {
    const children = (node.children || []).map((child) => planNode(child, cards, filter, depth + 1)).filter(Boolean);
    const filtered = Boolean(filter.type || filter.state || filter.query);
    if (!matches(node, filter) && !children.length) return null;
    const open = filtered || (remembered.open.has(node.id) ? remembered.open.get(node.id) : depth < 2);
    const card = cards.get(node.id);
    const acceptance = card && card.plan ? card.plan.acceptance || [] : [];
    const toggle = children.length ? button(open ? "▾" : "▸", "plan-toggle-" + node.id, (trigger) => {
      remembered.open.set(node.id, !open);
      trigger.dispatchEvent(new CustomEvent("plan-redraw", { bubbles: true }));
    }, "quiet small", { "aria-expanded": String(open), "aria-label": (open ? "Hide" : "Show") + " the items below " + node.title, disabled: filtered }) : h("span");
    return h("li", null, h("div", { class: "tree-row" }, toggle,
      openItem("plan-open-" + node.id, (t) => P.openWork(node.id, t),
        h("span", { class: "row" }, chip(typeLabel(node.item_type)), P.badge(node.state), node.owner === "human" ? chip("Owner: person") : null), h("strong", null, node.title)),
      h("div", { class: "tree-meta" }, node.descendants ? [P.progress(node.progress, "Progress of " + node.title), h("span", { class: "muted" }, progressText(node))] : null,
        P.canEdit() ? button("Add item", "plan-add-" + node.id, (t) => P.openForm("plan", { parent_id: node.id, item_type: CHILD_TYPE[node.item_type] || "task", parent: node }, t),
          "small tree-add", { "aria-label": "Add an item below " + node.title }) : null)),
    node.acceptance_total ? h("details", { class: "acceptance" }, h("summary", null, `Acceptance criteria (${node.acceptance_total})`),
      acceptance.length ? h("ol", null, acceptance.map((line) => h("li", null, line))) : h("p", { class: "muted" }, "Open the item to read its acceptance criteria.")) : null,
    children.length ? h("ul", { hidden: !open }, children) : null);
  }
  // Plan and Work fill the frame: the filters sit in a box at the top, which stays folded on a narrow screen until the
  // reader opens it, and the tree, the board or the rows scroll under it. The foot stays in place under them.
  const filterBox = (id, filtered, ...fields) => h("details", { class: "kn-filter-box", id, open: remembered.open.has(id) ? remembered.open.get(id) : filtered || window.innerWidth >= 640 },
    h("summary", { on: { click: (event) => remembered.open.set(id, !event.currentTarget.parentNode.open) } }, filtered ? "Filters are applied" : "Filters"), h("div", { class: "toolbar" }, fields));
  const paneBody = (name, ...children) => h("div", { class: "pane-rows pane-body", dataset: { scroll: name } }, children);
  const paneFoot = (...children) => h("div", { class: "pane-foot" }, children);
  P.registerView("plan", { title: "Plan", async render(container, params, ctx) {
      const [plan, board] = await Promise.all([P.get("plan"), P.get("board", { limit: "100" }).catch(() => null)]);
      const cards = new Map(((board && board.cards) || []).map((card) => [card.id, card]));
      const roots = plan.roots || [], counts = plan.counts || {}, phases = roots.filter((node) => node.item_type === "phase");
      const active = (plan.total || 0) - (counts.cancelled || 0), done = counts.done || 0;
      ctx.setSummary(`${plan.total || 0} ${noun(plan.total || 0)} ${verb(plan.total || 0, "is", "are")} planned` +
        (phases.length ? ` in ${P.count(phases.length, "phase")}.` : ".") + ` ${done} ${verb(done, "is", "are")} done.`);
      const filter = { type: params.type || "", state: params.state || "", query: params.query || "" };
      const tree = h("div", { class: "stack" }), head = h("div", { class: "list-head" });
      const drawTree = () => {
        const nodes = roots.map((node) => planNode(node, cards, filter, 0)).filter(Boolean);
        return nodes.length ? h("ul", { class: "tree" }, nodes) : P.empty(plan.total ? `No ${noun(2)} match these filters.` : `No ${noun(2)} are planned yet.`);
      };
      tree.addEventListener("plan-redraw", () => redraw(tree, drawTree));
      // The search box keeps its focus while the reader types, so a typed query redraws the tree alone.
      const update = (name, value) => { filter[name] = value; P.setParams({ ...filter }); if (name !== "query") redraw(head, drawHead); redraw(tree, drawTree); };
      const typesPresent = TYPES.filter((type) => JSON.stringify(roots).includes('"item_type":"' + type + '"'));
      const drawHead = () => filterBox("plan-filters", Boolean(filter.type || filter.state || filter.query),
        selectControl("plan-type", "Item type", [["", "All types"], ...typesPresent.map((type) => [type, typeLabel(type)])], filter.type, (value) => update("type", value)),
        selectControl("plan-state", "State", [["", "All states"], ...STATES.map((state) => [state, P.words(state)])], filter.state, (value) => update("state", value)),
        searchControl("plan-query", "Search titles", filter.query, (value) => update("query", value)));
      container.classList.add("list-view");
      put(container, head, h("section", { class: "list-pane" }, paneBody("rows",
        phases.length ? h("section", { class: "stack", "aria-label": "Timeline by phase" }, h("h3", null, "Timeline by phase"),
          h("ol", { class: "timeline" }, phases.map((phase, index) => h("li", { dataset: { tone: phase.state } },
            button([h("span", { class: "muted" }, "Phase " + (index + 1)), h("strong", null, phase.title), h("span", { class: "row" }, P.badge(phase.state)),
              P.progress(phase.progress, "Progress of " + phase.title), h("span", { class: "muted" }, progressText(phase))], "plan-phase-" + phase.id,
            (t) => P.openWork(phase.id, t), "item"))))) : null,
        tree, plan.note ? h("p", { class: "muted" }, plan.note) : null, plan.truncated ? h("p", { class: "notice" }, "The plan is limited, so some work items are not shown.") : null),
      paneFoot(h("span", { class: "row" }, P.progress(active ? done / active : null, "Share of work items done"), h("span", null, `${done} of ${P.count(active, noun(1), noun(2))} done.`)),
        P.canEdit() ? button("Add item", "plan-add-root", (t) => P.openForm("plan", { parent_id: null, item_type: phases.length ? "epic" : "phase" }, t), "primary small") : null)));
      redraw(head, drawHead);
      redraw(tree, drawTree);
  } });

  // Work.
  const SORTS = { title: (c) => String(c.title).toLowerCase(), state: (c) => STATES.indexOf(c.state), type: (c) => (c.plan || {}).item_type || "task",
    subject: (c) => c.subject, priority: (c) => PRIORITY[(c.plan || {}).priority || "normal"], issues: (c) => (c.issues || []).length, date: (c) => c.date };
  const COLUMNS = [["title", "Title"], ["state", "State"], ["type", "Type"], ["subject", "Subject"], ["priority", "Priority"], ["issues", "Issues"], ["date", "Created"]];
  function filterCards(cards, st) {
    const query = st.query.toLowerCase();
    return cards.filter((c) => (!st.subject || c.subject === st.subject) && (!st.state || c.state === st.state)
      && (!st.type || ((c.plan || {}).item_type || "task") === st.type)
      && (!st.sprint || ((c.plan || {}).sprint_id || "unassigned") === st.sprint || (st.sprint !== "unassigned" && (c.plan || {}).sprint_id === st.sprint))
      && (!query || (c.title + " " + c.intent + " " + JSON.stringify(c.plan || {})).toLowerCase().includes(query)));
  }
  // A column shows ten cards at a time; the number shown stays while the panel is open.
  const columnShown = (state) => remembered.open.get("column-" + state) || BOARD_PAGE;
  // The board keeps its columns beside an open pane: it is the scrolling region itself and scrolls sideways and down.
  function boardNode(cards, st) {
    const columns = STATES.map((state) => [state, cards.filter((c) => c.state === state)]);
    const shown = columns.filter(([state, items]) => items.length || (st.empty && !st.state) || st.state === state);
    const hidden = columns.length - shown.length;
    return [h("div", { class: "pane-rows pane-body board", dataset: { scroll: "board" } }, shown.map(([state, items]) => h("section", { class: "board-column", dataset: { tone: state }, "aria-label": P.words(state) },
      h("h3", null, P.badge(state), h("span", { class: "muted" }, String(items.length))),
      items.length ? h("ul", { class: "list" }, items.slice(0, columnShown(state)).map((c) => h("li", null, workButton(c, "work-card-")))) : P.empty(`No ${noun(1)} is in this state.`),
      items.length > columnShown(state) ? button(`Show ${Math.min(BOARD_PAGE, items.length - columnShown(state))} more of ${items.length - columnShown(state)}`, "work-more-" + state,
        () => { remembered.open.set("column-" + state, columnShown(state) + BOARD_PAGE); P.refresh(); }, "small quiet") : null))),
    hidden && !st.state ? `${hidden} empty ${verb(hidden, "column is", "columns are")} hidden.` : ""];
  }
  // The list mode is one row per work item, sorted by the chosen column, and a row opens the item beside the list.
  const rowSub = (c) => { const plan = c.plan || {}, issue = issueInfo(c.issues);
    return [[P.words(c.state), lower(typeLabel(plan.item_type)), lower(P.words(c.subject)), plan.priority && plan.priority !== "normal" ? lower(P.words(plan.priority)) + " priority" : null].filter(Boolean).join(", "),
      issue ? issue.text : null, "Created " + P.date(c.date)].filter(Boolean).join(". ") + "."; };
  function listNode(cards, st) {
    const getter = SORTS[st.sort] || SORTS.state;
    const ordered = [...cards].sort((a, b) => { const x = getter(a), y = getter(b); return (x < y ? -1 : x > y ? 1 : 0) * (st.dir === "desc" ? -1 : 1); });
    return [ordered.length ? h("ul", { class: "pane-rows", dataset: { scroll: "rows" } }, ordered.map((c) => h("li", null, P.paneRow("work-row-" + c.id, "work", c.title, rowSub(c), (t) => P.openWork(c.id, t)))))
      : h("div", { class: "pane-rows" }, P.empty(`No ${noun(1)} matches these filters.`)), ""];
  }
  P.registerView("work", { title: "Work", async render(container, params, ctx) {
      const [base, sprints] = await Promise.all([P.get("board", { limit: "100" }), P.get("sprints", { limit: "100" }).then((value) => value.sprints || [], () => [])]);
      const st = { tab: params.tab === "list" ? "list" : "board", subject: params.subject || "", state: params.state || "", sprint: params.sprint || "",
        type: params.type || "", query: params.query || "", empty: params.empty === "1", sort: params.sort || "state", dir: params.dir === "desc" ? "desc" : "asc" };
      ctx.setSummary(nowSentence({ total: base.total, counts: base.counts }));
      const results = h("section", { class: "list-pane" }), controls = h("div", { class: "list-head" });
      let token = 0;
      const save = () => P.setParams({ tab: st.tab === "list" ? "list" : "", subject: st.subject, state: st.state, sprint: st.sprint, type: st.type, query: st.query,
        empty: st.empty ? "1" : "", sort: st.sort === "state" ? "" : st.sort, dir: st.dir === "desc" ? "desc" : "" });
      const draw = async () => {
        const mine = ++token;
        let cards = base.cards || [];
        // Live mode asks the server when the first page does not hold every work item.
        if (P.live && base.more && (st.subject || st.sprint || st.query)) {
          cards = await P.get("board", { limit: "100", subject: st.subject, sprint_id: st.sprint, query: st.query }).then((value) => value.cards || [], () => cards);
        }
        if (mine !== token) return;
        const shown = filterCards(cards, st), [body, note] = st.tab === "board" ? boardNode(shown, st) : listNode(shown, st);
        // The count of the shown items and the Add item action stay in the foot, in reach under the scrolling board or rows.
        redraw(results, () => [body, paneFoot(h("p", { role: "status" }, `The view shows ${shown.length} of ${base.total || 0} ${noun(base.total || 0)}.` +
          (base.more ? ` Only the first ${(base.cards || []).length} are loaded without filters.` : "") + (note ? " " + note : "")),
        P.canEdit() ? button("Add item", "work-add", (t) => P.openForm("plan", { parent_id: null, item_type: "task" }, t), "primary small") : null)]);
      };
      const update = (name, value) => { st[name] = value; save(); drawControls(); draw(); };
      const subjects = [...new Set((base.cards || []).map((c) => c.subject).concat(st.subject ? [st.subject] : []))].sort();
      const typesPresent = TYPES.filter((type) => (base.cards || []).some((c) => ((c.plan || {}).item_type || "task") === type) || st.type === type);
      const drawControls = () => redraw(controls, () => [
        h("div", { class: "tabs", role: "group", "aria-label": "Work display" }, [["board", "Board"], ["list", "List"]].map(([value, label]) =>
          h("button", { type: "button", id: "work-tab-" + value, "aria-pressed": String(st.tab === value), on: { click: () => update("tab", value) } }, label))),
        filterBox("work-filters", Boolean(st.subject || st.state || st.sprint || st.type || st.query),
          selectControl("work-subject", "Subject", [["", "All subjects"], ...subjects.map((subject) => [subject, P.words(subject)])], st.subject, (value) => update("subject", value)),
          selectControl("work-state", "State", [["", "All states"], ...STATES.map((state) => [state, P.words(state)])], st.state, (value) => update("state", value)),
          selectControl("work-sprint", "Sprint", [["", "All sprints"], ["unassigned", "No sprint"], ...sprints.map((sprint) => [sprint.id, sprint.title])], st.sprint, (value) => update("sprint", value)),
          selectControl("work-type", "Item type", [["", "All types"], ...typesPresent.map((type) => [type, typeLabel(type)])], st.type, (value) => update("type", value)),
          searchControl("work-query", "Search", st.query, (value) => { st.query = value; save(); draw(); }),
          st.tab === "board" ? checkControl("work-empty", "Show empty columns", st.empty, (value) => update("empty", value))
            : [selectControl("work-sort", "Sort by", COLUMNS, st.sort, (value) => update("sort", value)),
              checkControl("work-desc", "Descending order", st.dir === "desc", (value) => update("dir", value ? "desc" : "asc"))])]);
      drawControls();
      container.classList.add("list-view");
      put(container, controls, results);
      await draw();
  } });

  // Work item pane.
  const NEXT_BUTTONS = {
    plan: ["Edit plan", "plan"], review_plan: ["Edit plan", "plan"], propose: ["Edit plan", "plan"], review: ["Edit plan", "plan"],
    finalize: ["Mark as done", "plan", { state: "done" }], request_review: ["Request check", "review", { role: "outcome" }],
  };
  function nextAction(work, card) {
    const step = work.next || {};
    const detail = step.next_step || {}, blocker = (detail.blockers || [])[0] || {}, read = detail.read_with || {};
    const known = NEXT_BUTTONS[step.action];
    if (known && P.canEdit() && (known[1] !== "plan" || card.plan || step.action === "plan")) return button(known[0], "work-next", (t) => P.openForm(known[1], workContext(card, known[2]), t), "primary");
    if (blocker.episode_id) return button("Open the prerequisite", "work-next", (t) => P.openWork(blocker.episode_id, t), "primary");
    if (read.view === "record" && read.id) return button("Open the record to review", "work-next", (t) => P.openRecord(read.id, t), "primary");
    if (detail.check_id) return button("Open the check", "work-next", (t) => openRun({ id: detail.check_id, episode_id: card.id }, t), "primary");
    if (read.view === "coverage") return h("p", { class: "muted" }, "Resolve the calls listed below under Needs reconciliation, or open the captures.");
    return null;
  }
  function checkReports(reviews) {
    return (reviews.runs || []).filter((run) => run.report).map((run) => h("details", null, h("summary", null, `${P.words(run.role)} by ${P.words(run.host)}: ${P.words(run.state).toLowerCase()}`),
      h("div", { class: "stack" }, run.report.summary ? h("p", null, run.report.summary) : null,
        (run.report.checks || []).length ? section("Checks", h("ul", null, run.report.checks.map((check) => h("li", null,
          `${(run.conditions || {})[check.criterion] || check.criterion}: ${P.words(check.result).toLowerCase()}. ${check.evidence || ""}`)))) : null,
        (run.report.findings || []).length ? section("Findings", textList(run.report.findings.map((finding) => finding.summary || finding.finding || finding))) : null,
        (run.report.checks_run || []).length ? section("Checks run by the agent", h("ul", null, run.report.checks_run.map((check) => h("li", null, `${check.command}: ${check.outcome}`)))) : null)));
  }
  // Focused problem: independent attempts on one problem, decided by a check that only the user sets.
  const FOCUS_BUSY = ["running", "reviewing"];
  const seconds = (ms) => (typeof ms === "number" ? P.count(Math.round(ms / 100) / 10, "second") : null);
  const commandText = (command) => command.map((part) => (/\s/.test(part) ? JSON.stringify(part) : part)).join(" ");
  function checkResult(attempt) {
    if (attempt.check_passed === true) return ["passed", "The check passed with exit code 0."];
    if (attempt.timed_out) return ["failed", "The check did not finish within its time limit."];
    if (typeof attempt.exit_code === "number") return ["failed", `The check failed with exit code ${attempt.exit_code}.`];
    return attempt.check_passed === false ? ["not_started", "The check did not run for this attempt."] : ["queued", "The check has not run yet."];
  }
  function focusAttempt(attempt, hypotheses) {
    const hypothesis = hypotheses.get(attempt.hypothesis_id) || {}, [tone, result] = checkResult(attempt), review = attempt.review_state;
    return h("section", { class: "card", dataset: { key: "focus-attempt-" + attempt.attempt } },
      h("h3", null, "Attempt " + attempt.attempt, attempt.selected ? chip("Selected") : null),
      h("div", { class: "row" }, chip(P.words(attempt.host)), P.badge(attempt.run_state)),
      kv([["Hypothesis", hypothesis.statement], ["Approach", hypothesis.approach], ["Check result", P.badge(tone, result)],
        ["Check duration", seconds(attempt.duration_ms)], ["Changed lines", typeof attempt.changed_lines === "number" ? String(attempt.changed_lines) : null],
        ["Review verdict", review ? P.badge(review, P.words(review)) : "No work review is recorded."],
        ["Merge", attempt.merge_state ? P.words(attempt.merge_state) : null], ["Rerouted from", attempt.rerouted_from]]),
      attempt.output_tail ? h("details", null, h("summary", null, "Last output of the check"), h("pre", { class: "source-text" }, attempt.output_tail)) : null,
      button("Open the run", "focus-run-" + attempt.attempt, (t) => openRun({ id: attempt.run_id }, t), "small"));
  }
  function focusReport(report) {
    const checks = report.checks, starts = P.count(report.problems, "start");
    const listed = (counts) => Object.entries(counts).map(([name, n]) => P.words(name) + ": " + n).join(", ") || null;
    return h("section", { class: "stack", dataset: { key: "focus-report" } }, h("h3", null, "Report"),
      h("p", null, `${starts} recorded ${P.count(report.attempts, "attempt")}. ${checks.passed} passed the check, ${checks.failed} failed it and ` +
        `${checks.not_run} did not run it. ${report.solved} ${verb(report.solved, "start", "starts")} ended with a passing review, ` +
        `${report.blocked} ended blocked and ${report.running} ${verb(report.running, "is", "are")} still running.`),
      kv([["Attempts per host", listed(report.hosts)], ["Tokens", report.tokens.toLocaleString("en")], ["Agent time", seconds(report.run_duration_ms)],
        ["Check time", seconds(report.check_duration_ms)], ["Review verdicts", listed(report.reviews)],
        ["First attempt sufficient", `${report.first_attempt_sufficient} of ${starts}`]]),
      h("p", { class: "muted" }, report.basis));
  }
  async function focusSection(host, card, ctx) {
    const focus = await P.get("focus", { id: card.id }).catch((error) => ({ error }));
    if (focus.error) return focus.error.notIncluded ? null : put(host, section("Focused problem", P.errorState(focus.error)));
    if (!focus.focus) return null;
    const block = focus.focus, check = block.check, context = { episode_id: card.id };
    const hypotheses = new Map(focus.hypotheses.map((item) => [item.id, item]));
    // The server refuses both actions in a panel that an assistant started, so the panel says so instead of offering them.
    const actions = !ctx.canEdit || FOCUS_BUSY.includes(focus.state) ? null : (P.health() || {}).assistant_started
      ? h("p", { class: "notice" }, "This control panel was started from inside an assistant session, so it does not set the check or start the attempts. Start the control panel from your own terminal to record them.")
      : h("div", { class: "row" }, P.formButton("Set the check", "focus_check", context, { class: "small" }),
        focus.eligible && check ? P.formButton("Start the attempts", "focus_start", context, { class: "small primary" }) : null);
    put(host, section("Focused problem",
      h("div", { class: "row" }, P.badge(focus.state), chip(P.words(block.mode) + " mode"), chip(P.count(block.max_attempts, "attempt") + " allowed")),
      h("p", null, block.problem),
      check ? kv([["Check command", h("code", null, commandText(check.command))], ["Time limit", P.count(check.timeout_seconds, "second")],
        ["Protected files", check.files.length ? h("div", { class: "row" }, check.files.map((file) => chip(file.path, { class: "chip mono" }))) : "The check names no file of the project."],
        ["Set", P.date(check.set_at)]])
        : h("p", { class: "notice", dataset: { tone: "review" } }, "No check is set. The user sets the check before any attempt starts, because the check runs a command on this computer."),
      h("p", { class: "muted" }, focus.eligible ? focus.reasons.map((item) => item.reason).join(" ")
        : "Attempts start only when the work item is blocked, when an earlier delegated run failed its review or its check, or when an outcome repeats a guarded failure."),
      actions,
      focus.attempts.length ? h("div", { class: "compare", dataset: { key: "focus-attempts" } }, focus.attempts.map((attempt) => focusAttempt(attempt, hypotheses)))
        : h("p", { class: "muted" }, "No attempt has started yet."),
      section("Ruled out hypotheses", listOf(focus.hypotheses.filter((item) => item.state === "ruled_out"),
        (item) => h("div", { class: "stack" }, h("strong", null, item.statement), h("span", { class: "muted" }, item.evidence_summary)), "No hypothesis is ruled out yet.")),
      focusReport(focus.report)));
  }
  // A quick edit and the delegation form send the complete saved plan with the changed fields.
  const PLAN_FIELDS = ["state", "next_action", "scope", "autonomy", "owner", "priority", "item_type", "parent_id", "sprint_id", "acceptance", "paths", "worktree"];
  P.planPayload = (plan, change, reason) => {
    const payload = { ...Object.fromEntries(PLAN_FIELDS.filter((name) => plan[name] && plan[name].length !== 0).map((name) => [name, plan[name]])), ...change, reason };
    if ((plan.depends_on || []).length) payload.depends_on = plan.depends_on.map((ref) => ({ episode_id: ref.episode_id, reason: ref.reason }));
    if (payload.state === "in_progress" && plan.state === "in_progress" && plan.session_id) payload.session_id = plan.session_id;
    return payload;
  };
  // Done and cancelled stay in the plan form, because they close the work.
  const QUICK_STATES = ["backlog", "ready", "blocked", "review"];
  function quickEdit(card, field, label, options) {
    const plan = card.plan, before = plan[field] || (field === "priority" ? "normal" : "backlog");
    return selectControl("work-quick-" + field, label, options.includes(before) ? options : [before, ...options], before, async (value) => {
      const reason = `The user changed the ${field} from ${lower(P.words(before))} to ${lower(P.words(value))} in the pane of the ${noun(1)}.`;
      try {
        await P.action("plan", { episode_id: card.id, expected_version: card.version, payload: P.planPayload(plan, { [field]: value }, reason) }, P.requestKey("plan"));
        P.toast(`The ${field} is now ${lower(P.words(value))}.`);
      } catch (error) { P.toast(error.message, "blocked"); P.refresh(); }
    });
  }
  const delegationBlock = (card, reviews) => (["done", "cancelled"].includes(card.plan.state) ? `This ${noun(1)} is finished, so it cannot be delegated.`
    : reviews.configured === false ? "No agent host is configured, so the work cannot be delegated." : "");
  // The shown state follows the evidence, so the first open issue explains a difference from the recorded state.
  const stateReason = (card) => { const issue = (card.issues || [])[0]; return issue ? "an issue is open: " + lower(issue.reason) : "its evidence requires it."; };
  P.registerPane("work", { async render(body, params, ctx) {
      const offset = remembered.history.get(params.id) || 0;
      const work = await P.get("work", { id: params.id });
      const card = work.card, plan = card.plan || null;
      ctx.setTitle(card.title);
      ctx.setKind(P.term("work_item") + ": " + lower(typeLabel((plan && plan.item_type) || "task")));
      const history = offset ? await P.get("work", { id: params.id, offset: String(offset) }).then((value) => value.history, (error) => ({ error })) : work.history;
      const reviews = work.reviews || {}, runs = (work.runs || {}).runs || [], step = work.next || {};
      const dependencyIssues = new Set((card.issues || []).filter((issue) => issue.type === "dependency").map((issue) => issue.episode_id));
      const titles = new Map(((work.lineage || {}).nodes || []).map((node) => [node.id, node.title]));
      const lineageHost = h("div", { class: "stack" }), focusHost = h("div", { class: "stack", dataset: { key: "work-focus" } }), callsHost = h("div");
      const sprintTitles = new Map(plan && plan.sprint_id ? await P.get("sprints", { limit: "100" }).then((value) => (value.sprints || []).map((sprint) => [sprint.id, sprint.title]), () => []) : []);
      const reports = checkReports(reviews), noDelegation = plan ? delegationBlock(card, reviews) : "";
      const hasIssues = (card.issues || []).length > 0, hasDependencies = Boolean(plan && (plan.depends_on || []).length);
      const hasRuns = runs.length > 0 || reports.length > 0 || Boolean(reviews.current) || reviews.configured === false;
      const hasHistory = Boolean(history.error || history.total);
      const folded = [[Boolean(plan), "Scope and allowed paths"], [hasDependencies, "Dependencies"], [hasIssues, "Issues"],
        [hasRuns, "Agent checks and delegated runs"], [hasHistory, "History"]].filter(([present]) => !present).map(([, name]) => name);
      // The next step and the actions of the item stay pinned under the scrolling body.
      const next = nextAction(work, card), pinned = next && next.tagName === "BUTTON";
      put(ctx.foot, pinned ? next : null, ctx.canEdit ? h("div", { class: "row" },
        button("Edit plan", "work-edit", (t) => P.openForm("plan", workContext(card), t), "small"),
        plan ? button("Allow paths", "work-allow", (t) => P.openForm("allow_paths", workContext(card), t), "small") : null,
        plan ? button("Delegate", "work-delegate", (t) => P.openForm("delegate", workContext(card), t), "small", { disabled: Boolean(noDelegation) }) : null,
        button("Request check", "work-check", (t) => P.openForm("review", workContext(card, { role: "outcome" }), t), "small"),
        button("Comment", "work-comment", (t) => P.openForm("comment", workContext(card), t), "small")) : null);
      put(body,
        h("div", { class: "row" }, P.badge(card.state),
          chip(P.words(card.subject)), plan && plan.priority && plan.priority !== "normal" ? chip(P.words(plan.priority) + " priority") : null,
          plan && plan.owner ? chip("Owner: " + (plan.owner === "human" ? "person" : "agent")) : null, h("span", { class: "muted" }, "Version " + card.version)),
        ctx.canEdit && noDelegation ? h("p", { class: "muted", dataset: { key: "work-delegate-blocked" } }, noDelegation) : null,
        ctx.canEdit && plan && !["done", "cancelled"].includes(plan.state) ? h("div", { class: "toolbar", dataset: { key: "work-quick" } },
          quickEdit(card, "state", "Recorded state", plan.owner === "human" ? [...QUICK_STATES.slice(0, 2), "in_progress", ...QUICK_STATES.slice(2)] : QUICK_STATES),
          quickEdit(card, "priority", "Priority", ["high", "normal", "low"])) : null,
        card.recorded_state && card.recorded_state !== card.state ? h("p", { class: "muted", dataset: { key: "work-recorded-state" } },
          `The plan records this ${noun(1)} as ${lower(P.words(card.recorded_state))}. It shows as ${lower(P.words(card.state))}, because ${stateReason(card)}`) : null,
        kv([["Intended result", card.intent], ["Done when", card.done_when]]),
        h("section", { class: "notice", dataset: card.state === "blocked" ? { tone: "blocked" } : null }, h("h3", null, "Next step"),
          h("p", null, step.reason || "No next step is recorded."),
          step.next_step && step.next_step.reason && step.next_step.reason !== step.reason ? h("p", { class: "muted" }, step.next_step.reason) : null, pinned ? null : next),
        hasIssues ? section("Issues", listOf(card.issues, (issue, index) => h("div", { class: "stack" }, h("span", { class: "row" }, toneBadge("review", P.words(issue.type))), h("span", null, issue.reason),
          issue.source_id || issue.record_id ? button("Open the evidence", "work-issue-" + index, (t) => P.openRecord(issue.source_id || issue.record_id, t), "small") : null,
          issue.run_id ? button("Open the check", "work-issue-run-" + index, (t) => openRun({ id: issue.run_id, episode_id: card.id }, t), "small") : null))) : null,
        (reviews.awaiting_user || []).length ? section("Needs your confirmation",
          h("p", { class: "muted" }, "The latest check found no evidence that a machine can use to confirm these criteria. Each is offered to you once, and your statement is recorded as your confirmation."),
          listOf(reviews.awaiting_user, (item) => h("div", { class: "stack", dataset: { key: "confirm-" + item.criterion } },
            h("span", { class: "row" }, chip(item.criterion)), h("strong", null, item.condition), h("span", { class: "muted" }, item.evidence),
            P.canEdit() ? P.formButton("Confirm", "confirm_criterion", { episode_id: card.id, criterion: item.criterion, condition: item.condition }, { class: "small primary" }) : null))) : null,
        callsHost,
        plan && (plan.acceptance || []).length ? section("Acceptance criteria", h("ol", null, plan.acceptance.map((line) => h("li", null, line)))) : null,
        plan ? section("Scope and allowed paths", kv([["Scope", plan.scope], ["Autonomy", P.words(plan.autonomy)], ["Next action", plan.next_action], ["Reason", plan.reason],
          ["Allowed paths", (plan.paths || []).length ? h("div", { class: "row" }, plan.paths.map((path) => chip(path, { class: "chip mono" })))
            : "No allowed paths are recorded. Delegated work needs at least one path."],
          ["Part of", plan.parent_id ? button(titles.get(plan.parent_id) || "Open the parent item", "work-parent", (t) => P.openWork(plan.parent_id, t), "small") : null],
          ["Sprint", plan.sprint_id ? sprintTitles.get(plan.sprint_id) || h("span", { class: "mono" }, plan.sprint_id) : null]])) : null,
        hasDependencies ? section("Dependencies", listOf(plan.depends_on, (ref) => openItem("work-dependency-" + ref.episode_id, (t) => P.openWork(ref.episode_id, t),
          h("span", { class: "row" }, dependencyIssues.has(ref.episode_id) ? toneBadge("blocked", "Not finished") : toneBadge("ready", "Finished")),
          h("strong", null, titles.get(ref.episode_id) || ref.episode_id), h("span", null, ref.reason)))) : null,
        hasRuns ? section("Agent checks and delegated runs",
          h("p", { class: "muted" }, reviews.configured === false ? "No agent host is configured, so agent checks and delegation cannot run."
            : reviews.current ? `The current ${P.words(reviews.current.role).toLowerCase()} check is ${P.words(reviews.current.state).toLowerCase()}.` : "No outcome check is required yet."),
          listOf(runs, (run) => runButton(run, "work-run-"), "No agent run is recorded."), reports) : null,
        focusHost, lineageHost,
        // The history is the longest part of the pane, so it starts closed. Paging keeps it open.
        !hasHistory ? null : h("details", { class: "stack", dataset: { key: "work-history" }, open: remembered.open.get("history-" + card.id) === true || offset > 0,
          on: { toggle: (event) => remembered.open.set("history-" + card.id, event.currentTarget.open) } },
        h("summary", null, h("h3", { class: "summary-title" }, "History"), history.total ? " " + P.count(history.total, "record") : ""),
        history.error ? P.errorState(history.error) : [
          h("p", { class: "muted" }, history.total ? `Records ${history.offset + 1} to ${history.offset + history.records.length} of ${history.total} are shown, oldest first.` : "No history is recorded."),
          listOf(history.records, (record) => openItem("work-history-" + record.id, (t) => openNode(record, t),
            h("span", { class: "row" }, chip(P.words(record.kind)), P.badge(record.status)), h("strong", null, record.title), h("span", { class: "muted" }, P.date(record.date))), ""),
          h("div", { class: "row" },
            history.offset > 0 ? button("Earlier records", "work-history-previous", () => { remembered.history.set(card.id, Math.max(0, history.offset - history.limit)); P.refresh(); }, "small") : null,
            history.more ? (P.live ? button("Later records", "work-history-next", () => { remembered.history.set(card.id, history.offset + history.limit); P.refresh(); }, "small")
              : h("p", { class: "muted" }, "This snapshot includes the first page of history only.")) : null)]),
        // Sections without content share one sentence.
        folded.length ? h("p", { class: "muted", dataset: { key: "work-folded" } }, "Nothing is recorded yet in: " + folded.join(", ") + ".") : null);
      const unconfirmedIssue = (card.issues || []).some((issue) => issue.type === "execution_unconfirmed");
      await Promise.all([lineageBlock(lineageHost, card.id, work.lineage), focusSection(focusHost, card, ctx),
        unconfirmedIssue ? unconfirmedCard({ episode_id: card.id }).then((node) => node && put(callsHost, node)) : null]);
  } });

  // Decisions.
  function reviewReasons(record) {
    const reasons = [], outcome = record.outcome;
    const assessment = outcome && ((outcome.detail || {}).payload || {}).assessment;
    if (REVIEW_STATUS.includes(record.status)) reasons.push("The evidence of this decision changed after it was recorded.");
    if (outcome && REVIEW_STATUS.includes(outcome.status)) reasons.push("The evidence of its outcome changed after it was recorded.");
    if ((assessment === "bad" || assessment === "mixed") && !(record.detail || {}).replaced_by) reasons.push("The outcome is not good and no revised decision is recorded.");
    return reasons;
  }
  const assessmentOf = (record) => (record.outcome ? ((record.outcome.detail || {}).payload || {}).assessment || "unknown" : "none");
  P.registerView("decisions", { title: "Decisions", async render(container, params) {
      const page = await P.get("records", { view: "decisions", limit: "100" }), records = page.records || [];
      const filter = { query: params.query || "", outcome: params.outcome || "", review: params.review === "1", current: params.current === "1" };
      const results = h("div", { class: "stack" });
      const draw = () => {
        const query = filter.query.toLowerCase();
        const shown = records.filter((record) => (!filter.outcome || assessmentOf(record) === filter.outcome) && (!filter.review || reviewReasons(record).length)
          && (!filter.current || !(record.detail || {}).replaced_by) && (!query || (record.title + " " + (record.episode_title || "") + " " + JSON.stringify((record.detail || {}).payload || {})).toLowerCase().includes(query)));
        return [h("p", { class: "muted", role: "status" }, `The list shows ${shown.length} of ${P.count(page.total || 0, "decision")}.` +
          (page.more ? ` Only the first ${records.length} are loaded. Search the records to find earlier decisions.` : "")),
        listOf(shown, (record) => {
          const reasons = reviewReasons(record);
          return openItem("decision-" + record.id, (t) => openDecision(record.id, t),
            h("span", { class: "row" }, outcomeBadge(record.outcome && assessmentOf(record)), record.status !== "recorded" ? P.badge(record.status) : null,
              reasons.length ? toneBadge("review", "Needs review", "needs-review-marker") : null),
            h("strong", null, record.title), h("span", { class: "muted" }, [record.episode_title, P.date(record.date)].filter(Boolean).join(". ")),
            reasons.length ? h("span", null, reasons.join(" ")) : null);
        }, records.length ? "No decision matches these filters." : "No decision is recorded.")];
      };
      const update = (name, value) => { filter[name] = value; P.setParams({ query: filter.query, outcome: filter.outcome, review: filter.review ? "1" : "", current: filter.current ? "1" : "" }); redraw(results, draw); };
      const bad = records.filter((record) => assessmentOf(record) === "bad").length;
      const review = records.filter((record) => reviewReasons(record).length).length;
      put(container, h("p", { class: "sentence" }, `${P.count(page.total || 0, "decision")} ${verb(page.total || 0, "is", "are")} recorded. ` +
        `${bad} ${verb(bad, "has", "have")} a bad outcome and ${review} ${verb(review, "needs", "need")} review.`),
      h("div", { class: "toolbar" }, searchControl("decision-query", "Search", filter.query, (value) => update("query", value)),
        selectControl("decision-outcome", "Outcome", [["", "All outcomes"], ["good", "Good"], ["bad", "Bad"], ["mixed", "Mixed"], ["unknown", "Unknown"], ["pending", "Pending"], ["none", "No outcome recorded"]],
          filter.outcome, (value) => update("outcome", value)),
        checkControl("decision-review", "Only decisions that need review", filter.review, (value) => update("review", value)),
        checkControl("decision-current", "Hide replaced decisions", filter.current, (value) => update("current", value))), results);
      redraw(results, draw);
  } });
  P.registerPane("decision", { async render(body, params, ctx) {
      const { record } = await P.get("record", { id: params.id });
      const detail = record.detail || {}, payload = detail.payload || {}, outcome = record.outcome;
      const observed = outcome ? (outcome.detail || {}).payload || {} : null;
      ctx.setTitle(record.title || record.id);
      ctx.setKind(P.words(record.kind));
      const reasons = reviewReasons(record);
      const lineageHost = h("div", { class: "stack" });
      put(body,
        h("div", { class: "row" }, P.badge(record.status), record.kind === "decision" ? outcomeBadge(observed && (observed.assessment || "unknown")) : null,
          reasons.length ? toneBadge("review", "Needs review", "needs-review-marker") : null, detail.actor ? chip("Recorded by " + detail.actor) : null,
          h("span", { class: "muted" }, P.date(record.date))),
        reasons.length ? h("div", { class: "notice", dataset: { tone: "review" } }, reasons.map((reason) => h("p", null, reason))) : null,
        h("div", { class: "row" }, record.episode_id ? button(`Open the ${noun(1)}${record.episode_title ? ": " + record.episode_title : ""}`, "decision-work", (t) => P.openWork(record.episode_id, t), "small") : null,
          detail.supersedes ? button("Open the earlier decision", "decision-earlier", (t) => openDecision(detail.supersedes, t), "small") : null,
          detail.replaced_by ? button("Open the revised decision", "decision-revised", (t) => openDecision(detail.replaced_by, t), "small") : null),
        kv([["Decision", payload.decision], ["Reason", payload.why], ["Reconsider when", payload.reconsider_when], ["Uncertainty", payload.uncertainty || (record.kind === "decision" ? "No uncertainty is recorded." : null)]]),
        record.kind === "decision" ? h("section", { class: "stack" }, h("h3", null, "Expected and observed"), h("div", { class: "compare" },
          h("div", { class: "card" }, h("h4", null, "Expected"), h("p", null, payload.expected || "No expected result is recorded.")),
          h("div", { class: "card" }, h("h4", null, "Observed"), observed ? [h("p", null, observed.observed || "The outcome has no observation text."),
            kv([["Assessment", observed.assessment_reason], ["Completion", observed.completion ? P.words(observed.completion) : null],
              ["Failure type", observed.failure_type ? h("span", { class: "mono" }, observed.failure_type) : null], ["Severity", observed.severity ? P.words(observed.severity) : null]]),
            button("Open the outcome", "decision-outcome", (t) => P.openRecord(outcome.id, t), "small")] : h("p", null, "No outcome is recorded yet.")))) : null,
        section("Alternatives", (payload.alternatives || []).length ? textList(payload.alternatives) : P.empty("No alternatives are recorded.")),
        section("Lessons considered", listOf(payload.lessons_considered, (entry) => h("div", { class: "stack" },
          h("span", { class: "row" }, entry.applies === "yes" ? toneBadge("guarded", "Applies") : toneBadge("neutral", "Does not apply"),
            button("Open the lesson", "decision-lesson-" + entry.lesson_id, (t) => P.openRecord(entry.lesson_id, t), "small")), h("span", null, entry.reason)),
        "No lesson is recorded as considered for this decision.")),
        section("Evidence", listOf(detail.evidence, (ref) => openItem("decision-evidence-" + ref.source_id, (t) => P.openRecord(ref.source_id, t),
          h("span", { class: "row" }, ref.status ? P.badge(ref.status) : null, ref.origin ? chip(P.words(ref.origin)) : null),
          h("strong", null, ref.title || ref.source_id), h("span", { class: "muted" }, ref.reason)), "No evidence is recorded.")),
        lineageHost);
      await lineageBlock(lineageHost, record.id, null);
  } });
})();
