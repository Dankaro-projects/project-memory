let extensionDetail = false;
function actionButton(label, action, primary = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (primary) button.className = "primary";
  button.addEventListener("click", () =>
    Promise.resolve().then(action).catch(disconnected),
  );
  return button;
}
function extensionEditor(title, label, save, reload) {
  editorState = { key: crypto.randomUUID(), save, reload };
  el("editor-title").textContent = title;
  el("editor-save").textContent = label;
  el("editor-fields").replaceChildren();
  el("editor-error").hidden = true;
  el("editor-reload").hidden = true;
  if (!el("editor").open) el("editor").showModal();
}
async function saveExtensionEditor(form) {
  const state = editorState;
  el("editor-save").disabled = true;
  el("editor-error").hidden = true;
  try {
    await state.save(Object.fromEntries(new FormData(form)), state.key);
    el("editor").close();
    healthTag = null;
    await poll();
    el("workspace-message").textContent = "The change was saved.";
  } catch (error) {
    el("editor-error").textContent = error.message;
    el("editor-error").hidden = false;
    el("editor-reload").hidden = error.status !== 409 || !state.reload;
  } finally {
    el("editor-save").disabled = false;
  }
}
async function editRequirements() {
  const current = await api("direction");
  extensionEditor(
    "Review project requirements",
    "Approve requirements",
    async (values, key) => {
      const requirements = [
        ...el("requirement-rows").querySelectorAll("textarea"),
      ].map((e) => e.value.trim());
      await post("actions", {
        operation: "requirements",
        request_key: key,
        data: {
          requirements,
          reason: values.reason,
          expected_version: current.version,
        },
      });
    },
    editRequirements,
  );
  const group = document.createElement("section");
  group.className = "wide";
  group.id = "requirement-rows";
  const add = (value) => {
    const row = document.createElement("div");
    row.className = "requirement-row";
    const label = document.createElement("label");
    label.textContent = "Requirement";
    const text = document.createElement("textarea");
    text.value = value;
    text.required = true;
    text.maxLength = 2000;
    label.append(text);
    row.append(
      label,
      actionButton("Remove", () => row.remove()),
    );
    group.append(row);
  };
  for (const text of current.requirements) add(text);
  el("editor-fields").append(
    group,
    actionButton("Add requirement", () => add("")),
  );
  editField("reason", "Reason for this revision", "", null, true);
}
function lessonControl(parent, record) {
  if (parent.querySelector("[data-lesson-control]")) return;
  const button = actionButton("Review lesson", async () => {
    const [fresh, work] = await Promise.all([
      api("record", { id: record.id }),
      api("board", { episode: record.episode_id, limit: 1 }),
    ]);
    extensionEditor(
      "Review lesson",
      "Record review",
      async (values, key) => {
        await post("actions", {
          operation: "lesson_review",
          request_key: key,
          data: {
            lesson_id: record.id,
            expected_version: work.cards[0].version,
            status: values.status,
            reason: values.reason,
          },
        });
      },
      async () => {
        el("editor").close();
        await open(record.id);
      },
    );
    const dl = document.createElement("dl");
    dl.className = "wide";
    for (const key of ["when", "do", "because", "exceptions"])
      field(dl, key, fresh.record.detail.payload[key]);
    el("editor-fields").append(dl);
    editField("status", "Review decision", "", [
      ["", "Select a decision"],
      ["accepted", "Accept"],
      ["rejected", "Reject"],
      ["retired", "Retire"],
    ]);
    editField("reason", "Reason for this review", "", null, true);
  });
  button.dataset.lessonControl = "";
  parent.prepend(button);
}
async function renderExtension() {
  if (!data.live && view !== "map") {
    el("extension-view").textContent =
      "Open the live workspace to manage skills and approvals.";
    return;
  }
  if (view === "skills") return renderSkills();
  if (view === "map") return renderMap();
  const version = ++renderVersion;
  const [blocked, review, lessons, direction] = await Promise.all([
    api("board", { state: "blocked", limit: 10 }),
    api("board", { state: "review", limit: 10 }),
    api("records", { view: "lessons", status: "proposed", limit: 10 }),
    api("direction"),
  ]);
  if (view !== "attention" || version !== renderVersion) return;
  const root = el("extension-view");
  root.replaceChildren();
  const section = (title, count) => {
    const s = document.createElement("section");
    s.className = "attention-group";
    const h = document.createElement("h3");
    h.textContent = title + " · " + count;
    s.append(h);
    root.append(s);
    return s;
  };
  for (const [title, result, state] of [
    ["Blocked work", blocked, "blocked"],
    ["Work to review", review, "review"],
  ]) {
    const group = section(title, result.total);
    for (const card of result.cards) {
      const line = document.createElement("div");
      line.className = "attention-row";
      line.append(actionButton(card.title, () => openWork(card.id)));
      const p = document.createElement("p");
      p.textContent =
        card.issues.map((i) => i.reason).join(" ") || card.plan?.reason || "";
      line.append(p);
      group.append(line);
    }
    if (result.more)
      group.append(
        actionButton("View all", () => {
          setView("board");
          el("status").value = state;
          render();
        }),
      );
    if (!result.total) {
      const p = document.createElement("p");
      p.textContent = "No work needs attention here.";
      group.append(p);
    }
  }
  const checks = (captureHealth?.recording_checks?.sessions || []).filter(
    (s) => s.status === "needs_attention",
  );
  if (checks.length || captureHealth?.capture?.failure) {
    const recording = section("Recording checks", checks.length);
    if (captureHealth?.capture?.failure) {
      const p = document.createElement("p");
      p.textContent = "Host capture failed. Some activity may be missing.";
      recording.append(p);
    }
    for (const check of checks)
      recording.append(
        actionButton(check.issues.map((i) => labels(i.type)).join(", "), () =>
          showCoverage(check.session_id),
        ),
      );
  }
  const group = section("Lessons awaiting review", lessons.total);
  for (const record of lessons.records)
    group.append(actionButton(record.title, () => open(record.id)));
  if (lessons.more)
    group.append(actionButton("View all lessons", () => setView("lessons")));
  const baseline = section(
    "Project requirements",
    direction.requirements.length,
  );
  const p = document.createElement("p");
  p.textContent =
    direction.status === "needs_review"
      ? "Supporting evidence has changed. Review the requirements."
      : "Review the complete requirements before approving a revision.";
  baseline.append(p, actionButton("Review requirements", editRequirements));
}
el("edit-requirements").addEventListener("click", () =>
  editRequirements().catch(disconnected),
);
