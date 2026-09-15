async function post(endpoint, payload) {
  const response = await fetch("api/" + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Project-Memory": csrf },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(15000),
  });
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(value.message || "The change could not be saved.");
    error.status = response.status;
    throw error;
  }
  return value;
}
function editField(name, label, value = "", choices = null, wide = false) {
  const wrap = document.createElement("label");
  wrap.textContent = label;
  if (wide) wrap.className = "wide";
  let input;
  if (choices) {
    input = document.createElement("select");
    for (const [id, title] of choices) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = title;
      input.append(option);
    }
  } else if (
    [
      "objective",
      "criterion",
      "scope",
      "next_action",
      "reason",
      "text",
    ].includes(name)
  )
    input = document.createElement("textarea");
  else {
    input = document.createElement("input");
    input.type = name.endsWith("_on") ? "date" : "text";
  }
  input.id = "edit-" + name;
  input.name = name;
  input.value = value ?? "";
  input.required = !["sprint_id"].includes(name);
  if (!choices) input.maxLength = 2000;
  wrap.append(input);
  el("editor-fields").append(wrap);
  return input;
}
async function editWork(kind, work = null) {
  editorState = { kind, work, key: crypto.randomUUID(), dependencies: [] };
  el("editor-fields").replaceChildren();
  el("editor-error").hidden = true;
  el("editor-reload").hidden = true;
  el("editor-title").textContent =
    kind === "comment"
      ? "Add comment"
      : kind === "sprint"
        ? work
          ? "Edit sprint"
          : "New sprint"
        : work
          ? "Edit plan"
          : "New action";
  el("editor-save").textContent =
    kind === "comment"
      ? "Add comment"
      : work
        ? "Save changes"
        : "Create " + (kind === "sprint" ? "sprint" : "action");
  const pick = (items) => items.map((s) => [s, labels(s)]);
  if (kind === "comment") editField("text", "Comment", "", null, true);
  else {
    if (!work) {
      editField("title", "Title", "", null, true);
      editField("objective", "Intended result", "", null, true);
      editField("criterion", "Completion criterion", "", null, true);
      if (kind === "plan")
        editField(
          "subject",
          "Subject",
          "code",
          pick(["code", "writing", "research", "general"]),
        );
    }
    if (kind === "sprint") {
      const schedule = work?.schedule || {},
        today = new Date().toISOString().slice(0, 10);
      editField("starts_on", "Starts on", schedule.starts_on || today);
      editField("ends_on", "Ends on", schedule.ends_on || today);
      editField(
        "sprint_status",
        "Status",
        schedule.status || "planned",
        pick(["planned", "active", "closed"]),
      );
      editField(
        "reason",
        "Reason for this plan",
        schedule.reason || "",
        null,
        true,
      );
    } else {
      const plan = work?.plan || {};
      editField(
        "owner",
        "Responsible",
        plan.owner || "agent",
        pick(["agent", "human"]),
      );
      editField("state", "Status", plan.state || "backlog", pick(workStates));
      editField(
        "priority",
        "Priority",
        plan.priority || "normal",
        pick(["high", "normal", "low"]),
      );
      editField("autonomy", "Agent scope", plan.autonomy || "suggest", [
        ["suggest", "Prepare a proposal"],
        ["act", "Act within the recorded scope"],
      ]);
      editField("scope", "Scope and exceptions", plan.scope || "", null, true);
      editField(
        "next_action",
        "Next action",
        plan.next_action || "",
        null,
        true,
      );
      editField(
        "sprint_id",
        "Sprint",
        plan.sprint_id || "",
        [
          ["", "No sprint"],
          ...sprintRows
            .filter(
              (s) => s.schedule?.status !== "closed" || s.id === plan.sprint_id,
            )
            .map((s) => [s.id, s.title]),
        ],
        true,
      );
      const group = document.createElement("section");
      group.className = "wide";
      const heading = document.createElement("h3");
      heading.textContent = "Dependencies";
      group.append(heading);
      const rows = document.createElement("div");
      rows.id = "dependency-rows";
      group.append(rows);
      const add = document.createElement("button");
      add.type = "button";
      add.textContent = "Add dependency";
      add.addEventListener("click", () => addDependency());
      group.append(add);
      el("editor-fields").append(group);
      editorState.dependencies = plan.depends_on || [];
      for (const ref of editorState.dependencies) await addDependency(ref);
      editField(
        "reason",
        "Reason for this plan",
        plan.reason || "",
        null,
        true,
      );
      const ownership = () => {
        const inProgress = [...el("edit-state").options].find(
          (o) => o.value === "in_progress",
        );
        inProgress.disabled =
          el("edit-owner").value === "agent" && plan.state !== "in_progress";
      };
      el("edit-owner").addEventListener("input", ownership);
      ownership();
    }
  }
  if (!el("editor").open) el("editor").showModal();
  el("editor").querySelector("input,textarea,select")?.focus();
}
async function addDependency(ref = null) {
  const row = document.createElement("div");
  row.className = "dependency-row";
  const label = document.createElement("label");
  label.textContent = "Depends on";
  const select = document.createElement("select");
  select.required = true;
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Select an action";
  select.append(empty);
  label.append(select);
  let offset = 0;
  const more = document.createElement("button");
  more.type = "button";
  more.textContent = "Load more actions";
  const load = async () => {
    const page = await api("board", { limit: 100, offset });
    for (const item of page.cards) {
      if (item.id === editorState.work?.id) continue;
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.title;
      select.append(option);
    }
    offset += page.cards.length;
    more.hidden = !page.more;
  };
  more.addEventListener("click", () =>
    load().catch((error) => {
      el("editor-error").hidden = false;
      el("editor-error").textContent = error.message;
    }),
  );
  label.append(more);
  const why = document.createElement("label");
  why.textContent = "Dependency reason";
  const text = document.createElement("input");
  text.required = true;
  text.maxLength = 2000;
  text.value = ref?.reason || "";
  why.append(text);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => row.remove());
  row.append(label, why, remove);
  el("dependency-rows").append(row);
  await load();
  if (ref) {
    if (![...select.options].some((o) => o.value === ref.episode_id)) {
      const page = await api("board", { episode: ref.episode_id, limit: 1 });
      for (const item of page.cards) {
        const option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.title;
        select.append(option);
      }
    }
    select.value = ref.episode_id;
  }
}
el("work-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (editorState.save) {
    await saveExtensionEditor(event.target);
    return;
  }
  const { kind, work, key } = editorState,
    values = Object.fromEntries(new FormData(event.target));
  let content = {};
  if (work) content = { episode_id: work.id, expected_version: work.version };
  else
    content = {
      title: values.title,
      objective: values.objective,
      criterion: values.criterion,
      subject: values.subject || "general",
    };
  if (kind === "comment") content.text = values.text;
  else if (kind === "sprint")
    content.payload = {
      starts_on: values.starts_on,
      ends_on: values.ends_on,
      status: values.sprint_status,
      reason: values.reason,
    };
  else {
    content.payload = Object.fromEntries(
      [
        "state",
        "next_action",
        "scope",
        "autonomy",
        "reason",
        "owner",
        "priority",
      ].map((k) => [k, values[k]]),
    );
    content.payload.sprint_id = values.sprint_id || null;
    content.payload.depends_on = [...el("dependency-rows").children].map(
      (row) => ({
        episode_id: row.querySelector("select").value,
        reason: row.querySelector("input").value,
      }),
    );
    if (work?.plan?.session_id && values.state === "in_progress")
      content.payload.session_id = work.plan.session_id;
  }
  el("editor-save").disabled = true;
  el("editor-error").hidden = true;
  try {
    await post("actions", { operation: kind, data: content, request_key: key });
    el("editor").close();
    el("workspace-message").textContent =
      kind === "comment"
        ? "The comment was added."
        : "The " + (kind === "sprint" ? "sprint" : "plan") + " was saved.";
    healthTag = null;
    await poll();
  } catch (error) {
    el("editor-error").hidden = false;
    el("editor-error").textContent = error.message;
    el("editor-reload").hidden = error.status !== 409;
  } finally {
    el("editor-save").disabled = false;
  }
});
el("editor-cancel").addEventListener("click", () => el("editor").close());
el("editor-reload").addEventListener("click", async () => {
  el("editor-save").disabled = true;
  el("editor-reload").disabled = true;
  try {
    if (editorState.reload) {
      await editorState.reload();
      return;
    }
    const { kind, work } = editorState;
    const result = await api(kind === "sprint" ? "sprints" : "board", {
      episode: work.id,
      limit: 1,
    });
    await editWork(kind, (result.cards || result.sprints)[0]);
  } catch (error) {
    el("editor-error").textContent = error.message;
    el("editor-error").hidden = false;
  } finally {
    el("editor-save").disabled = false;
    el("editor-reload").disabled = false;
  }
});
el("new-work").addEventListener("click", () => editWork("plan"));
el("new-sprint").addEventListener("click", () => editWork("sprint"));
el("edit-sprint").addEventListener("click", () =>
  editWork(
    "sprint",
    sprintRows.find((s) => s.id === el("sprint").value),
  ),
);
