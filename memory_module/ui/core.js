/*
 * Project Memory control panel: core.js, the foundation for graphs.js, views_work.js, views_knowledge.js and forms.js.
 *
 * viewer.py joins the application files in that order into one script element. vendor/cytoscape.min.js is a separate
 * script element that runs first and defines the global `cytoscape`. Every file uses the global `Panel`. Boot runs
 * after all files are evaluated, so the other files register their views, panes and forms at load time.
 *
 * Rules: build elements with Panel.h. Never use innerHTML, outerHTML, insertAdjacentHTML, eval or new Function. Set
 * computed sizes and colours through element.style, because the content security policy blocks style attributes in
 * markup. Link to outside pages only with Panel.link. Interface text uses complete, plain sentences without dashes as
 * punctuation.
 *
 * Data access
 *   Panel.live; Panel.canEdit() is false in a snapshot and before the first health response. Hide edit controls then.
 *   Panel.get(name, params): promise of GET api/<name> with string values (empty values are dropped). Live mode sends
 *     the stored ETag, reuses the cached value on 304 and shares identical requests in flight. Export mode reads
 *     snapshot.responses[Panel.key(name, params)] and rejects with error.notIncluded.
 *   Panel.action(op, data, key): POST api/actions {operation, data, request_key} with the X-Project-Memory CSRF
 *     header. Rejects with a Panel.Error with status, conflict (409) and details. Success triggers a poll.
 *     Panel.requestKey(prefix) is created when a form opens and reused for every submit of that draft.
 *   Panel.key(name, params), Panel.health(), Panel.snapshot, Panel.onRevision(fn(revision)).
 * Polling: live mode reads api/health one second after the previous read ends, while visible, so a change shows
 *   within two seconds. A new revision clears the response cache and
 *   renders the current view and the open detail pane again, keeping the scroll position of #main, of the pane and of
 *   every element with data-scroll, the focus, and the text that the reader typed into a text control (give
 *   interactive elements a stable id or data-key; keep filters in route params). Nothing renders while a form dialog
 *   is open. A failed update shows "Update failed" and an alert, and both clear on the next successful poll.
 *
 * Views: Panel.registerView(name, {title, section, render(container, params, ctx)}). Hash #name/key=value&key=value.
 *   render may be async; the container is laid out but hidden until it resolves, and a rejected render shows
 *   Panel.errorState. ctx: live, canEdit, revision, onShown(fn), setSummary(text) (the sentence beside the title).
 *   The window never scrolls: the view scrolls inside #main, under the page head with its icon, title and sentence. The
 *   sidebar holds Now without a heading, then Work (plan, work, decisions), Knowledge (records, requirements, learning,
 *   sessions) and the folded System (architecture, dependencies, agents, machine, hive, usage), which also takes any
 *   other view that names a section. The top bar names the project, the section and the view as breadcrumbs. A sidebar
 *   icon and the page icon are the symbol i-<view name> of viewer.html. The shared database and page blocks are in blocks.js. Panel.go(name, params), Panel.route(), Panel.setParams(params) (hash
 *   only, no render), Panel.refresh().
 *
 * Detail pane, the side peek (beside the view, never over it): Panel.registerPane(kind, {render(body, params, ctx)}); ctx adds
 *   setTitle(text), setKind(text) and foot, the pinned foot for the actions of the item, which stays in reach however
 *   long the body is. Panel.openPane(kind, params, trigger): a trigger inside the pane pushes the open entry on the
 *   back stack, and any other trigger starts again. The row that opened the pane is marked with data-selected and
 *   aria-current, J and K open the next and the previous row of its kind, and the first trigger gets focus on close.
 *   Below 1180 pixels one pane shows at a time. Panel.openRecord(id, trigger) uses the 'record' pane and
 *   Panel.openWork(id, trigger) the 'work' pane, registered by views_knowledge.js and views_work.js. Escape closes.
 *   Full width, a toggle of the pane bar, hides the view behind the pane so a long document reads at the width of
 *   both; the view keeps its scroll position, and closing the pane shows it again. params.route names the route keys
 *   that close with the pane.
 * List pane: a view that adds the class list-view to its container fills #main and scrolls only inside its list.
 *   Panel.listPane({title, count, tone, note, rows, empty, foot}) has a fixed head and foot around the scrolling rows,
 *   and Panel.paneRow(key, icon, title, sub, handler(trigger)) is one row of 60 pixels that opens a pane or a view.
 *   A list-head above the list pane holds the switch and the filters of a view, and a pane-body is a scrolling region
 *   of the list pane that holds a tree, a board or cards in place of rows (Work and Plan in views_work.js).
 *
 * Forms (modal dialog): Panel.registerForm(name, {title, submitLabel, render(fields, context, form),
 *   submit(values, context, form) -> {operation, data}, done(result, context) -> toast text, reload}).
 *   Panel.openForm(name, context, trigger) resolves with the action result, or null when closed. A 409 keeps the
 *   draft and shows "Reload saved version", which draws the form again with a new key from the context the form
 *   opened with. reload(context) returns another context instead, and reload: false offers no reload. Throw
 *   new Panel.FormError(message) in submit to stop before sending.
 *   Panel.formValues(form): checkboxes as booleans, data-list controls as arrays of non empty lines, data-number
 *   controls as numbers or null, multiple selects as arrays, other controls as trimmed text.
 *   Panel.field(label, control, hint), Panel.input, Panel.textarea, Panel.select(name, options, value, attrs).
 *   Panel.filterField(kind, id, label, value, onChange, options): a labelled select, check or search control with a
 *   stable id that reports its new value on change. options are the select options or the search placeholder.
 *
 * Elements and text
 *   Panel.h(tag, attrs, ...children): attrs class, text, dataset, style (object), on (events), hidden, value,
 *     checked, disabled, selected, multiple, required, href (# links only) and other attributes.
 *   Panel.icon(name): the inline symbol i-<name> of viewer.html, hidden from assistive technology.
 *   Panel.put(host, ...children) appends every child that is not null. Panel.button(label, key, handler(trigger),
 *     className, attrs), Panel.chip(text, attrs), Panel.kv(pairs) (leaves out empty values), Panel.lower(text).
 *   Panel.badge(state, label), Panel.tone(state) (blocked, review, in_progress, ready, done, backlog, guarded,
 *     neutral), Panel.colors (hex per tone), Panel.link(href, text) (http and https only), Panel.markdown(text)
 *     (raw HTML stays text), Panel.words(value), Panel.term(key) (template aware), Panel.template(), Panel.date,
 *     Panel.count(n, one, many), Panel.progress(fraction, label), Panel.stateStrip(counts, {legend}),
 *     Panel.empty(message), Panel.errorState(error), Panel.toast(message, tone), Panel.alert, Panel.clearAlert.
 *
 * panel.css classes: view, view-head, sentence, row, stack, toolbar, tabs (aria-pressed buttons, or role tab with a tab panel), grid, card,
 *   proposed, list, chip, kv, empty, notice, badge, table-wrap with table.data, progress, graph, graph.compact,
 *   graph-layout, graph-side, legend, document, source-text, field, form-grid, hint, muted, mono; buttons primary, quiet, danger, small, item.
 */
