/*
 * Project Memory control panel: forms.js, every form that changes records or starts agent runs.
 *
 * Each form is registered with Panel.registerForm under the name of its workspace action, so views open them with
 * Panel.openForm(name, context, trigger), or with Panel.formButton(label, name, context, attrs), which returns null
 * when the panel cannot edit. Every form loads its current data when it opens, so the context carries ids only:
 *   plan {episode_id} edits a work item, {parent_id, item_type} or {} creates one; sprint {episode_id} or {};
 *   comment {episode_id}; requirements {}; lesson_review {lesson_id, status} with triggers on acceptance;
 *   allow_paths {episode_id, paths, reason}; component {component_id}, or {kind, path} to add one;
 *   confirm_component {component_id}; answer_kickoff {question_ids}; delegate {episode_id}; merge, discard and
 *   request_work_review {run_id}; review {episode_id, role}; cancel_run {run_id};
 *   instructions {role} edits the base instruction text of an agent role.
 *
 * A form that cannot act on the current data explains why in the form alert and disables Save.
 */
(() => {
  "use strict";
  const P = Panel;
  const h = P.h;
  const STATES = ["backlog", "ready", "in_progress", "blocked", "review", "done", "cancelled"];
  const ITEM_TYPES = ["phase", "epic", "story", "task", "research", "deliverable", "workflow"];
  const SUBJECTS = ["general", "code", "writing", "research"];
  const ACTIVE = ["queued", "running", "cancelling"];
  const RULE_ROLES = ["assistant", "worker", "reviewer"];
  const COMPONENT_KINDS = ["system", "component", "service", "workflow", "integration", "dataset", "stakeholder", "workstream", "deliverable", "process"];

  // Helpers.
  const need = (value, message) => {
    if (!value || (Array.isArray(value) && !value.length)) throw new P.FormError(message);
    return value;
  };
  const stop = (message) => { throw new Error(message); };
  const hint = (text) => h("p", { class: "hint" }, text);
  const notice = (...children) => h("div", { class: "notice" }, children.map((child) => (typeof child === "string" ? h("p", null, child) : child)));
  const group = (legend, ...children) => h("fieldset", null, h("legend", null, legend), children);
  const chips = (values) => (values && values.length ? h("div", { class: "row" }, values.map((value) => h("span", { class: "chip mono" }, value))) : "None recorded");
  const kv = (entries) => h("dl", { class: "kv" }, entries.filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([label, value]) => [h("dt", null, label), h("dd", null, value)]));
  const list = (name, value, rows = "3") => P.textarea(name, value || [], { rows, dataset: { list: "" } });
  const area = (name, value, rows = "3") => P.textarea(name, value, { rows });
  const roleChecks = (chosen) => h("div", { class: "choices" }, RULE_ROLES.map((role) =>
    h("label", { class: "field check" }, h("input", { type: "checkbox", name: "role_" + role, checked: (chosen || []).includes(role) }),
      h("span", null, P.words(role)))));
  function choices(name, options, value) {
    return h("div", { class: "choices", role: "radiogroup" }, options.map(([optionValue, label, detail, disabled]) =>
      h("label", { class: "field check" }, h("input", { type: "radio", name, value: optionValue, checked: optionValue === value, disabled: Boolean(disabled) }),
        h("span", null, label, detail ? h("small", { class: "hint" }, detail) : null))));
  }
  function number(values, name, low, high, unit) {
    const value = values[name];
    if (value === null || value === undefined) return undefined;
    if (!Number.isInteger(value) || value < low || value > high) throw new P.FormError(`The time limit must be a whole number of ${unit} from ${low} to ${high}.`);
    return value;
  }
  const lower = (value) => value.charAt(0).toLowerCase() + value.slice(1);
  const work = async (id) => (await P.get("work", { id })).card;
  const run = async (id) => (await P.get("run", { id })).run;
  async function workItems(exclude) {
    const plan = await P.get("plan");
    const items = [];
    const walk = (nodes, depth) => {
      for (const node of nodes || []) {
        if (node.id === exclude) continue;
        items.push({ id: node.id, title: node.title, depth });
        walk(node.children, depth + 1);
      }
    };
    walk(plan.roots, 0);
    return items;
  }
  const indent = (item) => "  ".repeat(item.depth) + item.title;
  function withOption(options, value, label) {
    return value && !options.some(([id]) => id === value) ? [...options, [value, label || value]] : options;
  }
  function hostSentence(host) {
    const name = P.words(host.host);
    if (!host.installed) return `The ${name} program is not installed on this computer.`;
    if (host.available) return `${name} is available.`;
    return `${name} is unavailable${host.until ? " until " + P.date(host.until) : ""}.` + (host.reason ? " " + host.reason : "");
  }
  function runFacts(value) {
    return kv([["Role", P.words(value.role)], ["Host", P.words(value.host)], ["State", P.badge(value.state)],
      ["Summary", value.summary], ["Changed files", value.role === "work" ? String(value.changed_files || 0) : null],
      ["Work review", value.role !== "work" ? null : value.review ? P.badge(value.review.state, P.words(value.review.state) + " on " + P.words(value.review.host)) : "No work review is recorded."],
      ["Merge", value.merge ? P.badge(value.merge.state) : null], ["Allowed paths", value.paths ? chips(value.paths) : null]]);
  }
  function requireHosts() {
    const health = P.health() || {};
    if (!health.review_host) stop("No agent host is configured for this project. Run project-memory setup with the Codex or Claude client, then open this form again.");
    return health;
  }
  const reloadIds = (names) => (context) => Object.fromEntries(names.filter((name) => context[name] !== undefined).map((name) => [name, context[name]]));

  // Buttons that open forms. They are absent when the panel cannot edit, so a snapshot shows no actions.
  P.formButton = (label, name, context = {}, attrs = {}) => {
    if (!P.canEdit()) return null;
    const ids = Object.values(context).filter((value) => typeof value === "string").join(":");
    return h("button", { type: "button", ...attrs, dataset: { key: "form:" + name + (ids ? ":" + ids : ""), ...(attrs.dataset || {}) },
      on: { click: (event) => P.openForm(name, context, event.currentTarget) } }, label);
  };

  // Plan: create or edit a work item.
  P.registerForm("plan", {
    title: (context) => (context.episode_id ? "Edit the plan" : "Add a " + lower(P.term("work_item"))),
    submitLabel: "Save the plan",
    async render(fields, context, form) {
      const [card, items, sprints] = await Promise.all([context.episode_id ? work(context.episode_id) : null, workItems(context.episode_id),
        P.get("sprints", { limit: "100" }).then((value) => value.sprints || [], () => [])]);
      context.card = card;
      const plan = (card && card.plan) || {};
      if (card) {
        fields.append(kv([["Title", card.title], ["Intended result", card.intent], ["Completion criterion", card.done_when], ["Subject", P.words(card.subject)]]),
          hint("The intended result and the completion criterion of an existing work item are fixed. Create a linked work item when the objective changes."));
      } else {
        fields.append(P.field("Title", P.input("title", "", { required: true })),
          P.field("Intended result", area("objective", "", "2"), "Describe the result that this work item delivers."),
          P.field("Completion criterion", area("criterion", "", "2"), "Describe how you will know that the work item is finished."),
          P.field("Subject", P.select("subject", SUBJECTS, "general")));
      }
      const owner = P.select("owner", [["agent", "An agent"], ["human", "A person"]], plan.owner || "agent");
      const state = P.select("state", STATES, plan.state || "backlog");
      const stateField = P.field("State", state);
      const stateHint = stateField.appendChild(h("small", { hidden: true }));
      const syncState = () => {
        const locked = owner.value === "agent" && plan.state !== "in_progress";
        [...state.options].find((option) => option.value === "in_progress").disabled = locked;
        stateHint.hidden = !locked;
        stateHint.textContent = locked ? "In progress is set by the agent session that claims the work. Select Ready to queue the work for an agent." : "";
      };
      owner.addEventListener("change", syncState);
      syncState();
      const parentOptions = withOption([["", "No parent"], ...items.map((item) => [item.id, indent(item)])], context.parent_id || plan.parent_id);
      const sprintOptions = withOption([["", "No sprint"], ...sprints.filter((sprint) => (sprint.schedule || {}).status !== "closed" || sprint.id === plan.sprint_id)
        .map((sprint) => [sprint.id, sprint.title])], plan.sprint_id);
      fields.append(h("div", { class: "form-grid" },
        P.field("Item type", P.select("item_type", ITEM_TYPES, context.item_type || plan.item_type || "task")),
        P.field("Parent", P.select("parent_id", parentOptions, context.parent_id || plan.parent_id || "")),
        P.field("Responsible", owner), stateField,
        P.field("Priority", P.select("priority", ["high", "normal", "low"], plan.priority || "normal")),
        P.field("Agent autonomy", P.select("autonomy", [["suggest", "Prepare a proposal"], ["act", "Act within the scope and allowed paths"]], plan.autonomy || "suggest")),
        P.field("Sprint", P.select("sprint_id", sprintOptions, plan.sprint_id || ""))));
      fields.append(P.field("Scope", area("scope", plan.scope), "Describe what the work includes, and any exceptions."),
        P.field("Next action", area("next_action", plan.next_action, "2")),
        P.field("Acceptance criteria", list("acceptance", plan.acceptance), "Write one complete sentence per line."),
        P.field("Allowed paths", list("paths", plan.paths), "Write one pattern per line, such as docs/** or workflows/. A pattern without wildcards also allows everything beneath it. Agents can change only files that match these patterns."));
      const rows = h("div", { class: "stack" });
      const dependencyOptions = [["", "Select a work item"], ...items.map((item) => [item.id, item.title])];
      const addRow = (ref = {}) => {
        const row = h("div", { class: "dependency-row" },
          P.field("Depends on", P.select("dependency", withOption(dependencyOptions, ref.episode_id), ref.episode_id || "")),
          P.field("Reason", P.input("dependency_reason", ref.reason)),
          h("button", { type: "button", class: "small quiet", on: { click: () => row.remove() } }, "Remove"));
        rows.append(row);
        return row;
      };
      for (const ref of plan.depends_on || []) addRow(ref);
      fields.append(group("Dependencies", hint("This work item waits until each prerequisite is complete."), rows,
        h("div", null, h("button", { type: "button", class: "small", on: { click: () => addRow().querySelector("select").focus() } }, "Add a dependency"))));
      fields.append(P.field("Reason for this change", area("reason", "", "2"), "The reason is recorded with this revision of the plan."));
    },
    submit(values, context, form) {
      const card = context.card;
      const old = (card && card.plan) || {};
      if (!card) {
        need(values.title, "Write the title.");
        need(values.objective, "Write the intended result.");
        need(values.criterion, "Write the completion criterion.");
      }
      need(values.scope, "Write the scope of the work.");
      need(values.next_action, "Write the next action.");
      need(values.reason, "Write the reason for this change.");
      if (values.owner === "agent" && values.state === "in_progress" && old.state !== "in_progress") {
        throw new P.FormError("Only an active agent session can move agent work to In progress. Select Ready to queue the work, or make a person responsible.");
      }
      const dependsOn = [];
      for (const row of form.querySelectorAll(".dependency-row")) {
        const episodeId = row.querySelector("select").value;
        const reason = row.querySelector("input").value.trim();
        if (!episodeId && !reason) continue;
        if (!episodeId || !reason) throw new P.FormError("Select the prerequisite and write the reason for each dependency.");
        dependsOn.push({ episode_id: episodeId, reason });
      }
      const payload = { state: values.state, next_action: values.next_action, scope: values.scope, autonomy: values.autonomy, reason: values.reason,
        owner: values.owner, priority: values.priority, item_type: values.item_type };
      if (values.parent_id) payload.parent_id = values.parent_id;
      if (values.sprint_id) payload.sprint_id = values.sprint_id;
      if (dependsOn.length) payload.depends_on = dependsOn;
      if (values.acceptance.length) payload.acceptance = values.acceptance;
      if (values.paths.length) payload.paths = values.paths;
      if (values.state === "in_progress" && old.state === "in_progress" && old.session_id) payload.session_id = old.session_id;
      if (card) return { operation: "plan", data: { episode_id: card.id, expected_version: card.version, payload } };
      return { operation: "plan", data: { title: values.title, objective: values.objective, criterion: values.criterion, subject: values.subject, payload } };
    },
    reload: reloadIds(["episode_id", "parent_id", "item_type"]),
    done: (result, context) => (context.episode_id ? "The plan is saved." : "The " + lower(P.term("work_item")) + " is created."),
  });

  // Sprint.
  P.registerForm("sprint", {
    title: (context) => (context.episode_id ? "Edit the sprint" : "Add a sprint"),
    submitLabel: "Save the sprint",
    async render(fields, context) {
      let sprint = null;
      if (context.episode_id) {
        sprint = ((await P.get("sprints", { episode: context.episode_id, limit: "1" })).sprints || [])[0];
        if (!sprint) stop("The sprint was not found. Close this form and select the sprint again.");
      }
      context.sprint = sprint;
      const schedule = (sprint && sprint.schedule) || {};
      const today = new Date().toISOString().slice(0, 10);
      if (sprint) fields.append(kv([["Title", sprint.title], ["Intended result", sprint.intent]]));
      else {
        fields.append(P.field("Title", P.input("title", "")), P.field("Intended result", area("objective", "", "2")),
          P.field("Completion criterion", area("criterion", "", "2")));
      }
      fields.append(h("div", { class: "form-grid" }, P.field("Starts on", P.input("starts_on", schedule.starts_on || today, { type: "date" })),
        P.field("Ends on", P.input("ends_on", schedule.ends_on || today, { type: "date" })),
        P.field("Status", P.select("status", ["planned", "active", "closed"], schedule.status || "planned"))),
      P.field("Reason for this change", area("reason", "", "2")));
    },
    submit(values, context) {
      if (!context.sprint) {
        need(values.title, "Write the title.");
        need(values.objective, "Write the intended result.");
        need(values.criterion, "Write the completion criterion.");
      }
      need(values.starts_on && values.ends_on, "Select the start and end dates.");
      if (values.starts_on > values.ends_on) throw new P.FormError("The sprint must start on or before its end date.");
      need(values.reason, "Write the reason for this change.");
      const payload = { starts_on: values.starts_on, ends_on: values.ends_on, status: values.status, reason: values.reason };
      if (context.sprint) return { operation: "sprint", data: { episode_id: context.sprint.id, expected_version: context.sprint.version, payload } };
      return { operation: "sprint", data: { title: values.title, objective: values.objective, criterion: values.criterion, payload } };
    },
    reload: reloadIds(["episode_id"]),
    done: () => "The sprint is saved.",
  });

  // Comment on a work item.
  P.registerForm("comment", {
    title: "Add a comment",
    submitLabel: "Save the comment",
    async render(fields, context) {
      const { record } = await P.get("record", { id: context.episode_id });
      context.version = record.detail.version;
      fields.append(kv([[P.term("work_item"), record.title]]), P.field("Comment", area("text", "", "5")));
    },
    submit(values, context) {
      need(values.text, "Write the comment.");
      return { operation: "comment", data: { episode_id: context.episode_id, expected_version: context.version, text: values.text } };
    },
    reload: reloadIds(["episode_id"]),
    done: () => "The comment is saved.",
  });

  // Requirements approval.
  P.registerForm("requirements", {
    title: "Review the requirements",
    submitLabel: "Approve these requirements",
    async render(fields, context) {
      const value = await P.get("requirements", { limit: "100" });
      const items = value.items || {};
      context.version = items.version;
      const established = items.status !== "not_established";
      const texts = established ? (items.items || []).map((item) => item.text) : [];
      fields.append(hint(established
        ? `The current requirements are version ${items.version} with ${P.count(items.total, "requirement")}. Saving approves the complete list below as the next version.`
        : "No requirements baseline is approved yet. Write the complete list that you approve."));
      if (items.total > 100) fields.append(notice("The project has more than 100 requirements. An approval can contain at most 100, so combine related requirements first."));
      if (texts.some((text) => text.includes("\n"))) fields.append(notice("Some requirements contain line breaks. Each line below becomes a separate requirement, so join those lines before saving."));
      fields.append(P.field("Requirements", list("requirements", texts, "10"), "Write one complete requirement per line."),
        P.field("Reason for the approval", area("reason", "", "2")));
    },
    submit(values, context) {
      need(values.requirements, "Write at least one requirement.");
      need(values.reason, "Write the reason for the approval.");
      return { operation: "requirements", data: { requirements: values.requirements, reason: values.reason, expected_version: context.version } };
    },
    reload: () => ({}),
    done: (result) => `Requirements version ${result.version} is approved.`,
  });

  // Base instruction text of one agent role. Every earlier version stays, so an earlier text is saved again to return to it.
  P.registerForm("instructions", {
    title: (context) => "Edit the " + context.role + " instructions",
    submitLabel: "Save the base text",
    async render(fields, context) {
      const value = ((await P.get("learning")).instructions || { roles: {} }).roles[context.role];
      if (!value) stop("This role has no instruction text in this panel.");
      context.saved = value.base || "";
      context.project = String(value.base_source || "").indexOf("instructions-base:") === 0;
      fields.append(kv([["Role", P.words(context.role)], ["In force", value.base_source],
        ["Version", value.base_version === null || value.base_version === undefined ? "The shipped text" : String(value.base_version)],
        ["Rules composed now", String((value.rule_ids || []).length)]]),
      hint("This text is the base of every " + context.role + " prompt. The accepted rules are added below it and are not edited here. "
        + "Saving writes a new version and keeps every earlier one."),
      P.field("Base text", area("text", context.saved, "16")),
      P.field("Reason", area("reason", "", "2")));
    },
    submit(values, context) {
      need(values.text, "Write the base text of this role.");
      if (context.project && values.text.trim() === context.saved.trim()) throw new P.FormError("The saved text of this role is already this text.");
      const data = { role: context.role, text: values.text };
      if (values.reason) data.reason = values.reason;
      return { operation: "instructions", data };
    },
    reload: reloadIds(["role"]),
    done: (result) => "Version " + result.version + " of the " + result.role + " instructions is saved.",
  });

  // Lesson review with triggers on acceptance.
  P.registerForm("lesson_review", {
    title: "Review the lesson",
    submitLabel: "Save the review",
    async render(fields, context, form) {
      const { record } = await P.get("record", { id: context.lesson_id });
      if (record.kind !== "lesson") stop("This record is not a lesson.");
      const episode = (await P.get("record", { id: record.episode_id })).record;
      const lesson = record.detail.payload || {};
      context.version = episode.detail.version;
      context.hadTriggers = Boolean((lesson.paths || []).length || (lesson.keywords || []).length || lesson.failure_type);
      fields.append(h("div", { class: "row" }, P.badge(record.status)),
        kv([["When", lesson.when], ["Do", lesson.do], ["Because", lesson.because], ["Exceptions", lesson.exceptions]]));
      const status = context.status || (record.status === "accepted" ? "retired" : "accepted");
      fields.append(P.field("Decision", choices("status", [["accepted", "Accept the lesson", "Agents receive it as a guard when its triggers match."],
        ["rejected", "Reject the lesson", "The lesson stays in the records and does not guide agents."],
        ["retired", "Retire the lesson", "An accepted lesson stops guiding agents."]], status)));
      const triggers = group("Triggers",
        hint("A guard reminds agents of this lesson when a change touches one of the paths, the text contains a keyword, or a failure has this type. An accepted lesson without triggers is not shown as a guard."),
        P.field("Paths", list("paths", lesson.paths), "Write one pattern per line, such as src/export/** or deliverables/."),
        P.field("Keywords", list("keywords", lesson.keywords, "2"), "Write one keyword per line. A keyword matches as a whole word."),
        P.field("Failure type", P.input("failure_type", lesson.failure_type), "Use the failure type that outcomes record, such as encoding_error."),
        P.field("Roles", roleChecks(lesson.roles), "A lesson with a role becomes a rule in the prompt of that role. A lesson without a role stays a guard."));
      const sync = () => {
        const accepted = (form.querySelector('input[name="status"]:checked') || {}).value === "accepted";
        triggers.hidden = !accepted;
        for (const control of triggers.querySelectorAll("input, textarea")) control.disabled = !accepted;
      };
      for (const radio of fields.querySelectorAll('input[name="status"]')) radio.addEventListener("change", sync);
      fields.append(triggers, P.field("Reason", area("reason", "", "2")));
      sync();
    },
    submit(values, context) {
      need(values.status, "Select whether to accept, reject or retire the lesson.");
      need(values.reason, "Write the reason for your decision.");
      context.decision = values.status;
      const data = { lesson_id: context.lesson_id, expected_version: context.version, status: values.status, reason: values.reason };
      // Triggers on a review replace all triggers of the lesson, so they are sent together.
      const roles = RULE_ROLES.filter((role) => values["role_" + role]);
      if (values.status === "accepted" && (values.paths.length || values.keywords.length || values.failure_type || roles.length || context.hadTriggers)) {
        Object.assign(data, { paths: values.paths, keywords: values.keywords });
        if (values.failure_type) data.failure_type = values.failure_type;
        if (roles.length) data.roles = roles;
      }
      return { operation: "lesson_review", data };
    },
    reload: reloadIds(["lesson_id", "status"]),
    done: (result, context) => ({ accepted: "The lesson is accepted.", rejected: "The lesson is rejected.", retired: "The lesson is retired." })[context.decision] || "The review is saved.",
  });

  // Allow more paths, for example after a scope block.
  P.registerForm("allow_paths", {
    title: "Allow more paths",
    submitLabel: "Allow these paths",
    async render(fields, context) {
      const card = await work(context.episode_id);
      context.version = card.version;
      fields.append(kv([[P.term("work_item"), card.title], ["Allowed paths", chips((card.plan || {}).paths)]]));
      if (!card.plan) stop("Record a plan for this work item before allowing paths.");
      fields.append(hint("Agents can change only files that match the allowed paths. Add the folders or files that the work needs, not the whole project."),
        P.field("Paths to allow", list("paths", context.paths), "Write one pattern per line."),
        P.field("Reason", area("reason", context.reason, "2")));
    },
    submit(values, context) {
      need(values.paths, "Write at least one path to allow.");
      need(values.reason, "Write the reason for allowing these paths.");
      return { operation: "allow_paths", data: { episode_id: context.episode_id, expected_version: context.version, paths: values.paths, reason: values.reason } };
    },
    reload: reloadIds(["episode_id", "paths", "reason"]),
    done: (result) => "The allowed paths now include " + (result.added || []).join(", ") + ".",
  });

  // Authored components.
  async function authored(id) {
    const value = await P.get("components", { limit: "500" });
    const found = (value.components || []).find((item) => item.id === id);
    if (!found) stop("The component was not found. It may have been renamed; close this form and select it again.");
    return found;
  }
  const defaultKind = () => ({ engagement: "workstream", automation: "system" })[P.template()] || "component";
  P.registerForm("component", {
    title: (context) => (context.component_id ? "Edit " : "Add a ") + lower(P.term("component")),
    submitLabel: "Save",
    async render(fields, context) {
      const item = context.component_id ? await authored(context.component_id) : {};
      fields.append(P.field("Title", P.input("title", item.title)),
        h("div", { class: "form-grid" }, P.field("Kind", P.select("kind", COMPONENT_KINDS, item.kind || context.kind || defaultKind())),
          P.field("Status", P.select("status", [["proposed", "Proposed"], ["confirmed", "Confirmed by you"], ["retired", "Retired"]], item.status || "proposed"))),
        P.field("Description", area("description", item.description, "4")),
        P.field("Project path", P.input("path", item.path || context.path),
          "Optional. Enter the part of the project that this item describes, such as src/app, workflows, service:slack or "
          + "n8n:workflows/lead-intake.json. Open the Architecture view first and copy the exact name it shows, because a "
          + "service name comes from the workflow export. An item without a path, such as a stakeholder or a workstream, "
          + "shows its work only when a link records the relation."));
    },
    submit(values, context) {
      need(values.title, "Write the title.");
      need(values.description, "Write the description.");
      const data = { title: values.title, kind: values.kind, status: values.status, description: values.description };
      if (context.component_id) data.component_id = context.component_id;
      if (values.path) data.path = values.path;
      return { operation: "component", data };
    },
    reload: reloadIds(["component_id", "kind", "path"]),
    done: () => "The " + lower(P.term("component")) + " is saved.",
  });
  P.registerForm("confirm_component", {
    title: () => "Confirm the " + lower(P.term("component")),
    submitLabel: "Confirm",
    async render(fields, context) {
      const item = await authored(context.component_id);
      context.item = item;
      fields.append(kv([["Title", item.title], ["Kind", P.words(item.kind)], ["Status", P.badge(item.status)], ["Description", item.description],
        ["Project path", item.path], ["Proposed by", item.actor]]),
      hint("Confirming records that this item describes the project correctly. Agents cannot change a confirmed item. Confirming grants no permission to change files."));
      if (item.status === "confirmed") stop("This item is already confirmed.");
    },
    submit(values, context) {
      const item = context.item;
      const data = { component_id: item.id, title: item.title, kind: item.kind, description: item.description, status: "confirmed" };
      if (item.path) data.path = item.path;
      return { operation: "component", data };
    },
    reload: reloadIds(["component_id"]),
    done: (result) => result.title + " is confirmed.",
  });

  // Kickoff answers.
  P.registerForm("answer_kickoff", {
    title: "Answer kickoff questions",
    submitLabel: "Record the answer",
    async render(fields, context) {
      const kickoff = await P.get("kickoff");
      if (!kickoff.template) stop("This project was not created from a template, so it has no kickoff questions.");
      context.questions = kickoff.questions || [];
      const open = context.questions.filter((question) => !question.answered);
      const selected = context.question_ids || (open[0] ? [open[0].id] : []);
      fields.append(hint("Select the questions that your answer covers. The answer is recorded as a note in the first phase."),
        group("Questions", context.questions.map((question) => P.field(question.text + (question.answered ? " (answered)" : ""),
          h("input", { type: "checkbox", name: "question:" + question.id, checked: selected.includes(question.id) })))),
        P.field("Answer", area("text", "", "6")));
    },
    submit(values, context) {
      const ids = context.questions.filter((question) => values["question:" + question.id]).map((question) => question.id);
      need(ids, "Select at least one question that the answer covers.");
      need(values.text, "Write the answer.");
      return { operation: "answer_kickoff", data: { question_ids: ids, text: values.text } };
    },
    reload: reloadIds(["question_ids"]),
    done: (result, context) => "The answer is recorded.",
  });

  // Delegate a work item to an agent host.
  P.registerForm("delegate", {
    title: "Delegate the work",
    submitLabel: "Delegate",
    async render(fields, context) {
      const card = await work(context.episode_id);
      const plan = card.plan || {};
      const health = P.health() || {};
      fields.append(kv([[P.term("work_item"), card.title], ["Scope", plan.scope], ["Allowed paths", chips(plan.paths)]]),
        hint("An agent works on a separate branch in a git worktree and can change only files that match the allowed paths. Another agent host reviews the result, and nothing is merged until you decide."));
      const hosts = health.hosts || [];
      fields.append(P.field("Agent host", choices("host", [["", "Choose an available host", "Project Memory uses the configured host first and the other host when it is unavailable."],
        ...hosts.map((host) => [host.host, P.words(host.host), hostSentence(host), !host.installed])], "")),
      P.field("Time limit in seconds", P.input("max_seconds", "", { type: "number", min: "60", max: "14400", step: "60", placeholder: "1800", dataset: { number: "" } }),
        "Optional. The run stops after this time. The default is 1800 seconds."));
      requireHosts();
      if (!card.plan) stop("Record a plan for this work item before delegating it.");
      if (["done", "cancelled"].includes(plan.state)) stop("This work item is finished, so it cannot be delegated.");
      if (plan.autonomy !== "act") stop("Delegation requires the autonomy Act within the scope and allowed paths. Edit the plan first.");
      if (!(plan.paths || []).length) stop("Delegation requires allowed paths. Edit the plan and add the paths that the work needs.");
    },
    submit(values, context) {
      const data = { episode_id: context.episode_id };
      if (values.host) data.host = values.host;
      const seconds = number(values, "max_seconds", 60, 14400, "seconds");
      if (seconds !== undefined) data.max_seconds = seconds;
      return { operation: "delegate", data };
    },
    reload: reloadIds(["episode_id"]),
    done: (result) => `The work is delegated to ${P.words(result.host)}. The run is ${P.words(result.state).toLowerCase()}.`,
  });

  // Merge, discard, review again and cancel agent runs.
  async function workRun(id, fields) {
    const value = await run(id);
    fields.append(runFacts(value));
    if (value.role !== "work") stop("Only delegated work runs can be merged, discarded or reviewed again.");
    return value;
  }
  P.registerForm("merge", {
    title: "Merge the delegated work",
    submitLabel: "Merge",
    async render(fields, context) {
      const value = await workRun(context.run_id, fields);
      context.passed = Boolean(value.review && value.review.state === "pass");
      if (value.merge) stop(`This run is already ${value.merge.state}.`);
      if (value.state !== "completed") stop("Only a completed run can be merged. This run is " + P.words(value.state).toLowerCase() + ".");
      if (!value.changed_files) stop("This run changed no files, so there is nothing to merge.");
      if (value.review && ACTIVE.includes(value.review.state)) stop("A work review of this run is still active. Wait for it or cancel it before merging.");
      if (context.passed) {
        fields.append(hint("The work review passed. Merging adds the commit of the delegated branch to the project branch with a merge commit, then removes the worktree and the branch."));
        return;
      }
      const state = value.review ? P.words(value.review.state).toLowerCase() : "missing";
      fields.append(notice(`The work review has not passed. Its state is ${state}.`,
        "You can still merge as the user by recording an override reason. The reason is stored with the merge. Agents cannot merge with an override.",
        "To have the work reviewed again instead, close this form and request a new work review."),
      P.field("Override reason", area("override_reason", "", "3"), "Explain why the work can be merged without a passing review."));
    },
    submit(values, context) {
      const data = { run_id: context.run_id };
      if (!context.passed) data.override_reason = need(values.override_reason, "Write the override reason, or close this form and request a new work review.");
      return { operation: "merge", data };
    },
    reload: reloadIds(["run_id"]),
    done: (result) => "The delegated work is merged." + (result.next_step ? " " + result.next_step.reason : ""),
  });
  P.registerForm("discard", {
    title: "Discard the delegated work",
    submitLabel: "Discard",
    async render(fields, context) {
      const value = await workRun(context.run_id, fields);
      if (ACTIVE.includes(value.state)) stop("This run is still active. Cancel it before discarding it.");
      fields.append(hint("Discarding removes the worktree and the branch of this run. The run and its report stay in the records."),
        P.field("Reason", area("reason", "", "3")));
    },
    submit(values, context) {
      need(values.reason, "Write the reason for discarding the work.");
      return { operation: "discard", data: { run_id: context.run_id, reason: values.reason } };
    },
    reload: reloadIds(["run_id"]),
    done: () => "The delegated work is discarded.",
  });
  P.registerForm("request_work_review", {
    title: "Request a new work review",
    submitLabel: "Request the review",
    async render(fields, context) {
      const value = await workRun(context.run_id, fields);
      fields.append(hint("A new review is possible after the previous review failed, was cancelled or could not start. The other agent host reviews the changes."));
      if (value.state !== "completed") stop("Only a completed run can be reviewed.");
      if (value.merge) stop(`This run is already ${value.merge.state}, so it needs no new review.`);
      if (value.review && (ACTIVE.includes(value.review.state) || value.review.state === "pass")) stop("The latest work review is " + P.words(value.review.state).toLowerCase() + ", so a new review is not needed.");
    },
    submit: (values, context) => ({ operation: "request_work_review", data: { run_id: context.run_id } }),
    reload: reloadIds(["run_id"]),
    done: () => "A new work review is requested.",
  });
  P.registerForm("review", {
    title: "Request an agent check",
    submitLabel: "Request the check",
    async render(fields, context) {
      const card = await work(context.episode_id);
      fields.append(kv([[P.term("work_item"), card.title], ["State", P.badge(card.state)]]),
        P.field("Check", choices("role", [["outcome", "Outcome check", "An agent compares the recorded result with every completion criterion."],
          ["intent", "Intent check", "An agent compares the plan and the next action with the requirements and the cited evidence."],
          ["recovery", "Recovery check", "An agent inspects the execution receipts after an interruption and states what remains unknown."]], context.role || "outcome")),
        P.field("Time limit in seconds", P.input("max_seconds", "300", { type: "number", min: "30", max: "900", step: "30", dataset: { number: "" } })),
        P.field("Retry a check that stopped or failed", h("input", { type: "checkbox", name: "retry" })),
        hint("The check reads the records and the project files. It does not change files or records."));
      requireHosts();
    },
    submit(values, context) {
      need(values.role, "Select the check.");
      const data = { episode_id: context.episode_id, role: values.role, retry: values.retry };
      const seconds = number(values, "max_seconds", 30, 900, "seconds");
      if (seconds !== undefined) data.max_seconds = seconds;
      return { operation: "review", data };
    },
    reload: reloadIds(["episode_id", "role"]),
    done: (result) => `The ${P.words(result.role).toLowerCase()} check is requested on ${P.words(result.host)}.`,
  });
  P.registerForm("cancel_run", {
    title: "Stop the agent run",
    submitLabel: "Stop the run",
    async render(fields, context) {
      const value = await run(context.run_id);
      fields.append(runFacts(value), hint("Project Memory asks the agent process to stop. Work that the run already finished is kept in its worktree."));
      if (!ACTIVE.includes(value.state)) stop("This run is not active, so there is nothing to stop.");
    },
    submit: (values, context) => ({ operation: "cancel_run", data: { run_id: context.run_id } }),
    reload: reloadIds(["run_id"]),
    done: () => "The run is asked to stop.",
  });
})();
