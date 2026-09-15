// Comparable reading journeys use identical synthetic records and a real local server.
const {chromium}=require(process.env.MEMORY_PLAYWRIGHT||'playwright');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {execFileSync}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
const {navigate}=require('./navigation.cjs');
(async()=>{
  const root=path.resolve(__dirname,'../..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'memory-usability-'));
  const runtime=process.env.MEMORY_UX_RUNTIME||root,baseline=process.env.MEMORY_UX_BASELINE==='1';
  const output=path.resolve(process.env.MEMORY_WORKSPACE_OUTPUT||'.memory/ux-review/browser');fs.mkdirSync(output,{recursive:true});
  const python=process.env.MEMORY_PYTHON||'python';let browser,server,emptyServer;
  const checks=[],errors=[],external=[],journeys=[];
  try{
    const info=JSON.parse(execFileSync(python,['-c',`import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from tests.test_viewer_usability import fixture
from memory_module.live import start
info=fixture(Path(sys.argv[1]));print(json.dumps({**info,'server':start(info['database'])}))`,temp,runtime],{cwd:root,encoding:'utf8'}));
    server=info.server;
    browser=await chromium.launch({headless:true,executablePath:process.env.MEMORY_CHROMIUM,args:['--no-sandbox']});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.setDefaultTimeout(10000);
    page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url())&&![server,emptyServer].some(s=>s&&r.url().startsWith(s.url)))external.push(r.url());});
    await page.goto(server.url);
    await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Live');
    await page.screenshot({path:path.join(output,'entry.png'),fullPage:true});
    if(!baseline){
      const summary=page.getByRole('navigation',{name:'Work by status'});
      for(const [state,label] of [['in_progress','In progress'],['ready','Ready'],['blocked','Blocked'],['review','In review']]){
        const selector=summary.getByRole('button',{name:label+': 1',exact:true});
        await selector.click();
        assert.equal(await selector.getAttribute('aria-pressed'),'true');
        assert.equal(await page.locator('.overview-work').count(),1);
        await page.getByRole('button',{name:'View all '+label.toLowerCase()+' work',exact:true}).click();
        assert.equal(await page.locator('#status').inputValue(),state);
        await page.locator('.work-card').first().waitFor();
        assert.equal(await page.locator('.work-card').count(),1);
        await navigate(page,'overview');
      }
      await navigate(page,'board');
      await page.locator('#status').selectOption('');
      await navigate(page,'overview');
      await page.getByRole('button',{name:'New action',exact:true}).click();
      await page.locator('#editor').waitFor({state:'visible'});
      await page.keyboard.press('Escape');
      await page.locator('#editor').waitFor({state:'hidden'});
      await summary.getByRole('button',{name:'Blocked: 1',exact:true}).click();
      const attention=page.getByRole('button',{name:'Open work Validate the staging import',exact:true});
      await page.keyboard.press('Tab');
      await attention.focus();
      assert.equal(await attention.evaluate(e=>getComputedStyle(e).outlineStyle),'solid');
      await page.keyboard.press('Enter');
      await page.getByRole('button',{name:'Edit plan',exact:true}).waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await attention.evaluate(e=>e===document.activeElement),true);
      await summary.getByRole('button',{name:'In progress: 1',exact:true}).click();
      const guidance=page.locator('.overview-guidance[data-action="finalize"]');
      assert.equal(await guidance.evaluate(e=>getComputedStyle(e).color),'rgb(37, 113, 90)');
      checks.push('status counts open the matching full work lists','header new action opens the existing editor','keyboard opens work and returns focus with a visible outline','ready-to-finish guidance uses success styling');
      execFileSync(python,['-c',`import sys
from memory_module import Memory,codex_host
with Memory(sys.argv[1]) as m:
 for event in [{'hook_event_name':'UserPromptSubmit','prompt':'Inspect the browser fixture.'},{'hook_event_name':'PreToolUse','tool_name':'Read','tool_use_id':'lookup'},{'hook_event_name':'PostToolUse','tool_name':'Read','tool_use_id':'lookup','tool_response':{'exit_code':0}}]:
  codex_host.capture(m,{'session_id':'overview-focus','turn_id':'1',**event})`,info.database],{cwd:runtime});
      await page.locator('[data-focus="recording-checks"]').waitFor();
      for(const key of ['requirements','work-board','new-action','all-work','decision-history','lessons','recording-checks','attention']){
        const control=page.locator('.project-overview [data-focus="'+key+'"]');
        await control.focus();
        const old=await control.elementHandle();
        execFileSync(python,['-c',`import sys
from memory_module import Memory
from memory_module.workspace import action
with Memory(sys.argv[1]) as m:
 action(m,'comment',{'episode_id':sys.argv[2],'expected_version':m.episode(sys.argv[2])['version'],'text':'Refresh while a control has focus.'},'focus-'+sys.argv[3])`,info.database,info.work,key],{cwd:runtime});
        await page.waitForFunction(element=>!element.isConnected,old);
        assert.equal(await page.evaluate(()=>document.activeElement.dataset.focus),key,'Refresh preserves '+key);
        await old.dispose();
      }
      checks.push('automatic database refresh preserves every header, footer and follow-up control focus');
    }
    let clicks=0,start=performance.now();const click=async locator=>{clicks++;await locator.click();};
    const current='Use strict UTF-8 and retain a separate tagged Latin-1 path.';
    if(baseline) await navigate(page,'decisions',click);
    await click(page.getByRole('button',{name:'Open '+current,exact:true}));
    if(baseline){
      await click(page.getByRole('button',{name:'Show related records',exact:true}));
      await click(page.getByRole('button',{name:'Outcome: Both encoding fixtures passed after the repair.',exact:true}));
    }
    await page.getByText('Both encoding fixtures passed after the repair.',{exact:true}).first().waitFor();
    journeys.push({task:'Read the observed outcome of the revised decision from the entry page',completed:true,clicks,elapsed_ms:Math.round(performance.now()-start)});
    await page.keyboard.press('Escape');clicks=0;start=performance.now();
    if(baseline){
      await click(page.getByRole('button',{name:'Open Decode every input as strict UTF-8.',exact:true}));
      await click(page.getByRole('button',{name:'Show related records',exact:true}));
      await click(page.getByRole('button',{name:'Outcome: The tagged Latin-1 fixture failed.',exact:true}));
    }else{
      await click(page.getByRole('button',{name:'Open '+current,exact:true}));
      await click(page.getByRole('button',{name:'Earlier decision',exact:true}));
    }
    await page.getByText('The tagged Latin-1 fixture failed.',{exact:true}).first().waitFor();
    journeys.push({task:'Inspect the earlier failed choice after closing the revised result',completed:true,clicks,elapsed_ms:Math.round(performance.now()-start)});
    await page.screenshot({path:path.join(output,'earlier-decision.png'),fullPage:true});
    if(!baseline){
      assert.match(await page.locator('.consequence-comparison').textContent(),/Bad outcome/);
      assert.doesNotMatch(await page.locator('.consequence-comparison').textContent(),/Both encoding fixtures passed/);
      await page.getByRole('button',{name:'Later revision',exact:true}).click();
      await page.getByRole('button',{name:'Expand',exact:true}).click();
      assert.equal(await page.locator('#detail-expand').getAttribute('aria-pressed'),'true');
      await page.screenshot({path:path.join(output,'decision-expanded.png'),fullPage:true});
      checks.push('failed and successful outcomes remain attributed to their own decisions','revision navigation preserves the earlier choice','decision reading expands without changing records');
    }
    await page.keyboard.press('Escape');
    await navigate(page,'documents');
    await page.getByRole('button',{name:'Open Encoding contract.md',exact:true}).click();
    if(!baseline){
      await page.getByRole('navigation',{name:'Document sections'}).getByRole('button',{name:'Tagged exception',exact:true}).click();
      await page.getByRole('button',{name:'Read original text',exact:true}).click();
      assert.match(await page.locator('[data-field=body] pre').textContent(),/## Tagged exception\n\nPreserve the tagged Latin-1 exception/);
      await page.getByRole('button',{name:'Read formatted text',exact:true}).click();
      assert.equal(await page.getByRole('navigation',{name:'Document sections'}).count(),1);
      checks.push('document outline locates the exception','original source text remains available');
      await page.screenshot({path:path.join(output,'document-reader.png'),fullPage:true});
    }
    await page.keyboard.press('Escape');
    if(!baseline){
      await navigate(page,'overview');
      await page.getByRole('navigation',{name:'Work by status'}).getByRole('button',{name:'Blocked: 1',exact:true}).click();
      await page.getByRole('button',{name:'Open work Validate the staging import',exact:true}).click();
      await page.getByRole('button',{name:'Edit plan',exact:true}).waitFor();
      assert.match(await page.locator('.work-reading').textContent(),/Obtain access to staging/);
      await page.keyboard.press('Escape');
      await navigate(page,'board');await page.getByLabel('View format',{exact:true}).selectOption('list');
      assert.equal(await page.locator('#board-grid').evaluate(e=>e.classList.contains('work-list')),true);
      await page.getByRole('button',{name:'Open work Preserve supported encodings',exact:true}).click();
      await page.getByRole('button',{name:'Edit plan',exact:true}).click();
      await page.locator('#edit-next_action').fill('Check the retained exception with the user.');
      await page.locator('#editor-save').click();await page.locator('#editor').waitFor({state:'hidden'});
      await page.waitForFunction(()=>document.querySelector('.work-reading')?.textContent.includes('Check the retained exception with the user.'));
      await page.keyboard.press('Escape');await navigate(page,'overview');
      const updatedOverview=await (await page.request.get(server.url+'api/overview')).json();
      const updatedState=Object.entries(updatedOverview.work.groups).find(([,items])=>items.some(item=>item.id===info.work))[0];
      await page.locator('.overview-status button[data-state="'+updatedState+'"]').click();
      await page.locator('#extension-view').getByText('Check the retained exception with the user.',{exact:true}).waitFor();
      await navigate(page,'decisions');
      assert.equal(await page.locator('#status option[value=file_missing]').count(),0);
      await page.getByLabel('View format',{exact:true}).selectOption('table');
      assert.equal(await page.locator('#record-table').isVisible(),true);
      await page.getByLabel('View format',{exact:true}).selectOption('reading');
      assert.equal(await page.locator('#reading-view').isVisible(),true);
      await page.locator('#query').fill('UTF-8');
      await navigate(page,'documents');await navigate(page,'decisions');
      assert.equal(await page.locator('#query').inputValue(),'UTF-8');
      await navigate(page,'lessons');
      await page.getByRole('button',{name:'Open Check the normal and exceptional paths.',exact:true}).waitFor();
      assert.match(await page.locator('#reading-view').textContent(),/Undocumented encodings remain unsupported/);
      assert.match(await page.locator('#reading-view').textContent(),/Proposed/);
      checks.push('overview opens attention items directly','work lists retain planning actions','saved work refreshes on the overview','filters are appropriate to the current record type','detailed tables remain available','lesson conditions and exceptions remain visible without acceptance');
      await navigate(page,'overview');
      await page.getByRole('button',{name:'Open '+current,exact:true}).waitFor();
      await page.route('**/api/overview?*',route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({message:'The overview fixture is unavailable.'})}));
      const overviewFailure=page.waitForResponse(r=>r.url().includes('/api/overview?')&&r.status()===500);
      fs.appendFileSync(path.join(temp,'Encoding contract.md'),'\nThe source changes after this decision.\n');
      await overviewFailure;
      await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Update failed');
      await page.waitForResponse(r=>r.url().includes('/api/overview?')&&r.status()===500);
      assert.equal(await page.locator('#connection').isVisible(),true);
      await page.unroute('**/api/overview?*');
      await page.waitForFunction(()=>document.querySelector('#live-status').textContent==='Live');
      await page.getByRole('button',{name:'Open '+current,exact:true}).click();
      await page.getByText('Outcome evidence needs review',{exact:true}).waitFor();
      assert.match(await page.locator('.consequence-comparison').textContent(),/Both encoding fixtures passed/);
      await page.keyboard.press('Escape');
      checks.push('record filters survive navigation','overview refresh failure stays visible and recovers without another write','changed evidence marks the decision and its own outcome for review');
      for(const width of [320,390,768,1440]){
        await page.setViewportSize({width,height:1000});
        if(width<760) await page.locator('#menu-toggle').click();
        await navigate(page,'overview');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Overview overflow at '+width);
        await page.screenshot({path:path.join(output,'overview-'+width+'.png'),fullPage:true});
      }
      await page.goto(pathToFileURL(path.join(temp,'snapshot.html')).href);
      assert.equal(await page.getByRole('navigation',{name:'Work by status'}).getByRole('button').count(),4);
      assert.equal(await page.getByRole('button',{name:'New action',exact:true}).count(),0);
      await page.getByRole('button',{name:'Open '+current,exact:true}).click();
      assert.match(await page.locator('.consequence-comparison').textContent(),/Both encoding fixtures passed/);
      assert.equal(await page.getByRole('button',{name:'Edit plan',exact:true}).count(),0);
      for(const width of [320,390]){
        await page.keyboard.press('Escape');
        await page.setViewportSize({width,height:844});
        await navigate(page,'overview');
        await page.getByRole('navigation',{name:'Work by status'}).getByRole('button',{name:'Blocked: 1',exact:true}).click();
        await page.getByRole('button',{name:'Open work Validate the staging import',exact:true}).click();
        assert.match(await page.locator('.work-reading').textContent(),/Obtain access to staging/);
        assert.equal(await page.getByRole('button',{name:'Edit plan',exact:true}).count(),0);
        await page.keyboard.press('Escape');
        await page.getByRole('button',{name:'Open '+current,exact:true}).click();
        await page.getByRole('button',{name:'Earlier decision',exact:true}).click();
        assert.match(await page.locator('.consequence-comparison').textContent(),/The tagged Latin-1 fixture failed/);
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await page.screenshot({path:path.join(output,'offline-mobile-'+width+'.png'),fullPage:true});
      }
      await page.setViewportSize({width:1440,height:1000});
      checks.push('mobile offline snapshots open full work text and earlier decision outcomes at 320 and 390 pixels');
      checks.push('overview fits mobile and desktop widths','offline decision history reads exported outcomes without editing controls');
      const empty=JSON.parse(execFileSync(python,['-c',`import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from memory_module.install import setup
from memory_module.live import start
Path(sys.argv[1]).mkdir()
info=setup(Path(sys.argv[1]));print(json.dumps({'database':info['database'],'server':start(info['database'])}))`,path.join(temp,'empty'),runtime],{cwd:root,encoding:'utf8'}));
      emptyServer=empty.server;
      await page.goto(emptyServer.url);
      await page.getByRole('navigation',{name:'Work by status'}).waitFor();
      assert.deepEqual(await page.locator('.overview-status button > span').allTextContents(),['0','0','0','0']);
      assert.match(await page.locator('.project-overview').textContent(),/No work in progress/);
      await page.screenshot({path:path.join(output,'overview-empty.png'),fullPage:true});
      execFileSync(python,['-c',`import sys
from memory_module import Memory
from memory_module.workspace import action
with Memory(sys.argv[1]) as m:
 for i in range(5):
  action(m,'plan',{'title':'Review '+('long-reference-'*12)+str(i),'objective':'Preserve the complete recorded instruction.','criterion':'The action remains readable.','subject':'code','payload':{'state':'ready','next_action':'Inspect '+('the full supporting evidence and its exception. '*10),'scope':'Exercise long content.','autonomy':'suggest','reason':'The browser fixture checks long recorded work.'}},'long-'+str(i))`,empty.database],{cwd:runtime,encoding:'utf8'});
      await page.getByRole('button',{name:'Ready: 5',exact:true}).click();
      assert.equal(await page.locator('.overview-work').count(),3);
      for(const width of [320,768,1440]){
        await page.setViewportSize({width,height:1000});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Long work overflow at '+width);
      }
      await page.locator('.overview-work').first().click();
      assert.match(await page.locator('.work-reading').textContent(),/the full supporting evidence and its exception/);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.overview-columns').evaluate(e=>e.getBoundingClientRect().bottom<=innerHeight),true,'Dense overview fits desktop viewport');
      await page.getByRole('button',{name:'View all ready work',exact:true}).click();
      await page.locator('.work-card').nth(4).waitFor();
      assert.equal(await page.locator('.work-card').count(),5);
      await navigate(page,'overview');
      const moving=page.locator('.overview-work').first();
      const movingId=await moving.getAttribute('data-focus');
      await moving.focus();
      const oldRow=await moving.elementHandle();
      execFileSync(python,['-c',`import sys
from memory_module import Memory
from memory_module.workspace import action
with Memory(sys.argv[1]) as m:
 action(m,'plan',{'episode_id':sys.argv[2],'expected_version':m.episode(sys.argv[2])['version'],'payload':{'state':'blocked','next_action':'Inspect the missing prerequisite.','scope':'Exercise long content.','autonomy':'suggest','reason':'The browser fixture moves a focused row.'}},'move-focused-row')`,empty.database,movingId],{cwd:runtime});
      await page.waitForFunction(element=>!element.isConnected,oldRow);
      await page.waitForFunction(id=>!document.querySelector('.overview-work[data-focus="'+id+'"]') && document.querySelector('.overview-status [data-state="ready"]').getAttribute('aria-label')==='Ready: 4',movingId);
      assert.equal(await page.evaluate(()=>document.activeElement.dataset.focus),'status-ready');
      assert.equal(await page.locator('.overview-status button[data-state="ready"]').getAttribute('aria-pressed'),'true');
      await oldRow.dispose();
      checks.push('a work row leaving the selected queue returns focus to its status selector');
      checks.push('empty overview shows zero counts and actionable entry points','long authored titles and next actions wrap without overflow','bounded overview rows link to all five recorded items');
    }
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    const result={passed:true,baseline,browser:await browser.version(),journeys,checks,errors,external_requests:external,meaning:'Scripted navigation on identical synthetic records, not human usability or model-token measurements.'};
    fs.writeFileSync(path.join(output,'usability-results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  }finally{if(browser)await browser.close();for(const active of [server,emptyServer])if(active)try{process.kill(active.pid);}catch(e){if(e.code!=='ESRCH')throw e;}fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
