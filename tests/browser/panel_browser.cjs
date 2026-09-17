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
const RAIL = ["Now", "Plan", "Work", "Architecture", "Dependencies", "Decisions", "Learning", "Agents", "Machine", "Hive", "Usage", "Sessions", "Records", "Requirements"];
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
  // The checks cause a conflict and an aborted update on purpose; Chromium reports both as resource errors.
  const expected = /^Failed to load resource: (the server responded with a status of (400|409)|net::ERR_FAILED)/;
  page.on("console", (message) => {
    if (message.type() === "error" && !expected.test(message.text())) problems.push("console: " + message.text());
  });
  return problems;
}
const settle = (page) => page.waitForFunction(() => !document.querySelector("#main .view.pending, .drawer-content.pending"));
async function closeDrawer(page) {
  if (await page.locator("#drawer").isHidden()) return;
  await page.evaluate(() => Panel.closeDrawer());
  await page.waitForFunction(() => document.getElementById("drawer").hidden);
}
async function go(page, hash) {
  // The drawer stays open across view changes by design, so each section starts from a closed drawer.
  await closeDrawer(page);
  await page.evaluate((value) => { location.hash = value; }, hash);
  await page.waitForFunction((name) => document.querySelector(`#main .view[data-view="${name}"]:not(.pending)`), hash.split("/")[0].slice(1));
  await settle(page);
}
async function noOverflow(page, width, label) {
  await page.setViewportSize({ width, height: 900 });
  await page.waitForTimeout(250);
  const size = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  assert.ok(size[0] <= size[1], `${label} overflows at ${width} pixels: ${size}`);
}
const text = (page, selector) => page.locator(selector).first().innerText();
const field = (page, name) => page.locator(`#form-fields [name="${name}"]`);
const savedToast = async (page, pattern) => {
  await page.waitForSelector("#form-dialog:not([open])", { state: "attached", timeout: 15000 });
  assert.match(await page.locator(".toast").last().textContent(), pattern);
};

