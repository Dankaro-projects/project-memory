"use strict";
const data = JSON.parse(document.getElementById("memory-data").textContent);
const el = (id) => document.getElementById(id);
const words = {
  when: "Applies when",
  do: "Suggested action",
  because: "Reason",
  why: "Decision basis",
  expected: "Expected consequence",
  attribution: "Attributed cause",
  uncertainty: "Uncertainty",
  anti_pattern: "Anti-pattern",
  needs_review: "Needs review",
  review_due: "Review due",
  current_copy: "Current stored copy",
  file_changed: "File changed",
  file_missing: "File missing",
  file_unreadable: "File cannot be checked",
  execution_unconfirmed: "Execution not confirmed",
  consequence_pending: "Consequence not assessed",
  action_result: "Execution result",
  host_receipt: "Host receipt",
  replaced: "Revised",
  observed: "Observed",
  supersedes: "Earlier choice",
  replaced_by: "Later revision",
  decision_id: "Decision",
  good: "Good outcome",
  bad: "Bad outcome",
};
Object.assign(words, {
  intent_unassessed: "Unassessed requests",
  activity_unassigned: "Unassigned activity",
  outcome_missing: "Missing outcomes",
  capture_gap: "Capture gap",
  plan_missing: "Missing plan",
  tokens: "Model tokens",
  context_characters: "Context characters",
  human_corrections: "Human corrections",
  maintenance_ms: "Maintenance (ms)",
  repeated_research: "Repeated research",
  alternatives: "Alternatives considered",
  decision: "Decision",
  reconsider_when: "Reconsider when",
  source_id: "Evidence source",
  not_established: "Not established",
  before: "Original wording",
  after: "Corrected wording",
  scope: "Scope of this correction",
  reason: "Reason",
  date: "Recorded at",
  recorded: "Recorded",
  subject: "Subject",
  kind: "Record type",
  id: "Record ID",
  status: "Status",
  project_revision: "Project revision",
  requirements: "Requirements",
  version: "Version",
  actor: "Recorded by",
});
const labels = (s) =>
  words[s] ||
  String(s)
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
Object.assign(words, {
  backlog: "Backlog",
  ready: "Ready",
  in_progress: "In progress",
  blocked: "Blocked",
  review: "Review",
  done: "Done",
  cancelled: "Cancelled",
  work_plan: "Work plan",
  sprint: "Sprint",
  autonomy: "Autonomy",
  suggest: "Suggest first",
  act: "Act within scope",
  agent: "Agent",
  human: "Human",
  intent: "Intended result",
  done_when: "Completion criterion",
  next_action: "Next action",
});
const workById = new Map((data.work || []).map((r) => [r.id, r]));
let sprintRows = data.sprints || [],
  sprintOffset = 0,
  workDetail = false;
const workHistoryShown = new Map();
const workStates = [
  "backlog",
  "ready",
  "in_progress",
  "blocked",
  "review",
  "done",
  "cancelled",
];
const byId = new Map(data.records.map((r) => [r.id, r]));
const pending = new Map(data.pending.map((r) => [r.id, r]));
let view = "overview",
  page = 1,
  selectedId = null;
let livePage = null,
  renderVersion = 0,
  healthTag = null,
  polling = false,
  lastSuccess = null;
let csrf = null,
  reviewHost = null,
  editorState = null;
const expandedChecks = new Set();
const expandedCheckPanels = new Set();
const originalTextShown = new Set();
const detailHistory = [];
let restoringDetail = false,
  detailTrigger = null,
  detailTriggerLabel = null;
function selectDetail(id, refresh) {
  if (!refresh && selectedId && selectedId !== id && !restoringDetail)
    detailHistory.push({ id: selectedId, work: workDetail });
  if (!el("detail").open) {
    detailTrigger = document.activeElement;
    detailTriggerLabel = detailTrigger?.getAttribute("aria-label");
  }
  el("detail-back").hidden = !detailHistory.length;
}
function showDetail(refresh, scroll) {
  if (!el("detail").open) {
    el("detail").show();
    document.body.classList.add("inspector-open");
    el("detail-title").focus();
  }
  el("detail").scrollTop = refresh ? scroll : 0;
}
const dateLabel = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      }).format(date) + " UTC";
};
function metadata(parent, record) {
  const group = document.createElement("div");
  group.className = "record-meta";
  for (const value of [
    record.kind,
    record.subject,
    record.state || record.status,
  ])
    if (value) {
      const span = document.createElement("span");
      span.className = "badge";
      span.dataset.state = value;
      span.textContent = labels(value);
      group.append(span);
    }
  if (record.date) {
    const time = document.createElement("time");
    time.dateTime = record.date;
    time.textContent = dateLabel(record.date);
    group.append(time);
  }
  parent.append(group);
  el("detail-kind").textContent = labels(record.kind || "Work item");
}
Object.assign(words, {
  owner: "Responsible",
  outcome: "Outcome",
  action: "Action",
  intent: "Intent",
  recovery: "Recovery",
  pass: "Passed",
  changes_required: "Changes required",
  uncertain: "Uncertain",
  failed: "Failed",
  queued: "Queued",
  running: "Running",
  cancelling: "Cancelling",
  timed_out: "Timed out",
  interrupted: "Interrupted",
  stale: "Stale",
  high: "High",
  normal: "Normal",
  low: "Low",
  planned: "Planned",
  closed: "Closed",
});
const initialFilters = new URLSearchParams(location.search);
const decisions = data.records.filter((r) => r.kind === "decision"),
  assessed = data.records.filter(
    (r) =>
      r.kind === "outcome" &&
      ["good", "bad"].includes(r.detail.payload.assessment),
  );
el("overview").textContent =
  decisions.length +
  " decisions · " +
  assessed.length +
  " recorded assessments · " +
  data.pending.length +
  " follow-ups · " +
  data.records.filter((r) => r.status === "needs_review").length +
  " records need review";
el("project").textContent = data.project;
el("snapshot").textContent =
  "Exported " +
  data.exported_at +
  " · " +
  data.records.length +
  " records · Source bodies " +
  (data.source_bodies_included ? "included" : "omitted");
el("scope").textContent =
  "Scope: " +
  (Object.entries(data.scope)
    .filter(([, v]) => v)
    .map(([k, v]) => labels(k) + ": " + v)
    .join(" · ") || "Entire project");
for (const rule of data.requirements) {
  const li = document.createElement("li");
  li.textContent = rule;
  el("requirements").append(li);
}
function option(id, value, text) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = text;
  el(id).append(o);
}
for (const s of [...new Set(data.records.map((r) => r.subject))].sort())
  option("subject", s, labels(s));
for (const s of [
  ...new Set([
    ...data.records.map((r) => r.status),
    ...data.pending.map((r) => r.state),
  ]),
].sort())
  option("status", s, labels(s));
for (const r of data.records.filter((r) => r.kind === "episode"))
  option("episode", r.id, r.title);
