const { navigate } = require('./navigation.cjs');
const { chromium } = require(process.env.MEMORY_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { execFileSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'memory-dependencies-'));
  const output = path.resolve(process.env.MEMORY_WORKSPACE_OUTPUT || path.join(root, '.memory/dependencies-browser'));
  fs.mkdirSync(output, { recursive: true });
  const run = code => JSON.parse(execFileSync(process.env.MEMORY_PYTHON || 'python', ['-c', code, temp], { cwd: root, encoding: 'utf8' }));
  let browser, server, page;
  const errors = [], external = [];
  try {
    server = run(`import sys,json
from pathlib import Path
from memory_module import Memory
from memory_module.install import setup
from memory_module.workspace import action
from memory_module.live import start
p=Path(sys.argv[1]);info=setup(p,requirements=['Preserve verified architecture.'])
(p/'package.json').write_text(json.dumps({'dependencies':{'react':'^19.0.0','react-dom':'^19.0.0','zod':'^4.0.0'},'devDependencies':{'vite':'^8.0.0','typescript':'^6.0.0'}}))
(p/'pyproject.toml').write_text('[build-system]\\nrequires=["hatchling>=1.27,<2"]\\n')
(p/'scripts').mkdir();(p/'scripts/requirements.txt').write_text('openpyxl==3.1.5\\n')
with Memory(info['database']) as m:
 (p/'architecture.md').write_text('# Architecture\\n\\n## Software dependencies\\n\\n| Dependency | Role |\\n|---|---|\\n| React / React DOM | Browser UI and rendering. |\\n\\n### Exception\\nOperator imports use Python.\\n\\n## Other scope\\nUnrelated content.\\n')
 m.document(str(p/'architecture.md'))
 w=action(m,'plan',{'title':'Data import architecture','objective':'Keep imported data available offline.','criterion':'The architecture explains its dependencies.','payload':{'state':'ready','scope':'Document current dependencies.','next_action':'Inspect the evidence.','autonomy':'act','reason':'The user requests the architecture.'}},'work')
 s=m.source('storage','Storage choice','Keep imports offline.','The user requires imported records to remain available offline.','tool')
 action(m,'map',{'episode_id':w['episode_id'],'expected_version':1,'mode':'architecture','map_version':0,'reason':'Explain the storage dependency.','nodes':[{'id':'node_app','title':'Importer','kind':'component','description':'Imports verified records.','reference':'','status':'confirmed'},{'id':'node_store','title':'Local storage','kind':'system','description':'Keeps imported records available offline.','reference':s['id'],'status':'proposed'}],'edges':[{'id':'edge_use','from':'node_app','to':'node_store','type':'uses','reason':'The importer reads saved records without a network connection.','status':'proposed'}]},'map')
 m.export_html(p/'snapshot.html',include_bodies=True)
print(json.dumps(start(info['database'])))`);
    browser = await chromium.launch({ headless: true, executablePath: process.env.MEMORY_CHROMIUM });
    page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    page.on('pageerror', e => errors.push(String(e)));
    page.on('request', r => { if (/^https?:/.test(r.url()) && !r.url().startsWith(server.url)) external.push(r.url()); });
    await page.goto(server.url);
    await navigate(page, 'dependencies');
    await page.locator('.dependency-table').waitFor();
    await page.getByLabel('Dependency documentation').selectOption({ label: 'architecture.md · Software dependencies' });
    assert.match(await page.locator('.dependency-documentation').innerText(), /Browser UI and rendering/);
    assert.match(await page.locator('.dependency-documentation').innerText(), /Operator imports use Python/);
    assert.doesNotMatch(await page.locator('.dependency-documentation').innerText(), /Unrelated content/);
    await page.getByRole('button', { name: 'Open original document', exact: true }).click();
    await page.getByRole('heading', { name: 'architecture.md', exact: true }).waitFor();
    await page.locator('#close').click();
    assert.equal(await page.locator('.dependency-table tbody tr').count(), 7);
    assert.match(await page.locator('.dependency-summary').innerText(), /7 declarations · 3 manifests/);
    await page.getByLabel('Dependency group').selectOption('Development');
    assert.equal(await page.locator('.dependency-table tbody tr').count(), 2);
    await page.getByLabel('Dependency group').selectOption('');
    await page.getByLabel('Dependency manifest').selectOption('scripts/requirements.txt');
    assert.equal(await page.locator('.dependency-table tbody tr').count(), 1);
    assert.match(await page.locator('.dependency-table').innerText(), /openpyxl/);
    await page.getByLabel('Dependency manifest').selectOption('');
    await page.getByLabel('Search dependencies').fill('react-dom');
    assert.equal(await page.locator('.dependency-table tbody tr').count(), 1);
    await page.getByLabel('Search dependencies').fill('');
    await page.getByRole('button', { name: 'Open evidence', exact: true }).click();
    await page.getByRole('heading', { name: 'Storage choice', exact: true }).waitFor();
    await page.locator('#close').click();
    await page.getByRole('button', { name: 'Open architecture', exact: true }).click();
    await page.getByRole('button', { name: 'Inspect Local storage', exact: true }).waitFor();
    await navigate(page, 'dependencies');
    await page.locator('.dependency-table').waitFor();
    await page.screenshot({ path: path.join(output, 'dependencies-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(output, 'dependencies-mobile.png'), fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1050 });
    const manifest = path.join(temp, 'package.json');
    const updated = JSON.parse(fs.readFileSync(manifest)); updated.dependencies.react = '^20.0.0';
    fs.writeFileSync(manifest, JSON.stringify(updated));
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.getByRole('cell', { name: '^20.0.0', exact: true }).waitFor();
    await page.goto(pathToFileURL(path.join(temp, 'snapshot.html')).href);
    await navigate(page, 'dependencies');
    await page.locator('.dependency-table').waitFor();
    assert.match(await page.locator('.dependency-documentation').innerText(), /Browser UI and rendering/);
    assert.equal(await page.getByRole('cell', { name: '^20.0.0', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Refresh', exact: true }).count(), 0);
    assert.match(await page.locator('.dependency-intro').innerText(), /Snapshot from/);
    assert.equal(await page.locator('.dependency-table tbody tr').count(), 7);
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    const report = { passed: true, checks: ['Manifest counts and groups', 'Search and structured manifest filters',
      'Recorded dependency roles and exceptions with original document access', 'Evidence and architecture navigation', 'Desktop and mobile layout', 'Live manifest refresh', 'Offline snapshot retains original versions and purpose documentation'], errors, external_requests: external };
    fs.writeFileSync(path.join(output, 'validation.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } catch (error) {
    if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true });
    throw error;
  } finally {
    if (browser) await browser.close();
    if (server) try { process.kill(server.pid); } catch (error) { if (error.code !== 'ESRCH') throw error; }
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
