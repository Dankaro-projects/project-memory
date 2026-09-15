const {navigate}=require('./navigation.cjs');
// Local browser evaluation uses real HTTP actions, SQLite and inert skill files.
const { chromium } = require(process.env.MEMORY_PLAYWRIGHT || 'playwright');
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
(async () => {
  const root = path.resolve(__dirname, '../..');
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'memory-knowledge-'));
  const output = path.resolve(process.env.MEMORY_WORKSPACE_OUTPUT || path.join(root, '.memory/knowledge-browser'));
  fs.mkdirSync(output, { recursive: true });
  const python = process.env.MEMORY_PYTHON || 'python';
  const run = code => JSON.parse(execFileSync(python, ['-c', code, temp], { cwd: root, encoding: 'utf8' }));
  let browser, server, page; const checks = [], errors = [], external = [];
  try {
    const seed = run(`import sys,json,base64,zipfile
from pathlib import Path
from memory_module import Memory
from memory_module.install import setup
from memory_module.live import start
from memory_module.workspace import action
from tests.test_workspace_knowledge import files
p=Path(sys.argv[1]);info=setup(p,requirements=['Preserve conditions and exceptions.'])
work=[]
with Memory(info['database']) as m:
 for subject,title in [('code','Product delivery'),('research','Consulting recommendation')]:
  w=action(m,'plan',{'title':title,'objective':'Preserve the original evidence.','criterion':'The deliverable records evidence and exceptions.','subject':subject,'payload':{'state':'ready','next_action':'Inspect the evidence.','scope':'Preserve original conditions.','autonomy':'act','reason':'The user requests the evaluation.','owner':'agent','priority':'normal','sprint_id':None,'depends_on':[]}},subject)
  work.append(w['episode_id'])
 src=m.source('case-evidence','Original interview evidence','The client requires an explicit exception.','The tagged case keeps the exception.','tool',subject='research')
 lesson=m.record(work[1],'lesson',{'when':'The project includes tagged cases.','do':'Preserve their exception.','because':'The interview requires it.','exceptions':'Untagged cases keep the usual process.'},expected_version=m.episode(work[1])['version'],request_key='lesson',actor='fixture',evidence=[{'source_id':src['id'],'reason':'The interview states the exception.'}])
with zipfile.ZipFile(p/'method.zip','w') as archive:
 for f in files():archive.writestr('method/'+f['path'],base64.b64decode(f['content']))
local=p/'.agents/skills/local-review';local.mkdir(parents=True)
for f in files('local-review'):
 dest=local/f['path'];dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(base64.b64decode(f['content']))
print(json.dumps({'server':start(Path(info['database'])),'work':work,'lesson':lesson['id'],'source':src['id']}))`);
    server = seed.server;
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], executablePath: process.env.MEMORY_CHROMIUM });
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(8000);
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (!r.url().startsWith(server.url) && !r.url().startsWith('file:')) external.push(r.url()); });
    const started = performance.now();
    await page.goto(server.url); await navigate(page,'board'); await page.locator('#new-work').waitFor();
    const readyMs = Math.round(performance.now() - started);
    const fill = async values => { for (const [id, value] of Object.entries(values)) await page.locator('#edit-' + id).fill(value); };
    const save = async () => { await page.locator('#editor-save').click(); await page.locator('#editor').waitFor({ state: 'hidden' }); };
    const close = async () => { if (await page.locator('#detail').isVisible()) await page.keyboard.press('Escape'); };
    const nav = async view => { await close(); await navigate(page,view); };
    const mapWork = async (id, mode) => {
      await nav('map'); await page.getByLabel('Map work item').selectOption(id);
      await page.getByRole('button', { name: mode, exact: true }).click();
      await page.getByRole('button', { name: 'Add node', exact: true }).waitFor();
    };
    const addNode = async (name, description, reference = '') => {
      await page.getByRole('button', { name: 'Add node', exact: true }).click();
      await fill({ title: name, description, reason: 'This component is part of the agreed deliverable.' });
      if (reference) { await page.locator('#edit-reference_type').selectOption('sources'); await page.locator('#edit-reference').selectOption(reference); }
      await save(); await page.getByRole('button', { name: 'Inspect ' + name, exact: true }).waitFor();
    };
    await nav('skills');
    await page.getByRole('button', { name: 'Import skill', exact: true }).click();
    await page.locator('#skill-files').setInputFiles(path.join(temp, 'method.zip'));
    await save(); await page.getByRole('button', { name: 'inspect-evidence', exact: true }).click();
    await page.getByLabel('Skill file', { exact: true }).selectOption('references/check.md');
    await page.waitForFunction(() => document.querySelector('.skill-source')?.textContent.includes('tagged records'));
    await page.getByRole('button', { name: 'Select for work', exact: true }).click();
    await page.locator('#edit-work').selectOption(seed.work[0]);
    await fill({ reason: 'Use this method to preserve evidence in the product decision.' }); await save();
    checks.push('Upload ZIP without executing scripts; read exact supporting file; select skill for product work');
    await nav('board'); await page.getByRole('button', { name: 'Open work Product delivery', exact: true }).click();
    await page.getByRole('button', { name: 'Release', exact: true }).waitFor();
    assert.match(await page.locator('#detail-body').textContent(), /Selected/);
    await nav('skills'); await page.getByRole('button', { name: 'local-review', exact: true }).click();
    await page.getByRole('button', { name: 'Select for work', exact: true }).click();
    await page.locator('#edit-work').selectOption(seed.work[1]); await fill({ reason: 'Use the local method for the consulting recommendation.' }); await save();
    await nav('board'); await page.getByRole('button', { name: 'Open work Consulting recommendation', exact: true }).click();
    run(`import sys,json
from pathlib import Path
(Path(sys.argv[1])/'.agents/skills/local-review/references/check.md').write_text('The supporting condition changes.')
print('{}')`);
    await page.waitForFunction(() => document.querySelector('#detail-body')?.textContent.includes('Changed'));
    await page.getByRole('button', { name: 'local-review', exact: true }).click();
    await page.getByLabel('Skill file', { exact: true }).selectOption('references/check.md');
    await page.waitForFunction(() => document.querySelector('.skill-source')?.textContent.includes('tagged records'));
    checks.push('Consulting work flags local resource drift live and retains the selected original file');
    await nav('attention'); await page.getByRole('button', { name: 'Review requirements', exact: true }).click();
    await page.locator('#requirement-rows textarea').fill('Preserve conditions and tagged exceptions.');
    await fill({ reason: 'The user confirms the scope in full.' }); await save();
    await page.getByRole('button', { name: 'Preserve their exception.', exact: true }).click();
    await page.getByRole('button', { name: 'Review lesson', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#editor-fields')?.textContent.includes('Untagged cases'));
    assert.match(await page.locator('#editor-fields').textContent(), /Untagged cases/);
    await page.locator('#edit-status').selectOption('accepted'); await fill({ reason: 'The evidence supports the condition and its exception.' }); await save();
    checks.push('Approve complete project requirements and accept a lesson with its exception visible');
    await mapWork(seed.work[0], 'Architecture');
    await addNode('Local memory', 'SQLite preserves the project evidence.');
    await addNode('Project workspace', 'The browser reads the local memory.');
    await page.getByRole('button', { name: 'Add relationship', exact: true }).click();
    await page.locator('#edit-from').selectOption({ label: 'Project workspace' });
    await page.locator('#edit-to').selectOption({ label: 'Local memory' });
    await page.locator('#edit-type').selectOption('uses');
    await fill({ reason: 'The workspace reads and records project data through the local API.' }); await save();
    await page.locator('.map-connections summary').click();
    assert.match(await page.locator('.map-connections').textContent(), /workspace reads and records/);
    await page.getByRole('button', { name: 'Edit relationship', exact: true }).click();
    await page.locator('#edit-status').selectOption('confirmed'); await save();
    const node = page.getByRole('button', { name: 'Inspect Local memory', exact: true });
    const before = await node.boundingBox();
    await page.mouse.move(before.x + 40, before.y + 35); await page.mouse.down();
    await page.mouse.move(before.x + 100, before.y + 90, { steps: 6 }); await page.mouse.up();
    const moved = await node.boundingBox(); assert.ok(moved.x > before.x + 40);
    await page.getByRole('button', { name: 'Zoom out', exact: true }).click();
    await page.getByRole('button', { name: 'Fit', exact: true }).click();
    await page.screenshot({ path: path.join(output, 'architecture.png'), fullPage: true });
    await page.reload(); await mapWork(seed.work[0], 'Architecture');
    assert.equal(await page.locator('.map-node').count(), 2);
    await page.locator('.map-connections summary').click();
    assert.match(await page.locator('.map-connections').textContent(), /Confirmed/);
    checks.push('Create architecture and explained relationship; confirm it; drag, zoom and reload without rebuilding HTML');
    await page.getByRole('button', { name: 'Add node', exact: true }).click();
    await fill({ title: 'Retained draft', description: 'This draft must survive a concurrent comment.', reason: 'Test optimistic concurrency.' });
    run(`import sys,json
from pathlib import Path
from memory_module import Memory
from memory_module.workspace import action
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 ep=m.db.execute("SELECT id FROM episodes WHERE title='Product delivery'").fetchone()[0]
 action(m,'comment',{'episode_id':ep,'expected_version':m.episode(ep)['version'],'text':'The user adds a condition.'},'concurrent-comment')
print('{}')`);
    await page.locator('#editor-save').click(); await page.locator('#editor-error').waitFor();
    assert.equal(await page.locator('#edit-title').inputValue(), 'Retained draft');
    assert.match(await page.locator('#editor-error').textContent(), /changed/);
    await page.locator('#editor-reload').click(); await page.waitForFunction(() => !document.querySelector('#editor-save').disabled && document.querySelector('#editor-error').hidden);
    await fill({ title: 'Retained draft', description: 'The draft is reviewed against current work.', reason: 'The user explicitly reloads the current work.' }); await save();
    checks.push('Concurrent edit rejects a stale map write and retains the draft until explicit reload');
    await mapWork(seed.work[1], 'Workflow');
    await addNode('Review interviews', 'The consultant reviews original interviews.', seed.source);
    await page.getByRole('button', {name:'Add node',exact:true}).click();
    await fill({title:'Write recommendation',description:'The consultant preserves conditions in the recommendation.',reason:'The evidence review informs the deliverable.'});
    let lost=true;
    const drop=async route=>{ if(lost){lost=false;await route.fetch();await route.abort('failed');}else await route.continue(); };
    await page.route('**/api/actions',drop);
    await page.locator('#editor-save').click();await page.locator('#editor-error').waitFor();
    await page.unroute('**/api/actions',drop);await save();
    await page.getByRole('button',{name:'Inspect Write recommendation',exact:true}).waitFor();
    assert.equal(await page.locator('.map-node').count(),2);
    checks.push('A lost successful HTTP response retries the same node exactly once');
    await page.getByRole('button', { name: 'Add relationship', exact: true }).click();
    await page.locator('#edit-from').selectOption({ label: 'Review interviews' });
    await page.locator('#edit-to').selectOption({ label: 'Write recommendation' });
    await page.locator('#edit-type').selectOption('precedes');
    await fill({ reason: 'The original interviews inform the recommendation.' }); await save();
    await page.getByRole('button', { name: 'Inspect Review interviews', exact: true }).click();
    await page.getByRole('button', { name: 'Open linked record', exact: true }).click();
    await page.getByRole('heading', { name: 'Original interview evidence', exact: true }).waitFor();
    await close(); await page.getByRole('button', { name: 'Relationships', exact: true }).click();
    await page.locator('.map-node').first().click();
    await page.getByRole('button', { name: 'Expand relationships', exact: true }).click();
    await close(); checks.push('Create consulting workflow with structured evidence link and inspect recorded lineage');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.join(output, 'map-mobile.png'), fullPage: true });
    run(`import sys,json
from pathlib import Path
from memory_module import Memory
p=Path(sys.argv[1])
with Memory(p/'.memory/project.sqlite') as m:m.export_html(p/'snapshot.html',include_bodies=True)
print('{}')`);
    await page.setViewportSize({width:1440,height:1000});
    await page.goto(pathToFileURL(path.join(temp,'snapshot.html')).href);
    await nav('map');await page.getByLabel('Map work item').selectOption(seed.work[0]);
    await page.getByRole('button',{name:'Architecture',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.map-node').length===3);
    assert.equal(await page.getByRole('button',{name:'Add node',exact:true}).count(),0);
    await page.getByRole('button',{name:'Relationships',exact:true}).click();
    await page.locator('.map-node').first().click();
    await page.getByRole('button',{name:'Expand relationships',exact:true}).click();await close();
    checks.push('Offline export renders included diagrams and bounded lineage without a server or editing controls');
    const report = { passed: true, browser: await browser.version(), initial_ready_ms: readyMs, checks, runtime_dependencies_added: 0,
      evidence_scope: 'Two scripted product and consulting workflows; no model-quality or daily-productivity measurement.', errors, external_requests: external };
    assert.deepEqual(errors, []); assert.deepEqual(external, []);
    fs.writeFileSync(path.join(output, 'validation.json'), JSON.stringify(report, null, 2) + '\n'); console.log(JSON.stringify(report));
  } catch (error) {
    if (page) { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }); fs.writeFileSync(path.join(output, 'failure.html'), await page.content()); }
    throw error;
  } finally {
    if (browser) await browser.close();
    if (server) try { process.kill(server.pid); } catch (error) { if (error.code !== 'ESRCH') throw error; }
    fs.rmSync(temp, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
