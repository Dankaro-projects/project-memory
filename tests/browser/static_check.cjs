// Syntax and content security policy check for the control panel. No browser and no fixture, so it runs on every platform.
// Usage: node tests/browser/static_check.cjs
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "../..");
const UI = path.join(ROOT, "memory_module", "ui");
const PYTHON = process.env.MEMORY_PYTHON || "python";
const results = [];
const step = (name) => { results.push(name); console.log("ok  " + name); };

// The panel builds every element through Panel.h. These patterns match the use of a forbidden
// interface, not the words themselves, so the rule sentence in the core.js header stays readable.
const FORBIDDEN = [
  [/\.innerHTML\b/, "innerHTML"],
  [/\.outerHTML\b/, "outerHTML"],
  [/\.insertAdjacentHTML\s*\(/, "insertAdjacentHTML"],
  [/\beval\s*\(/, "eval"],
  [/\bnew\s+Function\s*\(/, "new Function"],
  [/document\.write\s*\(/, "document.write"],
  [/<\/script/i, "a closing script tag"],
];
// viewer.py inserts each file into one script or style element, and the interface text follows the
// writing rules of the project, which use no dash as punctuation.
const DASHES = /[–—]/;

const PY = `
import json
import sys
from memory_module import viewer

destination = sys.argv[1]
template = viewer.html_template()
data = {'live': False, 'project': 'Static check', 'exported_at': '2026-01-01T00:00:00+00:00',
        'scope': {}, 'responses': {}, 'omitted': []}
with open(destination + '/export.html', 'w', encoding='utf-8') as stream:
    stream.write(viewer.render(template, data))
with open(destination + '/live.html', 'w', encoding='utf-8') as stream:
    stream.write(viewer.render(template, dict(data, live=True), live=True))
print(json.dumps({'scripts': list(viewer.UI_SCRIPTS), 'styles': list(viewer.UI_STYLES), 'vendor': viewer.VENDOR_SCRIPT}))
`;

const policyOf = (page) => {
  const found = page.match(/<meta http-equiv="Content-Security-Policy" content="([^"]*)"/);
  assert.ok(found, "the page carries no content security policy");
  return found[1];
};
// viewer.render hashes every script element without attributes. The data element carries
// attributes and is JSON, so the same rule keeps it out of the policy.
const bodies = (page, tag) => {
  const pattern = new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, "g");
  return [...page.matchAll(pattern)].map((match) => match[1]);
};
const digest = (body) => "'sha256-" + crypto.createHash("sha256").update(body, "utf8").digest("base64") + "'";

(() => {
  // Every file the packaged viewer names must exist, otherwise the panel ships without its views.
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "panel-static-"));
  const declared = JSON.parse(execFileSync(PYTHON, ["-c", PY, temp], { cwd: ROOT, encoding: "utf8" }).trim());
  for (const name of [...declared.scripts, ...declared.styles]) {
    assert.ok(fs.existsSync(path.join(UI, name)), `memory_module/viewer.py names a file that does not exist: ui/${name}`);
  }
  assert.ok(fs.existsSync(path.join(ROOT, "memory_module", declared.vendor)), "the vendor script is missing");
  step(`viewer.py names ${declared.scripts.length} browser scripts and each one exists`);

  // Each file parses on its own, and the joined script parses as the browser receives it.
  const sources = declared.scripts.map((name) => fs.readFileSync(path.join(UI, name), "utf8"));
  declared.scripts.forEach((name, index) => {
    try {
      new vm.Script(sources[index], { filename: name });
    } catch (error) {
      assert.fail(`ui/${name} does not parse: ${error.message}`);
    }
  });
  new vm.Script(sources.join("\n"), { filename: "panel bundle" });
  step("every browser script parses on its own and as one joined script");

  for (const [index, name] of declared.scripts.entries()) {
    for (const [pattern, label] of FORBIDDEN) {
      assert.ok(!pattern.test(sources[index]), `ui/${name} uses ${label}, which the panel rules forbid`);
    }
    assert.ok(!DASHES.test(sources[index]), `ui/${name} contains an em dash or an en dash`);
  }
  for (const name of declared.styles) {
    const style = fs.readFileSync(path.join(UI, name), "utf8");
    assert.ok(!/<\/style/i.test(style), `ui/${name} contains a closing style tag`);
    assert.ok(!DASHES.test(style), `ui/${name} contains an em dash or an en dash`);
  }
  step("no browser file uses innerHTML, outerHTML, insertAdjacentHTML, eval, new Function or document.write");

  // The policy names the hash of every executable script and of the stylesheet.
  const page = fs.readFileSync(path.join(temp, "export.html"), "utf8");
  const policy = policyOf(page);
  const scripts = bodies(page, "script");
  assert.equal(scripts.length, 2, "the page should carry the vendor script and the application script");
  for (const body of scripts) {
    assert.ok(policy.includes(digest(body)), "a script element is not named in the content security policy");
  }
  const styles = bodies(page, "style");
  assert.equal(styles.length, 1);
  assert.ok(policy.includes(digest(styles[0])), "the stylesheet is not named in the content security policy");
  assert.ok(/script-src 'sha256-[^']+' 'sha256-[^']+'/.test(policy), "script-src should name exactly the two script hashes");
  step("the policy carries the hash of both scripts and of the stylesheet");

  assert.ok(policy.startsWith("default-src 'none'"), "the policy should deny every source by default");
  for (const value of ["'unsafe-inline'", "'unsafe-eval'", "*", "http:", "https:"]) {
    assert.ok(!policy.includes(value), `the policy should not allow ${value}`);
  }
  assert.ok(policy.includes("base-uri 'none'") && policy.includes("form-action 'none'"), "the policy should deny a base and a form action");
  assert.ok(policy.includes("font-src data:"), "the embedded fonts need font-src data:");
  assert.ok(!policy.includes("connect-src"), "an exported snapshot must not be allowed to reach the network");
  step("the exported policy denies every source except the hashed script, style and embedded fonts");

  const livePolicy = policyOf(fs.readFileSync(path.join(temp, "live.html"), "utf8"));
  assert.ok(livePolicy.includes("connect-src 'self'"), "live mode needs connect-src 'self'");
  assert.ok(!livePolicy.includes("'unsafe-inline'"));
  step("live mode adds connect-src 'self' and nothing else");

  // The data element is JSON and carries no executable script.
  const data = page.split('<script id="memory-data" type="application/json">')[1].split("</script>")[0];
  JSON.parse(data);
  assert.ok(!/[<>]/.test(data), "the embedded data must escape the angle brackets that could close its element");
  step("the embedded data is JSON with escaped angle brackets");

  fs.rmSync(temp, { recursive: true, force: true });
  console.log(`\n${results.length} checks passed.`);
})();