async function views(page, kind, expected) {
  await go(page, "#now");
  assert.deepEqual(await page.locator(".nav-link").allTextContents(), RAIL);
  assert.equal(await text(page, "#main .sentence"), "1 work item is in progress, 2 are blocked and 1 needs review.");
  assert.match(await text(page, "#main .card.kickoff"), new RegExp(expected.template));
  // Section 17.12: four cards of at most five items each; decisions and scope blocks open from the view head.
  assert.equal(await page.locator("#main .now-grid > .card").count(), 4);
  assert.equal(await page.locator('#main [data-key^="now-attention-"]').count(), 5);
  assert.equal(await page.locator("[data-key=now-open-decisions]").count(), 1);
  assert.equal(await page.locator("[data-key=now-open-blocks]").count(), 1);
  assert.ok(await page.locator("#ledger .strip-part").count() >= 4, "the state ledger shows no work states");
  step(`${kind}: Now states the project position, the kickoff checklist and the attention list`);

  // The checklist must not push the project position, the blocked work and the attention list below the first screen.
  const checklist = await page.locator("#main .card.kickoff").boundingBox();
  assert.ok(checklist.height <= 360, `${kind}: the kickoff card is ${Math.round(checklist.height)} pixels high and fills the first screen`);
  const firstEntry = await page.locator('#main [data-key^="now-attention-"]').first().boundingBox();
  assert.ok(firstEntry.y < 900, `${kind}: the attention list starts at ${Math.round(firstEntry.y)} pixels, below the first screen`);
  assert.equal(await page.locator('#main [data-key^="now-attention-"] .item-action').count(), 5);
  assert.match(await text(page, "#main .strip-legend"), /\d+ in progress/);
  await page.click("[data-key=now-all-attention]");
  await page.waitForSelector("#drawer-body [data-key^='now-attention-']");
  assert.equal(await page.locator('#drawer-body [data-key^="now-attention-"]').count(), 8);
  const nowText = await text(page, "#drawer-body");
  await closeDrawer(page);
  await page.click("[data-key=now-open-decisions]");
  await page.waitForSelector("#drawer-body a[href='#decisions']");
  await closeDrawer(page);
  assert.match(nowText, /occurred once after the lesson was accepted/);
  assert.match(nowText, /changed 1 file and awaits a merge decision/);
  step(`${kind}: Now keeps the first screen, names each action and counts a single event in the singular`);

  await go(page, "#plan");
  assert.match(await text(page, "#main .sentence"), /16 work items are planned in 7 phases\. 0 are done\./);
  assert.equal(await page.locator("#main .timeline > li").count(), 7);
  const timeline = await page.locator("#main .timeline").evaluate((node) => [node.scrollWidth, node.clientWidth]);
  assert.ok(timeline[0] <= timeline[1], `${kind}: the timeline clips its last phase: ${timeline}`);
  step(`${kind}: Plan shows 7 phases and 16 work items, and the timeline shows every phase`);

  await go(page, "#work");
  assert.equal(await page.locator("#main .board-column").count(), 5);
  const board = await page.locator("#main .board").evaluate((node) => [node.scrollWidth, node.clientWidth]);
  assert.ok(board[0] <= board[1], `${kind}: the board clips a column at 1440 pixels: ${board}`);
  assert.match(await text(page, "#main"), /Waits for \d+ prerequisite/);
  await page.locator("#work-tab-list").click();
  await page.waitForFunction(() => document.querySelectorAll("#main table.data tbody tr").length === 16);
  await page.locator("#work-state").selectOption("blocked");
  await page.waitForFunction(() => document.querySelectorAll("#main table.data tbody tr").length === 2);
  assert.match(page.url(), /state=blocked/);
  step(`${kind}: Work lists 16 items and the state filter keeps 2 blocked items`);

  await go(page, "#architecture");
  assert.equal(await text(page, "#main .view-head h2"), expected.architecture);
  assert.match(await text(page, "#main .sentence"), expected.items);
  await page.waitForSelector("#main .graph canvas");
  assert.equal(await page.locator('.node-list button[data-key^="arch-node-"]').count(), expected.nodes);
  step(`${kind}: Architecture uses the template labels and draws ${expected.nodes} items`);

  // The sentence, the item list and the graph state the same number, and nothing is hidden before a filter is set.
  const sentence = await text(page, "#main .sentence");
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
    const files = await text(page, "#main");
    assert.match(files, /This view shows 4 files\./);
    assert.ok(!files.includes("blocked work"), "the file level still claims blocked work");
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
  assert.match(await text(page, "#dep-panel .sentence"), /8 dependencies connect 10 work items\./);
  assert.match(await text(page, "#dep-panel .sentence"), /1 blocked chain is marked in red/);
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

  await go(page, "#decisions");
  assert.match(await text(page, "#main .sentence"), /3 decisions are recorded\. 2 have a bad outcome and 1 needs review\./);
  assert.equal(await page.locator('#main [data-key^="decision-event_"]').count(), 3);
  assert.equal(await page.locator('#main [data-state="needs-review-marker"]').count(), 1);
  step(`${kind}: Decisions list 3 decisions with outcome badges and the review marker`);

  if (kind === "product") {
    await page.locator('#main [data-key^="decision-event_"]').first().click();
    await page.waitForFunction(() => /Lineage/.test(document.getElementById("drawer-body").textContent));
    await settle(page);
    assert.equal(await page.locator("#drawer-body .lineage-list").count(), 1, "a lineage of dozens of records still opens as a small picture");
    assert.match(await text(page, '#drawer-body [data-key$="-toggle"]'), /Show as graph/);
    await closeDrawer(page);
    step(`${kind}: a decision lineage of dozens of records opens as the readable list`);
  }

  await go(page, "#learning");
  const learning = await text(page, "#main");
  assert.match(learning, /2 accepted guards are active, and 1 guard recorded a recurrence\./);
  assert.match(learning, /Recurred 1 time/);
  assert.match(learning, /Failures without lessons/);
  assert.equal(await page.locator('#main .kn-guard[data-tone="blocked"] [data-key^="form:reassess:"]').count(), 1,
    `${kind}: the counted recurrence has no Reassess action next to it`);
  assert.equal(await page.locator('#main [data-key^="form:reassess:"]').first().innerText(), "Reassess");
  step(`${kind}: Learning shows the accepted guards, the recurrence with its Reassess action and the failures without lessons`);

  // Instructions: one panel per role with its base text, its rules, its omissions and its budget.
  assert.equal(await page.locator('#main [data-key^="instructions-"]').count(), 3);
  const assistant = await text(page, '[data-key="instructions-assistant"]');
  assert.match(assistant, /This prompt carries 1 rule and uses \d+ of 600 characters\./);
  assert.match(assistant, /The shipped file agents\/assistant\.md is in force\./);
  assert.match(assistant, /2 runs composed it/);
  const reviewer = await text(page, '[data-key="instructions-reviewer"]');
  assert.match(reviewer, /You saved version 1 of this text in this project\./);
  assert.match(reviewer, /Give one verdict of pass, changes required or uncertain\./);
  assert.match(reviewer, /Unproven/);
  const worker = await text(page, '[data-key="instructions-worker"]');
  assert.match(worker, /uses \d+ of 1,200 characters/);
  assert.match(worker, /1 further accepted rule is composed only into a run that matches the triggers\./);
  assert.match(worker, /Ineffective/);
  assert.match(worker, /Recurrences before 1, after 1/);
  step(`${kind}: the Instructions section states the base text, the rules in force and the budget of each role`);

  // On a phone the base text scrolls inside its own block and the rules stay on screen.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(250);
  const panel = await page.evaluate(() => {
    const card = document.querySelector('[data-key="instructions-reviewer"]');
    const base = card.querySelector(".kn-base");
    const rule = card.querySelector(".kn-rule");
    return { card: card.getBoundingClientRect().right, base: base.getBoundingClientRect().right,
      rule: rule ? rule.getBoundingClientRect().right : null };
  });
  assert.ok(panel.card <= 390, `${kind}: the reviewer panel ends at ${Math.round(panel.card)} pixels on a 390 pixel screen`);
  assert.ok(panel.base <= 390, `${kind}: the base text block ends at ${Math.round(panel.base)} pixels on a 390 pixel screen`);
  assert.ok(panel.rule !== null && panel.rule <= 390, `${kind}: a composed rule sits off a 390 pixel screen: ${panel.rule}`);
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: the Instructions section keeps the base text and the rules on a 390 pixel screen`);

  await go(page, "#agents");
  assert.match(await text(page, "#main .sentence"), /configured hosts can run work now/);
  assert.match(await text(page, "#main"), /1 delegated run awaits a merge decision/);
  assert.equal(await page.locator("#main table.data tbody tr").count(), 2);
  step(`${kind}: Agents show the hosts and the two recorded runs`);

  const agents = await text(page, "#main");
  assert.ok(!agents.includes("Not applicable"), `${kind}: the runs table still prints Not applicable`);
  assert.match(agents, /columns describe delegated work/);
  // On a phone the merge decision has to stay on screen instead of hiding behind a sideways scroll.
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(250);
  const merge = await page.evaluate(() => {
    const cell = [...document.querySelectorAll('#main .kn-table td[data-label="Merge"]')].find((node) => node.textContent.trim());
    return cell ? { right: cell.getBoundingClientRect().right, text: cell.textContent.trim() } : null;
  });
  assert.ok(merge && merge.right <= 390, `${kind}: the merge column sits off a 390 pixel screen: ${JSON.stringify(merge)}`);
  assert.match(merge.text, /Awaiting a decision|Not merged|merged/);
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: the runs table leaves the delegation columns empty for a check and keeps the merge state on a phone`);

  // The top bar states the phase of the project, because the phase decides who merges delegated work.
  assert.match(await text(page, "#phase"), /Lifecycle\s*Development/);
  assert.match(await page.locator("#phase .phase-button").getAttribute("title"), /may bring delegated work into the project after a passing work review/);
  step(`${kind}: the top bar states that the project is in development`);

  await go(page, "#machine");
  const machine = await text(page, "#main");
  assert.match(machine, /1 rule is in force on .+, promoted from 1 project\. 1 proposal from this project awaits your decision\./);
  assert.match(machine, /no outcome is combined across projects/);
  assert.equal(await page.locator('#main [data-key^="machine-rule-"]').count(), 1);
  assert.equal(await page.locator('#main [data-key^="promotion-"]').count(), 2);
  assert.match(await text(page, '#main [data-key^="machine-rule-"]'), /1 project promoted this rule/);
  assert.equal(await page.locator('#main [data-key^="machine-project-"]').count(), 1);
  assert.match(await text(page, '#main [data-key^="machine-project-"]'), /This project/);
  assert.equal(await page.locator('#main [data-key^="accept-promotion-"]').count(), 1);
  assert.equal(await page.locator('#main [data-key^="retire-rule-"]').count(), 1);
  step(`${kind}: Machine lists the promoted rule, its adoption count, the registry and the waiting proposal`);

  // The Usage view reads the ledger of the fixture machine memory: Codex at 91 percent and a Claude limit hit constrain both.
  await go(page, "#usage");
  assert.match(await text(page, "#main .sentence"), /^Usage was measured at .+ UTC\. 2 hosts are constrained\.$/);
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
  assert.ok(await page.locator("#main table.data").count() >= 1, "the records view lists no records");
  await go(page, "#requirements");
  assert.match(await text(page, "#main"), /The project requirements are not established yet\./);
  step(`${kind}: Records and Requirements render their current state`);

  for (const width of [1440, 768, 390, 320]) {
    for (const hash of ["#now", "#plan", "#work", "#architecture", "#decisions", "#learning", "#machine", "#hive", "#usage"]) {
      await go(page, hash);
      await noOverflow(page, width, `${kind} ${hash}`);
    }
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  step(`${kind}: no page overflow at 1440, 768, 390 and 320 pixels`);
}

