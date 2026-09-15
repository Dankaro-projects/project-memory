// Development-only browser checks against a real HTTP server and SQLite database.
const {chromium}=require(process.env.MEMORY_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const {navigate}=require('./navigation.cjs');
(async()=>{
 const root=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'memory-workspace-'));
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
  page.setDefaultTimeout(10000);
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(server.url))external.push(r.url());});
  const opened=performance.now();await page.goto(server.url);await navigate(page,'board');await page.locator('#new-work').waitFor();
  const readyMs=Math.round(performance.now()-opened);
  assert.equal(await page.locator('#template-notice').isVisible(),false);
  await page.locator('#about-open').click();
  await page.getByText('Using this workspace',{exact:true}).click();
  assert.match(await page.locator('#workspace-help').textContent(),/updates automatically while open/);
  await page.locator('#about-close').click();
  await page.locator('#new-sprint').click();
  for(const [id,value] of Object.entries({title:'Verify the contract',objective:'Complete the parser and its explanation.',criterion:'Both work items have supported outcomes.',reason:'The user groups related work.'}))await page.locator('#edit-'+id).fill(value);
  await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.waitForFunction(()=>[...document.querySelector('#sprint').options].some(o=>o.textContent.includes('Verify the contract')));
  const sprint=await page.locator('#sprint option').last().getAttribute('value');
  await page.locator('#new-work').click();
  for(const [id,value] of Object.entries({title:'Preserve encoding paths',objective:'Keep both supported encodings.',criterion:'UTF-8 is strict and tagged Latin-1 succeeds.',scope:'Keep the tagged Latin-1 exception.',next_action:'Inspect the existing parser.',reason:'The user requests a bounded check.'}))await page.locator('#edit-'+id).fill(value);
  await page.locator('#edit-state').selectOption('ready');await page.locator('#edit-sprint_id').selectOption(sprint);
  await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'Open work Preserve encoding paths'}).click();
  await page.getByRole('button',{name:'Edit plan',exact:true}).waitFor();
  assert.match(await page.locator('#detail-body').textContent(),/tagged Latin-1/);
  assert.equal(await page.locator('#detail').evaluate(e=>e.matches(':modal')),false);
  await navigate(page,'decisions');assert.equal(await page.locator('#detail').isVisible(),true);
  await navigate(page,'board');await page.getByRole('button',{name:'Open work Preserve encoding paths'}).waitFor();
  await page.keyboard.press('Escape');await page.locator('#detail').waitFor({state:'hidden'});
  await page.waitForFunction(()=>document.activeElement.getAttribute('aria-label')==='Open work Preserve encoding paths');
  let releaseBoard,boardRequested;
  const boardHeld=new Promise(resolve=>{releaseBoard=resolve;}),requestSeen=new Promise(resolve=>{boardRequested=resolve;});
  const holdBoard=async route=>{if(!new URL(route.request().url()).searchParams.has('episode')){boardRequested();await boardHeld;}await route.continue();};
  await page.route('**/api/board?*',holdBoard);
  await page.locator('#show-empty').check();await requestSeen;
  const focusedCard=await page.getByRole('button',{name:'Open work Preserve encoding paths'}).elementHandle();
  await focusedCard.focus();releaseBoard();
  await page.waitForFunction(card=>!card.isConnected,focusedCard);
  assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Open work Preserve encoding paths');
  await page.unroute('**/api/board?*',holdBoard);
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
  await navigate(page,'sources');await page.getByRole('button',{name:'Open Long document',exact:true}).click();
  await page.getByRole('button',{name:'Read original text',exact:true}).click();
  assert.equal(await page.locator('[data-field=body] pre').textContent().then(s=>s.length),12000);
  await page.getByRole('button',{name:'Read more source text',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('[data-field=body] pre')?.textContent.endsWith('Preserve the exception.'));
  await page.keyboard.press('Escape');await navigate(page,'board');
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
  run(`import sys,json
from pathlib import Path
from memory_module import Memory,reviews
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 ep=m.db.execute("SELECT id FROM episodes WHERE title='Preserve encoding paths'").fetchone()[0]
 reviews.configure(m,Path(sys.argv[1]),'codex')
 check=reviews.request(m,ep,request_key='browser-timeout')
 snapshot=check['snapshot']
 report={'verdict':'pass','summary':'A retained fixture report claims success.','checks':[{'criterion':c['id'],'evidence':'The fixture preserves its exception.','result':'met'} for c in snapshot['checklist']], 'constraint_checks':[{'constraint':c['id'],'applicability':'applies','reason':'The scope requires this condition.','evidence':'Fixture evidence.','result':'met'} for c in snapshot['constraints']], 'findings':[], 'lesson_proposals':[]}
 metrics={'duration_ms':300000,'input_characters':5000,'provider_usage':None,'phase':'awaiting_host','completed_inspections':7,'failed_inspections':0,'output_bytes':1234,'host_error_events':0,'last_activity_at':m.now(),'termination_reason':'execution_deadline'}
 with m._write(): m.db.execute("UPDATE review_runs SET state='timed_out',report=?,metrics=?,error=? WHERE id=?",(json.dumps(report),json.dumps(metrics),'The reviewer reached its execution deadline. It did not approve this work.',check['id']))
print('{}')`);
  await page.getByRole('button',{name:'Open work Preserve encoding paths'}).click();
  await page.locator('.agent-checks').waitFor();
  await page.locator('.agent-checks > summary').click();
  await page.getByText('The reviewer reached its execution deadline. It did not approve this work.',{exact:true}).waitFor();
  assert.match(await page.locator('.check-metrics').last().textContent(),/7 inspections completed/);
  await page.getByText('Retained report · This run did not approve the work',{exact:true}).click();
  assert.match(await page.locator('.check-result').textContent(),/Keep the tagged Latin-1 exception/);
  assert.match(await page.locator('.check-result').textContent(),/Project constraints/);
  await page.screenshot({path:path.join(output,'review-timeout.png'),fullPage:true});
  let failedDetails=0,failedHistory=0;
  const comment='A concurrent session records the recovery evidence.';
  await page.route('**/api/board?**',async route=>{
    if(new URL(route.request().url()).searchParams.has('episode')){
      failedDetails++;
      await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({message:'The detail refresh fixture is unavailable.'})});
    }else await route.continue();
  });
  const detailFailure=page.waitForResponse(r=>r.url().includes('/api/board?')&&r.status()===500);
  run(`import sys,json
from pathlib import Path
from memory_module import Memory
from memory_module.workspace import action
with Memory(Path(sys.argv[1])/'.memory/project.sqlite') as m:
 ep=m.db.execute("SELECT id,version FROM episodes WHERE title='Preserve encoding paths'").fetchone()
 action(m,'comment',{'episode_id':ep['id'],'expected_version':ep['version'],'text':'A concurrent session records the recovery evidence.'},'recovery-comment')
print('{}')`);
  await detailFailure;
  await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Update failed');
  await page.waitForResponse(r=>r.url().includes('/api/board?')&&r.status()===500);
  assert.equal(await page.locator('#connection').isVisible(),true);
  assert.equal(await page.locator('#detail-connection').isVisible(),true);
  assert.equal(await page.locator('#live-status').textContent(),'Update failed');
  await page.screenshot({path:path.join(output,'refresh-failure.png'),fullPage:true});
  await page.getByRole('button',{name:'Edit plan',exact:true}).click();
  await page.locator('#edit-next_action').fill('Keep this draft during recovery.');
  await page.unroute('**/api/board?**');
  await page.waitForResponse(r=>r.url().includes('/api/health?')&&r.status()===200);
  await page.waitForResponse(r=>r.url().includes('/api/health?')&&r.status()===200);
  assert.equal(await page.locator('#live-status').textContent(),'Update failed');
  assert.equal(await page.locator('#edit-next_action').inputValue(),'Keep this draft during recovery.');
  await page.keyboard.press('Escape');
  await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Live');
  assert.equal(await page.locator('#connection').isVisible(),false);
  assert.match(await page.locator('#detail-body').textContent(),new RegExp(comment));
  await page.route('**/api/records?**',async route=>{
    const params=new URL(route.request().url()).searchParams;
    if(params.get('view')==='events'&&params.has('episode')){
      failedHistory++;
      await route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({message:'The history fixture is unavailable.'})});
    }else await route.continue();
  });
  // A manual refresh uses the same nested history loader as background refreshes.
  await page.getByRole('button',{name:'Open work Preserve encoding paths'}).click();
  await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Update failed');
  await page.waitForResponse(r=>r.url().includes('/api/records?')&&r.status()===500);
  assert.equal(await page.locator('#live-status').textContent(),'Update failed');
  assert.equal(await page.locator('#connection').isVisible(),true);
  await page.unroute('**/api/records?**');
  await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Live');
  assert.match(await page.locator('#detail-body').textContent(),new RegExp(comment));
  assert.ok(failedDetails>=2);assert.ok(failedHistory>=2);
  await page.screenshot({path:path.join(output,'refresh-recovered.png'),fullPage:true});
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:390,height:844});await page.locator('#new-work').click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'workspace-editor-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const report={passed:true,browser:await browser.version(),initial_ready_ms:readyMs,checks:['create sprint','create action','structured sprint assignment','inspect intent and lineage','navigate while inspecting','Escape closes the inspector','restore focus to the current action','retain keyboard focus during a delayed board refresh','add comment','reject unsupported Done','preserve draft on concurrent edit','reload current version','save revised plan','sprint filtering','original text across source pagination','mobile form width','recording gaps appear without reload','inspect recording gaps','resolved recording gaps and open inspector update without reload','timeout diagnostics are visible','retained report cannot imply approval','constraint conditions remain readable','no external requests','no JavaScript errors']};
  report.checks.push('failed detail refresh remains visible across polls','nested history failure remains visible across polls','detail and history recover without another database change','unsaved editor defers recovery without hiding the failure');
  report.injected_failures={detail:failedDetails,history:failedHistory};
  fs.writeFileSync(path.join(output,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
 }finally{if(browser)await browser.close();if(server)try{process.kill(server.pid);}catch(error){if(error.code!=='ESRCH')throw error;}fs.rmSync(temp,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
