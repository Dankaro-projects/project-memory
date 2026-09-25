// Browser checks for the live control panel, over a generated fixture of each project kind.
// Usage: node tests/browser/panel_browser.cjs
// MEMORY_PLAYWRIGHT selects an existing Playwright installation and MEMORY_PYTHON selects the interpreter.
// The fixture starts a local server whose host programs refuse to run, so no agent is started here.
const { chromium } = require(process.env.MEMORY_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "../..");
const PYTHON = process.env.MEMORY_PYTHON || "python";
const RAIL = ["Now", "Plan", "Work", "Decisions", "Records", "Requirements", "Learning", "Sessions", "Architecture", "Dependencies", "Agents", "Machine", "Hive", "Usage"];
// The sizes at which the fixed frame is measured: a low wide window, a common laptop window and a phone.
const FRAMES = [[1600, 640], [1440, 900], [390, 800]];
// nodes is the number of items the architecture graph shows before any filter or toggle is changed.
const KINDS = {
  product: { template: "Software product template", architecture: "Components and packages", items: /2 code components/, nodes: 3 },
  engagement: { template: "Consulting engagement template", architecture: "Stakeholders and workstreams", items: /5 authored items/, nodes: 5 },
  automation: { template: "Workflow automation template", architecture: "Systems and workflows", items: /2 workflows and 3 connected services/, nodes: 5 },
};
const results = [];
const step = (name) => { results.push(name); console.log("ok  " + name); };
const states = [];

function serve(kind, directory, extra = [], name = kind) {
  const output = path.join(directory, name);
  const printed = execFileSync(PYTHON, [path.join(ROOT, "tests/browser/fixture.py"), "--kind", kind, "--output", output, ...extra, "--serve"],
    { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  const info = JSON.parse(printed);
  states.push(path.join(info.project, ".memory", "viewer.json"));
  return info;
}
function stopServers() {
  for (const state of states) {
    try {
      const pid = JSON.parse(fs.readFileSync(state, "utf8")).pid;
      if (pid) process.kill(pid);
    } catch (error) {
      // The server exits on its own when it is idle, so a missing process is not a failure.
    }
  }
}
function watch(page) {
  const problems = [];
  page.on("pageerror", (error) => problems.push("page error: " + error.message));
  // The checks cause a refused write, a bad request and an aborted update on purpose; Chromium reports them as resource errors.
  const expected = /^Failed to load resource: (the server responded with a status of (400|405)|net::ERR_FAILED)/;
  page.on("console", (message) => {
    if (message.type() === "error" && !expected.test(message.text())) problems.push("console: " + message.text());
  });
  return problems;
}
// A pinned step in the foot of the side peek stays on screen; a page without one passes.
const footInReach = (page) => page.evaluate(() => { const node = document.querySelector("#detail .detail-foot button");
  if (!node) return true; const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight; });
const settle = (page) => page.waitForFunction(() => !document.querySelector("#main .view.pending, .detail-content.pending"));
async function closePane(page) {
  if (await page.locator("#detail").isHidden()) return;
  await page.evaluate(() => Panel.closePane());
  await page.waitForFunction(() => document.getElementById("detail").hidden);
}
async function go(page, hash) {
  // The pane stays open across view changes by design, so each section starts from a closed pane.
  await closePane(page);
  await page.evaluate((value) => { location.hash = value; }, hash);
  await page.waitForFunction((name) => document.querySelector(`#main .view[data-view="${name}"]:not(.pending)`), hash.split("/")[0].slice(1));
  await settle(page);
}
async function noOverflow(page, width, label) {
  await page.setViewportSize({ width, height: 900 });
  await page.waitForTimeout(250);
  // The view scrolls inside #main, so a view that is too wide shows there and not on the page.
  const size = await page.evaluate(() => { const main = document.getElementById("main");
    return [document.documentElement.scrollWidth, document.documentElement.clientWidth, main.scrollWidth, main.clientWidth]; });
  assert.ok(size[0] <= size[1] && size[2] <= size[3], `${label} overflows at ${width} pixels: ${size}`);
}
// The window never scrolls: the page is as high as the window, and a scroll request moves nothing.
async function fixedFrame(page, label) {
  const size = await page.evaluate(() => { window.scrollTo(0, 5000); const moved = window.scrollY; window.scrollTo(0, 0);
    return [document.documentElement.scrollHeight, document.documentElement.clientHeight, moved]; });
  assert.ok(size[0] <= size[1] && size[2] === 0, `${label} scrolls the page: ${size}`);
}
const text = (page, selector) => page.locator(selector).first().innerText();

// The fixed frame shell: the sidebar with its sections and foot, the breadcrumbs and the project activity.
async function shell(page, kind) {
  assert.deepEqual(await page.locator("#nav h2, #nav summary > span").allTextContents(), ["Work", "Knowledge", "System"]);
  assert.equal(await page.locator(".nav-link svg.icon use").count(), RAIL.length);
  const symbols = await page.evaluate(() => [...document.querySelectorAll(".nav-link use, #page-icon use")].filter((use) => !document.getElementById(use.getAttribute("href").slice(1))).length);
  assert.equal(symbols, 0, "a sidebar or page icon names a symbol that the page does not hold");
  // The panel counts nothing that waits for the reader, so the sidebar carries no count.
  assert.equal(await page.locator("#nav .nav-count").count(), 0);
  step(`${kind}: the sidebar groups 14 views with icons under Work, Knowledge and System, and shows no count of things to do`);

  // System stays folded until the reader opens it or works in one of its views, and the breadcrumbs name the place.
  assert.equal(await page.locator("#nav-more").evaluate((node) => node.open), false);
  await go(page, "#usage");
  assert.equal(await page.locator("#nav-more").evaluate((node) => node.open), true);
  assert.equal(await page.locator('.nav-link[data-nav="usage"]').getAttribute("aria-current"), "page");
  assert.deepEqual((await page.locator("#crumbs li").allTextContents()).slice(1), ["System", "Usage"]);
  assert.equal(await page.locator('#crumbs li[aria-current="page"]').textContent(), "Usage");
  await page.locator("#nav-more summary").click();
  await go(page, "#now");
  assert.match(await text(page, "#project-name"), /Fixture|fixture/);
  const foot = await page.locator(".rail-foot").innerText();
  assert.match(foot, /Lifecycle\s*Development/);
  assert.match(foot, /Live/);
  assert.deepEqual((await page.locator("#crumbs li").allTextContents()).slice(1), ["Now"]);
  step(`${kind}: System opens for a view it holds, the breadcrumbs name the section and the view, and the sidebar names the project, the lifecycle stage and the live state`);

  // Project activity opens from the header row of every view and closes with Escape, with focus back on its button.
  await page.locator("#activity-toggle").click();
  assert.equal(await page.locator("#activity-toggle").getAttribute("aria-expanded"), "true");
  assert.match(await text(page, "#activity"), /WORK BY STATE[\s\S]*1 in progress[\s\S]*IN PROGRESS[\s\S]*AGENTS/i);
  await page.keyboard.press("Escape");
  assert.deepEqual(await page.evaluate(() => [document.getElementById("activity").hidden, document.activeElement.id]), [true, "activity-toggle"]);
  await page.locator("#activity-toggle").click();
  await page.locator('#activity [data-key^="activity-work-"]').first().click();
  await page.waitForFunction(() => !document.getElementById("detail").hidden && document.getElementById("detail-title").textContent !== "Loading");
  assert.equal(await page.locator("#activity").isHidden(), true);
  await closePane(page);
  step(`${kind}: Project activity shows the work by state, opens the work in progress and closes with Escape`);

  // Every focus stop shows a visible focus style: the five stops after the Search button, which reach the links of the sidebar.
  await page.locator("#find-open").focus();
  const stops = [];
  for (let index = 0; index < 5; index++) {
    await page.keyboard.press("Tab");
    stops.push(await page.evaluate(() => { const node = document.activeElement; return [node.id || node.dataset.key || node.dataset.nav || node.className, getComputedStyle(node).outlineStyle]; }));
  }
  assert.ok(stops.every((stop) => stop[1] !== "none") && new Set(stops.map((stop) => stop[0])).size === 5, `${kind}: a focus stop shows no focus style: ${JSON.stringify(stops)}`);
  step(`${kind}: the five focus stops after the search field show a visible focus style`);

  // No view scrolls the page at the three measured sizes. With System folded the sidebar fits a window 640 pixels high.
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    for (const name of RAIL) {
      await go(page, "#" + name.toLowerCase());
      await fixedFrame(page, `${kind} ${name} at ${width} by ${height}`);
    }
  }
  await page.setViewportSize({ width: 1600, height: 640 });
  await go(page, "#now");
  await page.evaluate(() => { document.getElementById("nav-more").open = false; });
  const rail = await page.evaluate(() => { const node = document.getElementById("rail"); return [node.scrollHeight, node.clientHeight]; });
  assert.ok(rail[0] <= rail[1], `${kind}: the sidebar with System folded needs ${rail[0]} pixels in a window 640 pixels high`);
  await page.setViewportSize({ width: 390, height: 800 });
  await page.locator("#menu-toggle").click();
  assert.equal(await page.locator('.nav-link[data-nav="requirements"]').isVisible(), true);
  assert.match(await page.locator(".rail-foot").innerText(), /Live/);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 900 });
  await go(page, "#now");
  step(`${kind}: no view scrolls the page at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels, and the narrow menu holds the sidebar with its foot`);
}

