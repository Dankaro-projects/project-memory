// Development-only browser checks against a real HTTP server and SQLite database.
const {chromium}=require(process.env.MEMORY_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'memory-workspace-'));
 const output=process.env.MEMORY_WORKSPACE_OUTPUT||path.join(root,'results/workspace-browser');
 const python=process.env.MEMORY_PYTHON||'python';let browser,server;
 const run=code=>JSON.parse(execFileSync(python,['-c',code,temp],{cwd:root,encoding:'utf8'}));
 try{
  server=run(`import sys,json
from pathlib import Path
from memory_module.install import setup
from memory_module.live import start
p=Path(sys.argv[1]);info=setup(p,requirements=['Preserve conditions and exceptions.'])
print(json.dumps(start(Path(info['database']))))`);
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],executablePath:process.env.MEMORY_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(server.url))external.push(r.url());});
  const opened=performance.now();await page.goto(server.url);await page.locator('#new-work').waitFor();
  const readyMs=Math.round(performance.now()-opened);
  assert.equal(await page.locator('#template-notice').isVisible(),false);
  await page.locator('#about-open').click();
  await page.getByText('Using this workspace',{exact:true}).click();
  assert.match(await page.locator('#workspace-help').textContent(),/updates automatically while open/);
  await page.locator('#about-close').click();
  await page.locator('#new-sprint').click();
  for(const [id,value] of Object.entries({title:'Verify the contract',objective:'Complete the parser and its explanation.',criterion:'Both work items have supported outcomes.',reason:'The user groups related work.'}))await page.locator('#edit-'+id).fill(value);
  await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
  const sprint=await page.locator('#sprint option').last().getAttribute('value');
  await page.locator('#new-work').click();
  for(const [id,value] of Object.entries({title:'Preserve encoding paths',objective:'Keep both supported encodings.',criterion:'UTF-8 is strict and tagged Latin-1 succeeds.',scope:'Keep the tagged Latin-1 exception.',next_action:'Inspect the existing parser.',reason:'The user requests a bounded check.'}))await page.locator('#edit-'+id).fill(value);
  await page.locator('#edit-state').selectOption('ready');await page.locator('#edit-sprint_id').selectOption(sprint);
  await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Open work Preserve encoding paths'}).click();
  await page.getByRole('button',{name:'Edit plan',exact:true}).waitFor();
  assert.match(await page.locator('#detail-body').textContent(),/tagged Latin-1/);
  assert.equal(await page.locator('#detail').evaluate(e=>e.matches(':modal')),false);
  await page.locator('[data-view=decisions]').click();assert.equal(await page.locator('#detail').isVisible(),true);
  await page.locator('[data-view=board]').click();await page.getByRole('button',{name:'Open work Preserve encoding paths'}).waitFor();
  await page.keyboard.press('Escape');await page.locator('#detail').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.activeElement.getAttribute('aria-label')==='Open work Preserve encoding paths');
  await page.getByRole('button',{name:'Open work Preserve encoding paths'}).click();
  await page.getByRole('button',{name:'Add comment',exact:true}).click();
  await page.locator('#edit-text').fill('The exception remains part of the completion criterion.');
  await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Edit plan',exact:true}).click();
  await page.locator('#edit-state').selectOption('done');await page.locator('#editor-save').click();
  await page.locator('#editor-error').waitFor();assert.match(await page.locator('#editor-error').textContent(),/Done requires/);
  await page.locator('#edit-state').selectOption('ready');await page.locator('#edit-next_action').fill('Keep this unsaved draft.');
  run(`import sys,json
from pathlib import Path
from memory_module import Memory
from memory_module.workspace import action
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 ep=m.db.execute("SELECT id,version FROM episodes WHERE title='Preserve encoding paths'").fetchone()
 action(m,'comment',{'episode_id':ep['id'],'expected_version':ep['version'],'text':'Another session records new evidence.'},'concurrent-comment')
print('{}')`);
  await page.locator('#editor-save').click();await page.locator('#editor-reload').waitFor();
  assert.equal(await page.locator('#edit-next_action').inputValue(),'Keep this unsaved draft.');
  await page.locator('#editor-reload').click();await page.waitForFunction(()=>document.querySelector('#edit-next_action').value==='Inspect the existing parser.');
  await page.locator('#edit-priority').selectOption('high');await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.locator('#close').click();await page.locator('#sprint').selectOption(sprint);
  assert.equal(await page.locator('.work-card').count(),1);fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'workspace-desktop.png'),fullPage:true});
  run(`import sys,json
from pathlib import Path
from memory_module import Memory
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 m.source('long-source','Long document','A bounded source fixture.','# Long document\\n'+('text '*2700)+'\\n## Final condition\\nPreserve the exception.','document')
print('{}')`);
  await page.locator('[data-view=sources]').click();await page.getByRole('button',{name:'Open Long document',exact:true}).click();
  await page.getByRole('button',{name:'Read original text',exact:true}).click();
  assert.equal(await page.locator('[data-field=body] pre').textContent().then(s=>s.length),12000);
  await page.getByRole('button',{name:'Read more source text',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-field=body] pre')?.textContent.endsWith('Preserve the exception.'));
  await page.keyboard.press('Escape');await page.locator('[data-view=board]').click();
  run(`import sys,json
from pathlib import Path
from memory_module import Memory,codex_host
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 for event in [{'hook_event_name':'UserPromptSubmit','prompt':'Inspect the local fixture.'},{'hook_event_name':'PreToolUse','tool_name':'Read','tool_use_id':'lookup'},{'hook_event_name':'PostToolUse','tool_name':'Read','tool_use_id':'lookup','tool_response':{'exit_code':0}}]:
  codex_host.capture(m,{'session_id':'browser-fixture','turn_id':'1',**event})
print('{}')`);
  const recording=page.locator('#recording-status button');await recording.waitFor();await recording.click();
  await page.getByRole('heading',{name:'Recording checks',exact:true}).waitFor();
  assert.match(await page.locator('#detail-body').textContent(),/not been explicitly assessed/);
  run(`import sys,json
from pathlib import Path
from memory_module import Memory
from memory_module.mcp import write
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 p=m.db.execute("SELECT id FROM host_receipts WHERE session_id='browser-fixture' AND event_name='UserPromptSubmit'").fetchone()[0]
 write(m,'checkpoint','browser-assessment',{'prompt_ids':[p],'effect':'informational','reason':'The agent inspected a read-only fixture.'},session_id='browser-fixture')
print('{}')`);
  await page.locator('#recording-status').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.querySelector('#detail-body').textContent.includes('The observed activity has an explicit assessment.'));
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844});await page.locator('#new-work').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'workspace-editor-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const report={passed:true,browser:await browser.version(),initial_ready_ms:readyMs,checks:['create sprint','create action','structured sprint assignment','inspect intent and lineage','navigate while inspecting','Escape closes the inspector','restore focus to the current action','add comment','reject unsupported Done','preserve draft on concurrent edit','reload current version','save revised plan','sprint filtering','original text across source pagination','mobile form width','recording gaps appear without reload','inspect recording gaps','resolved recording gaps and open inspector update without reload','no external requests','no JavaScript errors']};
  fs.writeFileSync(path.join(output,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
 }finally{if(browser)await browser.close();if(server)try{process.kill(server.pid);}catch(error){if(error.code!=='ESRCH')throw error;}fs.rmSync(temp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
