// Pure view logic.
function selectRecords(records, pending, filter) {
  const query = filter.query.toLowerCase();
  const rows = records.filter((r) => {
    const selected =
      filter.view === "direction"
        ? r.kind === "project_revision"
        : filter.view === "episodes"
          ? r.kind === "episode"
          : filter.view === "sources"
            ? r.kind === "source"
            : filter.view === "documents"
              ? r.kind === "source" && r.detail.origin === "document"
              : filter.view === "lessons"
                ? r.kind === "lesson"
                : filter.view === "pending"
                  ? pending.has(r.id)
                  : filter.view === "decisions"
                    ? r.kind === "decision"
                    : filter.view === "research"
                      ? r.kind === "research"
                      : filter.view === "corrections"
                        ? r.kind === "correction"
                        : filter.view === "patterns"
                          ? r.kind === "lesson" &&
                            ["anti_pattern", "recovery", "practice"].includes(
                              r.detail.payload.pattern_type,
                            )
                          : filter.view === "drift"
                            ? [
                                "needs_review",
                                "review_due",
                                "superseded",
                                "file_changed",
                                "file_missing",
                                "file_unreadable",
                              ].includes(r.status)
                            : filter.view === "captures"
                              ? r.kind === "host_receipt"
                              : ![
                                  "episode",
                                  "source",
                                  "host_receipt",
                                  "project_revision",
                                ].includes(r.kind);
    return (
      selected &&
      (!filter.subject || r.subject === filter.subject) &&
      (!filter.status ||
        (filter.view === "pending" ? pending.get(r.id)?.state : r.status) ===
          filter.status) &&
      (!filter.episode || r.episode_id === filter.episode) &&
      (!filter.from || r.date.slice(0, 10) >= filter.from) &&
      (!filter.to || r.date.slice(0, 10) <= filter.to) &&
      (!query || JSON.stringify(r).toLowerCase().includes(query))
    );
  });
  rows.sort((a, b) =>
    filter.order === "title"
      ? a.title.localeCompare(b.title)
      : filter.order === "oldest"
        ? a.date.localeCompare(b.date)
        : b.date.localeCompare(a.date),
  );
  return rows;
}
function pageRecords(rows, page, size) {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  page = Math.max(1, Math.min(page, pages));
  return {
    page,
    pages,
    records: rows.slice((page - 1) * size, page * size),
    start: rows.length ? (page - 1) * size + 1 : 0,
    end: Math.min(page * size, rows.length),
  };
}
// End pure view logic.
function render() {
  if (view === "overview") {
    renderOverview().catch(disconnected);
    return;
  }
  if (["skills", "map", "attention", "dependencies"].includes(view)) {
    renderExtension().catch(disconnected);
    return;
  }
  if (view === "board") {
    renderBoard().catch(disconnected);
    return;
  }
  if (data.live) {
    loadPage().catch(disconnected);
    return;
  }
  const filter = { view };
  for (const id of [
    "query",
    "subject",
    "status",
    "episode",
    "from",
    "to",
    "order",
  ])
    filter[id] = el(id).value;
  const rows = selectRecords(data.records, pending, filter);
  const size = Number(el("page-size").value),
    slice = pageRecords(rows, page, size);
  page = slice.page;
  draw(slice.records, rows.length, slice.pages, size);
}
function draw(records, total, pages, size) {
  drawReading(records);
  el("rows").replaceChildren();
  for (const r of records) {
    const tr = document.createElement("tr");
    for (const [value, cls] of [
      [r.date.slice(0, 10), "date"],
      [labels(r.subject), ""],
      [labels(r.kind), "type"],
      [r.title, "title"],
      [
        labels(view === "pending" ? pending.get(r.id)?.state : r.status),
        "status",
      ],
    ]) {
      const td = document.createElement("td");
      td.className = cls;
      const span = document.createElement("span");
      span.textContent = value;
      if (cls === "status") {
        span.className = "badge";
        span.dataset.state = r.status;
      }
      if (cls === "date") span.title = dateLabel(r.date);
      td.append(span);
      tr.append(td);
    }
    const td = document.createElement("td"),
      button = document.createElement("button");
    button.className = "row-open";
    button.textContent = "Open";
    button.setAttribute("aria-label", "Open " + r.title);
    button.addEventListener("click", () => open(r.id));
    td.append(button);
    tr.append(td);
    el("rows").append(tr);
  }
  el("empty").hidden = total !== 0;
  el("pending-note").hidden = view !== "pending";
  el("count").textContent = total
    ? `${(page - 1) * size + 1}–${Math.min(page * size, total)} of ${total} records`
    : "0 records";
  el("page").textContent = `Page ${page} of ${pages}`;
  el("previous").disabled = page === 1;
  el("next").disabled = page === pages;
}
function field(parent, key, value) {
  const wrap = document.createElement("div");
  wrap.className = "record-field";
  wrap.dataset.field = key;
  if (
    [
      "status",
      "Recorded state",
      "autonomy",
      "owner",
      "version",
      "project_revision",
      "origin",
      "checked_at",
      "review_after",
      "date",
      "subject",
      "kind",
      "actor",
    ].includes(key)
  )
    wrap.classList.add("compact");
  const dt = document.createElement("dt"),
    dd = document.createElement("dd");
  dt.textContent = labels(key);
  if (key === "body" && typeof value === "string" && /^#{1,6} /m.test(value))
    dt.hidden = true;
  wrap.append(dt, dd);
  parent.append(wrap);
  if (Array.isArray(value) && value.length && typeof value[0] === "object") {
    const table = document.createElement("table"),
      thead = document.createElement("thead"),
      head = document.createElement("tr"),
      tbody = document.createElement("tbody"),
      keys = [...new Set(value.flatMap((item) => Object.keys(item)))];
    for (const k of keys) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = labels(k);
      head.append(th);
    }
    thead.append(head);
    table.append(thead, tbody);
    for (const item of value) {
      const tr = document.createElement("tr");
      for (const k of keys) {
        const td = document.createElement("td");
        const val = item[k];
        td.textContent =
          val && typeof val === "object"
            ? JSON.stringify(val, null, 2)
            : typeof val === "string" && words[val]
              ? labels(val)
              : String(val ?? "Not recorded");
        tr.append(td);
      }
      tbody.append(tr);
    }
    dd.append(table);
  } else if (Array.isArray(value)) {
    if (value.length) {
      const list = document.createElement("ul");
      for (const text of value) {
        const li = document.createElement("li");
        li.textContent = String(text);
        list.append(li);
      }
      dd.append(list);
    } else dd.textContent = "None recorded";
  } else if (value && typeof value === "object") {
    const dl = document.createElement("dl");
    dd.append(dl);
    for (const [k, v] of Object.entries(value)) field(dl, k, v);
  } else if (
    typeof value === "string" &&
    !words[value] &&
    !wrap.classList.contains("compact")
  ) {
    if (key === "body") {
      const toggle = document.createElement("button"),
        content = document.createElement("div"),
        recordId = selectedId;
      toggle.type = "button";
      toggle.className = "source-toggle";
      const draw = () => {
        const original = originalTextShown.has(recordId);
        toggle.textContent = original
          ? "Read formatted text"
          : "Read original text";
        content.replaceChildren();
        if (original) {
          const pre = document.createElement("pre");
          pre.textContent = value;
          content.append(pre);
        } else content.append(documentText(value));
      };
      toggle.addEventListener("click", () => {
        if (originalTextShown.has(recordId)) originalTextShown.delete(recordId);
        else originalTextShown.add(recordId);
        draw();
        documentOutline(el("detail-body"));
      });
      draw();
      dd.append(toggle, content);
    } else dd.append(documentText(value));
  } else {
    dd.textContent =
      ["checked_at", "review_after", "date"].includes(key) && value
        ? dateLabel(value)
        : typeof value === "string" && words[value]
          ? labels(value)
          : String(value ?? "Not recorded");
  }
}
function inlineText(parent, text) {
  const pattern =
    /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
  let start = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(start, match.index)));
    const part = match[0];
    let node;
    if (part.startsWith("`")) {
      node = document.createElement("code");
      node.textContent = part.slice(1, -1);
    } else if (part.startsWith("**")) {
      node = document.createElement("strong");
      node.textContent = part.slice(2, -2);
    } else if (part.startsWith("*")) {
      node = document.createElement("em");
      node.textContent = part.slice(1, -1);
    } else {
      const link = part.match(/^\[([^\]]+)\]\((.+)\)$/);
      node = document.createElement("a");
      node.textContent = link[1];
      node.href = link[2];
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    }
    parent.append(node);
    start = match.index + part.length;
  }
  parent.append(document.createTextNode(text.slice(start)));
}
function documentText(text) {
  const root = document.createElement("div");
  root.className = "document";
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const cells = (line) =>
    line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((s) => s.trim());
  const tableRule = (line) =>
    line.includes("|") && cells(line).every((s) => /^:?-{3,}:?$/.test(s));
  const block = (line) => /^(#{1,6}\s|```|>\s?|[-*+]\s|\d+\.\s)/.test(line);
  for (let i = 0; i < lines.length; ) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const pre = document.createElement("pre"),
        code = document.createElement("code"),
        body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```"))
        body.push(lines[i++]);
      if (i < lines.length) i++;
      code.textContent = body.join("\n");
      pre.append(code);
      root.append(pre);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const h = document.createElement("h" + heading[1].length);
      inlineText(h, heading[2]);
      root.append(h);
      i++;
      continue;
    }
    if (i + 1 < lines.length && tableRule(lines[i + 1])) {
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      const table = document.createElement("table"),
        thead = document.createElement("thead"),
        tr = document.createElement("tr");
      for (const cell of cells(line)) {
        const th = document.createElement("th");
        th.scope = "col";
        inlineText(th, cell);
        tr.append(th);
      }
      thead.append(tr);
      table.append(thead);
      const body = document.createElement("tbody");
      table.append(body);
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        const row = document.createElement("tr");
        for (const cell of cells(lines[i++])) {
          const td = document.createElement("td");
          inlineText(td, cell);
          row.append(td);
        }
        body.append(row);
      }
      wrap.append(table);
      root.append(wrap);
      continue;
    }
    const item = line.match(/^([-*+]|\d+\.)\s+(.+)$/);
    if (item) {
      const ordered = /\d/.test(item[1]),
        list = document.createElement(ordered ? "ol" : "ul");
      if (ordered) list.start = parseInt(item[1]);
      while (i < lines.length) {
        const next = lines[i].match(/^([-*+]|\d+\.)\s+(.+)$/);
        if (!next || /\d/.test(next[1]) !== ordered) break;
        const li = document.createElement("li");
        inlineText(li, next[2]);
        list.append(li);
        i++;
      }
      root.append(list);
      continue;
    }
    if (line.startsWith(">")) {
      const quote = document.createElement("blockquote"),
        parts = [];
      while (i < lines.length && lines[i].startsWith(">"))
        parts.push(lines[i++].replace(/^>\s?/, ""));
      inlineText(quote, parts.join("\n"));
      root.append(quote);
      continue;
    }
    const paragraph = document.createElement("p"),
      parts = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !block(lines[i]) &&
      !(i + 1 < lines.length && tableRule(lines[i + 1]))
    )
      parts.push(lines[i++]);
    inlineText(paragraph, parts.join("\n"));
    root.append(paragraph);
  }
  return root;
}
function reference(parent, id, reason) {
  const p = document.createElement("p");
  if (byId.has(id) || data.live) {
    const b = document.createElement("button");
    b.textContent = byId.has(id)
      ? labels(byId.get(id).kind) + ": " + byId.get(id).title
      : id.startsWith("source_")
        ? "Open evidence"
        : id.startsWith("direction_")
          ? "Project requirements"
          : "Open linked record";
    b.addEventListener("click", () => open(id));
    p.append(b);
  } else {
    const span = document.createElement("span");
    span.textContent = id + " (outside this export)";
    p.append(span);
  }
  if (reason) {
    const span = document.createElement("span");
    span.textContent = " " + reason;
    p.append(span);
  }
  parent.append(p);
}
async function open(id, refresh = false) {
  extensionDetail = false;
  coverageSession = null;
  selectDetail(id, refresh);
  workDetail = false;
  selectedId = id;
  const scroll = el("detail").scrollTop,
    shownLength = refresh ? byId.get(id)?.detail.body?.length || 12000 : 12000;
  if (data.live) {
    try {
      const result = await api("record", { id });
      if (selectedId !== id) return;
      while (
        result.record.detail.body_more &&
        result.record.detail.body.length < shownLength
      ) {
        const next = (
          await api("record", {
            id,
            body_offset: result.record.detail.next_offset,
          })
        ).record.detail;
        result.record.detail.body += next.body;
        result.record.detail.next_offset = next.next_offset;
        result.record.detail.body_more = next.body_more;
      }
      byId.set(id, result.record);
    } catch (error) {
      disconnected(error);
      return;
    }
  }
  const r = byId.get(id);
  if (!r) return;
  el("detail-title").textContent = r.title;
  const body = el("detail-body");
  body.replaceChildren();
  metadata(body, r);
  const detail = r.detail;
  if (r.kind === "decision") {
    try {
      await decisionPage(r, body, refresh, scroll);
    } catch (error) {
      disconnected(error);
    }
    return;
  }
  const dl = document.createElement("dl");
  dl.className = "record-content";
  body.append(dl);
  const payload = detail.payload || detail;
  const first =
    r.kind === "correction"
      ? ["before", "after", "reason", "scope"]
      : r.kind === "source"
        ? ["summary", "body"]
        : [];
  const ordered = [
    ...first,
    ...Object.keys(payload).filter((k) => !first.includes(k)),
  ];
  const documentSummary = r.kind === "source" && detail.origin === "document";
  const technicalKeys = [
    "id",
    "episode_id",
    "session_id",
    "turn_id",
    "tool_use_id",
    "tool_name",
    "event_name",
    "source_key",
    "project_revision",
    "work_plan_id",
    "version",
    "actor",
    "seq",
    "request_key",
    "content_hash",
    "hash",
    "document",
  ];
  for (const key of ordered)
    if (
      key in payload &&
      !(documentSummary && key === "summary") &&
      !technicalKeys.includes(key) &&
      ![
        "subject",
        "kind",
        "status",
        "created_at",
        "evidence",
        "links",
        "source_text",
        "payload",
        "decision_id",
        "supersedes",
        "replaced_by",
        "review_reasons",
        "body_offset",
        "next_offset",
        "body_more",
      ].includes(key)
    )
      field(
        dl,
        key === "scope" && r.kind !== "correction"
          ? "Scope and exceptions"
          : key,
        payload[key],
      );
  if (pending.has(id)) {
    field(dl, "Follow-up", pending.get(id));
  }
  if (r.kind === "lesson") {
    field(dl, "Review status", detail.lesson_status || detail.status);
    field(
      dl,
      "Use",
      "Apply accepted lessons only within their recorded conditions and exceptions.",
    );
  }
  if (r.kind === "host_receipt")
    for (const key of [
      "session_id",
      "turn_id",
      "tool_use_id",
      "tool_name",
      "event_name",
    ])
      if (detail[key]) field(dl, key, detail[key]);
  if (r.kind === "source" && !data.live && !data.source_bodies_included)
    field(
      dl,
      "Full source",
      "This snapshot omits the source body. Use memory_get with view record, this record ID and body_offset 0.",
    );
  const refs = document.createElement("div");
  refs.className = "references";
  body.append(refs);
  if (detail.review_reasons)
    field(dl, "Why this needs review", detail.review_reasons);
  if (data.live && r.kind === "source" && detail.body_more) {
    const more = document.createElement("button");
    more.textContent = "Read more source text";
    let offset = detail.next_offset;
    more.addEventListener("click", async () => {
      more.disabled = true;
      try {
        const next = (await api("record", { id, body_offset: offset })).record
          .detail;
        if (selectedId !== id) return;
        detail.body += next.body;
        const holder = document.createElement("dl");
        field(holder, "body", detail.body);
        const existing = dl.querySelector("[data-field=body]");
        if (existing) existing.replaceWith(holder.firstElementChild);
        else dl.append(holder.firstElementChild);
        offset = next.next_offset;
        more.hidden = !next.body_more;
        documentOutline(body);
      } catch (error) {
        disconnected(error);
      } finally {
        more.disabled = false;
      }
    });
    body.append(more);
  }
  for (const ref of detail.evidence || [])
    reference(
      refs,
      ref.source_id,
      ref.reason + (ref.status ? " · " + labels(ref.status) : ""),
    );
  for (const ref of detail.links || [])
    reference(refs, ref.event_id, ref.reason);
  for (const key of ["decision_id", "supersedes", "replaced_by"])
    if (detail[key]) reference(refs, detail[key], labels(key));
  if (r.kind === "source" && !data.live) {
    const h = document.createElement("h2");
    h.textContent = "Records that use this evidence";
    refs.append(h);
    for (const related of data.records.filter((x) =>
      (x.detail.evidence || []).some((e) => e.source_id === id),
    ))
      reference(refs, related.id, labels(related.status));
  }
  if (data.live && r.kind === "source") {
    const heading = document.createElement("h2");
    heading.textContent = "Records that use this evidence";
    refs.append(heading);
    const more = document.createElement("button");
    more.textContent = "Show related records";
    let offset = 0;
    refs.append(more);
    const load = async () => {
      more.disabled = true;
      try {
        const result = await api("records", { related: id, offset, limit: 25 });
        if (selectedId !== id) return;
        for (const related of result.records) {
          byId.set(related.id, related);
          reference(refs, related.id, labels(related.status));
        }
        offset += result.records.length;
        more.hidden = !result.more;
        more.textContent = "Show more related records";
      } catch (error) {
        disconnected(error);
      } finally {
        more.disabled = false;
      }
    };
    more.addEventListener("click", load);
  }
  if (r.kind === "episode") {
    const button = document.createElement("button");
    button.textContent = "Show episode events";
    button.addEventListener("click", () => {
      if (![...el("episode").options].some((o) => o.value === id))
        option("episode", id, r.title);
      selectedId = null;
      el("detail").close();
      setView("events", {
        episode: id,
        query: "",
        subject: "",
        status: "",
        from: "",
        to: "",
      });
    });
    body.append(button);
  }
  const technical = document.createElement("details");
  technical.className = "technical";
  const summary = document.createElement("summary");
  summary.textContent = "Record details";
  technical.append(summary);
  const properties = document.createElement("dl");
  technical.append(properties);
  field(properties, "id", r.id);
  if (documentSummary) field(properties, "summary", payload.summary);
  for (const key of technicalKeys.filter((k) => k !== "id"))
    if (key in detail || key in payload)
      field(properties, key, payload[key] ?? detail[key]);
  body.append(technical);
  if (data.live && r.kind === "lesson") lessonControl(el("detail-body"), r);
  if (r.kind === "source") documentOutline(body);
  showDetail(refresh, scroll);
}
