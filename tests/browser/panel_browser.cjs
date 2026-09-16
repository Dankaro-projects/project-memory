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

const ROOT = path.resolve(__dirname, "../..");
const PYTHON = process.env.MEMORY_PYTHON || "python";
const RAIL = ["Now", "Plan", "Work", "Architecture", "Dependencies", "Decisions", "Learning", "Agents", "Machine", "Records", "Requirements"];
// nodes is the number of items the architecture graph shows before any filter or toggle is changed.
const KINDS = {
  product: { template: "Software product template", architecture: "Components and packages", items: /2 code components/, nodes: 3 },
  engagement: { template: "Consulting engagement template", architecture: "Stakeholders and workstreams", items: /5 authored items/, nodes: 5 },
  automation: { template: "Workflow automation template", architecture: "Systems and workflows", items: /2 workflows and 3 connected services/, nodes: 5 },
};
const results = [];
const step = (name) => { results.push(name); console.log("ok  " + name); };
const states = [];

function serve(kind, directory) {
  const output = path.join(directory, kind);
  const printed = execFileSync(PYTHON, [path.join(ROOT, "tests/browser/fixture.py"), "--kind", kind, "--output", output, "--serve"],
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
  assert.equal(await page.locator("#main .now-grid > .card").count(), 6);
  assert.equal(await page.locator('#main [data-key^="now-attention-"]').count(), 8);
  assert.ok(await page.locator("#ledger .strip-part").count() >= 4, "the state ledger shows no work states");
  step(`${kind}: Now states the project position, the kickoff checklist and the attention list`);

  // The checklist must not push the project position, the blocked work and the attention list below the first screen.
  const checklist = await page.locator("#main .card.kickoff").boundingBox();
  assert.ok(checklist.height <= 360, `${kind}: the kickoff card is ${Math.round(checklist.height)} pixels high and fills the first screen`);
  const firstEntry = await page.locator('#main [data-key^="now-attention-"]').first().boundingBox();
  assert.ok(firstEntry.y < 900, `${kind}: the attention list starts at ${Math.round(firstEntry.y)} pixels, below the first screen`);
  assert.equal(await page.locator('#main [data-key^="now-attention-"] .item-action').count(), 8);
  assert.match(await text(page, "#main .strip-legend"), /\d+ in progress/);
  const nowText = await text(page, "#main");
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
  step(`${kind}: Learning shows the accepted guards, the recurrence and the failures without lessons`);

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

  await go(page, "#records");
  assert.ok(await page.locator("#main table.data").count() >= 1, "the records view lists no records");
  await go(page, "#requirements");
  assert.match(await text(page, "#main"), /The project requirements are not established yet\./);
  step(`${kind}: Records and Requirements render their current state`);

  for (const width of [1440, 768, 390, 320]) {
    for (const hash of ["#now", "#plan", "#work", "#architecture", "#decisions", "#learning", "#machine"]) {
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

  // Allowing the blocked path of the scope block.
  await go(page, "#now");
  await page.getByRole("button", { name: "Allow paths" }).first().click();
  await page.waitForSelector("#form-dialog[open]");
  assert.equal(await field(page, "paths").inputValue(), "docs/brief.md");
  await field(page, "reason").fill("The work item needs the brief.");
  await page.locator("#form-save").click();
  await savedToast(page, /docs\/brief\.md/);
  step("the blocked path of a scope block is allowed from the Now view");

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
    console.log(`\n${results.length} checks passed.`);
  } finally {
    await browser.close();
    stopServers();
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exit(1); });