// The editing checks run on one fixture, because each action changes the records the other checks read.
async function editing(page, ids, posts) {
  await go(page, "#now");
  const trigger = `[data-key="now-in_progress-${ids.story}"]`;
  await page.locator(trigger).focus();
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("drawer-title").textContent === "Parse client files");
  await settle(page);
  const drawer = await text(page, "#drawer-body");
  for (const part of ["Intended result", "Done when", "Next step", "Acceptance criteria", "Scope and allowed paths", "src/app/**",
    "Dependencies", "Agent checks and delegated runs", "Lineage", "History", "Edit plan", "Allow paths", "Delegate", "Request check", "Comment"]) {
    assert.ok(drawer.includes(part), "the work drawer lacks " + part);
  }
  await page.keyboard.press("Escape");
  await page.waitForFunction((selector) => document.getElementById("drawer").hidden && document.activeElement.matches(selector), trigger);
  step("the keyboard opens the work drawer and Escape returns focus to the trigger");

  // A conflict keeps the draft, and Reload saved version recovers it.
  await page.locator(trigger).click();
  await settle(page);
  await page.locator('[data-key="work-edit"]').click();
  await page.waitForSelector("#form-dialog[open]");
  await page.waitForFunction(() => document.querySelectorAll("#form-fields [name]").length > 0);
  await field(page, "scope").fill("The work covers the sample client files only.");
  await field(page, "reason").fill("The user narrows the scope after the review.");
  await page.evaluate(async (id) => {
    const value = await Panel.get("record", { id });
    await Panel.action("comment", { episode_id: id, expected_version: value.record.detail.version, text: "This comment changes the version." },
      Panel.requestKey("panel-check"));
  }, ids.story);
  await page.locator("#form-save").click();
  await page.waitForSelector("#form-reload:not([hidden])", { timeout: 15000 });
  assert.match(await page.locator("#form-error").textContent(), /changed while you were editing/);
  assert.equal(await field(page, "scope").inputValue(), "The work covers the sample client files only.");
  step("a conflict keeps the draft and offers Reload saved version");
  await page.locator("#form-reload").click();
  await page.waitForSelector("#form-reload[hidden]", { state: "attached" });
  await field(page, "reason").fill("The user narrows the scope after the review.");
  await page.locator("#form-save").click();
  await savedToast(page, /plan is saved/);
  step("Reload saved version draws the saved plan and the next save succeeds");
  await closeDrawer(page);

  // Creating a work item from the Plan view.
  await go(page, "#plan");
  await page.getByRole("button", { name: "Add item" }).first().click();
  await page.waitForSelector("#form-dialog[open]");
  await field(page, "title").fill("Export the parsed records");
  await field(page, "objective").fill("Export the parsed records for the sales team.");
  await field(page, "criterion").fill("The sales team receives a complete export.");
  await field(page, "scope").fill("The export of the parsed records.");
  await field(page, "next_action").fill("Write the export.");
  await field(page, "acceptance").fill("The export lists every record.");
  await field(page, "paths").fill("src/app/**");
  await field(page, "reason").fill("The user plans the export.");
  await page.locator("#form-save").click();
  await savedToast(page, /is created/);
  await page.waitForFunction(() => /17 work items are planned/.test(document.querySelector("#main .view:not(.pending) .sentence").textContent));
  step("a new work item is created from the Plan view and the plan counts it");

  // Accepting a proposed lesson with its triggers.
  await go(page, "#learning");
  await page.locator(`[data-key="accept-${ids.proposed_lesson}"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=reason]");
  assert.equal(await page.locator("input[name=status][value=accepted]").isChecked(), true);
  await field(page, "reason").fill("The practice prevents unclear release notes.");
  await field(page, "paths").fill("docs/releases/**");
  await field(page, "keywords").fill("release note");
  await field(page, "failure_type").fill("unclear_wording");
  await field(page, "role_reviewer").check();
  await page.locator("#form-save").click();
  await savedToast(page, /lesson is accepted/);
  await page.waitForFunction(() => /3 accepted guards are active/.test(document.querySelector("#main .view:not(.pending)").textContent), null, { timeout: 15000 });
  assert.match(await text(page, "#main"), /release note/);
  const guard = await page.evaluate(async (id) => {
    const learning = await Panel.get("learning");
    return learning.guards.find((entry) => entry.lesson_id === id);
  }, ids.proposed_lesson);
  assert.deepEqual([guard.paths, guard.keywords, guard.failure_type, guard.roles],
    [["docs/releases/**"], ["release note"], "unclear_wording", ["reviewer"]]);
  assert.match(await text(page, '[data-key="instructions-reviewer"]'),
    /1 further accepted rule is composed only into a run that matches the triggers\./);
  step("a proposed lesson is accepted with its path, keyword and failure type triggers and with the reviewer role");

  // Saving a new version of the base text of one role.
  await page.locator('[data-key="form:instructions:reviewer"]').click();
  await page.waitForSelector("#form-dialog[open] textarea[name=text]");
  assert.match(await field(page, "text").inputValue(), /You are the reviewer in this project\./);
  await field(page, "text").fill("You are the reviewer in this project. State the evidence for every judgement and give one verdict.");
  await field(page, "reason").fill("The user shortens the reviewer instructions.");
  await page.locator("#form-save").click();
  await savedToast(page, /Version 2 of the reviewer instructions is saved/);
  await page.waitForFunction(() => /You saved version 2 of this text/.test(
    document.querySelector('[data-key="instructions-reviewer"]').textContent), null, { timeout: 15000 });
  assert.match(await text(page, '[data-key="base-reviewer"]'), /State the evidence for every judgement and give one verdict\./);
  step("a new version of the reviewer base text is saved and the panel shows it");

  // Reassessing the counted recurrence as the user removes it from the count.
  const recurrencesBefore = await page.evaluate(async () => (await Panel.get("learning")).recurrences.reduce((sum, entry) => sum + entry.total, 0));
  assert.equal(recurrencesBefore, 1);
  await page.locator(`#main [data-key^="form:reassess:"][data-key$=":${ids.recurrence_outcome}"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=reason]");
  assert.match(await text(page, "#form-fields"), /Observed/);
  assert.equal(await page.locator('input[name="assessment"][value="good"]').isChecked(), true);
  await field(page, "reason").fill("The archive import was repeated and every character arrived.");
  await page.locator("#form-save").click();
  await savedToast(page, /The outcome is reassessed as good\./);
  await page.waitForFunction(() => !/recorded a recurrence/.test(document.querySelector("#main .view:not(.pending) .sentence").textContent), null, { timeout: 15000 });
  await settle(page);
  assert.equal(await page.locator('#main [data-key^="form:reassess:"]').count(), 0);
  assert.doesNotMatch(await text(page, "#main"), /Recurred 1 time/);
  const after = await page.evaluate(async () => {
    const learning = await Panel.get("learning");
    return [learning.recurrences.length, learning.guards.reduce((sum, guard) => sum + (guard.recurrences || 0), 0)];
  });
  assert.deepEqual(after, [0, 0]);
  step("the user reassesses the counted recurrence and the recurrence count drops from 1 to 0");

  // Allowing the blocked path of the scope block.
  await go(page, "#now");
  await page.click("[data-key=now-open-blocks]");
  await page.getByRole("button", { name: "Allow paths" }).first().click();
  await page.waitForSelector("#form-dialog[open]");
  assert.equal(await field(page, "paths").inputValue(), "docs/brief.md");
  await field(page, "reason").fill("The work item needs the brief.");
  await page.locator("#form-save").click();
  await savedToast(page, /docs\/brief\.md/);
  step("the blocked path of a scope block is allowed from the scope blocks drawer of the Now view");
  await closeDrawer(page);

  // The phase of the project, which decides who merges delegated work.
  await page.locator('[data-key="phase-change"]').click();
  await page.waitForSelector("#form-dialog[open] textarea[name=reason]");
  await page.locator('input[name="phase"][value="production"]').check();
  await field(page, "reason").fill("The parser serves users now, so the user merges delegated work.");
  await page.locator("#form-save").click();
  await savedToast(page, /The project is in production/);
  await page.waitForFunction(() => /Production/.test(document.getElementById("phase").textContent), null, { timeout: 15000 });
  const phase = await page.evaluate(() => Panel.health().phase);
  assert.equal(phase.phase, "production");
  assert.equal(phase.version, 1);
  step("the phase of the project is changed to production with a reason and the top bar states it");

  // Accepting a proposed rule into the machine memory, with its text corrected first.
  await go(page, "#machine");
  await page.locator(`[data-key="accept-promotion-${ids.proposed_promotion}"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=reason]");
  assert.equal(await page.locator('input[name=status][value=accepted]').isChecked(), true);
  await field(page, "do").fill("State which checks ran and what each one reported before the merge.");
  await field(page, "reason").fill("The rule holds for every project on this computer.");
  await page.locator("#form-save").click();
  await savedToast(page, /The rule is in force on/);
  await page.waitForFunction(() => /2 rules are in force/.test(document.querySelector("#main .view:not(.pending) .sentence").textContent), null, { timeout: 15000 });
  assert.match(await text(page, "#main"), /State which checks ran and what each one reported before the merge\./);
  step("a proposed rule is corrected and accepted into the machine memory");

  // Retiring a rule of the machine memory. The rule and its history stay readable.
  await page.locator(`[data-key="retire-rule-${ids.machine_rule}"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=reason]");
  await field(page, "reason").fill("The allowed paths are now required before a run starts.");
  await page.locator("#form-save").click();
  await savedToast(page, /The rule is retired/);
  await page.waitForFunction(() => /1 rule is in force/.test(document.querySelector("#main .view:not(.pending) .sentence").textContent), null, { timeout: 15000 });
  assert.match(await text(page, "#main"), /Retired rules/);
  step("a rule of the machine memory is retired and stays readable");

  // Focus and the open view survive a new revision.
  await go(page, "#work");
  const card = `[data-key="work-card-${ids.review}"]`;
  await page.locator(card).focus();
  const revision = await page.evaluate(() => Panel.health().revision);
  await page.evaluate(async (id) => {
    const work = await Panel.get("work", { id });
    await Panel.action("comment", { episode_id: id, expected_version: work.card.version, text: "The browser check adds this comment." },
      Panel.requestKey("panel-check"));
  }, ids.review);
  await page.waitForFunction((value) => Panel.health().revision !== value, revision);
  await page.waitForTimeout(700);
  assert.ok(await page.evaluate((selector) => document.activeElement.matches(selector), card), "focus moved after the refresh");
  assert.match(page.url(), /#work/);
  step("a new revision keeps the open view and the focused work card");

  // Focus that moves while a refresh is still loading has to survive the swap instead of falling back to the body.
  await page.route("**/api/board*", async (route) => { await new Promise((resolve) => setTimeout(resolve, 1200)); await route.continue(); });
  const slow = await page.evaluate(() => Panel.health().revision);
  await page.evaluate(async (id) => {
    const work = await Panel.get("work", { id });
    await Panel.action("comment", { episode_id: id, expected_version: work.card.version, text: "The browser check adds a second comment." },
      Panel.requestKey("panel-check"));
  }, ids.review);
  await page.waitForFunction((value) => Panel.health().revision !== value, slow);
  await page.waitForTimeout(300);
  await page.locator(card).focus();
  await page.waitForTimeout(1800);
  await settle(page);
  assert.ok(await page.evaluate((selector) => document.activeElement.matches(selector), card), "focus moved during a slow refresh was discarded");
  await page.unroute("**/api/board*");
  step("focus that moves while a refresh is loading is kept when the new view appears");

  assert.ok(posts.length >= 5, "the checks should have sent several actions");
  assert.ok(posts.every((post) => post.csrf && post.csrf.length > 10), "an action was sent without the CSRF header");
  assert.ok(posts.every((post) => post.key && post.key.length > 10), "an action was sent without a request key");
  step(`every one of the ${posts.length} actions carried the CSRF header and a request key`);

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
// The dialog closes first and the panel releases the form in its close event, so a check waits for both.
async function closeForm(page) {
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => !document.getElementById("form-dialog").open && !document.getElementById("form-fields").children.length);
}
async function openFocus(page, id) {
  await closeDrawer(page);
  await page.evaluate((value) => Panel.openWork(value), id);
  await page.waitForFunction(() => document.getElementById("drawer-title").textContent === "Design the export format");
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
  step("focus: the work drawer shows the problem, the check, both attempts side by side, the ruled out hypothesis and the report");

  // A panel started by an assistant does not carry the actions of the user, so it names the reason instead of the buttons.
  const assistant = await page.evaluate(() => Boolean((Panel.health() || {}).assistant_started));
  const buttons = page.locator('[data-key="work-focus"] [data-key^="form:focus_"]');
  if (assistant) {
    assert.equal(await buttons.count(), 0, "a panel started by an assistant offers the focus actions");
    assert.match(await text(page, '[data-key="work-focus"]'), /started from inside an assistant session, so it does not set the check or start the attempts/);
  } else {
    assert.deepEqual(await buttons.allInnerTexts(), ["Set the check", "Start the attempts"]);
  }
  // openForm resolves only when the form closes, so the check does not wait for it.
  await page.evaluate((value) => { Panel.openForm("focus_check", { episode_id: value }); }, id);
  await page.waitForSelector('#form-dialog[open] textarea[name="command"]');
  assert.equal(await field(page, "command").inputValue(), "python3\n-m\nunittest\ntests.test_export_format");
  assert.equal(await field(page, "timeout_seconds").inputValue(), "120");
  await field(page, "timeout_seconds").fill("300");
  await page.locator("#form-save").click();
  // The fixture is not a git repository, so even a panel started by the user is refused before anything is recorded.
  await page.waitForFunction(() => document.getElementById("form-error").textContent.length > 0, null, { timeout: 15000 });
  assert.match(await page.locator("#form-error").textContent(), assistant ? /started from inside an assistant session/ : /is not committed with its current content: tests\/test_export_format\.py\./);
  await closeForm(page);
  // openForm resolves only when the form closes, so the check does not wait for it.
  await page.evaluate((value) => { Panel.openForm("focus_start", { episode_id: value }); }, id);
  await page.waitForSelector("#form-dialog[open] #form-save");
  await page.waitForFunction((statement) => document.getElementById("form-fields").textContent.includes(statement), FOCUS.third);
  assert.ok(!(await text(page, "#form-fields")).includes(FOCUS.first), "the start form lists a ruled out hypothesis as open");
  await closeForm(page);
  const plan = await page.evaluate(async (value) => (await Panel.get("focus", { id: value })).focus.check.timeout_seconds, id);
  assert.equal(plan, 120, "a refused Set the check changed the recorded check");
  step("focus: the Set the check and Start the attempts forms open with the recorded check and the open hypothesis, and a refused check changes nothing");

  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(250);
  const narrow = await boxes(page);
  assert.ok(narrow[1].y > narrow[0].y, `the attempts do not stack on a 390 pixel screen: ${JSON.stringify(narrow)}`);
  assert.ok(narrow.every((box) => box.x >= 0 && box.right <= 390), `an attempt sits off a 390 pixel screen: ${JSON.stringify(narrow)}`);
  await focusContent(page, "phone");
  await noOverflow(page, 390, "focus drawer");
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
  await go(page, "#hive");
  assert.equal(await text(page, "#main .sentence"), "1 swarm is recorded, and 1 is open.");
  const listed = await text(page, `[data-key="hive-swarm-${swarm}"]`);
  for (const part of ["Wrong delivery day in the export", "Manual swarm", "13 entries", "Blind phase first", "codex-1", "claude-1", "codex-2"]) {
    assert.ok(listed.includes(part), "the swarm card lacks " + part);
  }
  await page.locator(`[data-key="hive-open-${swarm}"]`).click();
  await page.waitForSelector('[data-key="hive-timeline"]');
  await settle(page);
  assert.match(page.url(), new RegExp("#hive/swarm=" + swarm));
  assert.equal(await text(page, "#main .sentence"), "Wrong delivery day in the export is open with 13 entries from 4 agents.");
  assert.equal(await cards(page).count(), 13);
  step("hive: the list shows the swarm with its kind, agents and counts, and the swarm opens as a timeline of 13 entries");

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

  // Filters by move and agent are kept in the route.
  await page.locator("#hive-move").selectOption("conclusion");
  await page.waitForFunction(() => /move=conclusion/.test(location.hash) && document.querySelectorAll('#main .view:not(.pending) [data-key="hive-timeline"] article').length === 1);
  await settle(page);
  await page.locator("#hive-move").selectOption("");
  await page.waitForFunction(() => !/move=/.test(location.hash) && document.querySelectorAll('#main .view:not(.pending) [data-key="hive-timeline"] article').length === 13);
  await settle(page);
  await page.locator("#hive-agent").selectOption("claude-1");
  await page.waitForFunction(() => /agent=claude-1/.test(location.hash) && document.querySelectorAll('#main .view:not(.pending) [data-key="hive-timeline"] article').length === 5);
  await settle(page);
  assert.deepEqual(await page.locator('[data-key="hive-timeline"] article [data-state^="host-"]').allInnerTexts(), ["Claude", "Claude", "Claude", "Claude", "Claude"]);
  await page.locator("#hive-agent").selectOption("");
  await page.waitForFunction(() => document.querySelectorAll('#main .view:not(.pending) [data-key="hive-timeline"] article').length === 13);
  await settle(page);
  step("hive: the move filter keeps the one conclusion and the agent filter keeps the 5 entries of claude-1");

  // A worker writes an entry while the page is open, and the change polling shows it.
  const written = Date.now();
  const appended = JSON.parse(execFileSync(PYTHON, [path.join(ROOT, "tests/browser/fixture.py"), "--append-hive-entry", fixture.database], { cwd: ROOT, encoding: "utf8" }));
  const stored = Date.now();
  await page.waitForSelector("#hive-" + appended.id, { timeout: 6000 });
  const shown = Date.now();
  assert.match(await text(page, "#hive-" + appended.id), new RegExp(HIVE_LATE.replace(/\./g, "\\.")));
  assert.ok(shown - stored <= 2000, `the new entry appeared ${shown - stored} milliseconds after it was stored`);
  await page.waitForFunction(() => document.querySelector('[data-key="hive-agent-codex-2"]').dataset.phase === "open");
  assert.equal(await text(page, "#main .sentence"), "Wrong delivery day in the export is open with 14 entries from 4 agents.");
  step(`hive: an entry written while the page is open appears ${shown - stored} milliseconds after it is stored (${stored - written} to store it), and codex-2 leaves the blind phase`);

  for (const width of [390, 320]) {
    await noOverflow(page, width, "hive timeline");
    for (const id of [e.conclusion, e.answer, e.reply, appended.id]) assert.ok(await inView(page, "#hive-" + id, width), `the entry ${id} sits off a ${width} pixel screen`);
  }
  const phone = await page.evaluate((ids) => ids.map((id) => document.getElementById("hive-" + id).getBoundingClientRect().width), [e.answer, e.reply]);
  // A nested entry keeps at least 70 percent of a 320 pixel screen, so the indentation of a thread never squeezes its text.
  assert.ok(phone.every((value) => value >= 224), `a nested entry is too narrow to read at 320 pixels: ${phone}`);
  await page.setViewportSize({ width: 1440, height: 900 });
  step("hive: the timeline with its nested replies stays readable at 390 and 320 pixels");

  // The user posts as workspace-user: an answer to the open question and a question to one agent.
  await page.locator(`[data-key="form:hive_post:${swarm}:answer:${e.user_question}"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=claim]");
  assert.match(await text(page, "#form-fields"), /time zone of the customer/);
  await field(page, "claim").fill("The export shows each day in the time zone of the warehouse");
  await page.locator("#form-save").click();
  await page.waitForFunction(() => /rule claim_sentence/.test(document.getElementById("form-error").textContent), null, { timeout: 15000 });
  await field(page, "claim").fill("The export shows each day in the time zone of the warehouse.");
  await page.locator("#form-save").click();
  await savedToast(page, /The answer e\d+ is posted\./);
  await page.waitForFunction((id) => document.querySelector(`#hive-${id} > .hive-thread [data-state="host-workspace-user"]`), e.user_question, { timeout: 15000 });
  await settle(page);
  await page.locator(`[data-key="form:hive_post:${swarm}:question"]`).click();
  await page.waitForSelector("#form-dialog[open] select[name=addressee]");
  await field(page, "claim").fill("Which file formats the date for the warehouse report?");
  await field(page, "addressee").selectOption("agent:codex-2");
  await page.locator("#form-save").click();
  await savedToast(page, /The question e\d+ is posted\./);
  await page.waitForFunction(() => /Addressed to agent codex-2\./.test(document.querySelector('#main .view:not(.pending) [data-key="hive-timeline"]').textContent), null, { timeout: 15000 });
  await page.locator(`[data-key="form:hive_post:${swarm}:observation"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=bases]");
  await field(page, "claim").fill("The warehouse report reads the export module.");
  await field(page, "bases").fill("src/app/export.py:1");
  await page.locator("#form-save").click();
  await page.waitForFunction(() => /no known kind/.test(document.getElementById("form-error").textContent));
  await field(page, "bases").fill("file: src/app/export.py:1");
  await page.locator("#form-save").click();
  await savedToast(page, /The observation e\d+ is posted\./);
  await page.waitForFunction(() => document.querySelectorAll('#main .view:not(.pending) [data-key="hive-timeline"] article').length === 17, null, { timeout: 15000 });
  step("hive: the user answers the open question, asks codex-2 a question and posts an observation, and a refused claim names its rule");

  await page.locator(`[data-key="form:hive_close:${swarm}"]`).click();
  await page.waitForSelector("#form-dialog[open] textarea[name=summary]");
  assert.match(await text(page, "#form-fields"), /Confirmed conclusions\s*1 of 1/);
  await field(page, "summary").fill("Keeping the offset fixes the delivery day in both exports.");
  await page.locator("#form-save").click();
  await savedToast(page, /The swarm is closed\. Confirmed conclusions: 1 of 1\. Proposed lessons: 0\./);
  await page.waitForFunction(() => /is closed with 17 entries/.test(document.querySelector("#main .view:not(.pending) .sentence").textContent), null, { timeout: 15000 });
  await settle(page);
  assert.equal(await page.locator('[data-key="hive-actions"]').count(), 0, "a closed swarm still offers actions");
  assert.equal(await page.locator('[data-key^="form:hive_post:"]').count(), 0, "a closed swarm still offers an answer");
  step("hive: the user closes the swarm with a summary, and the closed swarm offers no further posts");

  // Purge is a user action, so a panel started by an assistant refuses it and names the reason.
  await go(page, "#hive");
  const assistant = await page.evaluate(() => Boolean((Panel.health() || {}).assistant_started));
  if (assistant) {
    assert.equal(await page.locator('[data-key="form:hive_purge"]').count(), 0, "a panel started by an assistant offers the purge");
    assert.match(await text(page, '[data-key="hive-purge-refused"]'), /does not purge swarms/);
    const refusal = await page.evaluate(() => Panel.action("hive_purge", { closed_before_days: 0 }, Panel.requestKey("hive-purge")).then(() => "", (error) => error.message));
    assert.match(refusal, /does not purge swarms of the hive/);
    assert.equal(await text(page, "#main .sentence"), "1 swarm is recorded, and 0 are open.");
    step("hive: a panel started by an assistant offers no purge and the server refuses one");
  } else {
    await page.locator('[data-key="form:hive_purge"]').click();
    await page.waitForSelector("#form-dialog[open] input[name=closed_before_days]");
    await field(page, "closed_before_days").fill("0");
    await page.locator("#form-save").click();
    await savedToast(page, /1 swarm with 17 entries was purged\./);
    await page.waitForFunction(() => /No swarm is recorded yet/.test(document.querySelector("#main .view:not(.pending) .sentence").textContent), null, { timeout: 15000 });
    step("hive: the user purges the closed swarm and the list is empty");
  }
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
      const posts = [];
      page.on("request", (request) => {
        if (request.method() === "POST") {
          posts.push({ csrf: request.headers()["x-project-memory"], key: (JSON.parse(request.postData() || "{}")).request_key });
        }
      });
      await page.goto(fixture.url);
      await page.waitForFunction(() => document.getElementById("live-status").textContent === "Live", null, { timeout: 20000 });
      await views(page, kind, expected);
      if (kind === "product") await editing(page, fixture.ids, posts);
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
