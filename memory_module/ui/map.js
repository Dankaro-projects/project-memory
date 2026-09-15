const mapState = {
  work: "",
  focus: "",
  mode: "relationships",
  model: null,
  workVersion: null,
  zoom: 1,
  x: 0,
  y: 0,
  positions: {},
  showRetired: false,
};
function openProjectMap(id) {
  mapState.work = id;
  mapState.focus = id;
  setView("map");
}
async function renderMap() {
  const request = ++renderVersion,
    root = el("extension-view");
  const work = data.live
    ? await api("board", { limit: 100 })
    : { cards: data.work || [] };
  if (view !== "map" || request !== renderVersion) return;
  root.replaceChildren();
  const controls = document.createElement("div");
  controls.className = "extension-toolbar";
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Map work item");
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "Select work";
  select.append(none);
  for (const item of work.cards) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.title;
    select.append(option);
    workById.set(item.id, item);
  }
  if (
    mapState.work &&
    !work.cards.some((w) => w.id === mapState.work) &&
    data.live
  ) {
    const result = await api("board", { episode: mapState.work, limit: 1 });
    if (result.cards[0]) {
      const o = document.createElement("option");
      o.value = mapState.work;
      o.textContent = result.cards[0].title;
      select.append(o);
      workById.set(o.value, result.cards[0]);
    }
  }
  select.value = mapState.work;
  select.addEventListener("change", () => {
    mapState.work = select.value;
    mapState.focus = select.value;
    mapState.x = 0;
    mapState.y = 0;
    renderMap().catch(disconnected);
  });
  controls.append(select);
  if (work.more) {
    let offset = 100;
    const more = actionButton("Load more work", async () => {
      const result = await api("board", { limit: 100, offset });
      for (const item of result.cards) {
        const o = document.createElement("option");
        o.value = item.id;
        o.textContent = item.title;
        select.append(o);
        workById.set(item.id, item);
      }
      offset += result.cards.length;
      more.hidden = !result.more;
    });
    controls.append(more);
  }
  const tabs = document.createElement("div");
  tabs.className = "view-switch";
  for (const [mode, label] of [
    ["relationships", "Relationships"],
    ["workflow", "Workflow"],
    ["architecture", "Architecture"],
  ]) {
    const b = actionButton(label, () => {
      mapState.mode = mode;
      mapState.x = 0;
      mapState.y = 0;
      return renderMap();
    });
    b.setAttribute("aria-pressed", String(mapState.mode === mode));
    tabs.append(b);
  }
  controls.append(tabs);
  root.append(controls);
  if (!mapState.work) {
    const p = document.createElement("p");
    p.className = "extension-empty";
    p.textContent =
      "Select work to inspect its relationships or plan a diagram.";
    root.append(p);
    return;
  }
  if (mapState.mode !== "relationships") {
    const label = document.createElement("label"),
      input = document.createElement("input");
    input.type = "checkbox";
    input.checked = mapState.showRetired;
    input.addEventListener("change", () => {
      mapState.showRetired = input.checked;
      renderMap().catch(disconnected);
    });
    label.append(input, document.createTextNode("Show retired"));
    controls.append(label);
  }
  let graph;
  if (mapState.mode === "relationships") {
    graph = data.live
      ? await api("relationships", {
          id: mapState.focus || mapState.work,
          limit: 40,
          depth: 1,
        })
      : snapshotRelationships(mapState.focus || mapState.work);
  } else {
    graph = data.live
      ? await api("map", { episode: mapState.work, mode: mapState.mode })
      : (data.maps || []).find(
          (m) => m.episode_id === mapState.work && m.mode === mapState.mode,
        ) || { nodes: [], edges: [], version: 0 };
    mapState.model = graph;
    mapState.workVersion = workById.get(mapState.work)?.version;
    if (data.live)
      controls.append(
        actionButton("Add node", () => editMapNode(), true),
        actionButton("Add relationship", () => editMapEdge()),
      );
    if (graph.source_id)
      controls.append(
        actionButton("Read diagram record", () => open(graph.source_id)),
        actionButton("Version history", () => {
          mapState.focus = graph.source_id;
          mapState.mode = "relationships";
          return renderMap();
        }),
      );
  }
  if (view !== "map" || request !== renderVersion) return;
  const info = document.createElement("p");
  info.className = "map-note";
  info.textContent =
    mapState.mode === "relationships"
      ? graph.truncated
        ? "This neighbourhood is bounded. Expand a node to inspect further connections."
        : "These connections come from recorded evidence and history."
      : "This diagram records the design. Connections do not start agent actions.";
  root.append(info);
  const viewport = document.createElement("div");
  viewport.className = "map-viewport";
  viewport.tabIndex = 0;
  viewport.setAttribute("aria-label", "Project map canvas");
  const plane = document.createElement("div");
  plane.className = "map-plane";
  viewport.append(plane);
  root.append(viewport);
  const zoom = document.createElement("div");
  zoom.className = "map-zoom";
  const apply = () => {
    plane.style.transform = `translate(${mapState.x}px,${mapState.y}px) scale(${mapState.zoom})`;
  };
  zoom.append(
    actionButton("Zoom in", () => {
      mapState.zoom = Math.min(2, mapState.zoom * 1.2);
      apply();
    }),
    actionButton("Zoom out", () => {
      mapState.zoom = Math.max(0.1, mapState.zoom / 1.2);
      apply();
    }),
    actionButton("Fit", () => {
      const positions = nodes.map((n) => mapState.positions[n.id]);
      const minX = Math.min(0, ...positions.map((p) => p.x)),
        minY = Math.min(0, ...positions.map((p) => p.y));
      const width = Math.max(260, ...positions.map((p) => p.x + 260)) - minX;
      const height = Math.max(130, ...positions.map((p) => p.y + 130)) - minY;
      mapState.zoom = Math.min(
        1,
        (viewport.clientWidth - 40) / width,
        (viewport.clientHeight - 70) / height,
      );
      mapState.x = 20 - minX * mapState.zoom;
      mapState.y = 20 - minY * mapState.zoom;
      apply();
    }),
  );
  viewport.append(zoom);
  const key =
    "project-memory-layout:" +
    data.project +
    ":" +
    mapState.work +
    ":" +
    mapState.mode;
  try {
    mapState.positions = JSON.parse(localStorage.getItem(key) || "{}");
  } catch {
    el("workspace-message").textContent =
      "The browser could not restore the saved layout.";
    mapState.positions = {};
  }
  const nodes = graph.nodes.filter(
    (n) => mapState.showRetired || n.status !== "retired",
  );
  const visibleIds = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter(
    (e) =>
      (mapState.showRetired || e.status !== "retired") &&
      visibleIds.has(e.from) &&
      visibleIds.has(e.to),
  );
  nodes.forEach((n, i) => {
    mapState.positions[n.id] ??= {
      x: 40 + (i % 3) * 310,
      y: 40 + Math.floor(i / 3) * 170,
    };
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("map-edges");
  svg.setAttribute("width", "6000");
  svg.setAttribute("height", "6000");
  plane.append(svg);
  const drawEdges = () => {
    svg.replaceChildren();
    svg.setAttribute(
      "width",
      String(
        Math.max(1000, ...nodes.map((n) => mapState.positions[n.id].x + 300)),
      ),
    );
    svg.setAttribute(
      "height",
      String(
        Math.max(1000, ...nodes.map((n) => mapState.positions[n.id].y + 170)),
      ),
    );
    const defs = document.createElementNS(svg.namespaceURI, "defs"),
      marker = document.createElementNS(svg.namespaceURI, "marker");
    marker.id = "edge-arrow";
    for (const [k, v] of Object.entries({
      viewBox: "0 0 10 10",
      refX: "9",
      refY: "5",
      markerWidth: "6",
      markerHeight: "6",
      orient: "auto-start-reverse",
    }))
      marker.setAttribute(k, v);
    const tip = document.createElementNS(svg.namespaceURI, "path");
    tip.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    tip.setAttribute("fill", "currentColor");
    marker.append(tip);
    defs.append(marker);
    svg.append(defs);
    for (const edge of edges) {
      const a = mapState.positions[edge.from],
        b = mapState.positions[edge.to];
      if (!a || !b) continue;
      const forward = b.x >= a.x,
        x1 = a.x + (forward ? 240 : 0),
        x2 = b.x + (forward ? 0 : 240),
        y1 = a.y + 56,
        y2 = b.y + 56;
      const line = document.createElementNS(svg.namespaceURI, "path");
      line.setAttribute(
        "d",
        `M${x1},${y1} C${x1 + (forward ? 55 : -55)},${y1} ${x2 + (forward ? -55 : 55)},${y2} ${x2},${y2}`,
      );
      line.setAttribute("marker-end", "url(#edge-arrow)");
      line.dataset.status = edge.status;
      const title = document.createElementNS(svg.namespaceURI, "title");
      title.textContent = labels(edge.type) + ": " + edge.reason;
      line.append(title);
      svg.append(line);
      const text = document.createElementNS(svg.namespaceURI, "text");
      text.setAttribute("x", String((x1 + x2) / 2));
      text.setAttribute("y", String((y1 + y2) / 2 - 8));
      text.textContent = labels(edge.type);
      svg.append(text);
    }
  };
  let dragged = false;
  for (const node of nodes) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "map-node";
    card.dataset.node = node.id;
    const referenceStatus = graph.reference_status?.[node.reference];
    const stale = [
      "needs_review",
      "review_due",
      "superseded",
      "file_changed",
      "file_missing",
      "file_unreadable",
    ].includes(referenceStatus);
    card.dataset.status = stale ? "needs_review" : node.status;
    card.setAttribute("aria-label", "Inspect " + node.title);
    const type = document.createElement("small");
    type.textContent =
      labels(node.kind) +
      " · " +
      labels(node.status) +
      (stale ? " · Linked evidence needs review" : "");
    const title = document.createElement("strong");
    title.textContent = node.title;
    card.append(type, title);
    plane.append(card);
    const position = () => {
      card.style.left = mapState.positions[node.id].x + "px";
      card.style.top = mapState.positions[node.id].y + "px";
    };
    position();
    card.addEventListener("click", () => {
      if (dragged) {
        dragged = false;
        return;
      }
      inspectMapNode(node).catch(disconnected);
    });
    card.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      const start = {
        x: event.clientX,
        y: event.clientY,
        ...mapState.positions[node.id],
      };
      const cx = event.clientX,
        cy = event.clientY;
      card.setPointerCapture(event.pointerId);
      const move = (e) => {
        const dx = (e.clientX - cx) / mapState.zoom,
          dy = (e.clientY - cy) / mapState.zoom;
        if (Math.abs(dx) + Math.abs(dy) > 4) dragged = true;
        mapState.positions[node.id] = {
          x: Math.max(0, start.x + dx),
          y: Math.max(0, start.y + dy),
        };
        position();
        drawEdges();
      };
      const up = () => {
        card.removeEventListener("pointermove", move);
        card.removeEventListener("pointerup", up);
        card.removeEventListener("pointercancel", up);
        try {
          localStorage.setItem(key, JSON.stringify(mapState.positions));
        } catch {
          el("workspace-message").textContent =
            "The browser could not save this layout.";
        }
      };
      card.addEventListener("pointermove", move);
      card.addEventListener("pointerup", up);
      card.addEventListener("pointercancel", up);
    });
    card.addEventListener("keydown", (event) => {
      const shift = {
        ArrowLeft: [-20, 0],
        ArrowRight: [20, 0],
        ArrowUp: [0, -20],
        ArrowDown: [0, 20],
      }[event.key];
      if (!shift) return;
      event.preventDefault();
      mapState.positions[node.id].x = Math.max(
        0,
        mapState.positions[node.id].x + shift[0],
      );
      mapState.positions[node.id].y = Math.max(
        0,
        mapState.positions[node.id].y + shift[1],
      );
      position();
      drawEdges();
      try {
        localStorage.setItem(key, JSON.stringify(mapState.positions));
      } catch {
        el("workspace-message").textContent =
          "The browser could not save this layout.";
      }
    });
  }
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button")) return;
    const x = event.clientX,
      y = event.clientY,
      ox = mapState.x,
      oy = mapState.y;
    viewport.setPointerCapture(event.pointerId);
    const move = (e) => {
      mapState.x = ox + e.clientX - x;
      mapState.y = oy + e.clientY - y;
      apply();
    };
    const up = () => {
      viewport.removeEventListener("pointermove", move);
      viewport.removeEventListener("pointerup", up);
      viewport.removeEventListener("pointercancel", up);
    };
    viewport.addEventListener("pointermove", move);
    viewport.addEventListener("pointerup", up);
    viewport.addEventListener("pointercancel", up);
  });
  apply();
  drawEdges();
  if (!nodes.length) {
    const p = document.createElement("p");
    p.className = "map-empty";
    p.textContent =
      "Add the first " +
      (mapState.mode === "architecture" ? "component" : "step") +
      " to this diagram.";
    viewport.append(p);
  }
  const details = document.createElement("details");
  details.className = "map-connections";
  const summary = document.createElement("summary");
  summary.textContent = "Relationships · " + edges.length;
  details.append(summary);
  for (const edge of edges) {
    const row = document.createElement("div");
    row.className = "attention-row";
    const a = graph.nodes.find((n) => n.id === edge.from),
      b = graph.nodes.find((n) => n.id === edge.to);
    const p = document.createElement("p");
    p.textContent =
      (a?.title || edge.from) +
      " → " +
      (b?.title || edge.to) +
      " · " +
      labels(edge.type) +
      ". " +
      edge.reason +
      " (" +
      labels(edge.status) +
      ")";
    row.append(p);
    if (mapState.mode !== "relationships" && data.live)
      row.append(actionButton("Edit relationship", () => editMapEdge(edge)));
    details.append(row);
  }
  root.append(details);
}
function snapshotRelationships(focus) {
  const byId = new Map(
    data.records.map((r) => [
      r.id,
      {
        id: r.id,
        title: r.title,
        kind: r.kind,
        status: r.status,
        reference: r.id,
      },
    ]),
  );
  for (const w of data.work || [])
    byId.set(w.id, {
      id: w.id,
      title: w.title,
      kind: "work",
      status: w.state,
      reference: w.id,
    });
  const links = new Map();
  const edge = (from, to, type, reason) => {
    if (
      from &&
      to &&
      (from === focus || to === focus) &&
      byId.has(from) &&
      byId.has(to)
    ) {
      const id = from + ":" + type + ":" + to;
      links.set(id, { id, from, to, type, reason, status: "recorded" });
    }
  };
  for (const r of data.records) {
    const detail = r.detail || {};
    if (r.id !== r.episode_id)
      edge(
        r.episode_id,
        r.id,
        "contains",
        "This record belongs to this work item.",
      );
    for (const ref of detail.evidence || [])
      edge(ref.source_id, r.id, "supports", ref.reason);
    for (const ref of detail.links || [])
      edge(ref.event_id, r.id, "informs", ref.reason);
    edge(
      detail.decision_id,
      r.id,
      "has_" + r.kind,
      "This record preserves the decision lineage.",
    );
    edge(
      detail.supersedes,
      r.id,
      "revised_by",
      "The earlier version remains part of the history.",
    );
  }
  for (const w of data.work || [])
    for (const dep of w.plan?.depends_on || [])
      edge(w.id, dep.episode_id, "depends_on", dep.reason);
  const ids = new Set([focus]),
    edges = [];
  let truncated = false;
  for (const link of links.values()) {
    const other = link.from === focus ? link.to : link.from;
    if (!ids.has(other) && ids.size >= 40) {
      truncated = true;
      continue;
    }
    ids.add(other);
    edges.push(link);
  }
  return {
    nodes: [...ids].map((id) => byId.get(id)).filter(Boolean),
    edges,
    truncated,
  };
}
async function inspectMapNode(node) {
  if (mapState.mode === "relationships") {
    if (node.id.startsWith("episode_")) await openWork(node.id);
    else await open(node.id);
    const body = el("detail-body");
    if (el("detail").open)
      body.append(
        actionButton("Expand relationships", () => {
          mapState.focus = node.id;
          return renderMap();
        }),
      );
    return;
  }
  extensionDetail = true;
  selectedId = null;
  detailHistory.length = 0;
  el("detail-back").hidden = true;
  el("detail-title").textContent = node.title;
  const body = el("detail-body");
  body.replaceChildren();
  metadata(body, node);
  const status = mapState.model?.reference_status?.[node.reference];
  if (status) {
    const p = document.createElement("p");
    p.textContent = "Linked record: " + labels(status);
    body.append(p);
  }
  const p = document.createElement("p");
  p.textContent = node.description;
  body.append(p);
  if (node.reference)
    body.append(
      actionButton("Open linked record", () =>
        node.reference.startsWith("episode_")
          ? openWork(node.reference)
          : open(node.reference),
      ),
    );
  if (data.live)
    body.append(actionButton("Edit node", () => editMapNode(node)));
  showDetail(false, 0);
}
async function mapReferenceField(selected = "") {
  const input = editField(
    "reference",
    "Linked project record",
    selected,
    [["", "No linked record"]],
    true,
  );
  const category = editField("reference_type", "Record type", "events", [
    ["events", "Decisions and history"],
    ["sources", "Evidence sources"],
  ]);
  let offset = 0;
  const more = actionButton("Load more records", () => load());
  el("editor-fields").append(more);
  const load = async () => {
    const result = await api("records", {
      view: category.value,
      limit: 100,
      offset,
    });
    for (const item of result.records) {
      const o = document.createElement("option");
      o.value = item.id;
      o.textContent = labels(item.kind) + ": " + item.title;
      input.append(o);
    }
    offset += result.records.length;
    more.hidden = !result.more;
  };
  for (const work of workById.values()) {
    const o = document.createElement("option");
    o.value = work.id;
    o.textContent = work.title;
    input.append(o);
  }
  category.addEventListener("change", () => {
    offset = 0;
    input.replaceChildren();
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No linked record";
    input.append(option);
    load().catch(disconnected);
  });
  await load();
  if (selected && ![...input.options].some((o) => o.value === selected)) {
    const r = await api("record", { id: selected });
    const o = document.createElement("option");
    o.value = selected;
    o.textContent = r.record.title;
    input.append(o);
  }
  input.value = selected;
}
async function saveMap(
  nodes,
  edges,
  reason,
  key,
  version,
  workVersion,
  mode,
  episode,
) {
  await post("actions", {
    operation: "map",
    request_key: key,
    data: {
      episode_id: episode,
      expected_version: workVersion,
      mode,
      map_version: version,
      nodes,
      edges,
      reason,
    },
  });
}
async function reloadMapModel() {
  const [model, work] = await Promise.all([
    api("map", { episode: mapState.work, mode: mapState.mode }),
    api("board", { episode: mapState.work, limit: 1 }),
  ]);
  mapState.model = model;
  mapState.workVersion = work.cards[0].version;
}
async function editMapNode(node = null) {
  const model = mapState.model,
    episode = mapState.work,
    mode = mapState.mode,
    workVersion = mapState.workVersion,
    nodeId = node?.id || "node_" + crypto.randomUUID();
  extensionEditor(
    node ? "Edit diagram node" : "Add diagram node",
    "Save node",
    async (values, key) => {
      const value = {
        id: nodeId,
        title: values.title,
        kind: values.kind,
        description: values.description,
        reference: values.reference,
        status: values.status,
      };
      const nodes = node
        ? model.nodes.map((n) => (n.id === node.id ? value : n))
        : [...model.nodes, value];
      await saveMap(
        nodes,
        model.edges,
        values.reason,
        key,
        model.version,
        workVersion,
        mode,
        episode,
      );
    },
    async () => {
      await reloadMapModel();
      await editMapNode(
        node ? mapState.model.nodes.find((n) => n.id === node.id) : null,
      );
    },
  );
  editField("title", "Name", node?.title || "", null, true);
  editField(
    "kind",
    "Type",
    node?.kind || (mode === "architecture" ? "component" : "process"),
    ["system", "component", "process", "deliverable", "work"].map((v) => [
      v,
      labels(v),
    ]),
  );
  editField(
    "status",
    "Status",
    node?.status || "proposed",
    ["proposed", "confirmed", "retired"].map((v) => [v, labels(v)]),
  );
  editField("description", "Description", node?.description || "", null, true);
  await mapReferenceField(node?.reference || "");
  el("edit-reference").required = false;
  editField("reason", "Reason for this change", "", null, true);
}
function editMapEdge(edge = null) {
  const model = mapState.model,
    episode = mapState.work,
    mode = mapState.mode,
    workVersion = mapState.workVersion,
    edgeId = edge?.id || "edge_" + crypto.randomUUID();
  extensionEditor(
    edge ? "Edit relationship" : "Add relationship",
    "Save relationship",
    async (values, key) => {
      const value = {
        id: edgeId,
        from: values.from,
        to: values.to,
        type: values.type,
        reason: values.reason,
        status: values.status,
      };
      const edges = edge
        ? model.edges.map((e) => (e.id === edge.id ? value : e))
        : [...model.edges, value];
      await saveMap(
        model.nodes,
        edges,
        values.reason,
        key,
        model.version,
        workVersion,
        mode,
        episode,
      );
    },
    async () => {
      await reloadMapModel();
      editMapEdge(
        edge ? mapState.model.edges.find((e) => e.id === edge.id) : null,
      );
    },
  );
  const choices = [
    ["", "Select a node"],
    ...model.nodes
      .filter((n) => n.status !== "retired")
      .map((n) => [n.id, n.title]),
  ];
  editField("from", "From", edge?.from || "", choices);
  editField("to", "To", edge?.to || "", choices);
  editField(
    "type",
    "Relationship",
    edge?.type || "uses",
    [
      "depends_on",
      "precedes",
      "uses",
      "produces",
      "contains",
      "implements",
    ].map((v) => [v, labels(v)]),
  );
  editField(
    "status",
    "Status",
    edge?.status || "proposed",
    ["proposed", "confirmed", "retired"].map((v) => [v, labels(v)]),
  );
  editField("reason", "Relationship reason", edge?.reason || "", null, true);
}