// The rows of a database table, as the text of their titles.
// A row that opens an item: a row of a list or the title of a table row.
const ROW = "#main .pane-row, #main button.db-open";
const rowTitles = (page, scope = "#main") => page.locator(`${scope} button.db-open .db-title`).allTextContents();
async function views(page, kind, expected) {
  await go(page, "#now");
  assert.deepEqual(await page.locator(".nav-link .nav-title").allTextContents(), RAIL);
  // Now is a digest: a table for each place of the work, which holds exactly the items of the board in that place.
  const places = await page.evaluate(async () => {
    const cards = (await Panel.get("board", { limit: "100" })).cards, of = (...states) => cards.filter((card) => states.includes(card.state)).map((card) => card.title).sort();
    const shown = (id) => [...document.querySelectorAll(`[data-scroll="${id}-table"] .db-title`)].map((node) => node.textContent).sort();
    return { expected: [of("in_progress"), of("blocked", "review"), of("ready")], shown: [shown("now-progress"), shown("now-paused"), shown("now-ready")],
      headings: [...document.querySelectorAll("#main .digest-part h2")].map((node) => node.textContent) };
  });
  // The fixture archived nothing, so Now shows no Archived part.
  assert.deepEqual(places.headings, ["In progress", "Paused", "Ready to start", "Done", "Latest decisions"]);
  assert.deepEqual(places.shown, places.expected);
  assert.match(await text(page, "#view-summary"), /\d+ work items? (is|are) in progress, \d+ (is|are) blocked and \d+ needs? review\./);
  assert.ok(!/wait for you|waits for you/.test(await text(page, "#main")), `${kind}: Now still counts what waits for the reader`);
  step(`${kind}: Now shows the work in progress, paused and ready in tables that hold exactly the items of the board`);
  // Idle work that the expiry rule archived is a folded part of Now, which names the state a restore returns it to and
  // says to ask in the chat. The response of api/now carries one archived item for this step.
  if (kind === "product") {
    await page.route(/\/api\/now(\?|$)/, async (route) => {
      // The panel sends If-None-Match, so the route asks for the whole response and serves it without an ETag.
      const headers = { ...route.request().headers() };
      delete headers["if-none-match"];
      const response = await route.fetch({ headers }), body = await response.json();
      body.archived = [{ id: "episode_archived", title: "Retire the unused draft", state: "cancelled", item_type: "task", restore_state: "ready", idle_days: 14,
        last_activity: "2026-09-01T09:00:00+00:00" }];
      body.archived_total = 1;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });
    await go(page, "#plan");
    await go(page, "#now");
    await page.waitForSelector('#main [data-key="now-archived-note"]');
    assert.deepEqual(await page.locator("#main .digest-part h2").allTextContents(), ["In progress", "Paused", "Ready to start", "Done", "Archived", "Latest decisions"]);
    assert.match(await text(page, '#main [data-key="now-archived-note"]'), /Ask in the chat to restore one, and it returns to the state shown\./);
    await page.locator("#main details.toggle summary", { hasText: "most recent" }).last().click();
    await page.waitForFunction(() => [...document.querySelectorAll('[data-scroll="now-archived-table"] .db-title')].some((node) => node.textContent === "Retire the unused draft"));
    assert.match(await text(page, '[data-scroll="now-archived-table"]'), /Ready[\s\S]*14/);
    assert.ok(!/wait for you|waits for you/.test(await text(page, "#main")), "the Archived part counts what waits for the reader");
    await page.unroute(/\/api\/now(\?|$)/);
    await go(page, "#plan");
    await go(page, "#now");
    step("product: Now folds the archived work under Archived, with the state a restore returns it to and a note to restore it in the chat");
  }
  await shell(page, kind);

  // Quick find opens with Command K and with the slash key, lists the pages, finds a work item by its title and opens it.
  // The opened page joins Recent in the sidebar, and a page with three or more sections carries its outline.
  if (kind === "product") {
    await go(page, "#now");
    // The step before hid the menu button that held the focus, so the focus returns to the page before the key.
    await settle(page);
    await page.locator("#view-title").focus();
    await page.keyboard.press("Meta+k");
    await page.waitForSelector("#find[open]");
    assert.ok((await page.locator("#find .find-item").count()) >= RAIL.length, "quick find lists fewer pages than the sidebar");
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("find").open);
    await page.keyboard.press("/");
    await page.waitForSelector("#find[open]");
    await page.keyboard.type("Build the export");
    await page.waitForFunction(() => /Build the export/.test(document.querySelector('#find .find-item[aria-selected="true"]')?.textContent || ""));
    assert.match(await text(page, "#find"), /Search all records for/);
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.getElementById("detail-title").textContent === "Build the export" && !document.querySelector(".detail-content.pending"));
    assert.equal(await page.locator("#find").evaluate((node) => node.open), false);
    assert.match(await text(page, "#recent"), /Build the export/);
    assert.ok((await page.locator("#detail .outline button").count()) >= 3, "the page of a work item carries no outline of its sections");
    await closePane(page);
    await page.locator("#rail-hide").click();
    assert.equal(await page.locator("#rail").isVisible(), false);
    await page.locator("#menu-toggle").click();
    assert.equal(await page.locator("#rail").isVisible(), true);
    step(`${kind}: quick find opens with Command K and slash, opens a work item, which joins Recent and shows its outline, and the sidebar folds away and returns`);
  }

  // A paused item names what it waits for, and its row opens the item beside the digest with its next step.
  await go(page, "#now");
  const paused = page.locator('[data-scroll="now-paused-table"] button.db-open');
  assert.match(await text(page, '[data-scroll="now-paused-table"]'), /Waits for \d+ prerequisite/);
  const pausedTitle = await paused.first().locator(".db-title").textContent();
  await paused.first().click();
  await page.waitForFunction((title) => document.getElementById("detail-title").textContent === title, pausedTitle);
  await settle(page);
  assert.equal(await paused.first().getAttribute("aria-current"), "true");
  assert.match(await text(page, "#detail"), /Next step/);
  await closePane(page);
  assert.match(await text(page, "#main .card.kickoff"), new RegExp(expected.template));
  assert.equal(await page.locator("#main a[href='#decisions']").count(), 1);
  step(`${kind}: a paused item names what it waits for and opens beside the digest, and the kickoff checklist and the latest decisions keep their place`);

  // A proposed lesson opens its page from Learning, which says how to decide it in the chat and offers no action.
  await go(page, "#learning");
  await page.locator("#main .pane-row").first().click();
  await page.waitForFunction(() => document.getElementById("detail-kind").textContent === "Decide a lesson" && !document.querySelector(".detail-content.pending"));
  assert.match(await text(page, "#detail .chat-hint"), /Accept, reject or retire this lesson in the chat/);
  assert.equal(await page.locator("#detail .detail-foot button").count(), 0);
  await closePane(page);
  step(`${kind}: a proposed lesson opens as a page that says how to decide it in the chat`);

  // Plan is an outline table with nested sub-items and a gallery of the phases.
  await go(page, "#plan");
  assert.match(await text(page, "#view-summary"), /16 work items are planned in 7 phases\. 0 are done\./);
  assert.equal((await rowTitles(page)).length, 16);
  assert.ok(await page.locator('#main [data-key^="plan-toggle-"]').count() > 0, `${kind}: the outline offers no fold of sub-items`);
  const toggle = page.locator('#main [data-key^="plan-toggle-"][aria-expanded="true"]').first();
  const toggleKey = await toggle.getAttribute("data-key");
  await toggle.click();
  await page.waitForFunction((key) => document.querySelector(`[data-key="${key}"]`).getAttribute("aria-expanded") === "false", toggleKey);
  assert.ok((await rowTitles(page)).length < 16, `${kind}: folding a phase hides none of its sub-items`);
  await page.locator(`[data-key="${toggleKey}"]`).click();
  await page.locator("#plan-view-phases").click();
  await page.waitForFunction(() => document.querySelectorAll("#main .gallery > li").length === 7);
  const gallery = await page.locator("#main .gallery").evaluate((node) => [node.scrollWidth, node.clientWidth]);
  assert.ok(gallery[0] <= gallery[1], `${kind}: the gallery clips its last phase: ${gallery}`);
  step(`${kind}: Plan shows 16 work items in an outline whose phases fold, and a gallery of 7 phases`);

  // Work is a database: a table that sorts, filters, groups, hides properties and searches, and a board.
  await go(page, "#work");
  await page.waitForFunction(() => document.querySelectorAll("#main button.db-open").length === 16);
  assert.equal(await page.locator("#work-view-table").getAttribute("aria-pressed"), "true");
  assert.equal(await page.locator("#main [role=tab]").count(), 0);
  await page.locator("#work-head-title").click();
  await page.locator("#work-head-title").click();
  await page.waitForFunction(() => /sort=title/.test(location.hash) && /dir=desc/.test(location.hash));
  const titles = await rowTitles(page);
  assert.deepEqual(titles, [...titles].sort((a, b) => (a.toLowerCase() < b.toLowerCase() ? 1 : a.toLowerCase() > b.toLowerCase() ? -1 : 0)), "the rows are not sorted by title in descending order");
  await page.locator("#work-filter").click();
  await page.locator("#work-state").selectOption("blocked");
  await page.waitForFunction(() => document.querySelectorAll("#main button.db-open").length === 2);
  assert.match(page.url(), /state=blocked/);
  assert.match(await text(page, "#main .db-table tbody tr"), /Blocked[\s\S]*Waits for 1 prerequisite/);
  await page.locator("#work-state").selectOption("");
  await page.waitForFunction(() => document.querySelectorAll("#main button.db-open").length === 16);
  await page.locator("#work-group").click();
  await page.locator('[data-key="work-group-state"]').click();
  await page.waitForFunction(() => /group=state/.test(location.hash) && document.querySelectorAll("#main .db-group").length > 1);
  const groups = await page.evaluate(async () => new Set((await Panel.get("board", { limit: "100" })).cards.map((card) => card.state)).size);
  assert.equal(await page.locator("#main .db-group").count(), groups);
  const columns = await page.locator("#main .db-table thead th").count();
  await page.locator("#work-properties").click();
  await page.locator('[data-key="work-prop-next"]').click();
  await page.keyboard.press("Escape");
  await page.waitForFunction((count) => document.querySelectorAll("#main .db-table thead th").length === count - 1, columns);
  assert.match(page.url(), /hide=next/);
  await page.locator("#work-group").click();
  await page.locator('[data-key="work-group-none"]').click();
  // The search reads the title, the intent and the plan: the title of one row narrows the table and keeps that row.
  const probe = (await rowTitles(page)).find((title) => title.length > 12);
  await page.locator(".db-search .db-tool").click();
  await page.locator("#work-query").fill(probe);
  await page.waitForFunction((title) => { const shown = [...document.querySelectorAll("#main .db-title")].map((node) => node.textContent);
    return /query=/.test(location.hash) && shown.includes(title) && shown.length < 16; }, probe);
  await page.locator("#work-query").fill("");
  await page.waitForFunction(() => document.querySelectorAll("#main button.db-open").length === 16);
  step(`${kind}: Work shows 16 items in a table that sorts by title, filters by state, groups by state, hides a property and searches`);

  await page.locator("#work-view-board").click();
  await page.waitForFunction(() => document.querySelectorAll("#main .board-column").length === 5);
  const board = await page.locator("#main .board").evaluate((node) => [node.scrollWidth, node.clientWidth]);
  assert.ok(board[0] <= board[1], `${kind}: the board clips a column at 1440 pixels: ${board}`);
  assert.match(await text(page, "#main"), /Waits for \d+ prerequisite/);
  step(`${kind}: the board of Work shows 5 columns without clipping`);

  // Section 17.12: a board column shows ten cards at a time. For this step the board response carries 23 backlog cards.
  await page.route(/\/api\/board/, async (route) => {
    // The panel revalidates with an entity tag; asking without it returns the full body to widen.
    const headers = { ...route.request().headers() };
    delete headers["if-none-match"];
    const response = await route.fetch({ headers }), value = await response.json(), base = value.cards[0];
    value.cards = [...value.cards.filter((card) => card.state !== "backlog"),
      ...Array.from({ length: 23 }, (_, index) => ({ ...base, id: base.id + "-copy-" + index, title: "Copied item " + index, state: "backlog", issues: [] }))];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
  });
  await go(page, "#work/tab=board");
  const backlog = '#main .board-column[data-tone="backlog"] li';
  await page.waitForFunction((selector) => document.querySelectorAll(selector).length === 10, backlog);
  assert.match(await text(page, '#main [data-key="work-more-backlog"]'), /Show 10 more of 13/);
  await page.locator('#main [data-key="work-more-backlog"]').click();
  await page.waitForFunction((selector) => document.querySelectorAll(selector).length === 20, backlog);
  await page.locator('#main [data-key="work-more-backlog"]').click();
  await page.waitForFunction((selector) => document.querySelectorAll(selector).length === 23, backlog);
  assert.equal(await page.locator('#main [data-key="work-more-backlog"]').count(), 0);
  await page.unroute(/\/api\/board/);
  step(`${kind}: a board column of 23 cards shows 10, then 20, then all 23 with its Show more button`);

  // Architecture fills the frame: the template label and the filters sit in the head, the summary beside the title
  // counts the items, and the graph takes the height under the head beside the side column.
  await go(page, "#architecture");
  assert.equal(await text(page, "#main .view-head h2"), expected.architecture);
  assert.match(await text(page, "#view-summary"), expected.items);
  await page.waitForSelector("#main .graph canvas");
  assert.equal(await page.locator('.node-list button[data-key^="arch-node-"]').count(), expected.nodes);
  assert.equal(await page.locator("#main .list-head #arch-filters").count(), 1);
  step(`${kind}: Architecture uses the template labels and draws ${expected.nodes} items`);

  // The sentence, the item list and the graph state the same number, and nothing is hidden before a filter is set.
  const sentence = await text(page, "#view-summary");
  const counted = [...sentence.split(".")[0].matchAll(/(\d+)/g)].reduce((total, found) => total + Number(found[1]), 0);
  assert.equal(counted, expected.nodes, `${kind}: the sentence counts ${counted} items where the graph draws ${expected.nodes}`);
  assert.ok(!sentence.includes("hidden by the filters"), `${kind}: the sentence reports hidden items although no filter is set`);
  const explanation = await text(page, "#main");
  assert.equal(await page.locator("#arch-language").count(), kind === "product" ? 1 : 0);
  if (kind === "product") {
    assert.match(await text(page, "#main .layer-toggles"), /Code \(2\)/);
    assert.match(explanation, /Imports are read statically from source files\./);
  } else {
    assert.equal(await page.locator("#main .layer-toggles").count(), 0, `${kind}: an empty layer is offered as a filter`);
    assert.ok(!explanation.includes("Imports are read statically"), `${kind}: the architecture note describes source imports`);
    assert.ok(!explanation.includes("manifest declares"), `${kind}: the architecture note describes package manifests`);
  }
  if (kind === "engagement") assert.match(explanation, /no source files and no exported workflows/);
  if (kind === "automation") assert.match(explanation, /exported n8n JSON files/);
  step(`${kind}: the Architecture counts agree and its explanation names only what this project holds`);

  if (kind === "product") {
    await go(page, "#architecture/focus=src%2Fapp");
    const files = await text(page, "#main"), fileSummary = await text(page, "#view-summary");
    assert.match(fileSummary, /This view shows 4 files\./);
    assert.ok(!files.includes("blocked work") && !fileSummary.includes("blocked work"), "the file level still claims blocked work");
    assert.equal(await page.locator('#main .node-list [data-tone="blocked"]').count(), 0, "a file is still painted as blocked");
    assert.match(files, /Files carry no work state here/);
    step(`${kind}: the file level claims no blocked file and explains why files carry no state`);
  }
  if (kind === "engagement") {
    await page.locator('[data-key^="arch-node-"]').filter({ hasText: "Board report" }).first().click();
    const side = await text(page, "#main .graph-side");
    assert.match(side, /Draft the board report implements this item\./);
    assert.ok(!side.includes("episode_"), "the side panel prints a raw record identifier");
    step(`${kind}: the side panel names the work item of a link instead of its identifier`);
  }

  await go(page, "#dependencies");
  assert.match(await text(page, "#view-summary"), /8 dependencies connect 10 work items\./);
  assert.match(await text(page, "#view-summary"), /1 blocked chain is marked in red/);
  assert.ok(await page.locator(".chains li").count() >= 1, "no blocked chain is named");
  assert.equal(await page.locator('[data-key^="dep-node-"]').count(), 10);
  await page.waitForSelector("#dep-panel .graph canvas");
  step(`${kind}: Dependencies draw 10 work items, 8 dependencies and the blocked chain`);

  // The blocked chain has to be readable in the picture, so no two nodes or labels may cover each other.
  const overlaps = await page.evaluate(() => {
    const cy = (Panel.graphs() || [])[0];
    if (!cy) return -1;
    const boxes = cy.nodes().map((node) => node.renderedBoundingBox({ includeLabels: true }));
    let found = 0;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) found++;
      }
    }
    return found;
  });
  assert.equal(overlaps, 0, `${kind}: ${overlaps} pairs of dependency nodes or labels cover each other`);
  const swatches = await page.evaluate(() => {
    const pick = (tone) => document.querySelector(`#dep-panel .legend .swatch[data-tone="${tone}"]`);
    return [getComputedStyle(pick("done")).borderStyle, getComputedStyle(pick("backlog")).borderStyle];
  });
  assert.deepEqual(swatches, ["solid", "dashed"], "finished and unstarted work look the same in the legend");
  assert.equal(await page.locator("#dep-tab-packages").count(), kind === "product" ? 1 : 0);
  step(`${kind}: the dependency graph is readable and the package tab appears only where packages exist`);

  // The graph of Architecture and of Dependencies takes the height of the frame beside its side column at the two wide
  // sizes instead of a fixed height, and on a phone the graph keeps a height while the layout scrolls as one region.
  // No size scrolls the page, with the side column empty and with a selected item in it.
  const graphShape = () => page.evaluate(() => { const main = document.getElementById("main"), graph = main.querySelector(".graph"), layout = main.querySelector(".graph-layout"), side = main.querySelector(".graph-side");
    const box = graph.getBoundingClientRect(), frame = main.getBoundingClientRect();
    return { frame: main.scrollHeight <= main.clientHeight, height: Math.round(box.height), gap: Math.round(frame.bottom - box.bottom), share: box.height / frame.height,
      layoutScrolls: layout.scrollHeight > layout.clientHeight, sideScrolls: getComputedStyle(side).overflowY }; });
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    for (const hash of ["#architecture", "#dependencies"]) {
      await go(page, hash);
      await page.waitForSelector("#main .graph canvas");
      await fixedFrame(page, `${kind} ${hash} at ${width} by ${height}`);
      const shape = await graphShape();
      assert.equal(shape.frame, true, `${kind} ${hash} at ${width} by ${height}: the view scrolls outside its regions: ${JSON.stringify(shape)}`);
      if (width >= 1100) assert.ok(shape.gap <= 40 && shape.share >= 0.45 && shape.sideScrolls === "auto", `${kind} ${hash} at ${width} by ${height}: the graph does not take the height of the frame: ${JSON.stringify(shape)}`);
      else assert.ok(shape.height === 320 && shape.layoutScrolls, `${kind} ${hash} at ${width} by ${height}: the graph layout does not scroll as one region: ${JSON.stringify(shape)}`);
      if (hash === "#architecture") {
        await page.locator('[data-key^="arch-node-"]').first().click();
        await page.waitForSelector("#main .graph-side .card");
        await fixedFrame(page, `${kind} ${hash} with a selected item at ${width} by ${height}`);
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: the graphs of Architecture and Dependencies take the height of the frame at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels, and neither view scrolls the page with or without a selected item`);

  // Decisions is a database with saved views: each row carries its outcome and review marker, and a view keeps its route parameter.
  await go(page, "#decisions");
  assert.match(await text(page, "#view-summary"), /3 decisions are recorded\. 2 have a bad outcome and 1 needs review\./);
  assert.equal(await page.locator('#main button.db-open[data-key^="decisions-row-event_"]').count(), 3);
  assert.equal(await page.locator('#main [data-state="needs-review-marker"]').count(), 1);
  assert.equal(await page.locator("#main #decisions-filter").count(), 1);
  assert.match(await text(page, "#main .db-foot"), /Count\s*3/);
  await page.locator("#decisions-view-review").click();
  await page.waitForFunction(() => document.querySelectorAll("#main button.db-open").length === 1);
  assert.match(page.url(), /tab=review/);
  await page.locator("#decisions-view-all").click();
  await page.waitForFunction(() => document.querySelectorAll("#main button.db-open").length === 3);
  step(`${kind}: Decisions show 3 decisions in a table with outcome badges and the review marker, and the Needs review view keeps 1`);

  if (kind === "product") {
    await page.locator('#main [data-key^="decisions-row-event_"]').first().click();
    await page.waitForFunction(() => /Lineage/.test(document.getElementById("detail-body").textContent));
    await settle(page);
    assert.equal(await page.locator("#detail-body .lineage-list").count(), 1, "a lineage of dozens of records still opens as a small picture");
    assert.match(await text(page, '#detail-body [data-key$="-toggle"]'), /Show as graph/);
    await closePane(page);
    step(`${kind}: a decision lineage of dozens of records opens as the readable list`);
  }

  // Learning in the frame: one tab per part, the guards as rows, and the guard with the recurrence in the pane, which says how to reassess it.
  await go(page, "#learning");
  assert.match(await text(page, "#view-summary"), /^2 guards are active, 1 guard recorded a recurrence and 1 rule is ineffective\.$/);
  assert.deepEqual((await page.locator("#main .db-views button").allTextContents()).map((label) => label.replace(/\d+$/, "")),
    ["Proposed lessons", "Guards", "Failures without a lesson", "Instructions", "Scope changes", "Signals"]);
  assert.equal(await page.locator('#main [data-key="learning-tab-proposed"][aria-pressed="true"]').count(), 1);
  const learningTab = async (name) => {
    await page.click(`[data-key="learning-tab-${name}"]`);
    await page.waitForFunction((key) => { const node = document.querySelector(`#main .view:not(.pending) [data-key="${key}"]`); return node && node.getAttribute("aria-pressed") === "true"; }, "learning-tab-" + name);
    await settle(page);
  };
  await learningTab("guards");
  assert.match(await text(page, "#main .pane-row"), /Recurred 1 time/);
  await page.locator("#main .pane-row").first().click();
  await page.waitForFunction(() => document.getElementById("detail-kind").textContent === "Guard" && !document.querySelector(".detail-content.pending"));
  assert.match(await text(page, "#detail .chat-hint"), /ask the assistant in the chat to reassess it/);
  assert.deepEqual(await page.locator("#detail .detail-foot button").allTextContents(), ["Open the lesson"]);
  await closePane(page);
  await learningTab("failures");
  assert.equal(await page.locator("#main .pane-row").count(), 1);
  // The links of Now name a section, which the tabs replaced, so the section still selects its tab.
  await go(page, "#learning/section=instructions");
  assert.equal(await page.locator('#main [data-key="learning-tab-instructions"][aria-pressed="true"]').count(), 1);
  step(`${kind}: Learning shows its parts as tabs, the guard with the recurrence opens in the pane and says how to reassess it, and a section link selects its tab`);

  // Instructions: one row per role, and the pane of a role holds its base text, its rules, its omissions and its budget.
  assert.equal(await page.locator('#main [data-key^="instructions-row-"]').count(), 3);
  const role = async (name) => {
    await page.click(`[data-key="instructions-row-${name}"]`);
    await page.waitForFunction((key) => document.querySelector(`#detail [data-key="${key}"]`) && !document.querySelector(".detail-content.pending"), "instructions-" + name);
    return text(page, `#detail [data-key="instructions-${name}"]`);
  };
  const assistant = await role("assistant");
  assert.match(assistant, /This prompt carries 1 rule and uses \d+ of 600 characters\./);
  assert.match(assistant, /The shipped file agents\/assistant\.md is in force\./);
  assert.match(assistant, /2 runs composed it/);
  const reviewer = await role("reviewer");
  assert.match(reviewer, /You saved version 1 of this text in this project\./);
  assert.match(reviewer, /Give one verdict of pass, changes required or uncertain\./);
  assert.match(reviewer, /Unproven/);
  const worker = await role("worker");
  assert.match(worker, /uses \d+ of 1,200 characters/);
  assert.match(worker, /1 further accepted rule is composed only into a run that matches the triggers\./);
  assert.match(worker, /Ineffective/);
  assert.match(worker, /Recurrences before 1, after 1/);
  assert.match(await text(page, '#detail .chat-hint'), /give the assistant the new text in the chat/);
  step(`${kind}: the Instructions tab opens each role in the pane with its base text, its rules in force, its budget and how to change it in the chat`);

  // On a phone the base text scrolls inside its own block and the rules stay on screen.
  await role("reviewer");
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(250);
  const panel = await page.evaluate(() => {
    const card = document.querySelector('#detail [data-key="instructions-reviewer"]');
    const base = card.querySelector(".kn-base");
    const rule = card.querySelector(".kn-rule");
    return { card: card.getBoundingClientRect().right, base: base.getBoundingClientRect().right,
      rule: rule ? rule.getBoundingClientRect().right : null };
  });
  assert.ok(panel.card <= 390, `${kind}: the reviewer panel ends at ${Math.round(panel.card)} pixels on a 390 pixel screen`);
  assert.ok(panel.base <= 390, `${kind}: the base text block ends at ${Math.round(panel.base)} pixels on a 390 pixel screen`);
  assert.ok(panel.rule !== null && panel.rule <= 390, `${kind}: a composed rule sits off a 390 pixel screen: ${panel.rule}`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await closePane(page);
  step(`${kind}: the instructions pane keeps the base text and the rules on a 390 pixel screen`);

  // Agents in the frame: the runs are a table under the Runs tab, the hosts wait under their own tab, and a row opens the
  // run pane with Merge and Discard in its foot.
  await go(page, "#agents");
  assert.match(await text(page, "#view-summary"), /configured hosts can run work now/);
  assert.match(await text(page, "#view-summary"), /1 delegated run has changes that are not merged/);
  assert.equal(await page.locator("#main button.db-open").count(), 2);
  assert.deepEqual((await page.locator("#main .db-views button").allTextContents()).map((label) => label.replace(/\d+$/, "")), ["Runs", "Hosts", "Follow ups"]);
  await page.locator('[data-key="agents-tab-hosts"]').click();
  await page.waitForSelector('#main .view:not(.pending) [data-key="agents-tab-hosts"][aria-pressed="true"]');
  assert.equal(await page.locator("#main .pane-body .card").count(), 2);
  assert.match(await text(page, "#main .pane-body"), /Can run work|Cannot run work/);
  await go(page, "#agents");
  step(`${kind}: Agents show the two recorded runs in a table and the hosts under their tab`);

  const agents = await text(page, "#main");
  assert.ok(!agents.includes("Not applicable"), `${kind}: the run rows still print Not applicable`);
  assert.match(agents, /describe delegated work/);
  // On a phone the merge decision has to stay on screen instead of hiding behind a sideways scroll.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(250);
  const merge = await page.evaluate(() => {
    const badge = [...document.querySelectorAll("#main .db-table .badge")].find((node) => /Awaiting a decision|Not merged|merged/.test(node.textContent));
    return badge ? { right: badge.getBoundingClientRect().right, text: badge.textContent.trim() } : null;
  });
  assert.ok(merge && merge.right <= 390, `${kind}: the merge state sits off a 390 pixel screen: ${JSON.stringify(merge)}`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.locator("#main .db-table tbody tr").filter({ hasText: "Awaiting a decision" }).first().locator("button.db-open").click();
  await page.waitForFunction(() => !document.getElementById("detail").hidden && document.getElementById("detail-title").textContent !== "Loading" && !document.querySelector(".detail-content.pending"));
  assert.equal(await page.evaluate(() => document.getElementById("detail-kind").textContent), "Agent run");
  assert.match(await text(page, "#detail .chat-hint"), /To merge or discard this delegated work/);
  assert.equal(await page.locator("#detail .detail-foot button").count(), 0);
  await closePane(page);
  step(`${kind}: a run row keeps the merge state on a phone and opens the run page, which says how to merge or discard it in the chat`);

  // The foot of the rail states the phase of the project, because the phase decides who merges delegated work.
  assert.match(await text(page, "#phase"), /Lifecycle\s*Development/);
  assert.match(await page.locator("#phase .phase-button").getAttribute("title"), /may bring delegated work into the project after a passing work review/);
  step(`${kind}: the foot of the rail states that the project is in development`);

  // Machine in the frame: the proposals, the rules in force, the retired rules and the registry are regions under tabs,
  // each with the isolation notice, and the summary beside the title counts the rules and the proposals.
  await go(page, "#machine");
  assert.match(await text(page, "#view-summary"), /1 rule is in force on .+, promoted from 1 project\. Proposals of this project are listed under Proposals and are decided in the chat\./);
  assert.deepEqual((await page.locator("#main .db-views button").allTextContents()).map((label) => label.replace(/\d+$/, "")), ["Proposals", "Rules in force", "Retired rules", "Projects"]);
  assert.match(await text(page, '#main [data-key="machine-isolation"]'), /no outcome is combined across projects/);
  assert.equal(await page.locator('#main [data-key^="promotion-"]').count(), 2);
  assert.equal(await page.locator('#main [data-key^="promotion-"] .chat-hint').count(), 1, `${kind}: the waiting proposal does not say how to decide it in the chat`);
  // The proposal and rule cards use the width of the region: at least 480 pixels each at 1440 pixels.
  const cardWidths = (selector) => page.locator(selector).evaluateAll((nodes) => nodes.map((node) => Math.round(node.getBoundingClientRect().width)));
  assert.ok((await cardWidths('#main [data-key^="promotion-"]')).every((width) => width >= 480), `${kind}: a proposal card of Machine is narrower than 480 pixels`);
  await go(page, "#machine/tab=rules");
  assert.equal(await page.locator('#main [data-key^="machine-rule-"]').count(), 1);
  assert.ok((await cardWidths('#main [data-key^="machine-rule-"]')).every((width) => width >= 480), `${kind}: a rule card of Machine is narrower than 480 pixels`);
  assert.match(await text(page, '#main [data-key^="machine-rule-"]'), /1 project promoted this rule/);
  assert.match(await text(page, '#main [data-key="machine-isolation"]'), /no outcome is combined across projects/);
  await go(page, "#machine/tab=projects");
  assert.equal(await page.locator('#main [data-key^="machine-project-"]').count(), 1);
  assert.match(await text(page, '#main [data-key^="machine-project-"]'), /This project/);
  step(`${kind}: Machine lists the waiting proposal, the promoted rule with its adoption count and the registry under tabs`);

  // The Usage view reads the ledger of the fixture machine memory: Codex at 91 percent and a Claude limit hit constrain both.
  await go(page, "#usage");
  assert.match(await text(page, "#view-summary"), /^Usage was measured at .+ UTC\. 2 hosts are constrained\.$/);
  assert.equal(await page.locator('#main .list-view [data-scroll="rows"] [data-key="usage-note"]').count(), 1, `${kind}: the usage note is not in the scrolling region`);
  assert.equal(await page.locator('#main [data-key^="usage-host-"]').count(), 4);
  const codexCard = await text(page, '[data-key="usage-host-codex"]');
  assert.match(codexCard, /Constrained/);
  assert.match(codexCard, /168,000 tokens/);
  assert.match(codexCard, /91 percent of the 300 minute window codex, which resets at .+ UTC\./);
  assert.match(codexCard, /Configured for this project/);
  assert.match(await text(page, '[data-key="usage-host-claude"]'), /A limit was hit \(usage limit\) and resets at .+ UTC\./);
  const grokCard = await text(page, '[data-key="usage-host-grok"]');
  assert.match(grokCard, /Not constrained/);
  assert.match(grokCard, /Passed for version grok 0\.2\.93 on .+ UTC\. Roles: review and work\./);
  assert.match(await text(page, '[data-key="usage-host-opencode"]'), /0\.09 USD/);
  assert.equal(await page.locator('#main tr[data-key^="routing-"]').count(), 2);
  assert.match(await text(page, "#main table.data"), /Most headroom\s*The preferred host claude was not chosen/);
  step(`${kind}: Usage shows each host with its windows, cost, limit state, probe and the routing decisions of recent runs`);
  await page.setViewportSize({ width: 390, height: 900 });
  await settle(page);
  const usageBoxes = await page.locator('#main [data-key^="usage-host-"], #main tr[data-key^="routing-"]').evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().right));
  assert.ok(usageBoxes.length === 6 && usageBoxes.every((right) => right <= 390), `${kind}: a usage card or routing row sits off a 390 pixel screen: ${usageBoxes}`);
  await noOverflow(page, 390, `${kind} #usage`);
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: the Usage cards and routing rows stay on a 390 pixel screen`);

  await go(page, "#records");
  // Records is a database: every kind is a view, All record kinds is a table grouped by kind, and the filters wait as chips
  // behind the Filter tool of the bar. The summary beside the title counts the matches.
  assert.ok(await page.locator("#main button.db-open").count() >= 10, "the records view lists no records");
  assert.ok(await page.locator("#main .db-group").count() >= 2, "the kinds are not grouped in the table");
  assert.equal(await page.locator("#records-view-all").getAttribute("aria-pressed"), "true");
  const summary = (pattern) => page.waitForFunction((source) => new RegExp(source).test(document.getElementById("view-summary").textContent), pattern.source);
  await summary(/records match across all kinds\./);
  assert.equal(await page.locator("#main .db-filters").count(), 0);
  await page.locator("#records-filter").click();
  await page.waitForSelector("#main .db-filters");
  assert.deepEqual(await page.evaluate(() => ["records-subject", "records-status", "records-episode", "records-from", "records-to", "records-clear"].map((id) => document.getElementById(id).getClientRects().length > 0)), [true, true, true, true, true, true]);
  step(`${kind}: Records groups its kinds in one table, and its filters wait as chips behind the Filter tool`);
  // The filters apply on change, and typing keeps the focus while the results update.
  await page.locator("#records-view-decisions").click();
  await summary(/in decisions match/);
  assert.match(page.url(), /view=decisions/);
  await page.locator("#main .db-search .db-tool").click();
  await page.locator("#records-query").pressSequentially("zzzz");
  await summary(/^0 records in decisions/);
  assert.equal(await page.evaluate(() => document.activeElement.id), "records-query");
  assert.match(await text(page, "#main .db-scroll .empty"), /No records match these filters\./);
  await page.locator("#records-clear").click();
  await summary(/in decisions match/);
  step(`${kind}: the Records filters apply on change and keep the focus while the reader types`);
  // A work item carries one state word everywhere: Records shows the board state, not a second vocabulary.
  await page.locator("#records-view-episodes").click();
  await summary(/in work items match/);
  assert.equal(await page.locator('#main .db-table .badge[data-state="blocked"]').count(), 2);
  assert.equal(await page.locator('#main .db-table .badge[data-state="active"], #main .db-table .badge[data-state="settled"]').count(), 0);
  step(`${kind}: Records shows the two blocked work items by their board state`);
  await go(page, "#requirements");
  assert.match(await text(page, "#view-summary"), /^The requirements are not established yet and wait for your review\.$/);
  assert.deepEqual(await page.locator("#main .block-heading").allTextContents(), ["Requirements", "Approval evidence", "History"]);
  step(`${kind}: Requirements reads as a page with its properties, its numbered requirements, its evidence and its history`);

  // Opening an item never covers the list on a wide screen: the pane sits beside the view and the opening row stays marked.
  await go(page, "#work/tab=board");
  const cards = page.locator("#main .board button.item");
  const loaded = () => page.waitForFunction(() => !document.getElementById("detail").hidden && document.getElementById("detail-title").textContent !== "Loading"
    && !document.querySelector(".detail-content.pending"));
  await cards.first().click();
  await loaded();
  const beside = await page.evaluate(() => ["main", "detail"].map((id) => { const box = document.getElementById(id).getBoundingClientRect(); return [Math.round(box.left), Math.round(box.right)]; }));
  assert.ok(beside[0][1] > beside[0][0] + 300 && beside[0][1] <= beside[1][0], `${kind}: the pane covers the view at 1440 pixels: ${beside}`);
  assert.equal(await cards.first().getAttribute("aria-current"), "true");
  // The body of the page scrolls, and a pinned step in its foot stays in reach at a window height of 640 pixels.
  await page.setViewportSize({ width: 1600, height: 640 });
  assert.ok(await page.evaluate(() => { const body = document.querySelector("#detail .detail-scroll"); return body.scrollHeight > body.clientHeight; }), `${kind}: the page of the item does not scroll at 640 pixels`);
  assert.ok(await footInReach(page), `${kind}: the pinned step of the pane is out of reach at 640 pixels`);
  await fixedFrame(page, `${kind} work pane at 1600 by 640`);
  // J and K open the next and the previous row of the kind that opened the pane, and start no back stack.
  const first = await text(page, "#detail-title");
  await page.keyboard.press("j");
  await page.waitForFunction((title) => !["Loading", title].includes(document.getElementById("detail-title").textContent), first);
  assert.deepEqual([await cards.nth(0).getAttribute("aria-current"), await cards.nth(1).getAttribute("aria-current")], [null, "true"]);
  assert.equal(await page.locator("#detail-back").isHidden(), true);
  await page.keyboard.press("k");
  await page.waitForFunction((title) => document.getElementById("detail-title").textContent === title, first);
  step(`${kind}: the pane opens beside the view, marks its row, scrolls its page at 640 pixels and follows J and K`);

  // Below 1180 pixels one pane shows at a time, and Escape returns to the view and to the row that opened the pane.
  await page.setViewportSize({ width: 1000, height: 800 });
  assert.deepEqual(await page.evaluate(() => [getComputedStyle(document.getElementById("main")).visibility, document.getElementById("detail").getBoundingClientRect().width > 600]), ["hidden", true]);
  await fixedFrame(page, `${kind} work pane at 1000 by 800`);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.getElementById("detail").hidden && document.activeElement.matches("#main .board button.item"));
  assert.deepEqual(await page.evaluate(() => [getComputedStyle(document.getElementById("main")).visibility, document.querySelectorAll("#main [data-selected]").length]), ["visible", 0]);
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: below 1180 pixels one pane shows at a time and Escape returns to the opening row`);

  // Work and Plan in the frame: the board keeps its five columns beside the open pane and scrolls sideways inside its
  // region, the filters stay at the top, and the pane offers no edit and keeps the list of the lineage.
  await go(page, "#work/tab=board");
  await cards.first().click();
  await loaded();
  const withPane = await page.evaluate(() => { const main = document.getElementById("main"), board = main.querySelector('[data-scroll="board"]'), filters = document.getElementById("work-filter").getBoundingClientRect();
    return { main: main.scrollHeight <= main.clientHeight, sideways: board.scrollWidth > board.clientWidth, columns: board.querySelectorAll(".board-column").length,
      filters: filters.top >= 0 && filters.bottom <= window.innerHeight, selected: board.querySelector("[data-selected]").getBoundingClientRect().left >= board.getBoundingClientRect().left }; });
  assert.deepEqual(withPane, { main: true, sideways: true, columns: 5, filters: true, selected: true }, `${kind}: the board beside the pane: ${JSON.stringify(withPane)}`);
  // The page of a work item offers no edit: its foot holds at most the step that opens the next page.
  assert.equal(await page.locator("#detail-body select, #detail-body textarea").count(), 0);
  for (const part of ["Edit plan", "Allow paths", "Delegate", "Request check", "Comment"]) assert.ok(!(await text(page, "#detail")).includes(part), `${kind}: the page still offers ${part}`);
  await page.locator('#detail-body [data-key$="-toggle"]').click();
  assert.equal(await page.locator("#detail-body .lineage-list").count(), 1);
  assert.match(await text(page, '#detail-body [data-key$="-toggle"]'), /Show as graph/);
  await go(page, "#work");
  await page.locator("#main button.db-open").nth(1).click();
  await loaded();
  assert.deepEqual(await page.locator("#detail .detail-foot button").allTextContents(), ["Open the prerequisite"], `${kind}: the foot of the second phase`);
  step(`${kind}: the board keeps its five columns beside the open pane and scrolls sideways, the filters stay at the top, the page offers no edit and keeps the list of the lineage, and a waiting item opens its prerequisite`);

  // At the three measured sizes Work as a board, Work as a table and Plan scroll only inside their region, with and
  // without the pane, the tools of the bar stay in reach without scrolling, and the primary action of the item is visible.
  const inReach = (page, selector) => page.evaluate((value) => { const box = document.querySelector(value).getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight; }, selector);
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    for (const [hash, row, filters] of [["#work/tab=board", "#main .board button.item", "#work-filter"], ["#work", "#main button.db-open", "#work-filter"], ["#plan", "#main button.db-open", "#plan-filter"]]) {
      await go(page, hash);
      await page.waitForSelector(row);
      await fixedFrame(page, `${kind} ${hash} at ${width} by ${height}`);
      const region = await page.evaluate(() => { const main = document.getElementById("main"), rows = main.querySelector("[data-scroll]"); return [main.scrollHeight <= main.clientHeight, ["auto", "scroll"].includes(getComputedStyle(rows).overflowY)]; });
      assert.deepEqual(region, [true, true], `${kind} ${hash} at ${width} by ${height}: the view scrolls outside its rows: ${region}`);
      assert.ok(await inReach(page, filters), `${kind} ${hash} at ${width} by ${height}: the filters are out of reach`);
      await page.locator(row).first().click();
      await loaded();
      await fixedFrame(page, `${kind} ${hash} with the pane at ${width} by ${height}`);
      assert.ok(await footInReach(page), `${kind} ${hash} at ${width} by ${height}: the pinned step of the item is out of reach`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: Work as a board and as a list and Plan scroll only inside their rows at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels, with and without the pane, and the primary action stays in reach`);

  // Learning and Decisions fill the frame the same way, with and without the pane. A lesson, a guard and the
  // instructions of a role keep their primary action in the foot of the pane; the decision pane has no action.
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    for (const [hash, expected] of [["#learning", "Decide a lesson"], ["#learning/tab=guards", "Guard"], ["#learning/tab=instructions", "Instructions"], ["#decisions", "Decision"]]) {
      await go(page, hash);
      await page.waitForSelector(ROW);
      await fixedFrame(page, `${kind} ${hash} at ${width} by ${height}`);
      assert.ok(await page.evaluate(() => { const main = document.getElementById("main"); return main.scrollHeight <= main.clientHeight; }), `${kind} ${hash} at ${width} by ${height}: the view scrolls outside its rows`);
      await page.locator(ROW).first().click();
      await loaded();
      await fixedFrame(page, `${kind} ${hash} with the pane at ${width} by ${height}`);
      assert.equal(await page.evaluate(() => document.getElementById("detail-kind").textContent), expected);
      assert.ok(await footInReach(page), `${kind} ${hash} at ${width} by ${height}: the foot of the page is out of reach`);
    }
  }
  // Scope changes and Signals keep their cards in a scrolling region, and neither scrolls the page at 1600 by 640.
  await page.setViewportSize({ width: 1600, height: 640 });
  for (const hash of ["#learning/tab=scope", "#learning/tab=signals"]) {
    await go(page, hash);
    await page.waitForSelector('#main [data-scroll="rows"]');
    await fixedFrame(page, `${kind} ${hash} at 1600 by 640`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: Learning and Decisions scroll only inside their rows at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels, with and without the pane, the primary action stays in reach, and Scope changes and Signals scroll no page at 1600 by 640`);

  // Records pages in the foot and scrolls only inside its rows at the three sizes, with and without the pane; a lesson
  // keeps its decision in the foot of the pane. Requirements reads its text, version, evidence and history in one
  // region of the frame, and Review requirements stays in reach in its foot.
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    await go(page, "#records/view=events");
    await page.waitForSelector(ROW);
    await fixedFrame(page, `${kind} #records at ${width} by ${height}`);
    const region = await page.evaluate(() => { const main = document.getElementById("main"), rows = main.querySelector('[data-scroll="records-table"]'), foot = main.querySelector(".db-foot");
      return { frame: main.scrollHeight <= main.clientHeight, rows: rows.scrollHeight > rows.clientHeight, foot: foot.getBoundingClientRect().bottom <= window.innerHeight, pager: foot.textContent }; });
    assert.equal(region.frame && region.rows && region.foot, true, `${kind} #records at ${width} by ${height}: ${JSON.stringify(region)}`);
    assert.match(region.pager, /Showing 1 to \d+ of \d+\.\s*Previous\s*Next/);
    assert.ok(await inReach(page, "#records-filter"), `${kind} #records at ${width} by ${height}: the filters are out of reach`);
    await page.locator(ROW).first().click();
    await loaded();
    await fixedFrame(page, `${kind} #records with the pane at ${width} by ${height}`);
    await go(page, "#records/view=lessons");
    await page.locator(ROW).first().click();
    await loaded();
    assert.equal(await page.locator("#detail .chat-hint").count(), 1, `${kind} lesson record at ${width} by ${height}: the page does not say how to decide the lesson`);
    await go(page, "#requirements");
    await fixedFrame(page, `${kind} #requirements at ${width} by ${height}`);
    const shape = await page.evaluate(() => { const main = document.getElementById("main"), body = main.querySelector('[data-scroll="requirements"]');
      return { frame: main.scrollHeight <= main.clientHeight, scrolls: getComputedStyle(body).overflowY, parts: ["Version", "Approval evidence", "History", "Version 0"].every((part) => body.textContent.includes(part)) }; });
    assert.deepEqual(shape, { frame: true, scrolls: "auto", parts: true }, `${kind} #requirements at ${width} by ${height}: ${JSON.stringify(shape)}`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: Records and Requirements scroll only inside the frame at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels, the pager stays in the foot, and a lesson says how to decide it in the chat`);

  // Agents, Machine and Usage scroll only inside their region at the three sizes, with and without the run pane, whose
  // primary action stays in reach. Hive is measured with a swarm on the focus fixture.
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    for (const hash of ["#agents", "#agents/tab=hosts", "#machine", "#machine/tab=rules", "#machine/tab=projects", "#usage"]) {
      await go(page, hash);
      await fixedFrame(page, `${kind} ${hash} at ${width} by ${height}`);
      const region = await page.evaluate(() => { const main = document.getElementById("main"), rows = main.querySelector("[data-scroll]"); return [main.scrollHeight <= main.clientHeight, rows.scrollHeight >= rows.clientHeight]; });
      assert.deepEqual(region, [true, true], `${kind} ${hash} at ${width} by ${height}: the view scrolls outside its region: ${region}`);
    }
    await go(page, "#agents");
    await page.locator("#main .db-table tbody tr").filter({ hasText: "Awaiting a decision" }).first().locator("button.db-open").click();
    await loaded();
    await fixedFrame(page, `${kind} #agents with the pane at ${width} by ${height}`);
    assert.equal(await page.locator("#detail .chat-hint").count(), 1, `${kind} #agents at ${width} by ${height}: the run does not say how to merge it`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: Agents, Machine and Usage scroll only inside their region at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels, and the page of a run says how to merge it in the chat`);

  // A long document reads at full width: the toggle of the pane bar hides the list beside the pane, and Escape
  // restores the list with the focus on the row that opened the record.
  await go(page, "#records/view=documents");
  await page.locator(ROW).first().click();
  await loaded();
  await page.waitForFunction(() => /Outline/.test(document.getElementById("detail-body").textContent));
  const narrow = await page.evaluate(() => document.getElementById("detail").getBoundingClientRect().width);
  await page.locator("#detail-wide").click();
  const wide = await page.evaluate((before) => { const detail = document.getElementById("detail").getBoundingClientRect(), panes = document.querySelector(".panes").getBoundingClientRect();
    return { fills: Math.round(detail.width) === Math.round(panes.width) && detail.width > before + 300, view: getComputedStyle(document.getElementById("main")).visibility,
      pressed: document.getElementById("detail-wide").getAttribute("aria-pressed"), label: document.getElementById("detail-wide").textContent,
      // The page reads in a centred column of a Notion page, however wide the window is.
      document: (() => { const box = document.querySelector("#detail .document").getBoundingClientRect(); return box.width >= 600 && box.width <= 780 && Math.abs((box.left - panes.left) - (panes.right - box.right)) < 40; })() }; }, narrow);
  assert.deepEqual(wide, { fills: true, view: "hidden", pressed: "true", label: "Show the list", document: true }, `${kind}: the record pane at full width: ${JSON.stringify(wide)}`);
  await fixedFrame(page, `${kind} the record pane at full width`);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.getElementById("detail").hidden);
  assert.deepEqual(await page.evaluate((row) => [getComputedStyle(document.getElementById("main")).visibility, "wide" in document.getElementById("app").dataset, document.activeElement.matches(row), document.getElementById("detail-wide").textContent], ROW),
    ["visible", false, true, "Full width"]);
  step(`${kind}: a document reads at full width in a centred column with the list hidden beside the pane, and Escape restores the list`);

  for (const width of [1440, 768, 390, 320]) {
    for (const hash of ["#now", "#plan", "#work", "#architecture", "#decisions", "#learning", "#machine", "#hive", "#usage"]) {
      await go(page, hash);
      await noOverflow(page, width, `${kind} ${hash}`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: no page overflow at 1440, 768, 390 and 320 pixels`);
}

// A write in the chat, made as the assistant would make it through user_action: the workspace action on the database.
function chatComment(fixture, episode, text) {
  const script = "import sys\nfrom memory_module import Memory\nfrom memory_module.workspace import action\n" +
    "m = Memory(sys.argv[1])\naction(m, 'comment', {'episode_id': sys.argv[2], 'expected_version': m.episode(sys.argv[2])['version'], 'text': sys.argv[3]}, 'chat-' + str(abs(hash(sys.argv[3]))))\nm.close()\n";
  execFileSync(PYTHON, ["-c", script, fixture.database, episode, text], { cwd: ROOT });
}

// The panel is read only, so these checks run on one fixture: the server refuses every write, each place where the
// user decides says in a chat hint what to ask the assistant, and live updates from the chat keep the reader's place.
async function readOnly(page, fixture) {
  const ids = fixture.ids;
  await go(page, "#work");
  const trigger = `[data-key="work-row-${ids.story}"]`;
  await page.locator(trigger).focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("detail-title").textContent === "Parse client files");
  await settle(page);
  const shown = await text(page, "#detail");
  for (const part of ["Intended result", "Done when", "Next step", "Acceptance criteria", "Scope", "Allowed paths", "src/app/**",
    "Dependencies", "Agent checks and delegated runs", "Lineage", "History"]) {
    assert.ok(shown.includes(part), "the work pane lacks " + part);
  }
  // The history stays closed until the reader opens it, and sections without content share one sentence.
  assert.equal(await page.locator('#detail-body [data-key="work-history"]').evaluate((node) => node.open), false);
  const headings = await page.locator("#detail-body .block-heading").allTextContents();
  const folded = await page.locator('#detail-body [data-key="work-folded"]').allTextContents();
  for (const name of ["Dependencies", "Issues"]) {
    assert.notEqual(headings.includes(name), folded.join(" ").includes(name), "the section " + name + " is shown twice or not at all");
  }
  await page.keyboard.press("Escape");
  await page.waitForFunction((selector) => document.getElementById("detail").hidden && document.activeElement.matches(selector), trigger);
  step("the keyboard opens the work pane and Escape returns focus to the trigger");

  // No form and no write: the server refuses a write, and the places where the user decides point to the chat.
  assert.equal(await page.locator("#form-dialog").count(), 0);
  const refused = await page.evaluate(async () => { const response = await fetch("api/actions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    return [response.status, (await response.json()).error]; });
  assert.deepEqual(refused, [405, "ReadOnly"]);
  await go(page, "#learning");
  await page.locator("#main .pane-row").first().click();
  await page.waitForFunction(() => document.getElementById("detail-kind").textContent === "Decide a lesson" && !document.querySelector(".detail-content.pending"));
  assert.match(await text(page, "#detail .chat-hint"), /in the chat/);
  assert.equal(await page.locator("#detail .detail-foot button, #detail .detail-foot textarea").count(), 0);
  await closePane(page);
  await go(page, "#requirements");
  assert.match(await text(page, "#main .chat-hint"), /approve or revise the requirements/);
  await go(page, "#machine");
  assert.match(await text(page, "#main .chat-hint"), /Accept or decline this rule in the chat/);
  await go(page, "#agents");
  await page.locator("#main .db-table tbody tr").filter({ hasText: "Awaiting a decision" }).first().locator("button.db-open").click();
  await page.waitForFunction(() => !document.getElementById("detail").hidden && !document.querySelector(".detail-content.pending"));
  assert.match(await text(page, "#detail .chat-hint"), /To merge or discard this delegated work/);
  await closePane(page);
  for (const name of ["now", "plan", "work", "decisions", "records", "learning", "agents", "machine", "hive", "sessions"]) {
    await go(page, "#" + name);
    assert.equal(await page.locator('#main button.primary, #main [data-key^="form:"]').count(), 0, `the ${name} view still offers an edit action`);
  }
  step("the panel is read only: the server refuses a write, and the lesson, the requirements, a machine rule and a merge point to the chat");

  // Focus and the open view survive a new revision.
  await go(page, "#work/tab=board");
  const card = `[data-key="work-card-${ids.review}"]`;
  await page.locator(card).focus();
  const revision = await page.evaluate(() => Panel.health().revision);
  chatComment(fixture, ids.review, "The browser check adds this comment.");
  await page.waitForFunction((value) => Panel.health().revision !== value, revision);
  await page.waitForTimeout(700);
  assert.ok(await page.evaluate((selector) => document.activeElement.matches(selector), card), "focus moved after the refresh");
  assert.match(page.url(), /#work/);
  step("a new revision keeps the open view and the focused work card");

  // A live update keeps the selection, the scroll position of the board region and of the pane, and the text typed into a field.
  // The board scrolls inside its own region of the frame, so the view itself does not scroll.
  await page.setViewportSize({ width: 1600, height: 640 });
  await page.locator(card).click();
  await page.waitForFunction(() => document.getElementById("detail-title").textContent !== "Loading" && !document.querySelector(".detail-content.pending"));
  const before = await page.evaluate(() => { const region = document.querySelector('#main [data-scroll="board"]'), body = document.querySelector("#detail .detail-scroll");
    region.scrollTop = 90; body.scrollTop = 120; return [region.scrollTop, body.scrollTop]; });
  assert.ok(before[0] > 0 && before[1] > 0, `the board region or the pane does not scroll at 640 pixels: ${before}`);
  const update = async () => {
    const from = await page.evaluate(() => Panel.health().revision);
    chatComment(fixture, ids.review, "The browser check adds a second comment.");
    await page.waitForFunction((value) => Panel.health().revision !== value, from);
    await page.waitForTimeout(700);
    await settle(page);
  };
  await update();
  assert.deepEqual(await page.evaluate(() => [document.querySelector('#main [data-scroll="board"]').scrollTop, document.querySelector("#detail .detail-scroll").scrollTop]), before);
  assert.equal(await page.locator(card).getAttribute("aria-current"), "true");
  await closePane(page);
  await go(page, "#records");
  await page.locator("#records-query").focus();
  await page.keyboard.type("Parse");
  await update();
  assert.deepEqual(await page.evaluate(() => [document.activeElement.id, document.getElementById("records-query").value]), ["records-query", "Parse"]);
  await page.locator("#records-query").fill("");
  await page.setViewportSize({ width: 1440, height: 900 });
  await go(page, "#work/tab=board");
  step("a live update keeps the selected row, the scroll position of the view and of the pane, and the text typed into a field");

  // Focus that moves while a refresh is still loading has to survive the swap instead of falling back to the body.
  await page.route("**/api/board*", async (route) => { await new Promise((resolve) => setTimeout(resolve, 1200)); await route.continue(); });
  const slow = await page.evaluate(() => Panel.health().revision);
  chatComment(fixture, ids.review, "The browser check adds a third comment.");
  await page.waitForFunction((value) => Panel.health().revision !== value, slow);
  await page.waitForTimeout(300);
  await page.locator(card).focus();
  await page.waitForTimeout(1800);
  await settle(page);
  assert.ok(await page.evaluate((selector) => document.activeElement.matches(selector), card), "focus moved during a slow refresh was discarded");
  await page.unroute("**/api/board*");
  step("focus that moves while a refresh is loading is kept when the new view appears");

  // Update failures show and recover.
  await page.route("**/api/health", (route) => route.abort());
  await page.waitForFunction(() => document.getElementById("live-status").textContent === "Update failed", null, { timeout: 15000 });
  assert.match(await page.locator("#alerts").textContent(), /[Uu]pdate/);
  await page.unroute("**/api/health");
  await page.waitForFunction(() => document.getElementById("live-status").textContent === "Live", null, { timeout: 15000 });
  assert.equal(await page.locator("#alerts .alert").count(), 0);
  step("an update failure is reported and clears when the updates return");

}

// The Focus section runs on a separate product fixture with a finished focused problem, so the counts that the other
// checks assert stay unchanged. Attempt 1 failed its check on Codex and attempt 2 passed it on Claude and was merged.
const FOCUS = {
  problem: "The export drops the time zone of every date, so the sales team reads the wrong delivery day.",
  first: "The date formatter ignores the time zone of the parsed value.",
  second: "The parser converts every date to local time before the export reads it.",
  third: "The export template truncates the date to its first ten characters.",
};
async function openFocus(page, id) {
  await closePane(page);
  await page.evaluate((value) => Panel.openWork(value), id);
  await page.waitForFunction(() => document.getElementById("detail-title").textContent === "Design the export format");
  await settle(page);
  await page.waitForSelector('[data-key="work-focus"] [data-key="focus-report"]');
}
const boxes = (page) => page.evaluate(() => [...document.querySelectorAll('[data-key^="focus-attempt-"]')].map((node) => {
  const box = node.getBoundingClientRect();
  return { x: box.x, y: box.y, right: box.right };
}));
async function focusContent(page, label) {
  const section = await text(page, '[data-key="work-focus"]');
  for (const part of [FOCUS.problem, "Relay mode", "2 attempts allowed", "python3 -m unittest tests.test_export_format", "120 seconds",
    "tests/test_export_format.py", "An earlier delegated run failed its review or its focused check."]) {
    assert.ok(section.includes(part), `${label}: the Focus section lacks ${part}`);
  }
  assert.equal(await page.locator('[data-key^="focus-attempt-"]').count(), 2, `${label}: the Focus section does not show both attempts`);
  const first = await text(page, '[data-key="focus-attempt-1"]');
  for (const pattern of [/Codex/, new RegExp(FOCUS.first), /The check failed with exit code 1\./, /14/, /No work review is recorded\./, /Discarded/]) {
    assert.match(first, pattern, `${label}: attempt 1 lacks ${pattern}`);
  }
  const second = await text(page, '[data-key="focus-attempt-2"]');
  for (const pattern of [/Selected/, /Claude/, new RegExp(FOCUS.second), /The check passed with exit code 0\./, /Pass/, /Merged/]) {
    assert.match(second, pattern, `${label}: attempt 2 lacks ${pattern}`);
  }
  await page.locator('[data-key="focus-attempt-1"] details').evaluate((node) => { node.open = true; });
  assert.match(await text(page, '[data-key="focus-attempt-1"] details pre'), /AssertionError: the export dropped the time zone\nFAILED \(failures=1\)/);
  const ruledOut = await page.locator('[data-key="work-focus"] section.stack').filter({ hasText: "Ruled out hypotheses" }).last().innerText();
  assert.ok(ruledOut.includes(FOCUS.first) && ruledOut.includes("Last output line: FAILED (failures=1)."), `${label}: the ruled out hypothesis is missing`);
  assert.ok(!ruledOut.includes(FOCUS.second) && !ruledOut.includes(FOCUS.third), `${label}: a hypothesis that was not ruled out is listed as ruled out`);
  const report = await text(page, '[data-key="focus-report"]');
  assert.match(report, /1 start recorded 2 attempts\. 1 passed the check, 1 failed it and 0 did not run it\./);
  assert.match(report, /1 start ended with a passing review, 0 ended blocked and 0 are still running\./);
  for (const pattern of [/Codex: 1/, /Claude: 1/, /4,700/, /131 seconds/, /2\.7 seconds/, /Pass: 1/, /0 of 1 start/, /never from the report of an agent/]) {
    assert.match(report, pattern, `${label}: the report lacks ${pattern}`);
  }
}

async function focused(browser, directory) {
  const snapshotFile = path.join(directory, "focus.html");
  const fixture = serve("product", directory, ["--focus", "--hive", "--export", snapshotFile], "focus");
  const id = fixture.ids.focus_item;
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const problems = watch(page);
  await page.goto(fixture.url);
  await page.waitForFunction(() => document.getElementById("live-status").textContent === "Live", null, { timeout: 20000 });
  await openFocus(page, id);
  await focusContent(page, "desktop");
  const wide = await boxes(page);
  assert.ok(Math.abs(wide[0].y - wide[1].y) < 2 && wide[1].x > wide[0].right, `the attempts are not side by side at 1440 pixels: ${JSON.stringify(wide)}`);
  step("focus: the work pane shows the problem, the check, both attempts side by side, the ruled out hypothesis and the report");

  // The check runs a command on this computer, so the user sets it and starts the attempts in the chat, as the section says.
  assert.equal(await page.locator('[data-key="work-focus"] button').filter({ hasText: /Set the check|Start the attempts/ }).count(), 0);
  assert.match(await text(page, '[data-key="work-focus"] .chat-hint'), /Ask the assistant in the chat to start the attempts/);
  step("focus: the Focus section offers no form and says how to start the attempts in the chat");

  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(250);
  const narrow = await boxes(page);
  assert.ok(narrow[1].y > narrow[0].y, `the attempts do not stack on a 390 pixel screen: ${JSON.stringify(narrow)}`);
  assert.ok(narrow.every((box) => box.x >= 0 && box.right <= 390), `an attempt sits off a 390 pixel screen: ${JSON.stringify(narrow)}`);
  await focusContent(page, "phone");
  await noOverflow(page, 390, "focus pane");
  step("focus: the attempts stack and stay on a 390 pixel screen");
  assert.deepEqual(problems, [], "the Focus checks logged console or page errors");
  await page.close();

  // The read only snapshot shows the same Focus section without any action.
  const offline = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const offlineProblems = watch(offline);
  await offline.goto(pathToFileURL(fixture.snapshot).href);
  await offline.waitForFunction(() => /^Snapshot from /.test(document.getElementById("live-status").textContent), null, { timeout: 20000 });
  for (const width of [1440, 390]) {
    await offline.setViewportSize({ width, height: 900 });
    await openFocus(offline, id);
    await focusContent(offline, "snapshot at " + width + " pixels");
    assert.equal(await offline.locator('[data-key="work-focus"] [data-key^="form:focus_"]').count(), 0, `the snapshot offers a focus action at ${width} pixels`);
    assert.equal(await offline.locator('[data-key="work-focus"] button').filter({ hasText: /Set the check|Start the attempts/ }).count(), 0);
    assert.ok(!(await text(offline, '[data-key="work-focus"]')).includes("assistant session"), "the snapshot explains an action it does not offer");
    await noOverflow(offline, width, "focus snapshot");
  }
  assert.deepEqual(offlineProblems, [], "the Focus snapshot logged console or page errors");
  step("focus: the read only snapshot shows the Focus section without Set the check or Start the attempts at 1440 and 390 pixels");

  // The snapshot does not carry the hive, so the Hive view says so instead of failing.
  await go(offline, "#hive");
  assert.match(await text(offline, "#main"), /not included in this snapshot/);
  assert.deepEqual(offlineProblems, [], "the Hive view of the snapshot logged console or page errors");
  step("hive: the read only snapshot names the hive as not included");
  await go(offline, "#usage");
  assert.match(await text(offline, "#main"), /The usage ledger stays on the computer that holds it, so a snapshot carries no usage\./);
  assert.deepEqual(offlineProblems, [], "the Usage view of the snapshot logged console or page errors");
  step("usage: the read only snapshot names the usage ledger as not included");
  await offline.close();
  return fixture;
}

// The Hive view runs on the focus fixture, which also holds a swarm of three agents and the user (fixture.py hive_swarm).
const HIVE_LATE = "The template of the export cuts the offset from the date text.";
const cards = (page) => page.locator('[data-key="hive-timeline"] article');
const inView = (page, selector, width) => page.locator(selector).evaluate((node, limit) => {
  const box = node.getBoundingClientRect();
  return box.left >= 0 && box.right <= limit;
}, width);
async function hived(browser, fixture) {
  const { swarm, entries: e } = fixture.ids.hive;
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const problems = watch(page);
  await page.goto(fixture.url);
  await page.waitForFunction(() => document.getElementById("live-status").textContent === "Live", null, { timeout: 20000 });
  // Session flags are read in the panel and decided in the chat: a row opens the flag as a page that says so, and keys
  // typed into the search move nothing.
  await go(page, "#sessions");
  await page.waitForSelector('#main .view:not(.pending) [data-key="session-row-flag_fixture_4"]');
  assert.equal(await text(page, "[data-key=sessions-tab-flags]"), "Flags", "a Sessions tab shows a count of things to do");
  assert.equal(await page.locator("#main .pane-row").count(), 4);
  await page.click('[data-key="session-row-flag_fixture_4"]');
  await page.waitForFunction(() => /Wait for the review before the release/.test(document.getElementById("detail-title").textContent));
  await settle(page);
  assert.match(await text(page, "#detail .chat-hint"), /Confirm or dismiss this flag in the chat/);
  assert.equal(await page.locator("#detail .detail-foot button, #detail textarea").count(), 0);
  const quiet = () => page.evaluate(() => [/Wait for the review before the release/.test(document.getElementById("detail-title").textContent),
    document.querySelector("#main [data-selected]").dataset.key]);
  await page.locator("#find-open").click();
  await page.waitForSelector("#find[open]");
  await page.keyboard.type("jk");
  await page.waitForTimeout(400);
  assert.deepEqual([await page.locator("#search-input").inputValue(), ...await quiet()], ["jk", true, "session-row-flag_fixture_4"], "a key typed into the search field moved the selection");
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.getElementById("find").open);
  step("sessions: a flag opens as a page that says how to decide it in the chat, and keys typed into the search move nothing");
  // Sessions in the frame: the flags are rows under a tab, and no size scrolls the page with or without the pane.
  const flagPane = (pattern) => page.waitForFunction((source) => new RegExp(source).test(document.getElementById("detail-title").textContent) && !document.querySelector(".detail-content.pending"), pattern.source);
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    await go(page, "#sessions");
    await page.waitForSelector("#main .pane-row");
    await fixedFrame(page, `sessions at ${width} by ${height}`);
    await page.locator("#main .pane-row").first().click();
    await flagPane(/Wait for the review before the release/);
    await fixedFrame(page, `sessions with the pane at ${width} by ${height}`);
  }
  // The Proposals and Digests tabs scroll no page at 1600 by 640 either.
  await page.setViewportSize({ width: 1600, height: 640 });
  for (const hash of ["#sessions/tab=proposals", "#sessions/tab=digests"]) {
    await go(page, hash);
    await fixedFrame(page, `${hash} at 1600 by 640`);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`sessions: no page scroll at ${FRAMES.map((size) => size.join(" by ")).join(", ")} pixels with and without the pane, and the Proposals and Digests tabs scroll no page at 1600 by 640`);

  // J and K move along the flag rows while a flag is open.
  await go(page, "#sessions");
  await page.waitForFunction(() => /decided in the chat when you choose to/.test(document.getElementById("view-summary").textContent));
  assert.doesNotMatch(await text(page, "#view-summary"), /\d+ (open flag|pending proposal)/);
  assert.deepEqual(await page.locator("#main .db-views button").allTextContents(), ["Flags", "Proposals", "Digests"]);
  await page.click('[data-key="session-row-flag_fixture_3"]');
  await flagPane(/Use the second file instead/);
  assert.equal(await page.locator('#main [data-key="session-row-flag_fixture_3"]').getAttribute("aria-current"), "true");
  await page.locator("#detail-title").focus();
  await page.keyboard.press("j");
  await flagPane(/Do not change the export format/);
  await page.keyboard.press("k");
  await flagPane(/Use the second file instead/);
  await closePane(page);
  step("sessions: J and K move along the flag rows in the open page");

  // Hive in the frame: the swarms are rows, and a row opens the swarm as a conversation in the pane, whose body scrolls
  // and whose foot holds the actions of the user.
  await go(page, "#hive");
  assert.equal(await text(page, "#view-summary"), "1 swarm is recorded, and 1 is open.");
  const listed = await text(page, `#main .pane-row[data-key="hive-swarm-${swarm}"]`);
  for (const part of ["Wrong delivery day in the export", "Manual swarm", "13 entries", "Blind phase first", "codex-1", "claude-1", "codex-2"]) {
    assert.ok(listed.includes(part), "the swarm row lacks " + part);
  }
  await page.locator(`[data-key="hive-swarm-${swarm}"]`).click();
  await page.waitForSelector('#detail [data-key="hive-timeline"]');
  await settle(page);
  assert.equal(await page.locator(`#main [data-key="hive-swarm-${swarm}"]`).getAttribute("aria-current"), "true");
  assert.deepEqual(await page.evaluate(() => [document.getElementById("detail-kind").textContent, document.getElementById("detail-title").textContent]), ["Swarm", "Wrong delivery day in the export"]);
  assert.equal(await text(page, '#detail [data-key="hive-state"]'), "The swarm is open with 13 entries from 4 agents.");
  assert.equal(await cards(page).count(), 13);
  assert.match(await text(page, "#detail .chat-hint"), /To answer a question, ask one, post an observation or close the swarm, tell the assistant in the chat/);
  step("hive: the list shows the swarm row with its kind, agents and counts, and the row opens the swarm in the pane as a timeline of 13 entries");

  // Hosts are named in words as well as colours, and a verified command is marked with its exit code.
  assert.equal(await text(page, `#hive-${e.hypothesis} [data-state="host-codex"]`), "Codex");
  assert.equal(await text(page, `#hive-${e.support} [data-state="host-claude"]`), "Claude");
  assert.equal(await text(page, `#hive-${e.question} [data-state="host-workspace-user"]`), "User");
  assert.notEqual(await page.locator(`#hive-${e.hypothesis}`).evaluate((node) => node.style.borderLeftColor),
    await page.locator(`#hive-${e.support}`).evaluate((node) => node.style.borderLeftColor), "Codex and Claude entries share one colour");
  assert.match(await text(page, `#hive-${e.observation} [data-state="verified-command"]`), /^Verified command host_\w+, exit code 0$/);
  const conclusion = await text(page, `#hive-${e.conclusion}`);
  for (const pattern of [/Conclusion/, /High confidence/, /Confirmed/, /Keeping the offset in the export fixes the delivery day\./, /Cites e5 by codex-1/,
    new RegExp(`Supported by ${e.support} by claude-1`), new RegExp(`Challenged by ${e.challenge} by claude-1`)]) {
    assert.match(conclusion, pattern, "the conclusion lacks " + pattern);
  }
  assert.match(await text(page, `#hive-${e.question}`), /Addressed to agent codex-1\./);
  step("hive: each entry names its host, move and confidence, marks the verified command and the confirmed conclusion, and labels its links");

  // Answers and replies sit under their target; a challenge and a support stay in the timeline as labelled links.
  assert.equal(await page.locator(`#hive-${e.question} > .hive-thread > #hive-${e.answer}`).count(), 1, "the answer is not nested under its question");
  assert.equal(await page.locator(`#hive-${e.challenge} > .hive-thread > #hive-${e.reply}`).count(), 1, "the reply is not nested under the challenge");
  assert.equal(await page.locator(`[data-key="hive-timeline"] > #hive-${e.challenge}`).count(), 1, "the challenge is nested instead of shown in the timeline");
  assert.match(await text(page, `#hive-${e.challenge}`), new RegExp(`Challenges ${e.conclusion} by codex-1`));
  await page.locator(`[data-key="hive-link-${e.challenge}-Challenges-${e.conclusion}"]`).click();
  await page.waitForFunction((id) => document.activeElement && document.activeElement.id === "hive-" + id, e.conclusion);
  step("hive: answers and replies are nested under their target, and a challenge link moves focus to the challenged conclusion");

  // The blind phase is shown per agent.
  assert.equal(await page.locator('[data-key="hive-agent-codex-2"]').getAttribute("data-phase"), "blind");
  assert.match(await text(page, '[data-key="hive-agent-codex-2"]'), /Blind phase[\s\S]*has not posted its hypothesis/);
  assert.equal(await page.locator('[data-key="hive-agent-codex-1"]').getAttribute("data-phase"), "open");
  assert.match(await text(page, `#hive-${e.hypothesis}`), /Ends the blind phase of codex-1/);
  step("hive: codex-2 is marked as still in the blind phase, and the hypothesis that ended the phase of codex-1 says so");

  // Filters by move and agent redraw the pane and survive a live update.
  const shownEntries = (count) => page.waitForFunction((n) => document.querySelectorAll('#detail .detail-content:not(.pending) [data-key="hive-timeline"] article').length === n, count);
  await page.locator("#hive-move").selectOption("conclusion");
  await shownEntries(1);
  await settle(page);
  await page.locator("#hive-move").selectOption("");
  await shownEntries(13);
  await settle(page);
  await page.locator("#hive-agent").selectOption("claude-1");
  await shownEntries(5);
  await settle(page);
  assert.deepEqual(await page.locator('[data-key="hive-timeline"] article [data-state^="host-"]').allInnerTexts(), ["Claude", "Claude", "Claude", "Claude", "Claude"]);
  assert.equal(await page.locator("#hive-agent").inputValue(), "claude-1");
  await page.locator("#hive-agent").selectOption("");
  await shownEntries(13);
  await settle(page);
  step("hive: the move filter keeps the one conclusion and the agent filter keeps the 5 entries of claude-1");

  // The address carries the swarm and its filters: the selects follow it, a copied address opens the swarm with the
  // agent filter and marks its row, and closing the pane removes the three parameters.
  assert.match(page.url(), new RegExp(`#hive/swarm=${swarm}$`));
  await page.locator("#hive-agent").selectOption("claude-1");
  await shownEntries(5);
  await settle(page);
  assert.match(page.url(), new RegExp(`#hive/swarm=${swarm}&agent=claude-1$`));
  const address = page.url().slice(page.url().indexOf("#"));
  await go(page, "#now");
  await page.evaluate((value) => { location.hash = value; }, address);
  await page.waitForSelector('#detail [data-key="hive-timeline"]');
  await settle(page);
  await shownEntries(5);
  assert.deepEqual(await page.evaluate((id) => [document.getElementById("hive-agent").value, document.getElementById("hive-move").value, document.querySelector(`#main [data-key="hive-swarm-${id}"]`).getAttribute("aria-current")], swarm), ["claude-1", "", "true"]);
  await closePane(page);
  assert.match(page.url(), /#hive$/);
  await page.locator(`[data-key="hive-swarm-${swarm}"]`).click();
  await page.waitForSelector('#detail [data-key="hive-timeline"]');
  await settle(page);
  await page.locator("#hive-agent").selectOption("");
  await shownEntries(13);
  await settle(page);
  step("hive: the address carries the swarm and its filters, a copied address opens the swarm with its agent filter, and closing the pane clears them");

  // A worker writes an entry while the page is open, and the change polling shows it in the pane, which keeps its
  // reading position because its body is a scrolling region of the frame.
  const reading = await page.evaluate(() => { const body = document.querySelector("#detail .detail-scroll"); body.scrollTop = 400; return body.scrollTop; });
  assert.ok(reading > 0, "the body of the swarm pane does not scroll");
  const written = Date.now();
  const appended = JSON.parse(execFileSync(PYTHON, [path.join(ROOT, "tests/browser/fixture.py"), "--append-hive-entry", fixture.database], { cwd: ROOT, encoding: "utf8" }));
  const stored = Date.now();
  await page.waitForSelector("#hive-" + appended.id, { timeout: 6000 });
  const shown = Date.now();
  assert.match(await text(page, "#hive-" + appended.id), new RegExp(HIVE_LATE.replace(/\./g, "\\.")));
  assert.ok(shown - stored <= 2000, `the new entry appeared ${shown - stored} milliseconds after it was stored`);
  await page.waitForFunction(() => document.querySelector('[data-key="hive-agent-codex-2"]').dataset.phase === "open");
  await settle(page);
  assert.equal(await text(page, '#detail [data-key="hive-state"]'), "The swarm is open with 14 entries from 4 agents.");
  assert.equal(await page.evaluate(() => document.querySelector("#detail .detail-scroll").scrollTop), reading, "the live update lost the reading position of the timeline");
  await fixedFrame(page, "hive with the swarm pane");
  step(`hive: an entry written while the page is open appears ${shown - stored} milliseconds after it is stored (${stored - written} to store it), codex-2 leaves the blind phase, and the timeline keeps its reading position`);

  // No size scrolls the page with the swarm list alone or with the pane, and the open swarm says how to take part in the chat.
  for (const [width, height] of FRAMES) {
    await page.setViewportSize({ width, height });
    await fixedFrame(page, `hive with the pane at ${width} by ${height}`);
    assert.match(await text(page, "#detail .chat-hint"), /tell the assistant in the chat/, `hive at ${width} by ${height}: the open swarm does not say how to take part`);
    await closePane(page);
    await fixedFrame(page, `hive at ${width} by ${height}`);
    await page.locator(`[data-key="hive-swarm-${swarm}"]`).click();
    await page.waitForSelector('#detail [data-key="hive-timeline"]');
    await settle(page);
  }
  for (const width of [390, 320]) {
    await noOverflow(page, width, "hive timeline");
    for (const id of [e.conclusion, e.answer, e.reply, appended.id]) assert.ok(await inView(page, "#hive-" + id, width), `the entry ${id} sits off a ${width} pixel screen`);
  }
  const phone = await page.evaluate((ids) => ids.map((id) => document.getElementById("hive-" + id).getBoundingClientRect().width), [e.answer, e.reply]);
  // A nested entry keeps at least 70 percent of a 320 pixel screen, so the indentation of a thread never squeezes its text.
  assert.ok(phone.every((value) => value >= 224), `a nested entry is too narrow to read at 320 pixels: ${phone}`);
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`hive: no size scrolls the page with the swarm list or the pane, the open swarm points to the chat, and the timeline with its nested replies stays readable at 390 and 320 pixels`);

  // The user takes part in a swarm in the chat, so the panel offers no answer, question, close or purge.
  assert.equal(await page.locator('#detail button').filter({ hasText: /^(Answer|Ask a question|Post an observation|Close the swarm)$/ }).count(), 0);
  await closePane(page);
  assert.equal(await page.locator("#main button").filter({ hasText: /Purge closed swarms/ }).count(), 0);
  step("hive: the panel offers no post, close or purge, because the user takes part in the chat");
  assert.deepEqual(problems, [], "the Hive checks logged console or page errors");
  await page.close();
}

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "panel-browser-"));
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  try {
    for (const [kind, expected] of Object.entries(KINDS)) {
      const fixture = serve(kind, directory);
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      const problems = watch(page);
      await page.goto(fixture.url);
      await page.waitForFunction(() => document.getElementById("live-status").textContent === "Live", null, { timeout: 20000 });
      await views(page, kind, expected);
      if (kind === "product") await readOnly(page, fixture);
      assert.deepEqual(problems, [], `${kind} logged console or page errors`);
      step(`${kind}: no console or page errors in live mode`);
      await page.close();
    }
    await hived(browser, await focused(browser, directory));
    console.log(`\n${results.length} checks passed.`);
  } finally {
    await browser.close();
    stopServers();
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exit(1); });
