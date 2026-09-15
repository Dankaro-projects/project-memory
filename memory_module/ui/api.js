let refreshFailures = 0;
let refreshPending = false;
async function api(endpoint, params = {}, conditional = false) {
  const headers = {};
  if (conditional && healthTag) headers["If-None-Match"] = healthTag;
  const response = await fetch(
    "api/" + endpoint + "?" + new URLSearchParams(params),
    { headers, signal: AbortSignal.timeout(5000) },
  );
  if (response.status === 304) return null;
  const value = await response.json();
  if (!response.ok)
    throw new Error(value.message || "The viewer cannot read the database.");
  if (conditional) healthTag = response.headers.get("ETag");
  return value;
}
function disconnected(error) {
  refreshFailures += 1;
  refreshPending = true;
  el("live-status").textContent = "Update failed";
  el("live-status").dataset.state = "error";
  el("connection").hidden = false;
  el("connection").textContent =
    "Updates are unavailable. Retrying automatically. " + error.message;
  el("connection").title = "Last successful check: " + (lastSuccess || "none");
  el("detail-connection").hidden = false;
  el("detail-connection").textContent = el("connection").textContent;
}
async function loadPage() {
  if (view === "overview") return renderOverview();
  if (["skills", "map", "attention", "dependencies"].includes(view)) return renderExtension();
  if (view === "board") return renderBoard();
  const request = ++renderVersion,
    size = Number(el("page-size").value);
  const filter = { view, limit: size, offset: (page - 1) * size };
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
  const result = await api("records", filter);
  if (request !== renderVersion) return;
  const pages = Math.max(1, Math.ceil(result.total / size));
  if (page > pages) {
    page = pages;
    return loadPage();
  }
  const selected = byId.get(selectedId);
  byId.clear();
  if (selected) byId.set(selectedId, selected);
  data.records = result.records;
  pending.clear();
  for (const item of result.pending || []) pending.set(item.id, item);
  for (const record of result.records) byId.set(record.id, record);
  if (selected) byId.set(selectedId, selected);
  livePage = result;
  draw(result.records, result.total, pages, size);
}
