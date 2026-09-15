async function renderSkills() {
  const request = ++renderVersion,
    size = Number(el("page-size").value);
  const result = await api("skills", {
    query: el("query").value,
    limit: size,
    offset: (page - 1) * size,
  });
  if (view !== "skills" || request !== renderVersion) return;
  const root = el("extension-view");
  root.replaceChildren();
  const controls = document.createElement("div");
  controls.className = "extension-toolbar";
  controls.append(
    actionButton("Import skill", importSkill, true),
    actionButton("Refresh", renderSkills),
  );
  root.append(controls);
  const grid = document.createElement("div");
  grid.className = "skill-grid";
  root.append(grid);
  for (const skill of result.skills) {
    const card = document.createElement("article");
    card.className = "skill-card";
    const h = document.createElement("h3");
    h.append(actionButton(skill.name, () => openSkill(skill.id)));
    const p = document.createElement("p");
    p.textContent = skill.description || skill.error;
    const meta = document.createElement("small");
    meta.textContent =
      labels(skill.origin) +
      " · " +
      labels(skill.status) +
      (skill.version ? " · Version " + skill.version : "");
    card.append(h, p, meta);
    grid.append(card);
  }
  if (!result.total) {
    const p = document.createElement("p");
    p.className = "extension-empty";
    p.textContent =
      "Import a skill folder or ZIP, or add a skill to this project’s .agents/skills directory.";
    grid.append(p);
  }
  const pages = Math.max(1, Math.ceil(result.total / size));
  el("count").textContent = result.total + " skills";
  el("page").textContent = "Page " + page + " of " + pages;
  el("previous").disabled = page === 1;
  el("next").disabled = page >= pages;
}
function importSkill() {
  extensionEditor(
    "Import project skill",
    "Import skill",
    async (values, key) => {
      const input = el("skill-files"),
        files = [...input.files];
      if (!files.length)
        throw new Error("Select a skill folder or ZIP archive.");
      if (files.length > 100)
        throw new Error("A skill supports up to 100 files.");
      const encode = async (file) => {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let s = "";
        for (const b of bytes) s += String.fromCharCode(b);
        return btoa(s);
      };
      const selected = JSON.parse(values.version);
      let data = {
        expected_version: selected.version,
        ...(selected.id ? { replace_skill_id: selected.id } : {}),
      };
      if (files.length === 1 && files[0].name.toLowerCase().endsWith(".zip"))
        data.archive = await encode(files[0]);
      else {
        const total = files.reduce((n, f) => n + f.size, 0);
        if (total > 1_000_000)
          throw new Error("A skill package must be 1 MB or smaller.");
        data.files = await Promise.all(
          files.map(async (f) => ({
            path: f.webkitRelativePath
              ? f.webkitRelativePath.split("/").slice(1).join("/")
              : f.name,
            content: await encode(f),
          })),
        );
      }
      await post("actions", {
        operation: "skill_import",
        data,
        request_key: key,
      });
    },
    importSkill,
  );
  const label = document.createElement("label");
  label.className = "wide";
  label.textContent = "Skill files";
  const input = document.createElement("input");
  input.id = "skill-files";
  input.type = "file";
  input.required = true;
  input.accept = ".zip,.md";
  label.append(input);
  el("editor-fields").append(label);
  const mode = editField("import_mode", "Import from", "zip", [
    ["zip", "ZIP or SKILL.md"],
    ["folder", "Skill folder"],
  ]);
  mode.addEventListener("input", () => {
    input.value = "";
    if (mode.value === "folder") {
      input.setAttribute("webkitdirectory", "");
      input.removeAttribute("accept");
    } else {
      input.removeAttribute("webkitdirectory");
      input.accept = ".zip,.md";
    }
  });
  const versions = editField(
    "version",
    "Replace imported version",
    '{"version":0}',
    [['{"version":0}', "Import as a new skill"]],
  );
  api("skills", { limit: 100 })
    .then((result) => {
      for (const skill of result.skills.filter(
        (s) => s.origin === "imported",
      )) {
        const o = document.createElement("option");
        o.value = JSON.stringify({ version: skill.version, id: skill.id });
        o.textContent = skill.name + " · Version " + skill.version;
        versions.append(o);
      }
    })
    .catch(disconnected);
  const note = document.createElement("p");
  note.className = "wide muted";
  note.textContent =
    "Files stay in this project. Importing does not activate instructions or run scripts.";
  el("editor-fields").append(note);
}
async function openSkill(id) {
  const skill = await api("skill", { id });
  extensionDetail = true;
  selectedId = null;
  detailHistory.length = 0;
  el("detail-back").hidden = true;
  el("detail-title").textContent = skill.name;
  const body = el("detail-body");
  body.replaceChildren();
  metadata(body, { kind: "Skill", status: skill.status });
  const p = document.createElement("p");
  p.textContent = skill.description;
  body.append(p);
  const dl = document.createElement("dl");
  for (const key of ["compatibility", "license", "version", "location"])
    if (skill[key]) field(dl, key, skill[key]);
  body.append(dl);
  if (skill.origin !== "snapshot")
    body.append(actionButton("Select for work", () => selectSkill(skill)));
  const label = document.createElement("label");
  label.textContent = "File";
  const files = document.createElement("select");
  files.setAttribute("aria-label", "Skill file");
  for (const name of skill.files) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    files.append(option);
  }
  files.value = "SKILL.md";
  label.append(files);
  body.append(label);
  const pre = document.createElement("pre");
  pre.className = "skill-source";
  body.append(pre);
  let offset = 0;
  const more = actionButton("Read more", () => load(false));
  body.append(more);
  const load = async (reset) => {
    if (reset) {
      offset = 0;
      pre.textContent = "";
    }
    const result = await api("skill", { id, file: files.value, offset });
    if (result.revision !== skill.revision) {
      pre.textContent =
        "This skill changed while you were reading it. Reopen the skill to read its current version.";
      more.hidden = true;
      return;
    }
    pre.textContent += result.binary
      ? "Binary asset · " + result.bytes + " bytes"
      : result.text;
    offset = result.next_offset;
    more.hidden = !result.more;
  };
  files.addEventListener("change", () => load(true).catch(disconnected));
  await load(true);
  showDetail(false, 0);
}
async function workPicker(name, label, selected = "") {
  const input = editField(
    name,
    label,
    selected,
    [["", "Select an action"]],
    true,
  );
  let offset = 0;
  const more = actionButton("Load more actions", () => load());
  el("editor-fields").append(more);
  const load = async () => {
    const result = await api("board", { limit: 100, offset });
    for (const item of result.cards) {
      const o = document.createElement("option");
      o.value = item.id;
      o.textContent = item.title;
      input.append(o);
    }
    offset += result.cards.length;
    more.hidden = !result.more;
    input.value = selected;
  };
  await load();
  return input;
}
async function selectSkill(skill, work = null, state = "selected") {
  extensionEditor(
    state === "released" ? "Release skill selection" : "Select skill for work",
    "Save selection",
    async (values, key) => {
      const fresh = await api("board", { episode: values.work, limit: 1 });
      await post("actions", {
        operation: "skill_selection",
        request_key: key,
        data: {
          episode_id: values.work,
          expected_version: editorState.workVersion ?? fresh.cards[0].version,
          skill_id: skill.id,
          revision: skill.revision,
          state,
          reason: values.reason,
        },
      });
    },
    () => selectSkill(skill, work, state),
  );
  const picker = await workPicker("work", "Action", work?.id || "");
  const version = async () => {
    if (picker.value) {
      const result = await api("board", { episode: picker.value, limit: 1 });
      editorState.workVersion = result.cards[0].version;
    }
  };
  picker.addEventListener("change", () => version().catch(disconnected));
  await version();
  editField("reason", "Reason for this selection", "", null, true);
}
async function showWorkSkills(parent, work) {
  const result = await api("skill-selections", { episode: work.id });
  if (!result.selections.length) return;
  const section = document.createElement("section");
  section.className = "work-skills";
  const h = document.createElement("h3");
  h.textContent = "Skills";
  section.append(h);
  for (const item of result.selections) {
    const row = document.createElement("div");
    row.className = "attention-row";
    row.append(
      actionButton(item.name, () =>
        openSkill(item.snapshot_source_id || item.skill_id),
      ),
    );
    const p = document.createElement("p");
    p.textContent =
      labels(item.state) +
      " · " +
      labels(item.current_status) +
      ". " +
      item.reason;
    row.append(p);
    if (item.state !== "released")
      row.append(
        actionButton("Release", async () =>
          selectSkill(
            { id: item.skill_id, revision: item.revision },
            work,
            "released",
          ),
        ),
      );
    section.append(row);
  }
  parent.append(section);
}
