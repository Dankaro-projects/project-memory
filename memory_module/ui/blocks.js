/*
 * Project Memory control panel: blocks.js, the database and page blocks in the manner of Notion, built on core.js.
 *
 * Database
 *   Panel.dbBar(options): the bar above a database. Left, the view tabs {id, label, icon} with options.view pressed and
 *     options.onView(id). Right, the tools, each left out when its option is missing: filter {active, open, onToggle()}
 *     shows or hides the filter chips that the view draws under the bar; sort {options: [[key, label]], key, dir,
 *     onChange(key, dir), quiet (the default order, not marked as a sort of the reader)}; group {options: [[key, label]], key, onChange(key)} with "" for no grouping; properties
 *     {list: [[key, label]], hidden: [keys], onChange(hidden)}; search {id, value, placeholder, onInput(text)}, which
 *     reports the typed text after a pause of 200 milliseconds. options.id prefixes the stable ids of the controls. A
 *     view with more: true waits in the "more" menu at the end of the tabs until it is the current view.
 *   Panel.dbTable(options): a database table that is its own scrolling region. options: id, rows, rowKey(row),
 *     onOpen(row, trigger), properties [{key, label, icon, type, get(row), render(row), width, align, tone(row), order}],
 *     sort {key, dir}, onSort(key, dir), group (a property key or ""), groupExtra(key, rows) (a control in the group
 *     heading), tree {depth(row), toggle(row)} (nested sub-items: the indent and the fold before the title), keepOrder (the rows are sorted already), hidden [keys], empty (text), foot (text or nodes beside the count). A property with sortable false has
 *     no sort button in its header.
 *     The first property is the title: its cell is the button that opens the row, with the data-key id-row-<rowKey>, so
 *     J and K of core.js step through the rows. Types: title, status (Panel.badge), select (a pill; tone(row) colours
 *     it), text, date, number, progress (a fraction from 0 to 1), and custom through render(row). A group heading
 *     folds its rows, and the fold is kept while the panel is open. Panel.sortRows(rows, properties, sort) sorts rows.
 *   Panel.menu(trigger, label, build(node, close)): a popover under the trigger that closes on Escape, on a click
 *     outside and on a new revision. Panel.menuItem(label, {icon, checked, hint, key, onSelect}) is one row of it.
 * Page
 *   Panel.props(entries): the property block at the top of a page; entries are [icon, label, value] and empty values
 *     are left out. Panel.heading(level, text, count), Panel.callout(icon, children, tone), Panel.toggle(key, summary,
 *     children, open) with the fold kept while the panel is open, Panel.divider(), and Panel.chatHint(text), the callout that
 *     says what to ask the assistant, because the panel is read only.
 */
