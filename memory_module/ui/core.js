/*
 * Project Memory control panel: core.js, the foundation for graphs.js, views.js and forms.js.
 *
 * viewer.py joins the application files in the order core, graphs, views, forms into one script
 * element. vendor/cytoscape.min.js is a separate script element that runs first and defines the
 * global `cytoscape`. Every file uses the global `Panel`. Boot runs after all files are evaluated,
 * so the other files register their views, drawers and forms at load time.
 *
 * Rules: build elements with Panel.h. Never use innerHTML, outerHTML, insertAdjacentHTML, eval or
 * new Function. Set computed sizes and colours through element.style, because the content security
 * policy blocks style attributes in markup. Link to outside pages only with Panel.link. Interface
 * text uses complete, plain sentences without dashes as punctuation.
 *
 * Data access
 *   Panel.live; Panel.canEdit() is false in a snapshot and before the first health response. Hide edit controls then.
 *   Panel.get(name, params): promise of GET api/<name> with string values (empty values are dropped). Live mode sends
 *     the stored ETag, reuses the cached value on 304 and shares identical requests in flight. Export mode reads
 *     snapshot.responses[Panel.key(name, params)] and rejects with error.notIncluded. viewer.py exports the keys now,
 *     board {limit: '100'}, records {view, limit: '100'}, work {id}, record {id}, run {id}, lineage {id},
 *     architecture, learning, agents, kickoff, plan and components.
 *   Panel.action(op, data, key): POST api/actions {operation, data, request_key} with the X-Project-Memory CSRF
 *     header. Rejects with a Panel.Error with status, conflict (409) and details. Success triggers a poll.
 *     Panel.requestKey(prefix) is created when a form opens and reused for every submit of that draft.
 *   Panel.key(name, params), Panel.health(), Panel.snapshot, Panel.onRevision(fn(revision)).
 * Polling: live mode reads api/health every 2 seconds while visible. A new revision clears the response cache and
 *   renders the current view and the open drawer again, keeping scroll position and focus (give interactive elements
 *   a stable id or data-key; keep filters in route params). Nothing renders while a form dialog is open. A failed
 *   update shows "Update failed" and an alert, and both clear on the next successful poll.
 *
 * Views: Panel.registerView(name, {title, section, render(container, params, ctx)}). Hash #name/key=value&key=value.
 *   render may be async; the container is laid out but hidden until it resolves, and a rejected render shows
 *   Panel.errorState. ctx: live, canEdit, revision, onShown(fn). Rail order: now, plan, work, architecture,
 *   dependencies, decisions, learning, agents, records, requirements; section names the rail group of any other view.
 *   Panel.go(name, params), Panel.route(), Panel.setParams(params) (hash only, no render), Panel.refresh().
 *
 * Drawer (non modal): Panel.registerDrawer(kind, {render(body, params, ctx)}); ctx adds setTitle(text) and
 *   setKind(text). Panel.openDrawer(kind, params, trigger) pushes an open entry on the back stack and the trigger
 *   gets focus on close. Panel.openRecord(id, trigger) uses the 'record' drawer and Panel.openWork(id, trigger) the
 *   'work' drawer, both registered by views_knowledge.js and views_work.js. Escape closes the drawer.
 *
 * Forms (modal dialog): Panel.registerForm(name, {title, submitLabel, render(fields, context, form),
 *   submit(values, context, form) -> {operation, data}, done(result, context) -> toast text, reload(context)}).
 *   Panel.openForm(name, context, trigger) resolves with the action result, or null when closed. A 409 keeps the
 *   draft and shows "Reload saved version", which calls reload and draws the form with a new key. Throw
 *   new Panel.FormError(message) in submit to stop before sending.
 *   Panel.formValues(form): checkboxes as booleans, data-list controls as arrays of non empty lines, data-number
 *   controls as numbers or null, multiple selects as arrays, other controls as trimmed text.
 *   Panel.field(label, control, hint), Panel.input, Panel.textarea, Panel.select(name, options, value, attrs).
 *
 * Elements and text
 *   Panel.h(tag, attrs, ...children): attrs class, text, dataset, style (object), on (events), hidden, value,
 *     checked, disabled, selected, multiple, required, href (# links only) and other attributes.
 *   Panel.badge(state, label), Panel.tone(state) (blocked, review, in_progress, ready, done, backlog, guarded,
 *     neutral), Panel.colors (hex per tone), Panel.link(href, text) (http and https only), Panel.markdown(text)
 *     (raw HTML stays text), Panel.words(value), Panel.term(key) (template aware), Panel.template(), Panel.date,
 *     Panel.count(n, one, many), Panel.progress(fraction, label), Panel.stateStrip(counts, {legend}),
 *     Panel.empty(message), Panel.errorState(error), Panel.toast(message, tone), Panel.alert, Panel.clearAlert.
 *
 * panel.css classes: view, view-head, sentence, row, stack, toolbar, tabs (role tab, aria-selected), grid, card,
 *   proposed, list, chip, kv, empty, notice, badge, table-wrap with table.data, progress, graph, graph.compact,
 *   graph-layout, graph-side, legend, document, source-text, field, form-grid, hint, muted, mono; buttons primary, quiet, danger, small, item.
 */
