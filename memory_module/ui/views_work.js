/*
 * Project Memory control panel: views_work.js registers the Now, Plan, Work and Decisions views and the "work",
 * "decision" and "decide" panes. The panel is read only: a page says in a chat hint what to ask the assistant, which
 * records the decision of the user with the user_action operation. The panes draw the lineage with Panel.lineageGraph
 * of graphs.js, and runs open the "run" pane of views_knowledge.js. A work item with a focused problem shows its check,
 * its attempts side by side and its report. Learning and Sessions open the "decide" pane {kind, id} for a lesson, a flag
 * or a proposal. Now lists the calls that need reconciliation above its digest. Panel.viewTabs draws the tabs of a list view.
 */
(() => {
  "use strict";
  const P = Panel, { h, chip, button, put, kv, lower } = P;
  const STATES = ["backlog", "ready", "in_progress", "blocked", "review", "done", "cancelled"];
  const TYPES = ["phase", "epic", "story", "task", "research", "deliverable", "workflow"];
  const REVIEW_STATUS = ["needs_review", "review_due"];
  const remembered = { open: new Map(), history: new Map(), timers: {} };

  // Shared helpers.
  const noun = (n) => lower(P.term(n === 1 ? "work_item" : "work_items"));
  const verb = (n, one, many) => (n === 1 ? one : many);
  const typeLabel = (type) => (type === "deliverable" ? P.term("deliverable") : P.words(type || "task"));
  const toneBadge = (tone, label, state) => h("span", { class: "badge", dataset: { tone, state: state || tone } }, label);
  const heading = (title, count) => h("h3", null, h("span", null, title), typeof count === "number" ? chip(String(count)) : null);
  const listOf = (items, render, message) => (items && items.length
    ? h("ul", { class: "list" }, items.map((item, index) => h("li", null, render(item, index)))) : P.empty(message));
  const section = (title, ...children) => h("section", { class: "stack" }, P.heading(2, title), ...children);
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
  const selectControl = (id, label, options, value, onChange) => P.filterField("select", id, label, value, onChange, options);
  const checkControl = (id, label, checked, onChange) => P.filterField("check", id, label, checked, onChange);
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
              : null),
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
  const allLink = (href, text) => h("a", { href }, text);

  const BOARD_PAGE = 10;
  function unconfirmedItem(item) {
    const found = item.transcript || {}, suggested = found.suggested;
    const text = !found.found ? "No transcript entry was found for this call. Check its effect yourself."
      : found.result === "no_result" ? "The transcript holds the call but no result." : `The transcript reports the call as ${found.result}. ${found.excerpt || ""}`;
    return h("div", { class: "stack", dataset: { key: "unconfirmed-" + item.id } },
      h("span", { class: "row" }, chip(item.tool_name), item.read_only ? chip("Read only") : null, h("span", { class: "muted" }, P.date(item.created_at))),
      item.work_title ? h("span", null, item.work_title) : null, h("p", { class: "muted" }, text),
      suggested && found.result !== "no_result" ? h("p", { class: "muted" }, "The transcript suggests the result " + lower(P.words(suggested)) + ".") : null);
  }
  async function unconfirmedCard(params) {
    const data = await P.get("unconfirmed", params).catch(() => null);
    const items = (data && data.items) || [];
    if (!items.length) return null;
    const bulk = items.filter((item) => item.read_only && (item.transcript || {}).found && item.transcript.result !== "no_result").length;
    return h("section", { class: "card", dataset: { key: "now-unconfirmed", total: String(data.total || items.length) } }, heading("Needs reconciliation", data.total),
      h("p", { class: "muted" }, "These tool calls started without a recorded result, so their work items stay blocked. The suggestion comes from the transcript of the session."),
      P.chatHint(bulk ? `Ask the assistant in the chat to record these results. ${bulk} read-only ${bulk === 1 ? "call" : "calls"} can be resolved together.`
        : "Ask the assistant in the chat to record the result of each call."),
      listOf(items, unconfirmedItem));
  }
  // The tabs of a list view read as the views of a Notion database: an icon, the name and a quiet count, with the
  // selected tab underlined and brought into sight. entry.icon names the icon, and the table icon is the default.
  P.viewTabs = (label, prefix, tabs, selected, open, ctx) => {
    const node = h("div", { class: "db-bar" }, h("div", { class: "db-views", role: "group", "aria-label": label }, tabs.map((entry) => button([P.icon(entry.icon || "list"),
      h("span", null, entry.label), typeof entry.count === "number" ? h("span", { class: "db-count", dataset: entry.tone && entry.count ? { tone: entry.tone } : null }, String(entry.count)) : null],
    prefix + entry.id, () => open(entry.id), "db-view", { "aria-pressed": String(entry === selected) }))));
    // The row of tabs scrolls sideways, so the selected tab is brought into sight.
    ctx.onShown(() => node.querySelector('[aria-pressed="true"]').scrollIntoView({ block: "nearest", inline: "nearest" }));
    return node;
  };
  // Now is the home page of the project: a digest of the work in progress, the paused work, the work that can start and
  // the finished work, each item with its one next step, and the latest decisions. Nothing on it waits for the reader.
  const digestProperties = (extra) => [
    { key: "title", label: "Title", type: "title", width: 320, sortable: false, rowIcon: (c) => TYPE_ICONS[(c.plan || {}).item_type] || "work" },
    { key: "state", label: "State", type: "status", width: 128, sortable: false },
    ...(extra || []),
    { key: "next", label: "Next step", type: "text", width: 300, sortable: false, get: (c) => (c.plan || {}).next_action || "" },
    { key: "type", label: "Type", type: "select", width: 108, sortable: false, get: (c) => (c.plan || {}).item_type || "task", labelOf: (c) => typeLabel((c.plan || {}).item_type) },
  ];
  const WHY = { key: "why", label: "Waiting for", type: "custom", icon: "alert", width: 220, sortable: false,
    render: (c) => { const issue = issueInfo(c.issues); return issue ? chip(issue.text, { dataset: { tone: issue.tone } }) : c.state === "review" ? chip("A check or a review") : null; } };
  const digestTable = (id, rows, extra, empty) => h("div", { class: "db-inline" }, P.dbTable({ id, rows, rowKey: (c) => c.id, onOpen: (c, t) => P.openWork(c.id, t),
    properties: digestProperties(extra), keepOrder: true, empty }));
  P.registerView("now", { title: "Now", async render(container, params, ctx) {
      const [now, board] = await Promise.all([P.get("now"), P.get("board", { limit: "100" })]);
      const cards = board.cards || [], byState = (...states) => cards.filter((c) => states.includes(c.state));
      const newest = (list) => [...list].sort((a, b) => String(b.date).localeCompare(String(a.date)));
      const progress = byState("in_progress"), paused = byState("blocked", "review"), ready = byState("ready"), done = newest(byState("done"));
      ctx.setSummary(nowSentence(now));
      const [unconfirmed, kickoff] = await Promise.all([unconfirmedCard({}), kickoffCard(now)]);
      const part = (title, count, ...children) => h("section", { class: "stack digest-part" }, h("div", { class: "block-head" }, P.heading(2, title), h("span", null, String(count))), ...children);
      const decisions = now.latest_decisions || [];
      container.classList.add("list-view");
      put(container, h("div", { class: "list-pane" }, h("div", { class: "pane-rows pane-body digest", dataset: { scroll: "rows" } },
        kickoff, unconfirmed,
        part("In progress", progress.length, progress.length ? digestTable("now-progress", progress, null, "") : P.empty(`No ${noun(1)} is in progress.`)),
        part("Paused", paused.length, paused.length ? [h("p", { class: "muted" }, `A paused ${noun(1)} waits for another item, a check or a review. Its next step says what moves it.`),
          digestTable("now-paused", paused, [WHY], "")] : P.empty(`No ${noun(1)} is paused.`)),
        part("Ready to start", ready.length, ready.length ? digestTable("now-ready", ready, null, "") : P.empty(`No ${noun(1)} is ready to start.`)),
        part("Done", done.length, done.length ? P.toggle("now-done", `Show the ${Math.min(done.length, 10)} most recent`, digestTable("now-done", done.slice(0, 10), null, ""), false)
          : P.empty(`No ${noun(1)} is done yet.`)),
        part("Latest decisions", decisions.length, decisions.length ? [h("div", { class: "db-inline" }, P.dbTable({ id: "now-decisions", rows: decisions, rowKey: (d) => d.id, keepOrder: true,
          onOpen: (d, t) => openDecision(d.id, t), properties: [{ key: "title", label: "Decision", type: "title", width: 360, sortable: false, rowIcon: () => "decisions" },
            { key: "outcome", label: "Outcome", type: "custom", icon: "status", width: 170, sortable: false, render: (d) => outcomeBadge(d.outcome && d.outcome.assessment) },
            { key: "episode_title", label: P.term("work_item"), type: "text", icon: "relation", width: 260, sortable: false }, { key: "date", label: "Date", type: "date", width: 128, sortable: false }] })),
          allLink("#decisions", "Open all decisions")] : P.empty("No decision is recorded.")))));
  } });

  // The page of a lesson, a session flag or a session proposal. The panel is read only, so the page says how to decide it in the chat.
  const DECIDE = { lessons_to_accept: ["Decide a lesson", "Accept, reject or retire this lesson in the chat: tell the assistant your decision and its reason."],
    session_flags: ["Decide a flag", "Confirm or dismiss this flag in the chat: confirm it when a record is missing, and dismiss it when nothing is missing."],
    session_proposals: ["Decide a proposal", "Accept or reject this proposal in the chat, and name the work item that should hold it."] };
  P.registerPane("decide", { async render(body, params, ctx) {
      const [kindName, hint] = DECIDE[params.kind], lessons = params.kind === "lessons_to_accept", flags = params.kind === "session_flags";
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
      put(body, P.chatHint(hint));
  } });

  // Plan.
  const matches = (node, filter) => (!filter.type || node.item_type === filter.type) && (!filter.state || node.state === filter.state)
    && (!filter.query || String(node.title).toLowerCase().includes(filter.query.toLowerCase()));
  const progressText = (node) => {
    const active = node.descendants - (node.rollup.cancelled || 0);
    return active ? `${node.rollup.done} of ${active} done` : "No items below";
  };
  // Plan and Work fill the frame: the filters sit in a box at the top, which stays folded on a narrow screen until the
  // reader opens it, and the tree, the board or the rows scroll under it. The foot stays in place under them.
  const filterBox = (id, filtered, ...fields) => h("details", { class: "kn-filter-box", id, open: remembered.open.has(id) ? remembered.open.get(id) : filtered || window.innerWidth >= 640 },
    h("summary", { on: { click: (event) => remembered.open.set(id, !event.currentTarget.parentNode.open) } }, filtered ? "Filters are applied" : "Filters"), h("div", { class: "toolbar" }, fields));
  P.filterBox = filterBox;
  const paneFoot = (...children) => h("div", { class: "pane-foot" }, children);
  // The filter chips under the bar of a database. The Filter tool of the bar shows and hides them.
  const filterRow = (...fields) => h("div", { class: "db-filters" }, h("div", { class: "kn-filter-box" }, h("div", { class: "toolbar" }, fields)));
  P.filterRow = filterRow;
  // Plan is a database of the plan tree with two views: Outline, a table whose rows nest their sub-items as Notion
  // sub-items do, and Phases, a gallery of the phases with their progress.
  const TYPE_ICONS = { phase: "timeline", epic: "board", story: "page", task: "work", research: "search", deliverable: "page", workflow: "outline" };
  const planProperties = () => [
    { key: "title", label: "Title", type: "title", width: 380, sortable: false, rowIcon: (node) => TYPE_ICONS[node.item_type] || "work" },
    { key: "state", label: "State", type: "status", width: 132, sortable: false },
    { key: "type", label: "Type", type: "select", width: 112, sortable: false, get: (node) => node.item_type, labelOf: (node) => typeLabel(node.item_type) },
    { key: "progress", label: "Progress", type: "custom", icon: "status", width: 200, sortable: false,
      render: (node) => (node.descendants ? h("span", { class: "db-progress" }, P.progress(node.progress, "Progress of " + node.title), h("span", null, progressText(node))) : null) },
    { key: "acceptance", label: "Acceptance", type: "number", icon: "check", width: 116, sortable: false, get: (node) => (node.acceptance_total ? P.count(node.acceptance_total, "criterion", "criteria") : "") },
    { key: "owner", label: "Owner", type: "select", icon: "person", width: 104, sortable: false, get: (node) => node.owner, labelOf: (node) => (node.owner === "human" ? "Person" : "Agent") },
  ];
  // The rows of the outline: a node shows when it matches the filters or holds a match, and a filter opens every fold.
  function planRows(nodes, filter, depth, out) {
    const filtered = Boolean(filter.type || filter.state || filter.query);
    for (const node of nodes) {
      const below = [];
      planRows(node.children || [], filter, depth + 1, below);
      if (!matches(node, filter) && !below.length) continue;
      const open = filtered || (remembered.open.has(node.id) ? remembered.open.get(node.id) : depth < 2);
      out.push({ ...node, depth, folds: below.length > 0, open });
      if (open) out.push(...below);
    }
    return out;
  }
  P.registerView("plan", { title: "Plan", async render(container, params, ctx) {
      const plan = await P.get("plan");
      const roots = plan.roots || [], counts = plan.counts || {}, phases = roots.filter((node) => node.item_type === "phase");
      const active = (plan.total || 0) - (counts.cancelled || 0), done = counts.done || 0;
      ctx.setSummary(`${plan.total || 0} ${noun(plan.total || 0)} ${verb(plan.total || 0, "is", "are")} planned` +
        (phases.length ? ` in ${P.count(phases.length, "phase")}.` : ".") + ` ${done} ${verb(done, "is", "are")} done.`);
      const st = { tab: params.tab === "phases" && phases.length ? "phases" : "outline", type: params.type || "", state: params.state || "", query: params.query || "" };
      const results = h("section", { class: "list-pane" }), controls = h("div", { class: "db-controls" }), properties = planProperties();
      const save = () => P.setParams({ tab: st.tab === "phases" ? "phases" : "", type: st.type, state: st.state, query: st.query });
      const total = h("span", { class: "row" }, P.progress(active ? done / active : null, "Share of work items done"), h("span", null, `${done} of ${P.count(active, noun(1), noun(2))} done.`));
      const draw = () => redraw(results, () => {
        if (st.tab === "phases") return [h("ol", { class: "gallery pane-rows", dataset: { scroll: "phases" }, "aria-label": "Phases" }, phases.filter((phase) => matches(phase, st)).map((phase, index) =>
          h("li", { dataset: { tone: phase.state } }, button([h("span", { class: "gallery-cover", dataset: { tone: phase.state } }, P.badge(phase.state)),
            h("span", { class: "gallery-body" }, h("span", { class: "muted" }, "Phase " + (index + 1)), h("strong", null, phase.title),
              P.progress(phase.progress, "Progress of " + phase.title), h("span", { class: "muted" }, progressText(phase)))], "plan-phase-" + phase.id,
          (t) => P.openWork(phase.id, t), "item")))), paneFoot(total)];
        const rows = planRows(roots, st, 0, []);
        return [P.dbTable({ id: "plan", rows, rowKey: (node) => node.id, onOpen: (node, t) => P.openWork(node.id, t), properties, keepOrder: true,
          tree: { depth: (node) => node.depth, toggle: (node) => (node.folds ? h("button", { type: "button", class: "db-toggle", dataset: { key: "plan-toggle-" + node.id },
            "aria-expanded": String(node.open), "aria-label": (node.open ? "Hide" : "Show") + " the items below " + node.title, disabled: Boolean(st.type || st.state || st.query),
            on: { click: () => { remembered.open.set(node.id, !node.open); draw(); } } }, P.icon("down")) : null) },
          empty: plan.total ? `No ${noun(2)} match these filters.` : `No ${noun(2)} are planned yet.`,
          foot: [total, plan.note ? h("span", null, plan.note) : null, plan.truncated ? h("span", null, "The plan is limited, so some work items are not shown.") : null] })];
      });
      const update = (name, value) => { st[name] = value; save(); drawControls(); draw(); };
      const typesPresent = TYPES.filter((type) => JSON.stringify(roots).includes('"item_type":"' + type + '"'));
      const activeFilters = () => [st.type, st.state].filter(Boolean).length;
      const filtersOpen = () => (remembered.open.has("plan-filters") ? remembered.open.get("plan-filters") : activeFilters() > 0);
      const drawControls = () => redraw(controls, () => [
        P.dbBar({ id: "plan", label: "Plan views", view: st.tab, onView: (value) => update("tab", value),
          views: [{ id: "outline", label: "Outline", icon: "outline" }, ...(phases.length ? [{ id: "phases", label: "Phases", icon: "board" }] : [])],
          filter: { active: activeFilters(), open: filtersOpen(), onToggle: () => { remembered.open.set("plan-filters", !filtersOpen()); drawControls(); } },
          search: { id: "plan-query", value: st.query, placeholder: "Search titles", onInput: (value) => { st.query = value; save(); draw(); } } }),
        filtersOpen() ? filterRow(
          selectControl("plan-type", "Item type", [["", "All types"], ...typesPresent.map((type) => [type, typeLabel(type)])], st.type, (value) => update("type", value)),
          selectControl("plan-state", "State", [["", "All states"], ...STATES.map((state) => [state, P.words(state)])], st.state, (value) => update("state", value))) : null]);
      container.classList.add("list-view");
      put(container, controls, results);
      drawControls();
      draw();
  } });

  // Work: a database of work items with a table view and a board view, in the manner of Notion.
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
  const PRIORITY_TONE = { high: "blocked", normal: "backlog", low: "done" };
  const workProperties = (sprintTitle) => [
    { key: "title", label: "Title", type: "title", width: 320, rowIcon: () => "work" },
    { key: "state", label: "State", type: "status", width: 132, order: STATES },
    { key: "type", label: "Type", type: "select", width: 116, get: (c) => (c.plan || {}).item_type || "task", labelOf: (c) => typeLabel((c.plan || {}).item_type), order: TYPES },
    { key: "priority", label: "Priority", type: "select", width: 104, get: (c) => (c.plan || {}).priority || "normal", order: ["high", "normal", "low"],
      tone: (c) => PRIORITY_TONE[(c.plan || {}).priority || "normal"] },
    { key: "issues", label: "Issues", type: "custom", icon: "alert", width: 196, sortValue: (c) => (c.issues || []).length,
      render: (c) => { const issue = issueInfo(c.issues); return issue ? chip(issue.text, { dataset: { tone: issue.tone } }) : null; } },
    { key: "next", label: "Next step", type: "text", width: 340, get: (c) => (c.next_action !== undefined ? c.next_action : (c.plan || {}).next_action) },
    { key: "subject", label: "Subject", type: "select", width: 112 },
    { key: "sprint", label: "Sprint", type: "select", width: 150, icon: "timeline", get: (c) => (c.plan || {}).sprint_id || "", labelOf: (c) => sprintTitle.get((c.plan || {}).sprint_id) || "No sprint" },
    { key: "date", label: "Created", type: "date", width: 128 },
  ];
  const WORK_GROUPS = [["state", "State"], ["type", "Type"], ["priority", "Priority"], ["subject", "Subject"], ["sprint", "Sprint"]];
  P.registerView("work", { title: "Work", async render(container, params, ctx) {
      const [base, sprints] = await Promise.all([P.get("board", { limit: "100" }), P.get("sprints", { limit: "100" }).then((value) => value.sprints || [], () => [])]);
      const sprintTitle = new Map(sprints.map((sprint) => [sprint.id, sprint.title])), properties = workProperties(sprintTitle);
      const st = { tab: params.tab === "board" ? "board" : "table", subject: params.subject || "", state: params.state || "", sprint: params.sprint || "",
        type: params.type || "", query: params.query || "", empty: params.empty === "1", sort: params.sort || "", dir: params.dir === "desc" ? "desc" : "asc",
        group: params.group || "", hide: (params.hide || "").split(",").filter(Boolean) };
      ctx.setSummary(nowSentence({ total: base.total, counts: base.counts }));
      const results = h("section", { class: "list-pane" }), controls = h("div", { class: "db-controls" });
      let token = 0;
      const save = () => P.setParams({ tab: st.tab === "board" ? "board" : "", subject: st.subject, state: st.state, sprint: st.sprint, type: st.type, query: st.query,
        empty: st.empty ? "1" : "", sort: st.sort, dir: st.dir === "desc" ? "desc" : "", group: st.group, hide: st.hide.join(",") });
      const draw = async () => {
        const mine = ++token;
        let cards = base.cards || [];
        // Live mode asks the server when the first page does not hold every work item.
        if (P.live && base.more && (st.subject || st.sprint || st.query)) {
          cards = await P.get("board", { limit: "100", subject: st.subject, sprint_id: st.sprint, query: st.query }).then((value) => value.cards || [], () => cards);
        }
        if (mine !== token) return;
        // Without a sort of the reader the rows follow the order of the states, as the board does.
        const shown = P.sortRows(filterCards(cards, st), properties, { key: "state", dir: "asc" }), more = base.more ? `Only the first ${(base.cards || []).length} are loaded without filters.` : "";
        if (st.tab === "table") {
          redraw(results, () => P.dbTable({ id: "work", rows: shown, rowKey: (c) => c.id, onOpen: (c, t) => P.openWork(c.id, t), properties,
            sort: { key: st.sort, dir: st.dir }, onSort: (key, dir) => { st.sort = key; st.dir = dir; save(); drawControls(); draw(); },
            group: st.group, hidden: st.hide, empty: `No ${noun(1)} matches these filters.`, foot: [`${shown.length} of ${base.total || 0} shown.`, more].filter(Boolean).join(" ") }));
          return;
        }
        const [body, note] = boardNode(shown, st);
        redraw(results, () => [body, paneFoot(h("p", { role: "status" }, `The view shows ${shown.length} of ${base.total || 0} ${noun(base.total || 0)}.` + (more ? " " + more : "") + (note ? " " + note : "")))]);
      };
      const update = (name, value) => { st[name] = value; save(); drawControls(); draw(); };
      const subjects = [...new Set((base.cards || []).map((c) => c.subject).concat(st.subject ? [st.subject] : []))].sort();
      const typesPresent = TYPES.filter((type) => (base.cards || []).some((c) => ((c.plan || {}).item_type || "task") === type) || st.type === type);
      const active = () => [st.subject, st.state, st.sprint, st.type].filter(Boolean).length;
      const filtersOpen = () => (remembered.open.has("work-filters") ? remembered.open.get("work-filters") : active() > 0);
      const table = () => st.tab === "table";
      const drawControls = () => redraw(controls, () => [
        P.dbBar({ id: "work", label: "Work views", view: st.tab, onView: (value) => update("tab", value),
          views: [{ id: "table", label: "Table", icon: "table" }, { id: "board", label: "Board", icon: "board" }],
          filter: { active: active(), open: filtersOpen(), onToggle: () => { remembered.open.set("work-filters", !filtersOpen()); drawControls(); } },
          sort: table() ? { options: properties.map((property) => [property.key, property.label]), key: st.sort, dir: st.dir,
            onChange: (key, dir) => { st.sort = key; st.dir = dir; save(); drawControls(); draw(); } } : null,
          group: table() ? { options: WORK_GROUPS, key: st.group, onChange: (key) => update("group", key) } : null,
          properties: table() ? { list: properties.slice(1).map((property) => [property.key, property.label]), hidden: st.hide,
            onChange: (hide) => { st.hide = hide; save(); draw(); } } : null,
          search: { id: "work-query", value: st.query, placeholder: "Search " + noun(2), onInput: (value) => { st.query = value; save(); draw(); } } }),
        filtersOpen() ? filterRow(
          selectControl("work-subject", "Subject", [["", "All subjects"], ...subjects.map((subject) => [subject, P.words(subject)])], st.subject, (value) => update("subject", value)),
          selectControl("work-state", "State", [["", "All states"], ...STATES.map((state) => [state, P.words(state)])], st.state, (value) => update("state", value)),
          selectControl("work-sprint", "Sprint", [["", "All sprints"], ["unassigned", "No sprint"], ...sprints.map((sprint) => [sprint.id, sprint.title])], st.sprint, (value) => update("sprint", value)),
          selectControl("work-type", "Item type", [["", "All types"], ...typesPresent.map((type) => [type, typeLabel(type)])], st.type, (value) => update("type", value)),
          st.tab === "board" ? checkControl("work-empty", "Show empty columns", st.empty, (value) => update("empty", value)) : null) : null]);
      drawControls();
      container.classList.add("list-view");
      put(container, controls, results);
      await draw();
  } });

  // Work item pane.
  function nextAction(work, card) {
    const step = work.next || {};
    const detail = step.next_step || {}, blocker = (detail.blockers || [])[0] || {}, read = detail.read_with || {};
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
    const block = focus.focus, check = block.check;
    const hypotheses = new Map(focus.hypotheses.map((item) => [item.id, item]));
    // The check runs a command on this computer, so the user sets it and starts the attempts in the chat.
    const actions = FOCUS_BUSY.includes(focus.state) ? null : P.chatHint(check ? "Ask the assistant in the chat to start the attempts, or to set another check."
      : "Tell the assistant in the chat which command checks this problem, and it records the check as yours.");
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
      const reports = checkReports(reviews);
      const hasIssues = (card.issues || []).length > 0, hasDependencies = Boolean(plan && (plan.depends_on || []).length);
      const hasRuns = runs.length > 0 || reports.length > 0 || Boolean(reviews.current) || reviews.configured === false;
      const hasHistory = Boolean(history.error || history.total);
      const folded = [[Boolean(plan), "Scope"], [hasDependencies, "Dependencies"], [hasIssues, "Issues"],
        [hasRuns, "Agent checks and delegated runs"], [hasHistory, "History"]].filter(([present]) => !present).map(([, name]) => name);
      // The next step and the actions of the item stay pinned under the scrolling body.
      const next = nextAction(work, card), pinned = next && next.tagName === "BUTTON";
      put(ctx.foot, pinned ? next : null);
      const ownerText = plan && plan.owner ? (plan.owner === "human" ? "Person" : "Agent") : null;
      const block = (title, count, ...children) => h("section", { class: "stack" }, P.heading(2, title, count), ...children);
      put(body,
        // The properties of the item, as the property block at the top of a Notion page.
        P.props([["status", "State", P.badge(card.state)], ["select", "Type", chip(typeLabel((plan && plan.item_type) || "task"))],
          ["select", "Subject", chip(P.words(card.subject))],
          ["flag", "Priority", plan && plan.priority ? chip(P.words(plan.priority), { dataset: { tone: PRIORITY_TONE[plan.priority] || "backlog" } }) : null],
          ["person", "Owner", ownerText], ["relation", "Part of", plan && plan.parent_id ? button([P.icon("page"), titles.get(plan.parent_id) || "Open the parent item"], "work-parent", (t) => P.openWork(plan.parent_id, t), "link-button") : null],
          ["timeline", "Sprint", plan && plan.sprint_id ? sprintTitles.get(plan.sprint_id) || h("span", { class: "mono" }, plan.sprint_id) : null],
          ["date", "Created", card.date ? P.date(card.date) : null], ["number", "Version", String(card.version)]]),
        P.divider(),
        P.callout(card.state === "blocked" ? "alert" : "flag", [h("strong", null, "Next step"), h("p", null, step.reason || "No next step is recorded."),
          step.next_step && step.next_step.reason && step.next_step.reason !== step.reason ? h("p", { class: "muted" }, step.next_step.reason) : null, pinned ? null : next],
        card.state === "blocked" ? "blocked" : card.state === "review" ? "review" : null),
        card.recorded_state && card.recorded_state !== card.state ? h("p", { class: "muted", dataset: { key: "work-recorded-state" } },
          `The plan records this ${noun(1)} as ${lower(P.words(card.recorded_state))}. It shows as ${lower(P.words(card.state))}, because ${stateReason(card)}`) : null,
        card.intent ? block("Intended result", null, h("p", null, card.intent)) : null,
        card.done_when ? block("Done when", null, h("p", null, card.done_when)) : null,
        plan && (plan.acceptance || []).length ? block("Acceptance criteria", plan.acceptance.length, h("ol", { class: "block-list" }, plan.acceptance.map((line) => h("li", null, line)))) : null,
        hasIssues ? block("Issues", card.issues.length, h("ul", { class: "block-list plain" }, card.issues.map((issue, index) => h("li", { class: "stack" },
          h("span", { class: "row" }, toneBadge(issue.type === "dependency" ? "backlog" : "review", P.words(issue.type))), h("span", null, issue.reason),
          issue.source_id || issue.record_id ? button("Open the evidence", "work-issue-" + index, (t) => P.openRecord(issue.source_id || issue.record_id, t), "link-button") : null,
          issue.run_id ? button("Open the check", "work-issue-run-" + index, (t) => openRun({ id: issue.run_id, episode_id: card.id }, t), "link-button") : null)))) : null,
        (reviews.awaiting_user || []).length ? block("Needs your confirmation", reviews.awaiting_user.length,
          h("p", { class: "muted" }, "The latest check found no evidence that a machine can use to confirm these criteria. Confirm them in the chat, and your statement is recorded as your confirmation."),
          listOf(reviews.awaiting_user, (item) => h("div", { class: "stack", dataset: { key: "confirm-" + item.criterion } },
            h("span", { class: "row" }, chip(item.criterion)), h("strong", null, item.condition), h("span", { class: "muted" }, item.evidence)))) : null,
        callsHost,
        plan ? block("Scope", null, h("p", null, plan.scope || "No scope is recorded."), P.props([["select", "Autonomy", chip(P.words(plan.autonomy))], ["flag", "Next action", plan.next_action],
          ["text", "Reason", plan.reason], ["page", "Allowed paths", (plan.paths || []).length ? h("div", { class: "row" }, plan.paths.map((path) => chip(path, { class: "chip mono" })))
            : "No allowed paths are recorded. Delegated work needs at least one path."]])) : null,
        hasDependencies ? block("Dependencies", plan.depends_on.length, listOf(plan.depends_on, (ref) => openItem("work-dependency-" + ref.episode_id, (t) => P.openWork(ref.episode_id, t),
          h("span", { class: "row" }, dependencyIssues.has(ref.episode_id) ? toneBadge("blocked", "Not finished") : toneBadge("ready", "Finished")),
          h("strong", null, titles.get(ref.episode_id) || ref.episode_id), h("span", { class: "muted" }, ref.reason)))) : null,
        hasRuns ? block("Agent checks and delegated runs", runs.length || null,
          h("p", { class: "muted" }, reviews.configured === false ? "No agent host is configured, so agent checks and delegation cannot run."
            : reviews.current ? `The current ${P.words(reviews.current.role).toLowerCase()} check is ${P.words(reviews.current.state).toLowerCase()}.` : "No outcome check is required yet."),
          listOf(runs, (run) => runButton(run, "work-run-"), "No agent run is recorded."), reports) : null,
        focusHost, lineageHost,
        // The history is the longest part of the page, so it starts folded. Paging keeps it open.
        !hasHistory ? null : h("details", { class: "toggle", dataset: { key: "work-history" }, open: remembered.open.get("history-" + card.id) === true || offset > 0,
          on: { toggle: (event) => remembered.open.set("history-" + card.id, event.currentTarget.open) } },
        h("summary", null, P.icon("chev"), h("span", null, "History"), history.total ? h("span", { class: "block-count" }, P.count(history.total, "record")) : null),
        h("div", { class: "toggle-body" }, history.error ? P.errorState(history.error) : [
          h("p", { class: "muted" }, history.total ? `Records ${history.offset + 1} to ${history.offset + history.records.length} of ${history.total} are shown, oldest first.` : "No history is recorded."),
          listOf(history.records, (record) => openItem("work-history-" + record.id, (t) => openNode(record, t),
            h("span", { class: "row" }, chip(P.words(record.kind)), P.badge(record.status)), h("strong", null, record.title), h("span", { class: "muted" }, P.date(record.date))), ""),
          h("div", { class: "row" },
            history.offset > 0 ? button("Earlier records", "work-history-previous", () => { remembered.history.set(card.id, Math.max(0, history.offset - history.limit)); P.refresh(); }, "small") : null,
            history.more ? (P.live ? button("Later records", "work-history-next", () => { remembered.history.set(card.id, history.offset + history.limit); P.refresh(); }, "small")
              : h("p", { class: "muted" }, "This snapshot includes the first page of history only.")) : null)])),
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
  // Decisions is a database with three saved views: all decisions, the decisions that need review and the current
  // ones without a revision. Outcome is a filter chip, and the table sorts, groups and hides its properties.
  const OUTCOME_ORDER = ["bad", "mixed", "unknown", "pending", "good", "none"];
  const decisionProperties = () => [
    { key: "title", label: "Decision", type: "title", width: 380, rowIcon: () => "decisions" },
    { key: "outcome", label: "Outcome", type: "custom", icon: "status", width: 170, get: assessmentOf, order: OUTCOME_ORDER, sortValue: (record) => OUTCOME_ORDER.indexOf(assessmentOf(record)),
      labelOf: (record) => (OUTCOMES[assessmentOf(record)] || ["", "No outcome recorded"])[1], render: (record) => outcomeBadge(record.outcome && assessmentOf(record)) },
    { key: "review", label: "Review", type: "custom", icon: "alert", width: 140, get: (record) => (reviewReasons(record).length ? "needs_review" : ""),
      labelOf: () => "Needs review", render: (record) => (reviewReasons(record).length ? toneBadge("review", "Needs review", "needs-review-marker") : null) },
    { key: "status", label: "Status", type: "status", width: 130 },
    { key: "work", label: P.term("work_item"), type: "text", icon: "relation", width: 240, get: (record) => record.episode_title || "" },
    { key: "actor", label: "Recorded by", type: "text", icon: "person", width: 140, get: (record) => (record.detail || {}).actor || "" },
    { key: "date", label: "Date", type: "date", width: 128 },
  ];
  const DECISION_VIEWS = [{ id: "all", label: "All decisions", icon: "table" }, { id: "review", label: "Needs review", icon: "alert" }, { id: "current", label: "Current", icon: "check" }];
  P.registerView("decisions", { title: "Decisions", async render(container, params, ctx) {
      const page = await P.get("records", { view: "decisions", limit: "100" }), records = page.records || [], properties = decisionProperties();
      const st = { tab: DECISION_VIEWS.some((view) => view.id === params.tab) ? params.tab : "all", query: params.query || "", outcome: params.outcome || "",
        sort: params.sort || "date", dir: params.sort ? (params.dir === "desc" ? "desc" : "asc") : "desc", group: params.group || "", hide: (params.hide || "").split(",").filter(Boolean) };
      const results = h("section", { class: "list-pane" }), controls = h("div", { class: "db-controls" });
      const save = () => P.setParams({ tab: st.tab === "all" ? "" : st.tab, query: st.query, outcome: st.outcome, sort: st.sort === "date" && st.dir === "desc" ? "" : st.sort,
        dir: st.sort === "date" && st.dir === "desc" ? "" : st.dir, group: st.group, hide: st.hide.join(",") });
      const draw = () => redraw(results, () => {
        const query = st.query.toLowerCase();
        const shown = records.filter((record) => (!st.outcome || assessmentOf(record) === st.outcome) && (st.tab !== "review" || reviewReasons(record).length)
          && (st.tab !== "current" || !(record.detail || {}).replaced_by) && (!query || (record.title + " " + (record.episode_title || "") + " " + JSON.stringify((record.detail || {}).payload || {})).toLowerCase().includes(query)));
        return P.dbTable({ id: "decisions", rows: shown, rowKey: (record) => record.id, onOpen: (record, t) => openDecision(record.id, t), properties,
          sort: { key: st.sort, dir: st.dir }, onSort: (key, dir) => { st.sort = key; st.dir = dir; save(); drawControls(); draw(); }, group: st.group, hidden: st.hide,
          empty: records.length ? "No decision matches these filters." : "No decision is recorded.",
          foot: page.more ? `Only the first ${records.length} are loaded. Search the records to find earlier decisions.` : "" });
      });
      const update = (name, value) => { st[name] = value; save(); drawControls(); draw(); };
      const filtersOpen = () => (remembered.open.has("decision-filters") ? remembered.open.get("decision-filters") : Boolean(st.outcome));
      const drawControls = () => redraw(controls, () => [
        P.dbBar({ id: "decisions", label: "Decision views", view: st.tab, views: DECISION_VIEWS, onView: (value) => update("tab", value),
          filter: { active: st.outcome ? 1 : 0, open: filtersOpen(), onToggle: () => { remembered.open.set("decision-filters", !filtersOpen()); drawControls(); } },
          sort: { options: properties.map((property) => [property.key, property.label]), key: st.sort, dir: st.dir, quiet: !params.sort && st.sort === "date",
            onChange: (key, dir) => { st.sort = key; st.dir = dir; save(); drawControls(); draw(); } },
          group: { options: [["outcome", "Outcome"], ["review", "Review"], ["status", "Status"], ["work", P.term("work_item")]], key: st.group, onChange: (key) => update("group", key) },
          properties: { list: properties.slice(1).map((property) => [property.key, property.label]), hidden: st.hide, onChange: (hide) => { st.hide = hide; save(); draw(); } },
          search: { id: "decision-query", value: st.query, placeholder: "Search decisions", onInput: (value) => { st.query = value; save(); draw(); } } }),
        filtersOpen() ? filterRow(selectControl("decision-outcome", "Outcome", [["", "All outcomes"], ["good", "Good"], ["bad", "Bad"], ["mixed", "Mixed"], ["unknown", "Unknown"],
          ["pending", "Pending"], ["none", "No outcome recorded"]], st.outcome, (value) => update("outcome", value))) : null]);
      const bad = records.filter((record) => assessmentOf(record) === "bad").length;
      const review = records.filter((record) => reviewReasons(record).length).length;
      ctx.setSummary(`${P.count(page.total || 0, "decision")} ${verb(page.total || 0, "is", "are")} recorded. ` +
        `${bad} ${verb(bad, "has", "have")} a bad outcome and ${review} ${verb(review, "needs", "need")} review.`);
      container.classList.add("list-view");
      put(container, controls, results);
      drawControls();
      draw();
  } });
  P.registerPane("decision", { async render(body, params, ctx) {
      const { record } = await P.get("record", { id: params.id });
      const detail = record.detail || {}, payload = detail.payload || {}, outcome = record.outcome;
      const observed = outcome ? (outcome.detail || {}).payload || {} : null;
      ctx.setTitle(record.title || record.id);
      ctx.setKind(P.words(record.kind));
      const reasons = reviewReasons(record);
      const lineageHost = h("div", { class: "stack" });
      const link = (label, key, open) => button([P.icon("page"), label], key, open, "link-button");
      put(body,
        P.props([["status", "Status", P.badge(record.status)], record.kind === "decision" ? ["status", "Outcome", outcomeBadge(observed && (observed.assessment || "unknown"))] : null,
          ["alert", "Review", reasons.length ? toneBadge("review", "Needs review", "needs-review-marker") : null],
          ["relation", P.term("work_item"), record.episode_id ? link(record.episode_title || "Open the " + noun(1), "decision-work", (t) => P.openWork(record.episode_id, t)) : null],
          ["relation", "Earlier decision", detail.supersedes ? link("Open the earlier decision", "decision-earlier", (t) => openDecision(detail.supersedes, t)) : null],
          ["relation", "Revised decision", detail.replaced_by ? link("Open the revised decision", "decision-revised", (t) => openDecision(detail.replaced_by, t)) : null],
          ["person", "Recorded by", detail.actor], ["date", "Date", P.date(record.date)]].filter(Boolean)),
        reasons.length ? P.callout("alert", reasons.map((reason) => h("p", null, reason)), "review") : null,
        P.divider(),
        payload.decision ? h("section", { class: "stack" }, P.heading(2, "Decision"), h("p", null, payload.decision)) : null,
        payload.why ? h("section", { class: "stack" }, P.heading(2, "Reason"), h("p", null, payload.why)) : null,
        payload.reconsider_when ? h("section", { class: "stack" }, P.heading(3, "Reconsider when"), h("p", null, payload.reconsider_when)) : null,
        record.kind === "decision" ? h("section", { class: "stack" }, P.heading(3, "Uncertainty"), h("p", { class: payload.uncertainty ? null : "muted" }, payload.uncertainty || "No uncertainty is recorded.")) : null,
        record.kind === "decision" ? h("section", { class: "stack" }, P.heading(2, "Expected and observed"), h("div", { class: "compare" },
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