const Panel = (() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const MORE = "System";
  // The first group has no heading, as the home page of a Notion workspace sits above the named sections.
  const NAV = [["now", "Now", ""], ["plan", "Plan", "Work"], ["work", "Work", "Work"], ["decisions", "Decisions", "Work"],
    ["records", "Records", "Knowledge"], ["requirements", "Requirements", "Knowledge"],
    ["learning", "Learning", "Knowledge"], ["sessions", "Sessions", "Knowledge"],
    ["architecture", "Architecture", MORE], ["dependencies", "Dependencies", MORE], ["agents", "Agents", MORE],
    ["machine", "Machine", MORE], ["hive", "Hive", MORE], ["usage", "Usage", MORE]];
  // The dot colours of the Notion status properties, for the graphs that cannot read the style tokens.
  const COLORS = { blocked: "#e16f64", review: "#d9730d", in_progress: "#5b97bd", ready: "#6c9b7d",
    done: "#91918e", backlog: "#c4c3c0", guarded: "#9d68d3", neutral: "#9b9a97" };
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
  const views = new Map(), panes = new Map(), forms = new Map(), cache = new Map(), inflight = new Map();
  const revisionListeners = [];
  const state = { route: { name: "now", params: {} }, health: live ? null : (snapshot.responses || {}).health || null,
    revision: null, csrf: null, healthTag: null, polling: false, stale: false, template: null, renderToken: 0,
    paneToken: 0, pane: null, paneStack: [], paneTrigger: null, form: null, pendingRender: false, now: {} };

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
  // Panel.h builds HTML elements only, and an icon needs the SVG namespace.
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), use = svg.appendChild(document.createElementNS(svg.namespaceURI, "use"));
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    use.setAttribute("href", "#i-" + ($("i-" + name) ? name : "more"));
    return svg;
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
  const lower = (value) => String(value).charAt(0).toLowerCase() + String(value).slice(1);
  // append() turns null into the text "null", so views add their children through put.
  const put = (host, ...children) => host.append(...children.flat(Infinity).filter((node) => node !== null && node !== undefined && node !== false));
  const chip = (value, attrs) => h("span", { class: "chip", ...attrs }, value);
  const button = (label, key, handler, className, attrs) => h("button", { type: "button", class: className, dataset: { key }, ...attrs,
    on: { click: (event) => handler(event.currentTarget) } }, label);
  const filled = (value) => value !== undefined && value !== null && value !== "" && !(Array.isArray(value) && !value.length);
  const kv = (pairs) => h("dl", { class: "kv" }, pairs.filter((pair) => filled(pair[1])).map(([label, value]) => [h("dt", null, label), h("dd", null, value)]));
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
    // An alert region is announced on every change, so an unchanged message is not written again.
    if (node.querySelector("p").textContent !== message) node.querySelector("p").textContent = message;
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
  const request = (path, options, failure) => fetch(path, { cache: "no-store", ...options }).catch(() => { throw new PanelError(failure, { network: true }); });
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
  // A scoped snapshot leaves views out. The rail marks them, and the snapshot opens on the first view it holds.
  const omitted = (name) => !live && (snapshot.omitted || []).some((entry) => entry.key === (name === "dependencies" ? "work_graph" : name));
  function buildNav() {
    const entries = NAV.map(([name, title, section]) => [name, (views.get(name) || {}).title || title, section]);
    for (const [name, view] of views) if (!NAV.some((entry) => entry[0] === name) && view.section) entries.push([name, view.title || words(name), MORE]);
    const groups = new Map();
    for (const [name, title, section] of entries) {
      if (!groups.has(section)) groups.set(section, []);
      const missing = omitted(name);
      groups.get(section).push(h("li", null, h("a", { class: ["nav-link", missing && "nav-omitted"], href: "#" + name, dataset: { nav: name },
        "aria-label": missing ? title + ", not included in this snapshot" : null }, icon(name), h("span", { class: "nav-title" }, title))));
    }
    // The views of occasional use stay folded until the reader opens them or works in one of them.
    $("nav").replaceChildren(...[...groups].map(([section, items]) => (section === MORE
      ? h("details", { class: "nav-group nav-more", id: "nav-more" }, h("summary", null, icon("more"), h("span", null, section), icon("chev")), h("ul", null, items))
      : h("div", { class: "nav-group" }, section ? h("h2", null, section) : null, h("ul", null, items)))));
  }
  function updateChrome() {
    const title = (views.get(state.route.name) || {}).title || words(state.route.name);
    $("view-title").textContent = title;
    const entry = NAV.find(([name]) => name === state.route.name), section = entry ? entry[2] : MORE;
    const project = (state.health && state.health.project) || snapshot.project || "Project Memory";
    $("crumbs").replaceChildren(...[project, section !== title ? section : "", title].filter(Boolean).map((text, index, all) =>
      h("li", { "aria-current": index === all.length - 1 ? "page" : null }, text)));
    $("page-icon").replaceChildren(icon(state.route.name));
    document.title = title + " | " + ((state.health && state.health.project) || snapshot.project || "Project Memory");
    for (const node of document.querySelectorAll(".nav-link")) {
      if (node.dataset.nav !== state.route.name) { node.removeAttribute("aria-current"); continue; }
      node.setAttribute("aria-current", "page");
      if (node.closest("details")) node.closest("details").open = true;
    }
  }
  // The phase of the project decides who merges delegated work, so the foot of the rail states it and offers the change.
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
    const project = (state.health && state.health.project) || snapshot.project || "Project Memory";
    $("project-name").textContent = project;
    $("project-mark").textContent = project.trim().charAt(0).toUpperCase() || "P";
    updatePhase();
    await loadTemplate();
    state.now = await get("now").catch(() => ({}));
    if (!$("activity").hidden) drawActivity();
  }
  // Project activity: the work by state, the work in progress and the active agent runs, one click from every view.
  function drawActivity() {
    const now = state.now, active = ((now.agents || {}).active || []).length;
    const title = (text) => h("h2", null, text);
    $("activity").replaceChildren();
    put($("activity"), title("Work by state"), stateStrip(now.counts || {}, { legend: true }), title("In progress"),
      (now.in_progress || []).length ? h("ul", { class: "list" }, now.in_progress.map((item) => h("li", null,
        button(item.title, "activity-work-" + item.id, (trigger) => { setActivity(false); openWork(item.id, $("activity-toggle")); }, "item"))))
        : h("p", { class: "muted" }, "No work item is in progress."),
      title("Agents"), h("p", { class: "muted" }, active ? count(active, "agent run is", "agent runs are") + " active. " : "No agent run is active. ", h("a", { href: "#agents" }, "Open Agents")));
  }
  function setActivity(open) {
    $("activity").hidden = !open;
    $("activity-toggle").setAttribute("aria-expanded", String(open));
    if (open) drawActivity();
  }
  function setStatus(kind, message) {
    const node = $("live-status");
    node.dataset.state = kind;
    node.textContent = { connecting: "Connecting", live: "Live", failed: "Update failed", snapshot: "Snapshot from " + date(snapshot.exported_at) }[kind];
    node.title = kind === "live" ? "Last successful update at " + new Date().toLocaleTimeString("en-GB") + "." : kind === "snapshot" ? "This snapshot is read only." : "";
    if (kind === "failed") alert("Updates are unavailable. The control panel tries again every 2 seconds. " + (message || ""), "updates");
    else clearAlert("updates");
  }
  function setMenu(open) {
    if (open) $("app").style.setProperty("--menu-top", Math.max(0, Math.round(document.querySelector(".topbar").getBoundingClientRect().bottom)) + "px");
    $("app").dataset.menu = open ? "open" : "closed";
    $("menu-toggle").setAttribute("aria-expanded", String(open));
  }

  // Rendering that keeps scroll position and focus.
  function focusIdentity(root) {
    const active = document.activeElement;
    if (!active || active === root || !root.contains(active)) return null;
    const selection = typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
    return identity(active) ? { selector: identity(active), selection } : null;
  }
  function restoreFocus(root, identity) {
    const target = identity && root.querySelector(identity.selector);
    if (!target) return;
    target.focus({ preventScroll: true });
    try { if (identity.selection && target.setSelectionRange) target.setSelectionRange(identity.selection[0], identity.selection[1]); } catch (error) { /* Not a text control. */ }
  }
  const context = (shown) => ({ live, canEdit: canEdit(), revision: state.revision, onShown: (handler) => shown.push(handler) });
  const TYPED = "textarea, input[type=text], input[type=search], input:not([type])";
  const identity = (node) => (node.id ? "#" + CSS.escape(node.id) : node.dataset && node.dataset.key ? '[data-key="' + CSS.escape(node.dataset.key) + '"]' : null);
  // Renders into a hidden container next to the current content, then swaps, so a refresh does not flash.
  async function renderInto(root, container, render, token, current, keep) {
    const scroll = root.scrollTop;
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
    // A live update keeps what the reader typed and how far each inner pane is scrolled.
    const old = [...root.children].filter((node) => node !== container);
    const inside = (selector) => (keep ? old.flatMap((node) => [...node.querySelectorAll(selector)]) : []);
    const typed = inside(TYPED).filter((node) => identity(node) && node.value !== node.defaultValue).map((node) => [identity(node), node.value]);
    const scrolled = inside("[data-scroll]").map((node) => [node.dataset.scroll, node.scrollTop]);
    for (const node of old) node.remove();
    container.classList.remove("pending");
    for (const [selector, value] of typed) { const node = container.querySelector(selector); if (node && node.value === node.defaultValue) node.value = value; }
    for (const [name, top] of scrolled) { const node = container.querySelector('[data-scroll="' + CSS.escape(name) + '"]'); if (node) node.scrollTop = top; }
    if (keep) { root.scrollTop = scroll; restoreFocus(container, late || focus); }
    else root.scrollTop = 0;
    for (const handler of shown) handler();
    return true;
  }
  async function renderView(options = {}) {
    if (state.form) { state.pendingRender = true; return; }
    const token = ++state.renderToken;
    const { name, params } = state.route;
    updateChrome();
    if (!options.keep) $("view-summary").textContent = "";
    const setSummary = (text) => { if (token === state.renderToken) $("view-summary").textContent = text || ""; };
    const container = h("div", { class: "view pending", dataset: { view: name } });
    const done = await renderInto($("main"), container, (shown) => views.get(name).render(container, { ...params }, { ...context(shown), setSummary }),
      token, () => state.renderToken, options.keep);
    // A view that moved the reader to one of its sections keeps that focus; otherwise the title announces the new view.
    if (done) markSelected();
    if (done && options.focus && !$("main").contains(document.activeElement)) $("view-title").focus({ preventScroll: true });
  }
  function refresh() { renderView({ keep: true }); renderPane({ keep: true }); }

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

  // Detail pane: it sits beside the view, so opening an item never covers the list.
  function openPane(kind, params = {}, trigger) {
    if (!panes.has(kind)) throw new Error("No pane is registered for " + kind + ".");
    const origin = trigger || document.activeElement;
    // A link inside the pane leads deeper, so Back returns from it. Any other trigger starts again from its row.
    if (state.pane && origin && $("detail").contains(origin)) state.paneStack.push(state.pane);
    else { state.paneStack = []; state.paneTrigger = { node: origin, identity: origin && identity(origin) }; }
    state.pane = { kind, params: { ...params } };
    $("detail").hidden = false;
    $("app").dataset.pane = "open";
    markSelected();
    return renderPane({ focus: true });
  }
  async function renderPane(options = {}) {
    if (!state.pane) return;
    if (state.form && options.keep) { state.pendingRender = true; return; }
    const token = ++state.paneToken;
    const { kind, params } = state.pane;
    const mine = () => token === state.paneToken;
    $("detail-back").hidden = state.paneStack.length === 0;
    if (!options.keep) { $("detail-title").textContent = "Loading"; $("detail-kind").textContent = words(kind); }
    // The body scrolls and the foot stays pinned under it, so the actions of the item are always in reach.
    const body = h("div", { class: "detail-scroll", dataset: { scroll: "detail" } }), foot = h("div", { class: "detail-foot" });
    const container = h("div", { class: "detail-content pending" }, body, foot);
    const ctx = (shown) => ({ ...context(shown), foot, setTitle: (text) => { if (mine()) $("detail-title").textContent = text; },
      setKind: (text) => { if (mine()) $("detail-kind").textContent = text; } });
    const done = await renderInto($("detail-body"), container, (shown) => panes.get(kind).render(body, { ...params }, ctx(shown)),
      token, () => state.paneToken, options.keep);
    if (done && $("detail-title").textContent === "Loading") $("detail-title").textContent = "Details";
    if (done && options.focus) $("detail-title").focus({ preventScroll: true });
  }
  // Full width hides the view behind the pane. The toggle is built here, because the pane bar is part of the shell.
  function setWide(on) {
    if (on) $("app").dataset.wide = "open"; else delete $("app").dataset.wide;
    $("detail-wide").textContent = on ? "Show the list" : "Full width";
    $("detail-wide").setAttribute("aria-pressed", String(on));
  }
  function closePane() {
    if (!state.pane) return;
    for (const key of [state.pane, ...state.paneStack].flatMap((p) => p.params.route || [])) { delete state.route.params[key]; setParams(state.route.params); }
    Object.assign(state, { paneToken: state.paneToken + 1, pane: null, paneStack: [] });
    $("detail").hidden = true;
    delete $("app").dataset.pane;
    setWide(false);
    $("detail-body").replaceChildren();
    const trigger = state.paneTrigger || {};
    state.paneTrigger = null;
    markSelected();
    const target = (trigger.node && trigger.node.isConnected && trigger.node) || (trigger.identity && $("main").querySelector(trigger.identity));
    (target || $("view-title")).focus({ preventScroll: !target });
  }
  function backPane() {
    if (!state.paneStack.length) return closePane();
    state.pane = state.paneStack.pop();
    return renderPane({ focus: true });
  }
  // The row that opened the pane stays marked through every render of the view.
  function markSelected() {
    for (const node of $("main").querySelectorAll("[data-selected]")) { node.removeAttribute("aria-current"); delete node.dataset.selected; }
    const selector = state.pane && state.paneTrigger && state.paneTrigger.identity, row = selector && $("main").querySelector(selector);
    if (row) { row.setAttribute("aria-current", "true"); row.dataset.selected = ""; }
  }
  // J and K open the next and the previous row of the kind that opened the pane.
  function stepRow(by) {
    const row = $("main").querySelector("[data-selected]");
    const rows = row ? [...$("main").querySelectorAll(row.tagName)].filter((node) => node.className === row.className && node.dataset.key && node.getClientRects().length) : [];
    const next = rows[rows.indexOf(row) + by];
    if (next) { next.click(); next.scrollIntoView({ block: "nearest" }); }
  }
  // List pane: the head and the foot stay in place and only the rows scroll.
  const paneRow = (key, name, title, sub, handler) => button([icon(name), h("span", null, h("strong", { title }, title), sub ? h("span", { class: "muted" }, sub) : null), icon("chev")], key, handler, "pane-row");
  // The tab above already names the list, so its title is for assistive technology and the note reads as a quiet line.
  const listPane = (options) => h("section", { class: "list-pane" },
    h("div", { class: "pane-head" }, h("h2", { class: "visually-hidden" }, options.title, typeof options.count === "number" ? ", " + options.count : ""),
      options.note ? h("p", { class: "muted" }, options.note) : null),
    options.rows.length ? h("ul", { class: "pane-rows", dataset: { scroll: "rows" } }, options.rows.map((row) => h("li", null, row))) : h("div", { class: "pane-rows" }, empty(options.empty)),
    options.foot ? h("div", { class: "pane-foot" }, options.foot) : null);
  const openRecord = (id, trigger) => openPane("record", { id }, trigger);
  const openWork = (id, trigger) => openPane("work", { id }, trigger);

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
  function filterField(kind, id, label, value, onChange, options) {
    const report = (event) => onChange(kind === "check" ? event.currentTarget.checked : event.currentTarget.value.trim());
    return field(label, kind === "select" ? select(id, options, value, { id, on: { change: report } })
      : kind === "check" ? h("input", { type: "checkbox", id, name: id, checked: value, on: { change: report } })
        : input(id, value, { id, type: "search", autocomplete: "off", placeholder: options, on: { change: report } }));
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
      state.form = { name, definition, context: formContext, initial: { ...formContext }, key: requestKey(name), result: null, busy: false, resolve,
        trigger: trigger || document.activeElement };
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
      $("form-reload").hidden = !(error.conflict && current.definition.reload !== false);
    } finally {
      current.busy = false;
      if (state.form === current) $("form-save").disabled = false;
    }
  }
  async function reloadForm() {
    const current = state.form;
    if (!current || current.definition.reload === false) return;
    try {
      const reload = current.definition.reload;
      current.context = typeof reload === "function" ? await reload(current.context) : { ...current.initial };
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
      // One timer at a time: the next read starts a second after this one ends, so a slow read never skips a change.
      clearTimeout(state.pollTimer);
      state.pollTimer = setTimeout(poll, 1000);
    }
  }

  // Boot.
  function bindEvents() {
    window.addEventListener("hashchange", () => { state.route = parseHash(); setMenu(false); setActivity(false); renderView({ focus: true }); });
    $("activity-toggle").addEventListener("click", () => setActivity($("activity").hidden));
    document.addEventListener("click", (event) => { if (!$("activity").hidden && !event.target.closest("#activity, #activity-toggle")) setActivity(false); });
    $("menu-toggle").addEventListener("click", () => setMenu($("app").dataset.menu !== "open"));
    $("nav").addEventListener("click", (event) => { if (event.target.closest("a")) setMenu(false); });
    $("search").addEventListener("submit", (event) => { event.preventDefault(); const query = $("search-input").value.trim(); go("records", query ? { query } : {}); });
    $("detail-close").addEventListener("click", closePane);
    $("detail-back").addEventListener("click", backPane);
    $("detail-close").before(button("Full width", "detail-wide", () => setWide(!$("app").dataset.wide), "small", { id: "detail-wide", "aria-pressed": "false" }));
    $("form").addEventListener("submit", submitForm);
    $("form-cancel").addEventListener("click", () => $("form-dialog").close());
    $("form-reload").addEventListener("click", reloadForm);
    $("form-dialog").addEventListener("close", formClosed);
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || $("form-dialog").open) return;
      if (event.key === "Escape" && $("app").dataset.menu === "open") { setMenu(false); $("menu-toggle").focus(); }
      else if (event.key === "Escape" && !$("activity").hidden) { setActivity(false); $("activity-toggle").focus(); }
      else if (event.key === "Escape" && state.pane) { event.preventDefault(); closePane(); }
      else if (event.target.closest("input, textarea, select, [contenteditable]") || event.metaKey || event.ctrlKey || event.altKey) return;
      else if (event.key === "/") { event.preventDefault(); $("search-input").focus(); }
      else if (state.pane && /^[jk]$/i.test(event.key)) { event.preventDefault(); stepRow(event.key.toLowerCase() === "j" ? 1 : -1); }
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  }
  function boot() {
    buildNav();
    bindEvents();
    state.route = parseHash();
    if (!live) {
      if (!location.hash && omitted(state.route.name)) state.route = { name: (NAV.find(([name]) => !omitted(name)) || NAV[0])[0], params: {} };
      setStatus("snapshot"); updateShell(); renderView(); return;
    }
    setStatus("connecting");
    $("main").replaceChildren(empty("The control panel is connecting to the local server."));
    poll();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else queueMicrotask(boot);

  return {
    live, snapshot, colors: COLORS, Error: PanelError, FormError,
    canEdit, get, key, action, requestKey, health: () => state.health, onRevision: (handler) => revisionListeners.push(handler),
    registerView: (name, definition) => views.set(name, definition), registerPane: (kind, definition) => panes.set(kind, definition),
    registerForm: (name, definition) => forms.set(name, definition),
    go, route: () => ({ name: state.route.name, params: { ...state.route.params } }), setParams, refresh,
    openPane, closePane, backPane, openRecord, openWork, listPane, paneRow, openForm, formValues, field, input, textarea, select, filterField,
    h, icon, put, button, chip, kv, lower, badge, tone, link, markdown, words, term, template, date, count, progress, stateStrip, empty, errorState, toast, alert, clearAlert,
  };
})();
window.Panel = Panel;
