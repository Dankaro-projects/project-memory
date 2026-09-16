/*
 * Project Memory control panel: graphs.js. Cytoscape wrapper, the Architecture and Dependencies views, the component
 * form and the lineage graph used by work and decision details.
 *
 * Panel.lineageGraph(container, focusId, options): appends a lineage section to container and returns a promise of it.
 *   It never rejects; a load failure shows Panel.errorState inside the section. options: data (a lineage response such
 *   as work.lineage, so no request is made), compact, title and open(node, trigger). Without data it requests api
 *   lineage {id}, which a snapshot includes for decisions only. A toggle switches between the graph and a readable
 *   vertical list, a lineage of more than 24 records opens as that list, and the choice is kept for later sections.
 * Panel.graph(host, elements, options): a Cytoscape instance that fits itself to host when host gets a size. options:
 *   key (keeps the zoom and position across refreshes), layout, style, onTap(id), onDouble(id). Returns null without
 *   the graph library. Node data: id, label, size, shape, color; edge data: id, source, target, width. Panel.graphs()
 *   lists the live instances of the open page. The Architecture view opens the forms that forms.js registers:
 *   'component' with {component_id} or {}, and 'confirm_component' with {component_id} for a proposed item.
 */
(() => {
  "use strict";
  const { h, words } = Panel;
  const LARGE = 150;
  // Cytoscape fits the node boxes only, so this padding keeps the labels of the outer nodes inside the view.
  const PAD = 48;
  const FONT = "Manrope, ui-sans-serif, system-ui, sans-serif";
  const PACKAGE_COLOR = "#c8b99a";
  const instances = new Set();
  const viewports = new Map();
  const closedLists = new Set();
  const STYLE = [
    { selector: "node", style: { label: "data(label)", width: "data(size)", height: "data(size)", shape: "data(shape)", "background-color": "data(color)",
      "border-width": 1, "border-color": "#ffffff", "font-family": FONT, "font-size": 11, color: "#111111", "text-valign": "bottom", "text-margin-y": 4,
      "text-wrap": "wrap", "text-max-width": 128, "text-background-color": "#f7f5f1", "text-background-opacity": 0.9, "text-background-padding": 2 } },
    // Done and backlog are two close greys, so these two states also differ by their border.
    { selector: "node.state-done", style: { "border-width": 3, "border-color": "#1f2937" } },
    { selector: "node.state-backlog", style: { "border-width": 2, "border-color": "#4b5563", "border-style": "dashed" } },
    { selector: "edge", style: { width: "data(width)", "line-color": "#bdb6aa", "target-arrow-color": "#bdb6aa", "target-arrow-shape": "triangle",
      "curve-style": "bezier", "arrow-scale": 0.8 } },
    { selector: "edge.link", style: { "line-style": "dashed", "line-color": "#8f99ee", "target-arrow-color": "#8f99ee" } },
    { selector: "edge.chain", style: { "line-color": Panel.colors.blocked, "target-arrow-color": Panel.colors.blocked, width: 3 } },
    { selector: "node.dashed", style: { "border-style": "dashed", "border-width": 2, "border-color": "#111111" } },
    { selector: "node.chain", style: { "border-width": 3, "border-color": Panel.colors.blocked } },
    { selector: "node.focus", style: { "border-width": 4, "border-color": "#111111" } },
    { selector: "node:selected", style: { "border-width": 4, "border-color": "#4457e6" } },
  ];
  const clip = (text, limit) => (String(text).length > limit ? String(text).slice(0, limit - 3) + "..." : String(text));
  const joinList = (items) => (items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items[items.length - 1]);
  const toneColor = (state) => Panel.colors[Panel.tone(state)];

  // Cytoscape wrapper.
  function layoutFor(cy) {
    if (cy.nodes().length > LARGE) return { name: "cose", randomize: true, nodeRepulsion: () => 9000, idealEdgeLength: () => 90, nodeDimensionsIncludeLabels: true };
    const roots = cy.nodes().roots();
    // Labels sit under the nodes and count towards the size of every row, so no label covers the row below it.
    return { name: "breadthfirst", directed: true, spacingFactor: 1.5, avoidOverlap: true, nodeDimensionsIncludeLabels: true,
      roots: roots.length ? roots : undefined };
  }
  function graph(host, elements, options = {}) {
    for (const old of instances) if (old.scratch("seen") && !old.container().isConnected) { old.destroy(); instances.delete(old); }
    if (!document.getElementById("__________cytoscape_stylesheet")) {
      // Cytoscape adds a style element unless an element with this id exists. The policy blocks that element, and panel.css positions .graph.
      document.head.append(h("meta", { id: "__________cytoscape_stylesheet", name: "graph-library" }));
    }
    if (typeof cytoscape !== "function") return null;
    const cy = cytoscape({ container: host, elements, style: STYLE.concat(options.style || []), layout: { name: "preset" },
      minZoom: 0.08, maxZoom: 3, boxSelectionEnabled: false, autoungrabify: true, selectionType: "single" });
    instances.add(cy);
    const signature = elements.map((element) => element.data.id).join("|");
    let moved = false;
    let ready = false;
    const place = () => {
      if (cy.destroyed() || !host.clientWidth || !host.clientHeight) return;
      cy.scratch("seen", true);
      cy.resize();
      if (ready) { if (!moved) cy.fit(undefined, PAD); return; }
      ready = true;
      const layout = { ...(options.layout || layoutFor(cy)), fit: true, padding: PAD, animate: false };
      cy.layout(layout).run();
      const saved = options.key && viewports.get(options.key);
      if (saved && saved.signature === signature && layout.name !== "cose") { cy.viewport({ zoom: saved.zoom, pan: saved.pan }); moved = true; }
    };
    cy.panelMoved = () => { moved = true; };
    cy.panelFit = () => { moved = false; viewports.delete(options.key); cy.fit(undefined, PAD); };
    cy.on("dragpan scrollzoom pinchzoom", () => { moved = true; });
    cy.on("viewport", () => { if (moved && options.key) viewports.set(options.key, { signature, zoom: cy.zoom(), pan: { ...cy.pan() } }); });
    if (options.onTap) cy.on("tap", "node", (event) => options.onTap(event.target.id()));
    if (options.onDouble) cy.on("dbltap", "node", (event) => options.onDouble(event.target.id()));
    const observer = new ResizeObserver(place);
    observer.observe(host);
    cy.on("destroy", () => observer.disconnect());
    place();
    return cy;
  }
  function controls(cy, key) {
    if (!cy) return null;
    const zoom = (factor) => {
      cy.panelMoved();
      cy.zoom({ level: Math.min(3, Math.max(0.08, cy.zoom() * factor)), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
    };
    const button = (label, name, handler) => h("button", { type: "button", class: "small", dataset: { key: key + "-" + name }, on: { click: handler } }, label);
    return h("div", { class: "row graph-controls", role: "group", "aria-label": "Graph controls" },
      button("Zoom in", "in", () => zoom(1.25)), button("Zoom out", "out", () => zoom(0.8)), button("Fit to view", "fit", () => cy.panelFit()));
  }
  const canvas = (label, compact) => h("div", { class: compact ? "graph compact" : "graph", tabindex: "0", role: "img", "aria-label": label });
  function nodeList(key, summary, items) {
    return h("details", { class: "node-list", open: !closedLists.has(key),
      on: { toggle: (event) => (event.currentTarget.open ? closedLists.delete(key) : closedLists.add(key)) } },
    h("summary", { dataset: { key: key + "-summary" } }, summary), h("ul", { class: "list" }, items));
  }
  const swatch = (label, attrs) => h("span", null, h("span", { class: "swatch", ...attrs }), label);
  const section = (title, ...content) => h("section", { class: "stack" }, h("h4", null, title), content);
  const itemButton = (key, content, handler, current) => h("button", { type: "button", class: "item", dataset: { key }, "aria-current": current ? "true" : null,
    on: { click: (event) => handler(event.currentTarget) } }, content);
  // forms.js registers the component forms. A build without it must not raise an unhandled rejection.
  const openForm = (name, context, trigger) => Panel.openForm(name, context, trigger)
    .catch(() => Panel.toast("This form is not part of this build of the control panel."));

  // Architecture.
  const LAYERS = [["code", "Code"], ["n8n", "Workflows"], ["authored", "Authored"]];
  const STATUS_WORDS = { blocked: "Blocked", review: "Review", in_progress: "In progress", guarded: "Guarded by a lesson", idle: "No active work" };
  const LANGUAGE_NAMES = { python: "Python", typescript: "TypeScript", javascript: "JavaScript", dart: "Dart", n8n: "n8n" };
  const SHAPES = { component: "round-rectangle", file: "rectangle", workflow: "round-hexagon", n8n_node: "round-rectangle", package: "hexagon",
    service: "barrel", dataset: "barrel", stakeholder: "ellipse", workstream: "round-pentagon", deliverable: "round-tag", process: "rhomboid" };
  const SHAPE_WORDS = { component: "code components are rounded rectangles", file: "files are rectangles", workflow: "workflows are hexagons",
    package: "packages are sand coloured hexagons", service: "services are barrels", stakeholder: "stakeholders are circles",
    workstream: "workstreams are pentagons", deliverable: "deliverables are tags", trigger: "triggers are triangles" };
  const FLAGS = { proposed: "Proposed and not yet confirmed by the user.", retired: "Retired by the user.",
    id_conflict: "The authored id also names a project folder, so the item has a separate id.",
    declared_not_imported: "Declared in a manifest but not imported by any source file.",
    imported_not_declared: "Imported by source files but not declared in a manifest.", trigger: "This node starts the workflow." };
  const SECONDARY = new Set(["package", "service"]);
  // Every node belongs to exactly one group, so the sentence, the item list and the graph state the same number.
  const GROUPS = ["code component", "file", "workflow", "workflow node", "authored item", "connected service", "package"];
  const GROUP_OF_KIND = { file: "file", workflow: "workflow", n8n_node: "workflow node", service: "connected service", package: "package" };
  const groupOf = (node) => GROUP_OF_KIND[node.kind] || (node.layer === "code" ? "code component" : "authored item");
  const FILE_COLOR = "#cfc9bf";
  const isProposed = (node) => node.authored_status === "proposed";
  const labelOf = (node) => clip((node.authored && node.authored.title) || node.title || node.id, 48);
  const shapeOf = (node) => ((node.flags || []).includes("trigger") ? "round-triangle" : SHAPES[node.kind] || "round-octagon");
  const colorOf = (node) => (node.kind === "package" ? PACKAGE_COLOR : toneColor(node.status));
  function sizeOf(node) {
    const n8n = node.n8n || {};
    if (SECONDARY.has(node.kind)) return Math.round(Math.min(56, 24 + Math.sqrt(node.files || n8n.workflows || 0) * 8));
    const measure = node.lines || (n8n.nodes ? n8n.nodes * 15 : 0) || (node.files || 0) * 10;
    return Math.round(Math.min(96, 28 + Math.sqrt(measure) * 4));
  }
  function kindLabel(node) {
    if (node.kind === "component") return node.layer === "code" ? "Code component" : Panel.term("component");
    return { file: "File", workflow: "Workflow", n8n_node: "Workflow node", package: "Package", service: "Service" }[node.kind] || words(node.kind);
  }
  const canOpenFiles = (node) => (node.kind === "component" && node.layer === "code") || node.kind === "workflow";
  const focusOf = (node) => node.id.replace(/^component:/, "");
  const available = (name, params) => Panel.live || Object.prototype.hasOwnProperty.call(Panel.snapshot.responses || {}, Panel.key(name, params));

  function nodeFacts(node) {
    const facts = [];
    const n8n = node.n8n || {};
    const names = (items) => joinList((items || []).map((item) => item.name || item.type || String(item)));
    if (node.kind === "component" && node.layer === "code" || node.kind === "file") {
      facts.push(`${Panel.count(node.files, "source file")} with ${Panel.count(node.lines, "line")} in ${joinList(node.languages.map((l) => LANGUAGE_NAMES[l] || words(l))) || "no known language"}.`);
    }
    if (node.kind === "workflow") {
      facts.push(`The workflow has ${Panel.count(n8n.nodes || 0, "node")}.`);
      if ((n8n.triggers || []).length) facts.push(`It starts from ${joinList(n8n.triggers)}.`);
      if (n8n.active === false) facts.push("The export marks the workflow as inactive.");
      if (n8n.unresolved_calls) facts.push(`${Panel.count(n8n.unresolved_calls, "call")} to other workflows could not be resolved.`);
    }
    if (node.kind === "n8n_node") {
      facts.push(`The node type is ${n8n.type}.`);
      if (n8n.disabled) facts.push("The node is disabled in the export.");
    }
    if (node.kind === "service") facts.push(`${Panel.count(n8n.workflows || 0, "workflow")} ${n8n.workflows === 1 ? "uses" : "use"} this service.`);
    if ((n8n.credentials || []).length) facts.push(`It uses the credentials ${names(n8n.credentials)}. Credential values are not stored.`);
    if (node.kind === "package") {
      facts.push(`The ecosystem is ${node.ecosystem}.`);
      for (const item of node.declared || []) facts.push(`${item.manifest} declares ${item.requirement || "no version"} in the group ${item.group}.`);
      facts.push(node.files ? `${Panel.count(node.files, "source file")} ${node.files === 1 ? "imports" : "import"} it.` : "No source file imports it.");
    }
    return facts.length ? h("ul", { class: "facts" }, facts.map((fact) => h("li", null, fact))) : null;
  }

  function architectureDetail(node, model, lookups, ctx, actions) {
    const card = h("section", { class: ["card", isProposed(node) && "proposed"] },
      h("h3", null, labelOf(node)),
      h("div", { class: "row" }, model.focus ? null : Panel.badge(node.status, STATUS_WORDS[node.status]), h("span", { class: "chip" }, kindLabel(node)),
        node.authored ? Panel.badge(node.authored.status) : null),
      node.path ? h("p", { class: "mono muted" }, node.path) : null,
      node.authored && node.authored.description ? h("p", null, node.authored.description) : null,
      nodeFacts(node));
    if (canOpenFiles(node) && !model.focus) {
      if (node.kind === "component") {
        const files = h("ul", { class: "list" }, h("li", { class: "muted" }, "The file list is loading."));
        card.append(section("Files", files));
        Panel.get("architecture", { level: "file", focus: focusOf(node) }).then((value) => {
          const found = value.nodes.filter((item) => item.kind === "file");
          files.replaceChildren(...found.map((item) => h("li", null, h("span", { class: "mono" }, item.path), " ", h("span", { class: "muted" }, Panel.count(item.lines, "line")))));
        }, (error) => files.replaceChildren(h("li", { class: "muted" }, error.notIncluded ? "The file list is not included in this snapshot." : "The file list could not be loaded.")));
      }
    }
    const flags = (node.flags || []).filter((flag) => flag !== "proposed");
    if (flags.length) card.append(section("Flags", h("ul", { class: "facts" }, flags.map((flag) => h("li", null, FLAGS[flag] || words(flag))))));
    if (node.work.length) {
      card.append(section(Panel.term("work_items"), h("ul", { class: "list" }, node.work.map((id) => {
        const item = lookups.work.get(id);
        return h("li", null, itemButton("arch-work-" + id, [h("div", { class: "row" }, Panel.badge(item ? item.state : "backlog")), h("strong", null, item ? item.title : id)],
          (trigger) => Panel.openWork(id, trigger)));
      })), node.work_total > node.work.length ? h("p", { class: "muted" }, `${node.work_total - node.work.length} more are attached.`) : null));
    }
    if (node.lessons.length) {
      card.append(section("Lessons", h("ul", { class: "list" }, node.lessons.map((id) => {
        const title = h("strong", null, "Lesson " + id.slice(-6));
        Panel.get("record", { id }).then((value) => { title.textContent = value.record.title; }, () => null);
        return h("li", null, itemButton("arch-lesson-" + id, title, (trigger) => Panel.openRecord(id, trigger)));
      }))));
    }
    const links = node.links.map((id) => {
      const edge = model.edges.find((item) => item.link_id === id) || lookups.links.get(id);
      if (!edge) return null;
      const outgoing = edge.from === node.id || (node.authored && edge.from === node.authored.id);
      const endpoint = outgoing ? edge.to : edge.from;
      const other = lookups.nodes.get(endpoint);
      const item = lookups.work.get(endpoint);
      // A work item is not a node of this graph, so its title comes from the work graph instead of its identifier.
      const name = other ? labelOf(other) : item ? item.title
        : endpoint.startsWith("episode_") ? Panel.term("work_item") + " " + endpoint.slice(-6) : endpoint;
      const reason = (lookups.links.get(id) || {}).reason;
      return h("li", null, outgoing ? `This item ${words(edge.type).toLowerCase()} ${name}.` : `${name} ${words(edge.type).toLowerCase()} this item.`,
        reason ? h("p", { class: "muted" }, reason) : null);
    }).filter(Boolean);
    if (links.length) card.append(section("Links", h("ul", { class: "list" }, links)));
    const buttons = h("div", { class: "row" });
    if (canOpenFiles(node) && !model.focus) {
      if (available("architecture", { focus: focusOf(node), level: "file" })) {
        buttons.append(h("button", { type: "button", dataset: { key: "arch-open-" + node.id }, on: { click: () => actions.openFiles(node.id) } },
          node.kind === "workflow" ? "Open workflow nodes" : "Open files"));
      } else buttons.append(h("p", { class: "muted" }, "The file level is available in the live control panel."));
    }
    if (node.authored && ctx.canEdit) {
      if (isProposed(node)) {
        buttons.append(h("button", { type: "button", class: "primary", dataset: { key: "arch-confirm-" + node.id },
          on: { click: (event) => openForm("confirm_component", { component_id: node.authored.id }, event.currentTarget) } }, "Confirm"));
      }
      buttons.append(h("button", { type: "button", class: "quiet", dataset: { key: "arch-edit-" + node.id },
        on: { click: (event) => openForm("component", { component_id: node.authored.id }, event.currentTarget) } }, "Edit"));
    }
    if (node.kind === "package") {
      buttons.append(h("button", { type: "button", class: "quiet", on: { click: () => Panel.go("dependencies", { tab: "packages", q: node.title }) } }, "Show in the package table"));
    }
    if (buttons.childNodes.length) card.append(buttons);
    return card;
  }

  Panel.registerView("architecture", { title: "Architecture", async render(container, params, ctx) {
    const focus = params.focus || "";
    const update = (changes) => { Panel.setParams({ ...params, ...changes }); Panel.refresh(); };
    const head = h("div", { class: "view-head" });
    container.append(head);
    const back = () => h("button", { type: "button", id: "arch-back", on: { click: () => update({ focus: "", label: "", selected: params.from || "", from: "" }) } }, "Back to the overview");
    let model;
    try {
      model = await (focus ? Panel.get("architecture", { focus, level: "file" }) : Panel.get("architecture"));
    } catch (error) {
      if (!focus) throw error;
      head.append(h("h2", null, "Files in " + (params.label || focus)), back());
      container.append(Panel.errorState(error));
      return;
    }
    const [workGraph, authored] = await Promise.all([Panel.get("work_graph").catch(() => null), Panel.get("components").catch(() => null)]);
    const lookups = { nodes: new Map(model.nodes.map((node) => [node.id, node])), work: new Map(((workGraph || {}).nodes || []).map((item) => [item.id, item])), links: new Map() };
    for (const item of (authored || {}).components || []) for (const link of item.links || []) lookups.links.set(link.id, link);
    const allLayers = LAYERS.map((layer) => layer[0]);
    const shownLayers = focus ? allLayers : params.layers ? params.layers.split(",") : allLayers;
    const showPackages = params.packages === "1";
    const query = (params.q || "").toLowerCase();
    const filtered = Boolean(params.language || params.status || query);
    const matches = (node) => !query || [node.title, node.path, node.id, node.authored && node.authored.title].some((value) => value && String(value).toLowerCase().includes(query));
    const visible = new Set();
    for (const node of model.nodes) {
      if (SECONDARY.has(node.kind) || !shownLayers.includes(node.layer)) continue;
      if ((!params.language || (node.languages || []).includes(params.language)) && (!params.status || node.status === params.status) && matches(node)) visible.add(node.id);
    }
    for (const node of model.nodes) {
      if (!SECONDARY.has(node.kind) || !shownLayers.includes(node.layer) || (node.kind === "package" && !showPackages)) continue;
      const connected = model.edges.some((edge) => (edge.from === node.id && visible.has(edge.to)) || (edge.to === node.id && visible.has(edge.from)));
      if (!filtered || connected || (query && matches(node))) visible.add(node.id);
    }
    const nodes = model.nodes.filter((node) => visible.has(node.id));
    const edges = model.edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to) && (focus || shownLayers.includes(edge.layer)));
    const elements = [
      ...nodes.map((node) => ({ group: "nodes", data: { id: node.id, label: labelOf(node), size: sizeOf(node), shape: shapeOf(node),
        color: focus ? FILE_COLOR : colorOf(node) }, classes: isProposed(node) ? "dashed" : "" })),
      ...edges.map((edge, index) => ({ group: "edges", data: { id: "arch-edge-" + index, source: edge.from, target: edge.to,
        width: Math.min(8, 1 + Math.log2(Math.max(1, edge.weight || 1)) * 1.5) }, classes: edge.link_id ? "link" : "" }))];

    // Heading, sentence and filters.
    const workflowFocus = focus.startsWith("n8n:");
    head.append(h("h2", null, focus ? (workflowFocus ? "Nodes in " : "Files in ") + (params.label || focus) : Panel.term("architecture_legend")));
    if (focus) head.append(back());
    else if (ctx.canEdit) head.append(h("button", { type: "button", class: "primary", id: "arch-add", on: { click: (event) => openForm("component", {}, event.currentTarget) } }, "Add " + Panel.term("component").toLowerCase()));
    const parts = [];
    for (const name of GROUPS) {
      const found = nodes.filter((node) => groupOf(node) === name).length;
      if (found) parts.push(Panel.count(found, name));
    }
    let sentence = parts.length ? "This view shows " + joinList(parts) + "." : "No structure matches the current filters.";
    // A file takes the state of the work item whose paths cover its folder, so the file level states no work state.
    const blocked = focus ? 0 : nodes.filter((node) => node.status === "blocked").length;
    if (blocked) sentence += blocked === 1 ? " 1 of them has blocked work." : ` ${blocked} of them have blocked work.`;
    const proposed = nodes.filter(isProposed).length;
    if (proposed) sentence += ` ${Panel.count(proposed, "item")} ${proposed === 1 ? "awaits" : "await"} your confirmation.`;
    // Packages have their own toggle, so an unticked package is not reported as hidden by a filter nobody set.
    const hidden = model.nodes.length - nodes.length - (showPackages ? 0 : model.nodes.filter((node) => node.kind === "package").length);
    if (hidden > 0) sentence += ` ${Panel.count(hidden, "item")} ${hidden === 1 ? "is" : "are"} hidden by the filters.`;
    container.append(h("p", { class: "sentence" }, sentence));
    if (!model.nodes.length && !focus) {
      container.append(Panel.empty(`No structure is recorded yet. Add ${Panel.term("components").toLowerCase()}, source files or exported n8n workflows to the project.`));
      return;
    }

    const toolbar = h("div", { class: "toolbar" });
    // An empty layer, and a single layer, offer no choice, so they are not shown as controls.
    const inLayer = (layer) => model.nodes.filter((node) => node.layer === layer && node.kind !== "package").length;
    const usable = LAYERS.filter(([layer]) => inLayer(layer));
    if (!focus && usable.length > 1) {
      toolbar.append(h("fieldset", { class: "layer-toggles" }, h("legend", null, "Layers"), h("div", { class: "row" }, usable.map(([layer, label]) =>
        Panel.field(`${label} (${inLayer(layer)})`, h("input", { type: "checkbox", id: "arch-layer-" + layer, checked: shownLayers.includes(layer),
          on: { change: (event) => {
            const next = allLayers.filter((name) => (name === layer ? event.target.checked : shownLayers.includes(name)));
            update({ layers: next.length === allLayers.length ? "" : next.join(",") || "none" });
          } } }))))));
    }
    const packages = model.nodes.filter((node) => node.kind === "package").length;
    if (packages) {
      toolbar.append(Panel.field(`Show packages (${packages})`, h("input", { type: "checkbox", id: "arch-packages", checked: showPackages,
        on: { change: (event) => update({ packages: event.target.checked ? "1" : "" }) } })));
    }
    const languages = [...new Set(model.nodes.flatMap((node) => node.languages || []))].sort();
    // With one language, or none, the filter cannot change what the view shows.
    if (languages.length > 1) {
      toolbar.append(Panel.field("Language", Panel.select("language", [["", "All languages"], ...languages.map((language) => [language, LANGUAGE_NAMES[language] || words(language)])],
        params.language || "", { id: "arch-language", on: { change: (event) => update({ language: event.target.value }) } })));
    }
    toolbar.append(Panel.field("Status", Panel.select("status", [["", "All statuses"], ...Object.entries(STATUS_WORDS)], params.status || "",
      { id: "arch-status", on: { change: (event) => update({ status: event.target.value }) } })),
    Panel.field("Search", Panel.input("q", params.q, { type: "search", id: "arch-search", placeholder: "Name or path",
      on: { change: (event) => update({ q: event.target.value.trim() }) } })));
    container.append(toolbar);

    // Graph, legend and side panel.
    const resolve = (id) => lookups.nodes.get(id) || model.nodes.find((node) => node.authored && node.authored.id === id);
    const host = canvas(`Architecture graph with ${Panel.count(nodes.length, "item")}. The list next to the graph offers the same items for keyboard use.`);
    const side = h("aside", { class: "graph-side", "aria-label": "Selected item and item list" });
    let cy = null;
    const actions = {
      openFiles: (id) => {
        const node = lookups.nodes.get(id);
        if (node && canOpenFiles(node) && !focus) update({ focus: focusOf(node), label: labelOf(node), from: node.id, selected: "", q: "", language: "", status: "" });
      },
    };
    const drawSide = () => {
      const selected = params.selected ? resolve(params.selected) : null;
      const ordered = [...nodes].sort((a, b) => (SECONDARY.has(a.kind) - SECONDARY.has(b.kind)) || labelOf(a).localeCompare(labelOf(b)));
      side.replaceChildren(selected ? architectureDetail(selected, model, lookups, ctx, actions)
        : h("p", { class: "muted" }, "Select an item in the graph or in the list to see its files, work, lessons and links."),
      nodeList("arch-list", `All ${Panel.count(nodes.length, "item")} in the graph`, ordered.map((node) => h("li", null,
        itemButton("arch-node-" + node.id, [h("div", { class: "row" }, focus ? null : Panel.badge(node.status, STATUS_WORDS[node.status]), h("span", { class: "muted" }, kindLabel(node))),
          h("strong", null, labelOf(node))], () => select(node.id, true), selected && selected.id === node.id)))));
    };
    const select = (id, fromList) => {
      const node = resolve(id);
      params.selected = node ? node.id : "";
      Panel.setParams(params);
      if (cy) {
        cy.$(":selected").unselect();
        const element = node ? cy.getElementById(node.id) : null;
        if (element && element.length) { element.select(); if (fromList) cy.center(element); }
      }
      drawSide();
    };
    const shapes = [...new Set(nodes.map((node) => ((node.flags || []).includes("trigger") ? "trigger" : node.kind)))].map((kind) => SHAPE_WORDS[kind]).filter(Boolean);
    // The explanation names only what this project holds. A stakeholder map has no lines of code and no imports.
    const hasCode = model.nodes.some((node) => node.layer === "code" && node.kind !== "package");
    const hasFlows = model.nodes.some((node) => node.layer === "n8n");
    const facts = [];
    if (hasCode) facts.push("Node size follows the number of lines, and edge width follows the number of imports.");
    if (hasFlows) facts.push("A workflow is sized by the number of steps it holds.");
    if (!hasCode && !hasFlows) facts.push("Every item here is recorded by you or by an agent, so all items are drawn at the same size.");
    if (shapes.length) facts.push(words(joinList(shapes)) + ".");
    facts.push("Dashed edges are links that people or agents recorded.");
    const sources = [];
    if (hasCode) sources.push("Imports are read statically from source files. Dynamic loading, import hooks, build path aliases and generated code are not detected. The name a manifest declares can differ from the name that source files import.");
    if (hasFlows) sources.push("Workflows, their steps and their connections are read from exported n8n JSON files in the project. Credential values are never read.");
    if (!sources.length) sources.push("Every item in this view was recorded by you or by an agent. This project holds no source files and no exported workflows to read.");
    const legend = h("div", { class: "stack" },
      focus ? h("p", { class: "muted" }, "Files carry no work state here. A work item allows a path pattern such as a whole folder, so its state would mark every file inside that folder.")
        : h("div", { class: "legend", role: "list", "aria-label": "Status colours" }, Object.entries(STATUS_WORDS).map(([state, label]) =>
          h("span", { role: "listitem" }, h("span", { class: "swatch", dataset: { tone: Panel.tone(state) } }), label)),
        packages ? swatch("Package", { class: "swatch package" }) : null, swatch("Proposed, not yet confirmed", { class: "swatch dashed" })),
      h("p", { class: "muted" }, facts.join(" ")));
    const notes = h("div", { class: "stack" }, model.truncated ? h("p", { class: "notice" }, "The project has more source files than the reading limit, so this structure is incomplete.") : null,
      (model.issues || []).length ? h("p", { class: "muted" }, `${Panel.count(model.issues.length, "file")} could not be read completely.`) : null,
      h("p", { class: "muted" }, sources.join(" ")));
    const main = h("div", { class: "stack" }, host, legend, notes);
    container.append(h("div", { class: "graph-layout" }, main, side));
    if (!nodes.length) host.replaceWith(Panel.empty("No item matches the current filters and layers."));
    else {
      cy = graph(host, elements, { key: "architecture|" + focus + "|" + params.layers + "|" + params.packages, onTap: (id) => select(id), onDouble: actions.openFiles });
      if (cy) main.insertBefore(controls(cy, "arch"), host);
      else host.replaceWith(Panel.empty("The graph library is not available. Use the item list instead."));
    }
    if (cy && params.selected && resolve(params.selected)) cy.getElementById(resolve(params.selected).id).select();
    drawSide();
  } });

  // Dependencies.
  const ITEM_SHAPES = { phase: "round-rectangle", epic: "round-hexagon", story: "ellipse", research: "diamond", deliverable: "round-tag", workflow: "round-pentagon" };
  function blockedChains(byId, arrows) {
    const into = new Map();
    const out = new Map();
    for (const arrow of arrows) {
      if (!into.has(arrow.target)) into.set(arrow.target, []);
      if (!out.has(arrow.source)) out.set(arrow.source, []);
      into.get(arrow.target).push(arrow);
      out.get(arrow.source).push(arrow);
    }
    const edges = new Set();
    const nodes = new Set();
    const walk = (start, map, next, keepGoing) => {
      const seen = new Set([start]);
      for (const pending = [start]; pending.length;) {
        for (const arrow of map.get(pending.pop()) || []) {
          edges.add(arrow.id);
          nodes.add(arrow[next]);
          if (!seen.has(arrow[next]) && keepGoing(arrow[next])) { seen.add(arrow[next]); pending.push(arrow[next]); }
        }
      }
    };
    for (const item of byId.values()) {
      if (item.state !== "blocked") continue;
      nodes.add(item.id);
      walk(item.id, into, "source", (id) => byId.get(id).state === "blocked");
      walk(item.id, out, "target", () => true);
    }
    const depth = new Map([...nodes].map((id) => [id, 0]));
    for (let round = 0; round < nodes.size; round++) {
      for (const arrow of arrows) if (edges.has(arrow.id)) depth.set(arrow.target, Math.max(depth.get(arrow.target), depth.get(arrow.source) + 1));
    }
    const groups = [];
    const placed = new Set();
    for (const id of nodes) {
      if (placed.has(id)) continue;
      const group = [];
      for (const pending = [id]; pending.length;) {
        const current = pending.pop();
        if (placed.has(current)) continue;
        placed.add(current);
        group.push(current);
        for (const arrow of arrows) {
          if (!edges.has(arrow.id)) continue;
          if (arrow.source === current) pending.push(arrow.target);
          if (arrow.target === current) pending.push(arrow.source);
        }
      }
      groups.push(group.sort((a, b) => depth.get(a) - depth.get(b)));
    }
    return { edges, nodes, groups };
  }

  async function workTab(panel, params, update) {
    const data = await Panel.get("work_graph");
    const item = Panel.term("work_item").toLowerCase();
    const byId = new Map(data.nodes.map((node) => [node.id, node]));
    // An arrow points from the prerequisite to the work item that waits for it.
    const arrows = data.edges.map((edge) => (edge.type === "blocks" ? { id: edge.id, source: edge.from, target: edge.to } : { id: edge.id, source: edge.to, target: edge.from }))
      .filter((arrow) => byId.has(arrow.source) && byId.has(arrow.target));
    const chains = blockedChains(byId, arrows);
    const linked = new Set(arrows.flatMap((arrow) => [arrow.source, arrow.target]));
    const query = (params.q || "").toLowerCase();
    const shown = data.nodes.filter((node) => (params.all === "1" || linked.has(node.id)) && (!params.state || node.state === params.state)
      && (!params.type || node.item_type === params.type) && (!query || node.title.toLowerCase().includes(query)));
    const ids = new Set(shown.map((node) => node.id));
    const visibleArrows = arrows.filter((arrow) => ids.has(arrow.source) && ids.has(arrow.target));
    const blocked = data.nodes.filter((node) => node.state === "blocked").length;
    let sentence = arrows.length ? `${Panel.count(arrows.length, "dependency", "dependencies")} ${arrows.length === 1 ? "connects" : "connect"} ${Panel.count(linked.size, item)}.`
      : `No dependencies between ${item}s are recorded.`;
    sentence += blocked ? ` ${Panel.count(blocked, item)} ${blocked === 1 ? "is" : "are"} blocked, and ${Panel.count(chains.groups.length, "blocked chain")} ${chains.groups.length === 1 ? "is" : "are"} marked in red.`
      : ` No ${item} is blocked.`;
    const unlinked = data.nodes.length - linked.size;
    const states = [...new Set(data.nodes.map((node) => node.state))];
    const types = [...new Set(data.nodes.map((node) => node.item_type))];
    panel.append(h("p", { class: "sentence" }, sentence),
      h("div", { class: "toolbar" },
        Panel.field("State", Panel.select("state", [["", "All states"], ...states], params.state || "", { id: "dep-state", on: { change: (event) => update({ state: event.target.value }) } })),
        Panel.field("Type", Panel.select("type", [["", "All types"], ...types], params.type || "", { id: "dep-type", on: { change: (event) => update({ type: event.target.value }) } })),
        Panel.field("Search", Panel.input("q", params.q, { type: "search", id: "dep-search", placeholder: "Title", on: { change: (event) => update({ q: event.target.value.trim() }) } })),
        unlinked ? Panel.field(`Show ${item}s without dependencies (${unlinked})`, h("input", { type: "checkbox", id: "dep-all", checked: params.all === "1",
          on: { change: (event) => update({ all: event.target.checked ? "1" : "" }) } })) : null));
    const host = canvas(`Dependency graph with ${Panel.count(shown.length, item)}. The list next to the graph offers the same items for keyboard use.`);
    const main = h("div", { class: "stack" }, host);
    const open = (id, trigger) => Panel.openWork(id, trigger);
    const entry = (id, key) => {
      const node = byId.get(id);
      return h("li", null, itemButton(key + id, [h("div", { class: "row" }, Panel.badge(node.state), h("span", { class: "muted" }, words(node.item_type))), h("strong", null, node.title)],
        (trigger) => open(id, trigger)));
    };
    const side = h("aside", { class: "graph-side", "aria-label": "Blocked chains and item list" },
      h("div", { class: "legend", role: "list", "aria-label": "State colours" }, ["blocked", "review", "in_progress", "ready", "backlog", "done"].map((state) =>
        h("span", { role: "listitem" }, h("span", { class: "swatch", dataset: { tone: Panel.tone(state) } }), words(state)))),
      h("p", { class: "muted" }, "Arrows point from a prerequisite to the item that waits for it. Red arrows and borders mark blocked chains."),
      chains.groups.length ? h("section", { class: "card" }, h("h3", null, "Blocked chains"), h("ol", { class: "stack chains" }, chains.groups.map((group, index) =>
        h("li", null, h("p", { class: "muted" }, `Chain ${index + 1} connects ${Panel.count(group.length, item)} in order of dependency.`),
          h("ul", { class: "list" }, group.map((id) => entry(id, "dep-chain-" + index + "-"))))))) : null,
      nodeList("dep-list", `All ${Panel.count(shown.length, item)} in the graph`, shown.map((node) => entry(node.id, "dep-node-"))));
    panel.append(h("div", { class: "graph-layout" }, main, side));
    if (!shown.length) { host.replaceWith(Panel.empty(`No ${item} matches the current filters.`)); return; }
    const elements = [
      ...shown.map((node) => ({ group: "nodes", data: { id: node.id, label: clip(node.title, 40), size: node.item_type === "phase" ? 40 : 30,
        shape: ITEM_SHAPES[node.item_type] || "ellipse", color: toneColor(node.state) },
      classes: "state-" + node.state + (chains.nodes.has(node.id) ? " chain" : "") })),
      ...visibleArrows.map((arrow, index) => ({ group: "edges", data: { id: "dep-edge-" + index, source: arrow.source, target: arrow.target, width: 1.5 },
        classes: chains.edges.has(arrow.id) ? "chain" : "" }))];
    const cy = graph(host, elements, { key: "dependencies|" + [params.state, params.type, params.q, params.all].join("|"), onTap: (id) => open(id, host) });
    if (cy) main.insertBefore(controls(cy, "dep"), host);
    else host.replaceWith(Panel.empty("The graph library is not available. Use the item list instead."));
  }

  async function packagesTab(panel, params, update, model) {
    const rows = [];
    for (const node of model.nodes.filter((entry) => entry.kind === "package")) {
      const declared = (node.declared || []).length ? node.declared : [{ requirement: null, group: null, manifest: null }];
      for (const item of declared) rows.push({ node, requirement: item.requirement, group: item.group, manifest: item.manifest });
    }
    const packages = model.packages || { declared: [], manifests: [], issues: [] };
    if (!rows.length) {
      panel.append(Panel.empty("No packages are declared or imported in this project. Packages appear here when the project has manifests such as package.json, requirements.txt, pyproject.toml or pubspec.yaml."));
      return;
    }
    const query = (params.q || "").toLowerCase();
    const shown = rows.filter((row) => (!params.ecosystem || row.node.ecosystem === params.ecosystem) && (!params.group || row.group === params.group)
      && (!params.flag || (params.flag === "flagged" ? row.node.flags.length : row.node.flags.includes(params.flag))) && (!query || row.node.title.toLowerCase().includes(query)));
    const flagged = model.nodes.filter((node) => node.kind === "package" && node.flags.length).length;
    const manifests = packages.manifests || [];
    const row = (item) => h("tr", null,
      h("th", { scope: "row", class: "mono" }, item.node.title), h("td", null, item.node.ecosystem), h("td", { class: "mono" }, item.requirement || "Not declared"),
      h("td", null, item.group || "None"), h("td", { class: "mono" }, item.manifest || "None"),
      h("td", null, item.node.files ? Panel.count(item.node.files, "file") : "No file"),
      h("td", null, item.node.flags.length ? item.node.flags.map((flag) => h("span", { class: "chip", dataset: { tone: "review" }, title: FLAGS[flag] || "" }, words(flag))) : "None"));
    const table = h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("caption", { class: "visually-hidden" }, `Declared and imported packages, ${shown.length} shown.`),
      h("thead", null, h("tr", null, ["Package", "Ecosystem", "Requirement", "Group", "Manifest", "Imported by", "Flags"].map((label) => h("th", { scope: "col" }, label)))),
      h("tbody", null, shown.map(row))));
    panel.append(h("p", { class: "sentence" }, `The project declares ${Panel.count((packages.declared || []).length, "package")} in ${Panel.count(manifests.length, "manifest")}.` +
      (flagged ? ` ${Panel.count(flagged, "package")} ${flagged === 1 ? "has" : "have"} a flag.` : " No package has a flag.")),
    h("div", { class: "toolbar" },
      Panel.field("Ecosystem", Panel.select("ecosystem", [["", "All ecosystems"], ...[...new Set(rows.map((row) => row.node.ecosystem))].map((value) => [value, value])],
        params.ecosystem || "", { id: "pkg-ecosystem", on: { change: (event) => update({ ecosystem: event.target.value }) } })),
      Panel.field("Group", Panel.select("group", [["", "All groups"], ...[...new Set(rows.map((row) => row.group).filter(Boolean))].map((value) => [value, value])],
        params.group || "", { id: "pkg-group", on: { change: (event) => update({ group: event.target.value }) } })),
      Panel.field("Flags", Panel.select("flag", [["", "All packages"], ["flagged", "Packages with a flag"], ["declared_not_imported", "Declared but not imported"],
        ["imported_not_declared", "Imported but not declared"]], params.flag || "", { id: "pkg-flag", on: { change: (event) => update({ flag: event.target.value }) } })),
      Panel.field("Search", Panel.input("q", params.q, { type: "search", id: "pkg-search", placeholder: "Package name", on: { change: (event) => update({ q: event.target.value.trim() }) } }))),
    shown.length ? table : Panel.empty("No package matches the current filters."),
    manifests.length ? section("Manifests", h("ul", { class: "facts" }, manifests.map((item) => h("li", null, h("span", { class: "mono" }, item.path), ` declares ${Panel.count(item.count, "package")}.`)))) : null,
    section("Manifest issues", (packages.issues || []).length ? h("ul", { class: "facts" }, packages.issues.map((issue) => h("li", null, typeof issue === "string" ? issue
      : (issue.manifest ? issue.manifest + ": " : "") + issue.message))) : h("p", { class: "muted" }, "Every manifest was read without issues.")),
    h("p", { class: "muted" }, "Packages are read from manifest files and from static imports in source files."));
  }

  Panel.registerView("dependencies", { title: "Dependencies", async render(container, params) {
    // A project without manifests can never fill the package table, so that tab appears only where packages exist.
    const model = await Panel.get("architecture").catch(() => null);
    const packages = Boolean(model && model.nodes.some((node) => node.kind === "package"));
    const tab = packages && params.tab === "packages" ? "packages" : "work";
    const update = (changes) => { Panel.setParams({ ...params, ...changes }); Panel.refresh(); };
    const tabButton = (name, label) => h("button", { type: "button", role: "tab", id: "dep-tab-" + name, "aria-selected": String(tab === name), "aria-controls": "dep-panel",
      on: { click: () => { Panel.setParams(name === "packages" ? { tab: name } : {}); Panel.refresh(); } } }, label);
    const panel = h("div", { id: "dep-panel", class: "stack", role: "tabpanel", "aria-labelledby": packages ? "dep-tab-" + tab : null });
    if (packages) {
      container.append(h("div", { class: "tabs", role: "tablist", "aria-label": "Dependency views" },
        tabButton("work", Panel.term("work_item") + " dependencies"), tabButton("packages", "Packages")));
    }
    container.append(panel);
    await (tab === "packages" ? packagesTab(panel, params, update, model) : workTab(panel, params, update));
  } });

  // Lineage.
  const LINEAGE_RANK = { direction: 0, source: 0, episode: 1, component: 1, service: 1, package: 1, work_plan: 2, sprint: 2, research: 2, note: 2, decision: 3,
    action: 4, action_result: 4, check: 4, receipt: 4, outcome: 5, follow_up: 5, correction: 5, lesson: 6, lesson_review: 7 };
  const RANK_TITLES = ["Requirements and evidence", "Work items and components", "Plans, research and other records", "Decisions", "Actions and checks",
    "Outcomes and follow ups", "Lessons", "Lesson reviews"];
  const LINEAGE_SHAPES = { direction: "round-tag", source: "rectangle", episode: "round-rectangle", work_plan: "round-rectangle", decision: "diamond",
    outcome: "round-triangle", lesson: "star", lesson_review: "star", check: "barrel", receipt: "barrel", component: "round-hexagon" };
  let lineageMode = null;
  const rankOf = (kind) => (kind in LINEAGE_RANK ? LINEAGE_RANK[kind] : 2);
  const lineageKind = (kind) => ({ episode: Panel.term("work_item"), direction: "Project requirements", work_plan: "Plan", check: "Agent check", receipt: "Host receipt" }[kind] || words(kind));

  function openNode(node, trigger) {
    if (!node || node.kind === "missing") { Panel.toast("This record is not available."); return; }
    const id = node.id;
    if (id.startsWith("episode_")) return Panel.openWork(id, trigger);
    if (id.startsWith("check_")) {
      try { return Panel.openDrawer("run", { id }, trigger); } catch (error) { return Panel.go("agents"); }
    }
    if (id.startsWith("component:") || id.startsWith("service:")) return Panel.go("architecture", { selected: id });
    if (id.startsWith("package:")) return Panel.go("dependencies", { tab: "packages", q: node.title });
    return Panel.openRecord(id, trigger);
  }

  function lineageElements(nodes, edges, focusId) {
    const rows = new Map();
    for (const node of nodes) {
      const rank = rankOf(node.kind);
      if (!rows.has(rank)) rows.set(rank, []);
      rows.get(rank).push(node);
    }
    const elements = [];
    let y = 0;
    for (const rank of [...rows.keys()].sort((a, b) => a - b)) {
      const items = rows.get(rank);
      for (let start = 0; start < items.length; start += 6) {
        const line = items.slice(start, start + 6);
        line.forEach((node, index) => elements.push({ group: "nodes", position: { x: (index - (line.length - 1) / 2) * 170 + (start / 6) % 2 * 40, y },
          data: { id: node.id, label: clip(node.title || node.id, 40), size: node.id === focusId ? 40 : 28, shape: LINEAGE_SHAPES[node.kind] || "ellipse",
            color: toneColor(node.status) }, classes: node.id === focusId ? "focus" : "" }));
        y += 90;
      }
      y += 30;
    }
    const ids = new Set(nodes.map((node) => node.id));
    edges.filter((edge) => ids.has(edge.from) && ids.has(edge.to)).forEach((edge, index) =>
      elements.push({ group: "edges", data: { id: "lineage-edge-" + index, source: edge.from, target: edge.to, width: 1.5 } }));
    return elements;
  }

  function lineageList(nodes, edges, byId, focusId, key, open) {
    const related = new Map();
    for (const edge of edges) {
      for (const [self, other] of [[edge.from, edge.to], [edge.to, edge.from]]) {
        if (!related.has(self)) related.set(self, []);
        related.get(self).push(`${clip((byId.get(other) || { title: other }).title || other, 60)} (${words(edge.type).toLowerCase()})`);
      }
    }
    const groups = new Map();
    for (const node of [...nodes].sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")))) {
      const rank = rankOf(node.kind);
      if (!groups.has(rank)) groups.set(rank, []);
      groups.get(rank).push(node);
    }
    return h("div", { class: "stack lineage-list" }, [...groups].sort((a, b) => a[0] - b[0]).map(([rank, items]) => h("section", { class: "stack" },
      h("h4", null, RANK_TITLES[rank]), h("ul", { class: "list" }, items.map((node) => {
        const links = related.get(node.id) || [];
        return h("li", null, itemButton(key + "-item-" + node.id, [
          h("div", { class: "row" }, Panel.badge(node.status), h("span", { class: "muted" }, lineageKind(node.kind)), node.id === focusId ? h("span", { class: "chip" }, "This record") : null),
          h("strong", null, node.title || node.id),
          links.length ? h("p", { class: "muted" }, "Connected to " + joinList(links.slice(0, 3)) + (links.length > 3 ? `, and ${links.length - 3} more` : "") + ".") : null],
        (trigger) => open(node, trigger)));
      })))));
  }

  function drawLineage(root, data, focusId, options) {
    const nodes = data.nodes || [];
    const edges = data.edges || [];
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const key = "lineage-" + focusId;
    // Dozens of records cannot be read as one small picture, so a large lineage opens as the list until the reader asks for the graph.
    const dense = nodes.length > 24;
    const mode = typeof cytoscape !== "function" ? "list" : lineageMode || (dense ? "list" : "graph");
    const open = options.open || openNode;
    const toggle = h("button", { type: "button", class: "small", dataset: { key: key + "-toggle" },
      on: { click: () => {
        lineageMode = mode === "graph" ? "list" : "graph";
        drawLineage(root, data, focusId, options);
        const next = root.querySelector('[data-key="' + CSS.escape(key + "-toggle") + '"]');
        if (next) next.focus();
      } } }, mode === "list" ? "Show as graph" : "Show as list");
    root.replaceChildren(h("div", { class: "graph-head" }, h("h3", null, options.title || "Lineage"),
      h("span", { class: "muted" }, `${Panel.count(nodes.length, "record")} and ${Panel.count(edges.length, "connection")}.`), mode === "list" && typeof cytoscape !== "function" ? null : toggle));
    if (data.truncated) root.append(h("p", { class: "muted" }, "The lineage stops at a size limit. Open a connected record to follow its own lineage."));
    if (nodes.length <= 1) { root.append(Panel.empty("No connected records are recorded for this item.")); return; }
    if (mode === "list") { root.append(lineageList(nodes, edges, byId, focusId, key, open)); return; }
    const host = canvas(`Lineage graph of ${Panel.count(nodes.length, "record")}. Select Show as list for a readable version.`,
      options.compact && nodes.length <= 12);
    root.append(host);
    const cy = graph(host, lineageElements(nodes, edges, focusId), { key, layout: { name: "preset" }, onTap: (id) => open(byId.get(id), host) });
    root.append(controls(cy, key), h("p", { class: "muted" }, "Rows run from requirements and evidence at the top to outcomes and lessons at the bottom. The black border marks this record."));
  }

  function lineageGraph(container, focusId, options = {}) {
    const root = h("section", { class: "stack lineage" });
    container.append(root);
    const load = options.data ? Promise.resolve(options.data) : Panel.get("lineage", { id: focusId });
    return load.then((data) => { drawLineage(root, data, focusId, options); return root; },
      (error) => { root.replaceChildren(h("h3", null, options.title || "Lineage"), Panel.errorState(error)); return root; });
  }

  Panel.graph = graph;
  Panel.lineageGraph = lineageGraph;
  // The live graphs of the open page, so a browser check can measure the drawn nodes and their labels.
  Panel.graphs = () => [...instances].filter((cy) => !cy.destroyed() && cy.container() && cy.container().isConnected);
})();