(() => {
  "use strict";
  const P = Panel, { h, icon, put, button, chip, words } = P;
  const folds = new Map(), timers = {};

  // Menu.
  let current = null;
  function closeMenu(refocus) {
    if (!current) return;
    const { node, trigger, onKey, onDown } = current;
    current = null;
    node.remove();
    document.removeEventListener("keydown", onKey, true);
    document.removeEventListener("mousedown", onDown, true);
    if (trigger.isConnected) { trigger.setAttribute("aria-expanded", "false"); if (refocus) trigger.focus(); }
  }
  function menu(trigger, label, build) {
    const again = current && current.trigger === trigger;
    closeMenu(false);
    if (again) return;
    const node = h("div", { class: "menu", role: "dialog", "aria-label": label });
    const close = () => closeMenu(true);
    build(node, close);
    const onKey = (event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(true); } };
    const onDown = (event) => { if (!node.contains(event.target) && !trigger.contains(event.target)) closeMenu(false); };
    document.getElementById("app").append(node);
    const rect = trigger.getBoundingClientRect(), width = node.offsetWidth, height = node.offsetHeight;
    node.style.left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)) + "px";
    node.style.top = (rect.bottom + 4 + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 4) : rect.bottom + 4) + "px";
    trigger.setAttribute("aria-expanded", "true");
    current = { node, trigger, onKey, onDown };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onDown, true);
    const first = node.querySelector("button, input");
    if (first) first.focus();
  }
  P.onRevision(() => closeMenu(false));
  const menuItem = (label, options = {}) => h("button", { type: "button", class: "menu-item", dataset: { key: options.key },
    "aria-pressed": options.checked === undefined ? null : String(Boolean(options.checked)),
    on: { click: (event) => options.onSelect(event.currentTarget) } },
  options.icon ? icon(options.icon) : null, h("span", null, label), options.hint ? h("span", { class: "menu-hint" }, options.hint) : null,
  options.checked ? icon("check") : null);
  const menuLabel = (text) => h("div", { class: "menu-label" }, text);

  // Database bar.
  const tool = (id, name, label, active, handler) => h("button", { type: "button", class: ["db-tool", active && "active"], id, title: label,
    "aria-label": label, "aria-haspopup": handler.popup ? "dialog" : null, "aria-expanded": handler.popup ? "false" : null,
    on: { click: (event) => handler(event.currentTarget) } }, icon(name), active && typeof active === "string" ? h("span", null, active) : null);
  function dbBar(options) {
    const id = options.id, labelOf = (list, key) => (list.find(([value]) => value === key) || [key, words(key)])[1];
    // Views marked more wait in a menu at the end of the tabs, unless one of them is the current view.
    const all = options.views || [], shownViews = all.filter((view) => !view.more || view.id === options.view), rest = all.filter((view) => view.more && view.id !== options.view);
    const tabs = h("div", { class: "db-views", role: "group", "aria-label": options.label || "Views" }, shownViews.map((view) =>
      h("button", { type: "button", class: "db-view", id: id + "-view-" + view.id, "aria-pressed": String(view.id === options.view),
        on: { click: () => options.onView(view.id) } }, icon(view.icon || "table"), h("span", null, view.label))),
    rest.length ? h("button", { type: "button", class: "db-view", id: id + "-view-more", "aria-haspopup": "dialog", "aria-expanded": "false",
      on: { click: (event) => menu(event.currentTarget, "More views", (node, close) => put(node, rest.map((view) =>
        menuItem(view.label, { icon: view.icon || "table", key: id + "-view-" + view.id, onSelect: () => { close(); options.onView(view.id); } })))) } },
    h("span", null, rest.length + " more"), icon("down")) : null);
    const tools = h("div", { class: "db-tools" });
    if (options.filter) {
      const open = () => options.filter.onToggle();
      put(tools, tool(id + "-filter", "filter", options.filter.active ? "Filters, " + options.filter.active + " applied" : "Filter",
        options.filter.active ? "Filter" : false, open));
      tools.lastChild.setAttribute("aria-expanded", String(Boolean(options.filter.open)));
    }
    if (options.sort) {
      const sort = options.sort, active = sort.key && !sort.quiet ? labelOf(sort.options, sort.key) : false;
      const open = (trigger) => menu(trigger, "Sort", (node, close) => put(node, menuLabel("Sort by"),
        sort.options.map(([key, label]) => menuItem(label, { key: id + "-sort-" + key, checked: key === sort.key, onSelect: () => { close(); sort.onChange(key, sort.dir || "asc"); } })),
        h("div", { class: "menu-line" }),
        menuItem("Ascending", { icon: "arrow-up", key: id + "-sort-asc", checked: sort.dir !== "desc", onSelect: () => { close(); sort.onChange(sort.key, "asc"); } }),
        menuItem("Descending", { icon: "arrow-down", key: id + "-sort-desc", checked: sort.dir === "desc", onSelect: () => { close(); sort.onChange(sort.key, "desc"); } })));
      open.popup = true;
      put(tools, tool(id + "-sort", "sort", active ? "Sorted by " + active : "Sort", active, open));
    }
    if (options.group) {
      const group = options.group, active = group.key ? labelOf(group.options, group.key) : false;
      const open = (trigger) => menu(trigger, "Group", (node, close) => put(node, menuLabel("Group by"),
        [["", "No grouping"], ...group.options].map(([key, label]) => menuItem(label, { key: id + "-group-" + (key || "none"), checked: key === (group.key || ""),
          onSelect: () => { close(); group.onChange(key); } }))));
      open.popup = true;
      put(tools, tool(id + "-group", "group", active ? "Grouped by " + active : "Group", active, open));
    }
    if (options.properties) {
      const props = options.properties, hidden = new Set(props.hidden || []);
      const item = (key, label) => menuItem(label, { icon: hidden.has(key) ? "eye-off" : "eye", key: id + "-prop-" + key, onSelect: (row) => {
        if (hidden.has(key)) hidden.delete(key); else hidden.add(key);
        row.replaceWith(item(key, label));
        props.onChange([...hidden]);
      } });
      const open = (trigger) => menu(trigger, "Properties", (node) => put(node, menuLabel("Shown in the table"), props.list.map(([key, label]) => item(key, label))));
      open.popup = true;
      put(tools, tool(id + "-properties", "properties", hidden.size ? hidden.size + " hidden properties" : "Properties", false, open));
    }
    if (options.search) {
      const search = options.search;
      const field = h("input", { type: "search", id: search.id || id + "-search", value: search.value || "", placeholder: search.placeholder || "Type to search",
        autocomplete: "off", "aria-label": search.placeholder || "Search", on: {
          // A report that arrives after the reader moved to another view would change the route of that view, so it is
          // dropped. A live refresh of the same view keeps the report, because it keeps the typed text as well.
          input: (event) => { const text = event.currentTarget.value, view = P.route().name; clearTimeout(timers[id]);
            timers[id] = setTimeout(() => { if (P.route().name === view) search.onInput(text.trim()); }, 200); },
          blur: (event) => { if (!event.currentTarget.value) box.classList.remove("open"); } } });
      const box = h("div", { class: ["db-search", search.value && "open"] }, h("button", { type: "button", class: "db-tool", title: "Search", "aria-label": "Search",
        on: { click: () => { box.classList.add("open"); field.focus(); } } }, icon("search")), field);
      put(tools, box);
    }
    return h("div", { class: "db-bar" }, tabs, tools);
  }

  // Database table.
  const WIDTH = { title: 300, status: 136, select: 140, text: 260, date: 132, number: 92, progress: 150, custom: 180 };
  const ICON = { title: "text", status: "status", select: "select", text: "text", date: "date", number: "number", progress: "status", custom: "text" };
  const valueOf = (property, row) => (property.get ? property.get(row) : row[property.key]);
  function sortRows(rows, properties, sort) {
    const property = sort && properties.find((entry) => entry.key === sort.key);
    if (!property) return rows;
    const key = (row) => { const value = property.sortValue ? property.sortValue(row) : valueOf(property, row);
      return property.order ? property.order.indexOf(value) : typeof value === "string" ? value.toLowerCase() : value === null || value === undefined ? "" : value; };
    return [...rows].sort((a, b) => { const x = key(a), y = key(b); return (x < y ? -1 : x > y ? 1 : 0) * (sort.dir === "desc" ? -1 : 1); });
  }
  const shortDate = (value) => { const moment = new Date(value);
    return !value || Number.isNaN(moment.getTime()) ? "" : moment.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }); };
  function cell(property, row, options) {
    const value = valueOf(property, row), type = property.type || "text";
    if (property.render) return property.render(row);
    if (type === "title") {
      const open = h("button", { type: "button", class: "db-open", dataset: { key: options.id + "-row-" + options.rowKey(row) },
        on: { click: (event) => options.onOpen(row, event.currentTarget) } }, icon(property.rowIcon ? property.rowIcon(row) : "page"),
      h("span", { class: "db-title" }, value || "Untitled"), h("span", { class: "db-hint", "aria-hidden": "true" }, icon("open"), "Open"));
      // A tree indents the title by its depth and puts the fold of its sub-items before it, as Notion sub-items do.
      if (!options.tree) return open;
      return h("div", { class: "db-tree" }, h("span", { class: "db-indent", style: { width: options.tree.depth(row) * 22 + "px" } }),
        options.tree.toggle(row) || h("span", { class: "db-toggle-space" }), open);
    }
    if (value === null || value === undefined || value === "") return null;
    if (type === "status") return P.badge(value, property.label && property.labelOf ? property.labelOf(row) : undefined);
    if (type === "select") return chip(property.labelOf ? property.labelOf(row) : words(value), property.tone ? { dataset: { tone: property.tone(row) } } : null);
    if (type === "date") return h("span", { title: P.date(value) }, shortDate(value));
    if (type === "progress") return h("span", { class: "db-progress" }, P.progress(value, property.label), h("span", null, Math.round(value * 100) + "%"));
    return h("span", { class: "db-text", title: String(value) }, String(value));
  }
  function dbTable(options) {
    const shown = options.properties.filter((property, index) => index === 0 || !(options.hidden || []).includes(property.key));
    // keepOrder shows rows in the order the server sorted them, and the header still marks that sort.
    const rows = options.keepOrder ? options.rows : sortRows(options.rows, options.properties, options.sort);
    const width = shown.reduce((sum, property) => sum + (property.width || WIDTH[property.type || "text"]), 0);
    const head = h("thead", null, h("tr", null, shown.map((property) => {
      const sorted = options.sort && options.sort.key === property.key;
      const label = [icon(property.icon || ICON[property.type || "text"]), h("span", null, property.label), sorted ? icon(options.sort.dir === "desc" ? "arrow-down" : "arrow-up") : null];
      return h("th", { scope: "col", class: property.align === "end" ? "end" : null, "aria-sort": sorted ? (options.sort.dir === "desc" ? "descending" : "ascending") : null },
        options.onSort && property.sortable !== false ? h("button", { type: "button", id: options.id + "-head-" + property.key,
          on: { click: () => options.onSort(property.key, sorted && options.sort.dir !== "desc" ? "desc" : "asc") } }, label) : h("span", { class: "db-head" }, label));
    })));
    // Every cell names its property, so a phone shows each row as a card of labelled properties instead of a sideways scroll.
    const line = (row) => h("tr", null, shown.map((property) => h("td", { class: [property.type === "title" && "db-title-cell", property.align === "end" && "end"],
      dataset: { label: property.label } }, cell(property, row, options))));
    const groupBy = options.group && options.properties.find((property) => property.key === options.group);
    const bodies = [];
    if (groupBy) {
      const groups = new Map();
      for (const row of rows) { const value = valueOf(groupBy, row); const key = value === null || value === undefined || value === "" ? "" : String(value);
        if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row); }
      const keys = [...groups.keys()].sort((a, b) => (groupBy.order ? groupBy.order.indexOf(a) - groupBy.order.indexOf(b) : a < b ? -1 : a > b ? 1 : 0));
      for (const key of keys) {
        const fold = options.id + ":" + options.group + ":" + key, open = folds.has(fold) ? folds.get(fold) : true, items = groups.get(key);
        const label = key === "" ? h("span", { class: "muted" }, "No " + groupBy.label.toLowerCase())
          : groupBy.type === "status" ? P.badge(key) : groupBy.type === "select" ? chip(groupBy.labelOf ? groupBy.labelOf(items[0]) : words(key), groupBy.tone ? { dataset: { tone: groupBy.tone(items[0]) } } : null)
            : h("strong", null, groupBy.labelOf ? groupBy.labelOf(items[0]) : words(key));
        const toggle = h("button", { type: "button", class: "db-group", id: options.id + "-fold-" + (key || "none"), "aria-expanded": String(open),
          on: { click: (event) => { const body = event.currentTarget.closest("tbody"), folded = !body.classList.contains("folded");
            body.classList.toggle("folded", folded); event.currentTarget.setAttribute("aria-expanded", String(!folded)); folds.set(fold, !folded); } } },
        icon("down"), label, h("span", { class: "db-count" }, String(items.length)));
        const extra = options.groupExtra ? options.groupExtra(key, items) : null;
        bodies.push(h("tbody", { class: ["db-section", !open && "folded"] }, h("tr", { class: "db-group-row" }, h("th", { colspan: String(shown.length), scope: "rowgroup" },
          h("div", { class: "db-group-head" }, toggle, extra))), items.map(line)));
      }
    } else bodies.push(h("tbody", null, rows.map(line)));
    const table = h("table", { class: "db-table", style: { minWidth: width + "px" } }, h("colgroup", null, shown.map((property, index) =>
      h("col", { style: { width: index === shown.length - 1 ? "auto" : (property.width || WIDTH[property.type || "text"]) + "px" } }))), head, bodies);
    return h("div", { class: "pane-rows db-scroll", dataset: { scroll: options.id + "-table" } },
      rows.length ? table : [h("table", { class: "db-table", style: { minWidth: width + "px" } }, head), P.empty(options.empty || "No rows match.")],
      h("div", { class: "db-foot" }, h("span", { class: "muted" }, "Count"), h("span", null, String(rows.length)), options.foot ? h("div", { class: "muted db-foot-note" }, options.foot) : null));
  }

  // Page blocks.
  const props = (entries) => h("dl", { class: "props" }, entries.filter((entry) => entry[2] !== null && entry[2] !== undefined && entry[2] !== "" && !(Array.isArray(entry[2]) && !entry[2].length))
    .map(([name, label, value]) => h("div", { class: "prop" }, h("dt", null, icon(name || "text"), h("span", null, label)), h("dd", null, value))));
  const heading = (level, text, count) => h("h" + level, { class: "block-heading" }, text, typeof count === "number" ? h("span", { class: "block-count" }, String(count)) : null);
  const callout = (name, children, tone) => h("div", { class: "callout", dataset: { tone } }, icon(name || "info"), h("div", { class: "callout-body" }, children));
  const toggle = (key, summary, children, open) => h("details", { class: "toggle", open: folds.has("toggle:" + key) ? folds.get("toggle:" + key) : Boolean(open),
    on: { toggle: (event) => folds.set("toggle:" + key, event.currentTarget.open) } }, h("summary", null, icon("chev"), h("span", null, summary)), h("div", { class: "toggle-body" }, children));
  const divider = () => h("hr", { class: "divider" });
  // The panel is read only: a decision is taken in the chat, so a page says in one callout what to ask the assistant.
  const chatHint = (text) => h("div", { class: "callout chat-hint" }, icon("chat"), h("div", { class: "callout-body" }, h("p", null, text)));

  Object.assign(P, { dbBar, dbTable, sortRows, menu, menuItem, closeMenu, props, heading, callout, toggle, divider, chatHint, shortDate });
})();