const Panel = (() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const NAV = [["now", "Now", "Delivery"], ["plan", "Plan", "Delivery"], ["work", "Work", "Delivery"],
    ["architecture", "Architecture", "Structure"], ["dependencies", "Dependencies", "Structure"],
    ["decisions", "Decisions", "Oversight"], ["learning", "Learning", "Oversight"], ["agents", "Agents", "Oversight"],
    ["machine", "Machine", "Oversight"],
    ["records", "Records", "Reference"], ["requirements", "Requirements", "Reference"]];
  const COLORS = { blocked: "#b42318", review: "#b54708", in_progress: "#4457e6", ready: "#25715a",
    done: "#6b7280", backlog: "#9ca3af", guarded: "#7a5af8", neutral: "#65625d" };
  const TONES = {};
  for (const [tone, names] of Object.entries({
    blocked: "blocked failed fail bad scope_violation error rejected file_missing file_unreadable execution_unconfirmed",
    review: "review needs_review review_due mixed proposed host_unavailable timed_out cancelling file_changed unavailable partial",
    in_progress: "in_progress running active",
    ready: "ready pass passed good completed complete accepted confirmed merged available current settled",
    done: "done cancelled discarded retired superseded historical abandoned interrupted stale",
    backlog: "backlog queued not_started idle missing unchanged", guarded: "guarded",
  })) for (const name of names.split(" ")) TONES[name] = tone;
  const WORDS = { in_progress: "In progress", work_review: "Work review", host_receipt: "Host receipt",
    project_revision: "Project requirements", needs_review: "Needs review", scope_violation: "Outside the allowed paths",
    host_unavailable: "Host unavailable", execution_unconfirmed: "Execution unconfirmed" };
  const TERMS = {
    default: { work_item: "Work item", work_items: "Work items", component: "Component", components: "Components",
      deliverable: "Deliverable", deliverables: "Deliverables", architecture_legend: "Components" },
    product: { architecture_legend: "Components and packages" },
    engagement: { component: "Stakeholder or workstream", components: "Stakeholders and workstreams", architecture_legend: "Stakeholders and workstreams" },
    automation: { component: "System or workflow", components: "Systems and workflows", architecture_legend: "Systems and workflows" },
  };
  const STRIP = ["in_progress", "review", "blocked", "ready", "backlog", "done"];
  const snapshot = JSON.parse($("memory-data").textContent);
  const live = snapshot.live === true;
  const views = new Map(), drawers = new Map(), forms = new Map(), cache = new Map(), inflight = new Map();
  const revisionListeners = [];
  const state = { route: { name: "now", params: {} }, health: live ? null : (snapshot.responses || {}).health || null,
    revision: null, csrf: null, healthTag: null, polling: false, stale: false, template: null, renderToken: 0,
    drawerToken: 0, drawer: null, drawerStack: [], drawerTrigger: null, form: null, pendingRender: false };

  class PanelError extends Error { constructor(message, extra = {}) { super(message); this.name = "PanelError"; Object.assign(this, extra); } }
  class FormError extends Error {}

  // Elements.
  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (name === "class") node.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : value;
      else if (name === "text") node.textContent = String(value);
      else if (name === "dataset") Object.assign(node.dataset, value);
      else if (name === "style") { if (typeof value === "object") Object.assign(node.style, value); }
      else if (name === "on") for (const [type, handler] of Object.entries(value)) node.addEventListener(type, handler);
      else if (name === "href") { if (String(value).startsWith("#")) node.setAttribute("href", value); }
      else if (["value", "checked", "disabled", "selected", "hidden", "multiple", "required"].includes(name)) node[name] = value;
      else if (/^on|^(src|srcdoc|innerhtml|outerhtml)$/i.test(name)) throw new Error("Panel.h does not set the attribute " + name + ".");
      else node.setAttribute(name, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child !== null && child !== undefined && typeof child !== "boolean") node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }
  function words(value) {
    if (value === null || value === undefined || value === "") return "Not recorded";
    const spaced = String(value).replace(/_/g, " ");
    return WORDS[value] || spaced.charAt(0).toUpperCase() + spaced.slice(1);
  }
  const tone = (value) => TONES[value] || "neutral";
  const badge = (value, label) => h("span", { class: "badge", dataset: { tone: tone(value), state: String(value) } }, label || words(value));
  function link(href, text) {
    let url = null;
    try { url = new URL(String(href)); } catch (error) { url = null; }
    if (!url || (url.protocol !== "http:" && url.protocol !== "https:")) return h("span", null, text === undefined ? String(href) : text);
    return Object.assign(h("a", { target: "_blank", rel: "noopener noreferrer" }, text === undefined ? url.href : text), { href: url.href });
  }
  function date(value) {
    const moment = new Date(value);
    if (!value || Number.isNaN(moment.getTime())) return value ? String(value) : "Not recorded";
    return moment.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
  }
  const count = (n, singular, plural) => n + " " + (n === 1 ? singular : plural || singular + "s");
  const empty = (message) => h("p", { class: "empty" }, message);
  function progress(fraction, label) {
    const known = typeof fraction === "number" && Number.isFinite(fraction);
    const percent = known ? Math.round(Math.max(0, Math.min(1, fraction)) * 100) : 0;
    return h("div", { class: "progress", role: "progressbar", "aria-label": label || "Progress", "aria-valuemin": "0", "aria-valuemax": "100",
      "aria-valuenow": known ? String(percent) : null, "aria-valuetext": known ? percent + " percent" : "No progress is measured." },
    h("span", { style: { width: percent + "%" } }));
  }
  // The strip alone carries colour only, so options.legend adds the same counts in words for every reader.
  function stateStrip(counts, options = {}) {
    const present = STRIP.filter((name) => counts && counts[name]);
    const label = (name) => counts[name] + " " + words(name).toLowerCase();
    const strip = h("div", { class: "strip", role: "group",
      "aria-label": present.length ? "Work items by state: " + present.map(label).join(", ") + "." : "No work items are recorded." },
    present.map((name) => h("a", { class: "strip-part", href: "#work/state=" + name, dataset: { tone: name }, title: label(name),
      "aria-label": label(name), style: { flexGrow: String(counts[name]) } })));
    if (!options.legend) return strip;
    return h("div", { class: "stack" }, strip, h("div", { class: "legend strip-legend" },
      present.length ? present.map((name) => h("a", { class: "strip-key", href: "#work/state=" + name },
        h("span", { class: "swatch", dataset: { tone: name } }), label(name))) : h("span", { class: "muted" }, "No work items are recorded yet.")));
  }

  // Markdown subset, ported from the document renderer of the earlier workspace.
  function inline(parent, text) {
    let start = 0;
    for (const match of text.matchAll(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g)) {
      const part = match[0];
      parent.append(text.slice(start, match.index));
      if (part.startsWith("`")) parent.append(h("code", null, part.slice(1, -1)));
      else if (part.startsWith("**")) parent.append(h("strong", null, part.slice(2, -2)));
      else if (part.startsWith("*")) parent.append(h("em", null, part.slice(1, -1)));
      else { const found = part.match(/^\[([^\]]+)\]\((.+)\)$/); parent.append(link(found[2], found[1])); }
      start = match.index + part.length;
    }
    parent.append(text.slice(start));
    return parent;
  }
  function markdown(text) {
    const root = h("div", { class: "document" });
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
    const tableRule = (line) => line.includes("|") && cells(line).every((cell) => /^:?-{3,}:?$/.test(cell));
    const block = (line) => /^(#{1,6}\s|```|>\s?|[-*+]\s|\d+\.\s)/.test(line);
    const listItem = (line) => line.match(/^([-*+]|\d+\.)\s+(.+)$/);
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      if (line.startsWith("```")) {
        const body = [];
        for (i++; i < lines.length && !lines[i].startsWith("```"); i++) body.push(lines[i]);
        if (i < lines.length) i++;
        root.append(h("pre", null, h("code", null, body.join("\n"))));
        continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) { root.append(inline(h("h" + heading[1].length), heading[2])); i++; continue; }
      if (i + 1 < lines.length && tableRule(lines[i + 1])) {
        const body = h("tbody");
        for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) body.append(h("tr", null, cells(lines[i]).map((cell) => inline(h("td"), cell))));
        root.append(h("div", { class: "table-wrap" }, h("table", null, h("thead", null, h("tr", null, cells(line).map((cell) => inline(h("th", { scope: "col" }), cell)))), body)));
        continue;
      }
      const item = listItem(line);
      if (item) {
        const ordered = /\d/.test(item[1]);
        const list = h(ordered ? "ol" : "ul");
        if (ordered) list.start = parseInt(item[1], 10);
        for (let next = item; next && /\d/.test(next[1]) === ordered; next = i < lines.length ? listItem(lines[i]) : null) { list.append(inline(h("li"), next[2])); i++; }
        root.append(list);
        continue;
      }
      if (line.startsWith(">")) {
        const parts = [];
        for (; i < lines.length && lines[i].startsWith(">"); i++) parts.push(lines[i].replace(/^>\s?/, ""));
        root.append(inline(h("blockquote"), parts.join("\n")));
        continue;
      }
      const parts = [line];
      for (i++; i < lines.length && lines[i].trim() && !block(lines[i]) && !(i + 1 < lines.length && tableRule(lines[i + 1])); i++) parts.push(lines[i]);
      root.append(inline(h("p"), parts.join("\n")));
    }
    return root;
  }

  // Toasts, alerts and error states.
  function toast(message, toneName) {
    const node = $("toasts").appendChild(h("div", { class: "toast", dataset: { tone: toneName || "neutral" } }, message));
    setTimeout(() => node.remove(), 6000);
  }
  const findAlert = (id) => $("alerts").querySelector('[data-alert="' + CSS.escape(id) + '"]');
  function alert(message, id = "general") {
    const node = findAlert(id) || $("alerts").appendChild(h("div", { class: "alert", dataset: { alert: id } }, h("p"),
      h("button", { type: "button", class: "small", on: { click: () => clearAlert(id) } }, "Dismiss")));
    node.querySelector("p").textContent = message;
  }
  function clearAlert(id = "general") { const node = findAlert(id); if (node) node.remove(); }
  function errorState(error) {
    if (error && error.notIncluded) {
      return h("div", { class: "notice" }, h("p", null, "This content is not included in this snapshot."),
        h("p", { class: "muted" }, error.reason || "Export the project again without a scope, or open the live control panel with project-memory view."));
    }
    return h("div", { class: "notice", dataset: { tone: "blocked" } }, h("p", null, "This content could not be loaded."),
      h("p", { class: "muted" }, (error && error.message) || String(error)));
  }

  // Data access.
  const encode = (value) => encodeURIComponent(value).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase()).replace(/%20/g, "+");
  const present = (params) => Object.entries(params || {}).filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([name, value]) => [name, String(value)]);
  function key(name, params) {
    const entries = present(params).sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
    const query = entries.map(([param, value]) => encode(param) + "=" + encode(value)).join("&");
    return query ? name + "?" + query : name;
  }
  const timeout = (ms) => (typeof AbortSignal !== "undefined" && AbortSignal.timeout ? AbortSignal.timeout(ms) : undefined);
  const readJson = (response) => response.json().catch(() => ({}));
  async function request(path, options, failure) {
    try {
      return await fetch(path, { cache: "no-store", ...options });
    } catch (error) {
      throw new PanelError(failure, { network: true });
    }
  }
  async function fetchResponse(requestKey) {
    const cached = cache.get(requestKey);
    const response = await request("api/" + requestKey, { headers: cached ? { "If-None-Match": cached.etag } : {}, signal: timeout(15000) },
      "The local server could not be reached. The control panel tries again automatically.");
    if (response.status === 304 && cached) return cached.value;
    const value = await readJson(response);
    if (!response.ok) throw new PanelError(value.message || "The local server answered with status " + response.status + ".", { status: response.status, details: value });
    if (response.headers.get("ETag")) cache.set(requestKey, { etag: response.headers.get("ETag"), value });
    return value;
  }
  function get(name, params = {}) {
    const requestKey = key(name, params);
    if (!live) {
      const responses = snapshot.responses || {};
      if (Object.prototype.hasOwnProperty.call(responses, requestKey)) return Promise.resolve(responses[requestKey]);
      const omitted = (snapshot.omitted || []).find((entry) => entry.key === requestKey || entry.key === name);
      return Promise.reject(new PanelError("This content is not included in this snapshot.", { notIncluded: true, key: requestKey, reason: omitted ? omitted.reason : null }));
    }
    if (!inflight.has(requestKey)) inflight.set(requestKey, fetchResponse(requestKey).finally(() => inflight.delete(requestKey)));
    return inflight.get(requestKey);
  }
  function requestKey(prefix = "panel") {
    const random = crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "")
      : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
    return String(prefix).slice(0, 60) + ":" + random;
  }
  const canEdit = () => live && Boolean(state.csrf);
  async function action(operation, data, requestKeyValue) {
    if (!live) throw new PanelError("This snapshot is read only. Open the live control panel to make changes.", { readOnly: true });
    if (!state.csrf) throw new PanelError("The control panel is still connecting to the local server. Try again in a moment.");
    const response = await request("api/actions", { method: "POST", headers: { "Content-Type": "application/json", "X-Project-Memory": state.csrf },
      body: JSON.stringify({ operation, data, request_key: requestKeyValue }) },
    "The change was not confirmed because the local server could not be reached. Submit the same form again; its request key prevents a duplicate record.");
    const value = await readJson(response);
    if (!response.ok) {
      throw new PanelError(value.message || "The local server rejected the change with status " + response.status + ".",
        { status: response.status, conflict: response.status === 409, details: value });
    }
    setTimeout(poll, 50);
    return value;
  }

  // Template labels and shell.
  const template = () => state.template;
  const term = (name) => (TERMS[state.template] || {})[name] || TERMS.default[name] || words(name);
  async function loadTemplate() {
    if (state.template) return;
    const now = (snapshot.responses || {}).now;
    state.template = await get("kickoff").then((value) => value.template || null, () => (now && now.kickoff && now.kickoff.template) || null);
    if (state.template) $("app").dataset.template = state.template;
  }
  function buildNav() {
    const entries = NAV.map(([name, title, section]) => [name, (views.get(name) || {}).title || title, section]);
    for (const [name, view] of views) if (!NAV.some((entry) => entry[0] === name) && view.section) entries.push([name, view.title || words(name), view.section]);
    const groups = new Map();
    for (const [name, title, section] of entries) {
      if (!groups.has(section)) groups.set(section, []);
      groups.get(section).push(h("li", null, h("a", { class: "nav-link", href: "#" + name, dataset: { nav: name } }, title)));
    }
    $("nav").replaceChildren(...[...groups].map(([section, items]) => h("div", { class: "nav-group" }, h("h2", null, section), h("ul", null, items))));
  }
  function updateChrome() {
    const title = (views.get(state.route.name) || {}).title || words(state.route.name);
    $("view-title").textContent = title;
    document.title = title + " | " + ((state.health && state.health.project) || snapshot.project || "Project Memory");
    for (const node of document.querySelectorAll(".nav-link")) {
      if (node.dataset.nav === state.route.name) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    }
  }
  // The phase of the project decides who merges delegated work, so the top bar states it and offers the change.
  function updatePhase() {
    const value = (state.health || {}).phase;
    const node = $("phase");
    node.hidden = !value;
    if (!value) return;
    const name = words(value.phase);
    const explanation = value.meaning + (value.reason ? " Reason: " + value.reason : "");
    const parts = [h("span", { class: "phase-label" }, "Lifecycle"),
      h("span", { class: "phase-value", dataset: { tone: value.phase === "production" ? "review" : "in_progress" } }, name)];
    node.replaceChildren(canEdit()
      ? h("button", { type: "button", class: "phase-button", title: explanation, dataset: { key: "phase-change" },
        on: { click: (event) => openForm("phase", { phase: value.phase }, event.currentTarget) } }, parts)
      : h("span", { class: "phase-button", title: explanation }, parts));
  }
  async function updateShell() {
    $("project-name").textContent = (state.health && state.health.project) || snapshot.project || "Project Memory";
    updatePhase();
    await loadTemplate();
    const counts = await get("now").then((now) => now.counts || {}, () => ({}));
    $("ledger").replaceChildren(...stateStrip(counts).childNodes);
    $("ledger").hidden = !STRIP.some((name) => counts[name]);
  }
  function setStatus(kind, message) {
    const node = $("live-status");
    node.dataset.state = kind;
    node.textContent = { connecting: "Connecting", live: "Live", failed: "Update failed", snapshot: "Snapshot from " + date(snapshot.exported_at) }[kind];
    if (kind === "live") node.title = "Last successful update at " + new Date().toLocaleTimeString("en-GB") + ".";
    if (kind === "failed") alert("Updates are unavailable. The control panel tries again every 2 seconds. " + (message || ""), "updates");
    else clearAlert("updates");
  }
  function setMenu(open) {
    if (open) $("app").style.setProperty("--menu-top", Math.round(document.querySelector(".topbar").getBoundingClientRect().bottom) + "px");
    $("app").dataset.menu = open ? "open" : "closed";
    $("menu-toggle").setAttribute("aria-expanded", String(open));
  }

  // Rendering that keeps scroll position and focus.
  function focusIdentity(root) {
    const active = document.activeElement;
    if (!active || active === root || !root.contains(active)) return null;
    const selection = typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
    if (active.id) return { selector: "#" + CSS.escape(active.id), selection };
    return active.dataset && active.dataset.key ? { selector: '[data-key="' + CSS.escape(active.dataset.key) + '"]', selection } : null;
  }
  function restoreFocus(root, identity) {
    const target = identity && root.querySelector(identity.selector);
    if (!target) return;
    target.focus({ preventScroll: true });
    try { if (identity.selection && target.setSelectionRange) target.setSelectionRange(identity.selection[0], identity.selection[1]); } catch (error) { /* Not a text control. */ }
  }
  const context = (shown) => ({ live, canEdit: canEdit(), revision: state.revision, onShown: (handler) => shown.push(handler) });
  // Renders into a hidden container next to the current content, then swaps, so a refresh does not flash.
  async function renderInto(root, container, render, token, current, keep) {
    const scroll = [window.scrollX, window.scrollY, root.scrollTop];
    const focus = keep ? focusIdentity(root) : null;
    const shown = [];
    root.append(container);
    try {
      await render(shown);
    } catch (error) {
      if (current() === token) container.replaceChildren(errorState(error));
    }
    if (current() !== token) { container.remove(); return false; }
    // A render waits for its data, and the reader keeps working meanwhile, so the focus is read again here
    // and the later position wins. Reading it only at the start would send focus back or drop it on the body.
    const late = keep ? focusIdentity(root) : null;
    for (const old of [...root.children]) if (old !== container) old.remove();
    container.classList.remove("pending");
    if (keep) { window.scrollTo(scroll[0], scroll[1]); root.scrollTop = scroll[2]; restoreFocus(container, late || focus); }
    else root.scrollTop = 0;
    for (const handler of shown) handler();
    return true;
  }
  async function renderView(options = {}) {
    if (state.form) { state.pendingRender = true; return; }
    const token = ++state.renderToken;
    const { name, params } = state.route;
    updateChrome();
    if (!options.keep) window.scrollTo(0, 0);
    const container = h("div", { class: "view pending", dataset: { view: name } });
    const done = await renderInto($("main"), container, (shown) => views.get(name).render(container, { ...params }, context(shown)),
      token, () => state.renderToken, options.keep);
    if (done && options.focus) $("view-title").focus({ preventScroll: true });
  }
  function refresh() { renderView({ keep: true }); renderDrawer({ keep: true }); }

  // Router.
  function parseHash() {
    const raw = location.hash.replace(/^#/, "");
    const slash = raw.indexOf("/");
    const name = slash < 0 ? raw : raw.slice(0, slash);
    const params = slash < 0 ? {} : Object.fromEntries(new URLSearchParams(raw.slice(slash + 1)));
    return views.has(name) ? { name, params } : { name: "now", params: {} };
  }
  const hashFor = (name, params) => {
    const query = new URLSearchParams(present(params)).toString();
    return "#" + name + (query ? "/" + query : "");
  };
  function go(name, params = {}) {
    const hash = hashFor(name, params);
    if (location.hash !== hash) { location.hash = hash; return; }
    state.route = parseHash();
    renderView({ focus: true });
  }
  function setParams(params) {
    state.route = { name: state.route.name, params: { ...params } };
    history.replaceState(null, "", hashFor(state.route.name, state.route.params));
  }

  // Drawer.
  function openDrawer(kind, params = {}, trigger) {
    if (!drawers.has(kind)) throw new Error("No drawer is registered for " + kind + ".");
    if (state.drawer) state.drawerStack.push(state.drawer);
    else {
      const origin = trigger || document.activeElement;
      const identity = origin && (origin.id ? "#" + CSS.escape(origin.id) : origin.dataset && origin.dataset.key ? '[data-key="' + CSS.escape(origin.dataset.key) + '"]' : null);
      state.drawerTrigger = { node: origin, identity };
    }
    state.drawer = { kind, params: { ...params } };
    $("drawer").hidden = false;
    $("app").dataset.drawer = "open";
    return renderDrawer({ focus: true });
  }
  async function renderDrawer(options = {}) {
    if (!state.drawer) return;
    if (state.form && options.keep) { state.pendingRender = true; return; }
    const token = ++state.drawerToken;
    const { kind, params } = state.drawer;
    const mine = () => token === state.drawerToken;
    $("drawer-back").hidden = state.drawerStack.length === 0;
    if (!options.keep) { $("drawer-title").textContent = "Loading"; $("drawer-kind").textContent = words(kind); }
    const container = h("div", { class: "drawer-content pending" });
    const ctx = (shown) => ({ ...context(shown), setTitle: (text) => { if (mine()) $("drawer-title").textContent = text; },
      setKind: (text) => { if (mine()) $("drawer-kind").textContent = text; } });
    const done = await renderInto($("drawer-body"), container, (shown) => drawers.get(kind).render(container, { ...params }, ctx(shown)),
      token, () => state.drawerToken, options.keep);
    if (done && $("drawer-title").textContent === "Loading") $("drawer-title").textContent = "Details";
    if (done && options.focus) $("drawer-title").focus({ preventScroll: true });
  }
  function closeDrawer() {
    if (!state.drawer) return;
    Object.assign(state, { drawerToken: state.drawerToken + 1, drawer: null, drawerStack: [] });
    $("drawer").hidden = true;
    delete $("app").dataset.drawer;
    $("drawer-body").replaceChildren();
    const trigger = state.drawerTrigger || {};
    state.drawerTrigger = null;
    const target = (trigger.node && trigger.node.isConnected && trigger.node) || (trigger.identity && document.querySelector("#main " + trigger.identity));
    (target || $("view-title")).focus({ preventScroll: !target });
  }
  function backDrawer() {
    if (!state.drawerStack.length) return closeDrawer();
    state.drawer = state.drawerStack.pop();
    return renderDrawer({ focus: true });
  }
  const openRecord = (id, trigger) => openDrawer("record", { id }, trigger);
  const openWork = (id, trigger) => (drawers.has("work") ? openDrawer("work", { id }, trigger) : openRecord(id, trigger));

  // Forms.
  function field(label, control, hint) {
    const check = control && control.type === "checkbox";
    return h("label", { class: check ? "field check" : "field" }, check ? [control, h("span", null, label)] : [h("span", null, label), control], hint ? h("small", null, hint) : null);
  }
  const text = (value) => (value === undefined || value === null ? "" : String(value));
  const input = (name, value, attrs = {}) => h("input", { type: "text", ...attrs, name, value: text(value) });
  const textarea = (name, value, attrs = {}) => h("textarea", { ...attrs, name }, Array.isArray(value) ? value.join("\n") : text(value));
  function select(name, options, value, attrs = {}) {
    const chosen = (Array.isArray(value) ? value : [value]).map(text);
    return h("select", { ...attrs, name }, options.map((option) => {
      const [optionValue, label] = Array.isArray(option) ? option : [option, words(option)];
      return h("option", { value: String(optionValue), selected: chosen.includes(String(optionValue)) }, label);
    }));
  }
  function formValues(form) {
    const values = {};
    for (const control of form.querySelectorAll("[name]")) {
      if (control.disabled || !("value" in control)) continue;
      const name = control.name;
      if (control.type === "checkbox") values[name] = control.checked;
      else if (control.type === "radio") { if (control.checked) values[name] = control.value; }
      else if (control.multiple) values[name] = [...control.selectedOptions].map((option) => option.value);
      else if ("list" in control.dataset) values[name] = control.value.split("\n").map((line) => line.trim()).filter(Boolean);
      else if ("number" in control.dataset) values[name] = control.value.trim() === "" ? null : Number(control.value);
      else values[name] = control.value.trim();
    }
    return values;
  }
  function setFormError(message) { $("form-error").textContent = message || ""; $("form-error").hidden = !message; }
  async function drawForm() {
    const current = state.form;
    const { definition } = current;
    $("form-title").textContent = typeof definition.title === "function" ? definition.title(current.context) : definition.title || "Save changes";
    Object.assign($("form-save"), { textContent: definition.submitLabel || "Save", disabled: false });
    $("form-reload").hidden = true;
    setFormError("");
    $("form-fields").replaceChildren();
    try {
      await definition.render($("form-fields"), current.context, $("form"));
    } catch (error) {
      setFormError(error.message || String(error));
      $("form-save").disabled = true;
    }
    const first = state.form === current && $("form-fields").querySelector("input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled])");
    if (first) first.focus();
  }
  function openForm(name, formContext = {}, trigger) {
    const definition = forms.get(name);
    if (!definition) return Promise.reject(new Error("No form is registered for " + name + "."));
    if (!canEdit()) {
      toast(live ? "The control panel is still connecting. Try again in a moment." : "This snapshot is read only. Open the live control panel to make changes.");
      return Promise.resolve(null);
    }
    if (state.form) return Promise.resolve(null);
    return new Promise((resolve) => {
      state.form = { name, definition, context: formContext, key: requestKey(name), result: null, busy: false, resolve, trigger: trigger || document.activeElement };
      $("form-dialog").showModal();
      drawForm();
    });
  }
  async function submitForm(event) {
    event.preventDefault();
    const current = state.form;
    if (!current || current.busy) return;
    current.busy = true;
    $("form-save").disabled = true;
    $("form-reload").hidden = true;
    setFormError("");
    try {
      const { operation, data } = await current.definition.submit(formValues($("form")), current.context, $("form"));
      current.result = await action(operation, data, current.key);
      const message = current.definition.done ? current.definition.done(current.result, current.context) : null;
      $("form-dialog").close();
      toast(message || "Saved.");
    } catch (error) {
      if (state.form !== current) return;
      setFormError(error.message || String(error));
      $("form-reload").hidden = !(error.conflict && current.definition.reload);
    } finally {
      current.busy = false;
      if (state.form === current) $("form-save").disabled = false;
    }
  }
  async function reloadForm() {
    const current = state.form;
    if (!current || !current.definition.reload) return;
    try {
      current.context = await current.definition.reload(current.context);
      current.key = requestKey(current.name);
      await drawForm();
    } catch (error) {
      setFormError(error.message || String(error));
    }
  }
  function formClosed() {
    const current = state.form;
    if (!current) return;
    state.form = null;
    $("form-fields").replaceChildren();
    if (current.trigger && current.trigger.isConnected) current.trigger.focus({ preventScroll: true });
    current.resolve(current.result);
    if (state.pendingRender) { state.pendingRender = false; refresh(); }
  }

  // Polling.
  async function poll() {
    if (!live || state.polling || document.hidden) return;
    state.polling = true;
    try {
      const response = await request("api/health", { headers: state.healthTag ? { "If-None-Match": state.healthTag } : {}, signal: timeout(10000) },
        "The local server could not be reached.");
      if (response.status !== 304) {
        const value = await readJson(response);
        if (!response.ok) throw new PanelError(value.message || "The local server answered with status " + response.status + ".");
        state.healthTag = response.headers.get("ETag");
        state.health = value;
        state.csrf = value.csrf || null;
        if (value.revision !== state.revision || state.stale) {
          state.revision = value.revision;
          state.stale = false;
          cache.clear();
          for (const listener of revisionListeners) try { listener(value.revision); } catch (error) { /* A listener failure must not stop updates. */ }
          updateShell();
          refresh();
        }
      }
      setStatus("live");
    } catch (error) {
      state.healthTag = null;
      state.stale = true;
      setStatus("failed", error.message);
    } finally {
      state.polling = false;
    }
  }

  // Boot.
  const placeholder = (title) => ({ title, render(container) {
    container.append(h("div", { class: "view-head" }, h("h2", null, title)), empty("This view is not part of this build of the control panel yet."));
  } });
  function bindEvents() {
    window.addEventListener("hashchange", () => { state.route = parseHash(); setMenu(false); renderView({ focus: true }); });
    $("menu-toggle").addEventListener("click", () => setMenu($("app").dataset.menu !== "open"));
    $("nav").addEventListener("click", (event) => { if (event.target.closest("a")) setMenu(false); });
    $("search").addEventListener("submit", (event) => { event.preventDefault(); const query = $("search-input").value.trim(); go("records", query ? { query } : {}); });
    $("drawer-close").addEventListener("click", closeDrawer);
    $("drawer-back").addEventListener("click", backDrawer);
    $("form").addEventListener("submit", submitForm);
    $("form-cancel").addEventListener("click", () => $("form-dialog").close());
    $("form-reload").addEventListener("click", reloadForm);
    $("form-dialog").addEventListener("close", formClosed);
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || $("form-dialog").open) return;
      if (event.key === "Escape" && $("app").dataset.menu === "open") { setMenu(false); $("menu-toggle").focus(); }
      else if (event.key === "Escape" && state.drawer) { event.preventDefault(); closeDrawer(); }
      else if (event.key === "/" && !event.target.closest("input, textarea, select, [contenteditable]")) { event.preventDefault(); $("search-input").focus(); }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  }
  function boot() {
    for (const [name, title] of NAV) if (!views.has(name)) views.set(name, placeholder(title));
    buildNav();
    bindEvents();
    state.route = parseHash();
    $("mode-label").textContent = live ? "Local control panel" : "Read only snapshot";
    if (!live) { setStatus("snapshot"); updateShell(); renderView(); return; }
    setStatus("connecting");
    $("main").replaceChildren(empty("The control panel is connecting to the local server."));
    poll();
    setInterval(poll, 2000);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else queueMicrotask(boot);

  return {
    live, snapshot, colors: COLORS, Error: PanelError, FormError,
    canEdit, get, key, action, requestKey, health: () => state.health, onRevision: (handler) => revisionListeners.push(handler),
    registerView: (name, definition) => views.set(name, definition), registerDrawer: (kind, definition) => drawers.set(kind, definition),
    registerForm: (name, definition) => forms.set(name, definition),
    go, route: () => ({ name: state.route.name, params: { ...state.route.params } }), setParams, refresh,
    openDrawer, closeDrawer, backDrawer, openRecord, openWork, openForm, formValues, field, input, textarea, select,
    h, badge, tone, link, markdown, words, term, template, date, count, progress, stateStrip, empty, errorState, toast, alert, clearAlert,
  };
})();
window.Panel = Panel;
