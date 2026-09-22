// Browser checks for the offline snapshot: one local file, no network, no edit controls, inert payloads.
// Usage: node tests/browser/export_browser.cjs
// MEMORY_PLAYWRIGHT selects an existing Playwright installation and MEMORY_PYTHON selects the interpreter.
const { chromium } = require(process.env.MEMORY_PLAYWRIGHT || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "../..");
const PYTHON = process.env.MEMORY_PYTHON || "python";
const VIEWS = ["now", "plan", "work", "architecture", "dependencies", "decisions", "learning", "agents", "records", "requirements"];
const KINDS = {
  product: { architecture: "Components and packages", term: "Components" },
  engagement: { architecture: "Stakeholders and workstreams", term: "Stakeholders and workstreams" },
  automation: { architecture: "Systems and workflows", term: "Systems and workflows" },
};
const results = [];
const step = (name) => { results.push(name); console.log("ok  " + name); };

function snapshot(kind, directory) {
  const output = path.join(directory, kind);
  const file = path.join(directory, kind + ".html");
  const printed = execFileSync(PYTHON, [path.join(ROOT, "tests/browser/fixture.py"), "--kind", kind, "--output", output, "--export", file],
    { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(printed);
}
function watch(page) {
  const problems = [];
  page.on("pageerror", (error) => problems.push("page error: " + error.message));
  page.on("console", (message) => { if (message.type() === "error") problems.push("console: " + message.text()); });
  return problems;
}
const settle = (page) => page.waitForFunction(() => !document.querySelector("#main .view.pending, .detail-content.pending"));
async function go(page, hash) {
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
const pane = (page) => page.locator("#detail-body .detail-content:not(.pending)");
const loaded = (page) => page.waitForFunction(() => !document.getElementById("detail").hidden && document.getElementById("detail-title").textContent !== "Loading" && !document.querySelector(".detail-content.pending"));

(async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "export-browser-"));
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  try {
    for (const [kind, expected] of Object.entries(KINDS)) {
      const fixture = snapshot(kind, directory);
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      const problems = watch(page);
      const requests = [];
      page.on("request", (request) => { if (!request.url().startsWith("file:") && !request.url().startsWith("data:")) requests.push(request.url()); });
      await page.goto(pathToFileURL(fixture.snapshot).href);
      await page.waitForFunction(() => /^Snapshot from /.test(document.getElementById("live-status").textContent), null, { timeout: 20000 });
      assert.equal(await page.evaluate(() => Panel.canEdit()), false);
      assert.equal(await page.locator("#template-notice").isVisible(), false);
      assert.equal(await page.evaluate(() => Panel.term("components")), expected.term);
      step(`${kind}: the snapshot opens read only with its own labels and the export time`);

      // Every view renders from the embedded responses alone. An unscoped snapshot holds every
      // project view, so no view may fall back to the load failure that Panel.errorState renders.
      for (const name of VIEWS) {
        await go(page, "#" + name);
        const body = await text(page, "#main");
        assert.ok(body.length > 40, `${kind}: the ${name} view is empty`);
        assert.ok(!body.includes("This content could not be loaded."), `${kind}: the ${name} view failed to load`);
      }
      assert.match(await text(page, "#main"), /requirements/i);
      await go(page, "#now");
      assert.match(await text(page, "#view-summary"), /^\d+ items wait for you\.$/);
      await go(page, "#plan");
      assert.match(await text(page, "#view-summary"), /16 work items are planned in 7 phases\./);
      assert.equal(await page.locator("#main .list-view .tree").count(), 1, `${kind}: the plan tree is not in the frame`);
      await go(page, "#work/tab=list");
      assert.equal(await page.locator("#main .pane-row").count(), 16, `${kind}: the snapshot does not list the work items as rows`);
      await go(page, "#architecture");
      assert.equal(await text(page, "#main .view-head h2"), expected.architecture);
      await page.waitForSelector("#main .graph canvas");
      await go(page, "#dependencies");
      assert.match(await text(page, "#view-summary"), /8 dependencies connect 10 work items\./);
      await go(page, "#decisions");
      assert.equal(await page.locator('#main [data-key^="decision-event_"]').count(), 3);
      step(`${kind}: all ten views render from the embedded responses`);

      await go(page, "#agents");
      assert.ok(!(await text(page, "#main")).includes("Not applicable"), `${kind}: the snapshot runs table prints Not applicable`);
      // Every kind has its tab with the true count and its rows from the embedded response of the Now view.
      await go(page, "#now");
      const tabs = await page.evaluate(async () => {
        const kinds = (await Panel.get("now")).attention_kinds;
        return { expected: kinds.map((entry) => [entry.type, entry.count]), rows: kinds.reduce((sum, entry) => sum + entry.entries.length, 0),
          shown: [...document.querySelectorAll(".view-tabs button .chip")].slice(0, kinds.length).map((node) => [node.parentNode.dataset.key.replace("now-kind-", ""), Number(node.textContent)]) };
      });
      assert.deepEqual(tabs.shown, tabs.expected);
      assert.equal(tabs.rows, 9);
      await page.click('[data-key="now-kind-guard_recurrence"]');
      await page.waitForSelector('#main .view:not(.pending) [data-key="now-kind-guard_recurrence"][aria-pressed="true"]');
      assert.match(await text(page, "#main .pane-row"), /occurred once after the lesson was accepted/);
      // A lesson opens in the pane of a snapshot without a decision, because a snapshot is read only.
      await page.click('[data-key="now-kind-lessons_to_accept"]');
      await page.waitForSelector('#main .view:not(.pending) [data-key="now-kind-lessons_to_accept"][aria-pressed="true"]');
      await page.locator("#main .pane-row").first().click();
      await page.waitForFunction(() => document.getElementById("detail-kind").textContent === "Decide a lesson" && !document.querySelector(".detail-content.pending"));
      assert.equal(await page.locator("#detail .detail-foot button, #detail .detail-foot textarea").count(), 0);
      await page.evaluate(() => Panel.closePane());
      step(`${kind}: the snapshot shows every kind of Now with its true count and its rows, and offers no decision`);

      // No action is offered and none can be opened.
      for (const name of VIEWS) {
        await go(page, "#" + name);
        const controls = await page.locator('#main [data-key^="form:"], #main #arch-add, #main #review-requirements, ' +
          '#main [data-key^="accept-"], #main [data-key^="retire-"], #main [data-key^="merge-"], #main [data-key^="plan-add-"]').count();
        assert.equal(controls, 0, `${kind}: the ${name} view offers an edit control in a snapshot`);
      }
      assert.equal(await page.evaluate(() => Panel.openForm("plan", {})), null);
      assert.equal(await page.locator("#form-dialog").evaluate((node) => node.open), false);
      step(`${kind}: no view offers an edit control and no form can be opened`);

      // A snapshot holds no sessions response, so the Sessions view states that instead of failing, and Learning keeps its tabs.
      await go(page, "#sessions");
      assert.match(await text(page, "#main .notice"), /This content is not included in this snapshot\./);
      await go(page, "#learning/tab=instructions");
      assert.equal(await page.locator('#main [data-key^="instructions-row-"]').count(), 3);
      await page.locator('[data-key="instructions-row-worker"]').click();
      await page.waitForFunction(() => document.querySelector('#detail [data-key="instructions-worker"]') && !document.querySelector(".detail-content.pending"));
      assert.equal(await page.locator("#detail .detail-foot button").count(), 0);
      await page.evaluate(() => Panel.closePane());
      step(`${kind}: the snapshot states that Sessions is not included, and the instructions of a role open without an edit action`);

      // A record that the snapshot does not hold says so instead of failing.
      await page.evaluate(() => Panel.openRecord("event_missing_from_this_snapshot"));
      await page.waitForFunction(() => /not included in this snapshot/.test(document.getElementById("detail-body").textContent));
      await page.evaluate(() => Panel.closePane());
      step(`${kind}: a record outside the snapshot states that it is not included`);

      // The document keeps its structure, and the payloads inside it stay inert.
      await page.evaluate((id) => Panel.openRecord(id), fixture.ids.document);
      await page.waitForFunction(() => /Outline/.test((document.querySelector("#detail-body .detail-content:not(.pending)") || {}).textContent || ""));
      assert.equal(await pane(page).locator(".document table tbody tr").count(), 2);
      assert.equal(await pane(page).locator('a[href^="javascript:"]').count(), 0);
      assert.equal(await pane(page).locator("img").count(), 0);
      assert.equal(await pane(page).locator('a[href^="https:"]').first().getAttribute("rel"), "noopener noreferrer");
      assert.match(await pane(page).innerText(), /<img src=x onerror=/);
      await pane(page).locator('[data-key="source-toggle"]').click();
      assert.equal(await pane(page).locator("pre.source-text").count(), 1);
      await page.evaluate(() => Panel.closePane());
      step(`${kind}: the document keeps its table and outline, and its raw HTML and javascript link stay inert`);

      // The note that carries a closing script tag must not have run.
      await page.evaluate((id) => Panel.openRecord(id), fixture.ids.review);
      await page.waitForFunction(() => document.getElementById("detail-title").textContent !== "Loading");
      assert.equal(await page.evaluate(() => window.injected), undefined);
      await page.evaluate(() => Panel.closePane());
      step(`${kind}: the injected script payload did not run`);

      // Records and Requirements in the frame of a snapshot: no page scroll at 1600 by 640 and 390 by 800, the version of
      // the requirements inside the frame, the Kind filter applying on change, and a record in the pane at full width.
      for (const [width, height] of [[1600, 640], [390, 800]]) {
        await page.setViewportSize({ width, height });
        await go(page, "#records");
        await page.waitForSelector("#main .pane-row");
        await fixedFrame(page, `${kind} #records at ${width} by ${height}`);
        await go(page, "#requirements");
        await fixedFrame(page, `${kind} #requirements at ${width} by ${height}`);
        const version = await page.evaluate(() => { const main = document.getElementById("main").getBoundingClientRect(), term = [...document.querySelectorAll('#main [data-scroll="requirements"] dt')].find((node) => node.textContent === "Version");
          const box = term.nextElementSibling.getBoundingClientRect();
          return { text: term.nextElementSibling.textContent, inside: box.top >= main.top && box.bottom <= main.bottom && box.left >= main.left && box.right <= main.right }; });
        assert.ok(/^\d+$/.test(version.text) && version.inside, `${kind} #requirements at ${width} by ${height}: the version is not inside the frame: ${JSON.stringify(version)}`);
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      await go(page, "#records");
      const allKinds = await page.locator("#main .pane-row").count();
      await page.locator("#records-view").selectOption("decisions");
      await page.waitForFunction(() => /in decisions match/.test(document.getElementById("view-summary").textContent));
      const decisions = await page.locator("#main .pane-row").count();
      assert.ok(decisions > 0 && decisions < allKinds && (await page.locator("#main .kn-group").count()) === 0, `${kind}: the Kind filter of the snapshot did not change the rows: ${allKinds} before, ${decisions} after`);
      assert.match(page.url(), /view=decisions/);
      await page.locator("#main .pane-row").first().click();
      await loaded(page);
      const narrow = await page.evaluate(() => document.getElementById("detail").getBoundingClientRect().width);
      await page.locator("#detail-wide").click();
      const wide = await page.evaluate((before) => ({ wider: document.getElementById("detail").getBoundingClientRect().width > before + 300, view: getComputedStyle(document.getElementById("main")).visibility,
        label: document.getElementById("detail-wide").textContent }), narrow);
      assert.deepEqual(wide, { wider: true, view: "hidden", label: "Show the list" }, `${kind}: the record pane at full width: ${JSON.stringify(wide)}`);
      await fixedFrame(page, `${kind} the record pane at full width`);
      await page.locator("#detail-wide").click();
      assert.deepEqual(await page.evaluate(() => [getComputedStyle(document.getElementById("main")).visibility, document.getElementById("detail-wide").textContent]), ["visible", "Full width"]);
      await page.evaluate(() => Panel.closePane());
      step(`${kind}: Records and Requirements scroll no page at 1600 by 640 and 390 by 800, the version reads inside the frame, the Kind filter applies on change, and a record reads at full width`);

      for (const width of [1440, 768, 390, 320]) {
        for (const hash of ["#now", "#work", "#architecture", "#records"]) {
          await go(page, hash);
          await noOverflow(page, width, `${kind} ${hash}`);
        }
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      step(`${kind}: no page overflow at 1440, 768, 390 and 320 pixels`);

      assert.deepEqual(requests, [], `${kind}: the snapshot made a network request`);
      assert.deepEqual(problems, [], `${kind}: the snapshot logged console or page errors`);
      step(`${kind}: the snapshot made no network request and logged no error`);
      await page.close();
    }

    // A snapshot scoped by subject leaves Now out, so it opens on the first view it holds and marks the others in the rail.
    const whole = snapshot("product", path.join(directory, "scoped"));
    const scopedFile = path.join(directory, "scoped.html");
    execFileSync(PYTHON, ["-m", "memory_module.cli", "view", "--db", whole.database, "--project", whole.project, "--subject", "code", "--no-open", "--output", scopedFile],
      { cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
    const scoped = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await scoped.goto(pathToFileURL(scopedFile).href);
    await scoped.waitForFunction(() => document.getElementById("view-title").textContent === "Work" && !document.querySelector("#main .view.pending"));
    assert.ok(await scoped.locator("#main .board-column").count() >= 1, "the scoped snapshot opens on a view without content");
    assert.equal(await scoped.locator('.nav-link[data-nav="now"]').getAttribute("aria-label"), "Now, not included in this snapshot");
    assert.equal(await scoped.locator('.nav-link[data-nav="work"]').getAttribute("aria-label"), null);
    await scoped.close();
    step("a snapshot scoped by subject opens on Work and marks the views it leaves out");

    // The unrendered template explains how to open the panel instead of showing an empty page.
    const template = await browser.newPage();
    await template.goto(pathToFileURL(path.join(ROOT, "memory_module", "viewer.html")).href);
    assert.equal(await template.locator("#template-notice").isVisible(), true);
    assert.equal(await template.locator("#app").isVisible(), false);
    step("the unrendered template shows the launch instructions and no panel");
    console.log(`\n${results.length} checks passed.`);
  } finally {
    await browser.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exit(1); });
