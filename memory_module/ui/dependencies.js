const dependencyFilters = { query: "", group: "", manifest: "", documentation: "" };
async function renderDependencies() {
  const request = ++renderVersion;
  const result = data.live ? await api("dependencies") : data.dependencies;
  if (view !== "dependencies" || request !== renderVersion) return;
  const root = el("extension-view");
  root.replaceChildren();
  if (!result) {
    root.textContent = "This snapshot does not include a dependency inventory.";
    return;
  }
  const intro = document.createElement("p");
  intro.className = "dependency-intro";
  intro.textContent = "Declared packages and recorded architecture dependencies." +
    (data.live ? "" : " Snapshot from " + data.exported_at + ".");
  const controls = document.createElement("div");
  controls.className = "extension-toolbar dependency-filters";
  const query = document.createElement("input");
  query.type = "search";
  query.placeholder = "Search dependencies";
  query.setAttribute("aria-label", "Search dependencies");
  query.value = dependencyFilters.query;
  controls.append(query);
  for (const [key, label, values] of [
    ["group", "All groups", [...new Set(result.packages.map(p => p.group))].sort()],
    ["manifest", "All manifests", result.manifests.map(m => m.path)],
  ]) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Dependency " + key);
    for (const value of ["", ...values]) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value || label;
      select.append(opt);
    }
    select.value = values.includes(dependencyFilters[key]) ? dependencyFilters[key] : "";
    dependencyFilters[key] = select.value;
    select.addEventListener("change", () => { dependencyFilters[key] = select.value; draw(); });
    controls.append(select);
  }
  if (data.live) controls.append(actionButton("Refresh", () => renderDependencies().catch(disconnected)));
  const summary = document.createElement("p");
  summary.className = "dependency-summary";
  const groups = new Map();
  for (const item of result.packages) groups.set(item.group, (groups.get(item.group) || 0) + 1);
  summary.textContent = result.packages.length + " declarations · " +
    result.manifests.length + " manifests" +
    (groups.size ? " · " + [...groups].map(([name, count]) => count + " " + name.toLowerCase()).join(" · ") : "");
  const packages = document.createElement("div");
  packages.className = "dependency-packages";
  root.append(intro, summary, controls);
  for (const issue of result.issues) {
    const warning = document.createElement("p");
    warning.className = "dependency-warning";
    warning.textContent = issue.manifest + ": " + issue.message;
    root.append(warning);
  }
  root.append(packages);
  const scope = document.createElement("p");
  scope.className = "map-note";
  scope.textContent = result.scope;
  root.append(scope);
  const documentation = document.createElement("section");
  documentation.className = "dependency-documentation";
  const heading = document.createElement("h3");
  heading.textContent = "Purpose and decisions";
  documentation.append(heading);
  const sections = result.documentation || [];
  if (sections.length) {
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Dependency documentation");
    const keys = sections.map((section, i) => section.source_id + ":" + i);
    sections.forEach((section, i) => {
      const option = document.createElement("option");
      option.value = keys[i]; option.textContent = section.title + " · " + section.heading;
      select.append(option);
    });
    select.value = keys.includes(dependencyFilters.documentation) ? dependencyFilters.documentation : keys[0];
    const content = document.createElement("article");
    content.className = "dependency-context";
    const show = () => {
      dependencyFilters.documentation = select.value;
      const section = sections[keys.indexOf(select.value)];
      content.replaceChildren();
      const controls = document.createElement("div");
      controls.className = "extension-toolbar";
      controls.append(statusBadge(section.status), actionButton("Open original document", () => open(section.source_id)));
      const note = document.createElement("p");
      note.className = "map-note";
      note.textContent = "Recorded documentation. Check its source status before relying on versions or decisions.";
      content.append(controls, note, documentText(section.body));
    };
    select.addEventListener("change", show);
    documentation.append(select, content);
    show();
  } else {
    const empty = document.createElement("p");
    empty.className = "extension-empty";
    empty.textContent = !data.live && !data.source_bodies_included
      ? "This snapshot excludes document text. Export with source bodies to include dependency explanations."
      : "No dependency sections were found in captured documents. Record package roles and decisions under a Dependencies heading in the owning document.";
    documentation.append(empty);
  }
  root.append(documentation);
  const recorded = document.createElement("section");
  recorded.className = "dependency-recorded";
  root.append(recorded);
  query.addEventListener("input", () => { dependencyFilters.query = query.value; draw(); });
  function draw() {
    packages.replaceChildren();
    recorded.replaceChildren();
    const search = dependencyFilters.query.toLowerCase();
    const matching = result.packages.filter(p => (!dependencyFilters.group || p.group === dependencyFilters.group) &&
      (!dependencyFilters.manifest || p.manifest === dependencyFilters.manifest) &&
      (!search || [p.name, p.requirement, p.manifest].join(" ").toLowerCase().includes(search)));
    const table = document.createElement("table");
    table.className = "dependency-table";
    const caption = document.createElement("caption");
    caption.textContent = "Packages · " + matching.length + " shown";
    const head = document.createElement("thead"), tr = document.createElement("tr");
    for (const label of ["Dependency", "Declared version", "Group", "Declared in"]) {
      const th = document.createElement("th");
      th.scope = "col"; th.textContent = label; tr.append(th);
    }
    head.append(tr);
    const body = document.createElement("tbody");
    for (const item of matching) {
      const row = document.createElement("tr");
      for (const value of [item.name, item.requirement, item.group, item.manifest]) {
        const cell = document.createElement("td");
        cell.textContent = value; row.append(cell);
      }
      body.append(row);
    }
    table.append(caption, head, body);
    if (matching.length) packages.append(table);
    else {
      const empty = document.createElement("p");
      empty.className = "extension-empty";
      empty.textContent = result.packages.length ? "No dependencies match these filters." :
        result.manifests.length ? "No dependency declarations were read. Check the manifests and any reported errors." :
          "No supported package manifests were found in this project.";
      packages.append(empty);
    }
    const title = document.createElement("h3");
    title.textContent = "Recorded architecture dependencies";
    recorded.append(title);
    const items = result.recorded.filter(item => !search || JSON.stringify(item).toLowerCase().includes(search));
    for (const item of items) {
      const card = document.createElement("article");
      card.className = "dependency-context";
      const heading = document.createElement("h4");
      heading.textContent = item.name;
      heading.append(statusBadge(item.status));
      const purpose = document.createElement("p");
      purpose.textContent = item.description;
      card.append(heading, purpose);
      for (const use of item.used_by) {
        const text = document.createElement("p");
        text.textContent = use.name + ": " + use.reason;
        text.append(statusBadge(use.status));
        card.append(text);
      }
      const actions = document.createElement("div");
      actions.className = "extension-toolbar";
      actions.append(actionButton("Open architecture", () => {
        mapState.work = item.episode_id; mapState.mode = "architecture"; setView("map");
      }));
      if (item.reference) actions.append(actionButton("Open evidence", () => open(item.reference)));
      if (item.reference_status) actions.append(statusBadge(item.reference_status));
      const origin = document.createElement("small");
      origin.textContent = item.work_title;
      card.append(origin, actions);
      recorded.append(card);
    }
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "extension-empty";
      empty.textContent = search ? "No recorded architecture dependencies match this search." :
        "No dependency connections are recorded in Project map. Connect architecture nodes with Uses or Depends on and explain why.";
      recorded.append(empty);
    }
  }
  draw();
}
