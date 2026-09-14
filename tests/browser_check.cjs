// Optional development check. Playwright is not a runtime dependency.
const {chromium}=require(process.env.MEMORY_PLAYWRIGHT || 'playwright');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {execFileSync}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
 const root=path.resolve(__dirname,'..'),browserOutput=process.env.MEMORY_BROWSER_OUTPUT||path.join(root,'results/viewer-v3'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'memory-viewer-'));
 let browser;
 try{
  execFileSync(process.env.MEMORY_PYTHON||'python',['-c',`
from pathlib import Path
import sys
from memory_module import Memory
root=Path(sys.argv[1])
with Memory.create(root/'memory.sqlite','Browser fixture',['Plain English.']) as m:
 document=root/'Product vision.md'
 document.write_text('# Product vision\\nThe product preserves an explicitly requested export option.')
 captured=m.document(str(document))
 ep=m.start('Code episode','Inspect records','test','Fields visible',subject='code')
 for i in range(32):
  m.record(ep['id'],'note',{'text':'Record %02d'%i if i else '</script><script>window.injected=true</script>'},expected_version=i,request_key=str(i),actor='test',evidence=[{'source_id':captured['id'],'reason':'The vision defines the scope.'}] if i==0 else None)
 evidence=m.source('test','Evidence','Stored source','Exact evidence text','tool',subject='code')
 writing=m.start('Writing episode','Clarify a claim','correction','The historical quotation is preserved.',subject='writing')
 m.record(writing['id'],'correction',{'before':'The feature improves accuracy.','after':'The feature is intended to improve accuracy; it has not been evaluated.','reason':'The original statement described an untested benefit as established.','scope':'This correction applies to claims about features that have not been evaluated.'},expected_version=0,request_key='correction',actor='test',evidence=[{'source_id':evidence['id'],'reason':'The fixture supplies the original wording and its untested status.'}])
 document.write_text('# Product vision\\nThe product supports reviewed scheduled exports.')
 m.export_html(root/'viewer.html',include_bodies=True)
`,temp],{cwd:root});
  browser=await chromium.launch({headless:true,args:['--no-sandbox'],executablePath:process.env.MEMORY_CHROMIUM});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(path.join(temp,'viewer.html')).href);
  assert.equal(await page.locator('#rows tr').count(),2);
  await page.locator('[data-view=events]').click();
  assert.equal(await page.locator('#rows tr').count(),25);
  await page.locator('#next').click();assert.equal(await page.locator('#rows tr').count(),8);
  assert.equal(await page.locator('#page').textContent(),'Page 2 of 2');
  await page.locator('#query').fill('Record 31');assert.equal(await page.locator('#rows tr').count(),1);
  await page.locator('.row-open').click();assert.equal(await page.locator('#detail').evaluate(e=>e.open),true);
  assert.match(await page.locator('#detail-body').textContent(),/Record 31/);
  await page.locator('#close').click();
  await page.locator('#query').fill('window.injected');assert.equal(await page.locator('#rows tr').count(),1);
  await page.locator('.row-open').click();
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  await page.keyboard.press('Escape');
  await page.locator('#clear').click();await page.locator('#subject').selectOption('code');
  await page.locator('#from').fill('2099-01-01');assert.equal(await page.locator('#rows tr').count(),0);
  await page.locator('#clear').click();await page.locator('[data-view=sources]').click();
  await page.locator('.row-open').first().click();assert.match(await page.locator('#detail-body').textContent(),/Exact evidence text/);
  await page.keyboard.press('Escape');
  await page.locator('[data-view=documents]').click();assert.equal(await page.locator('#rows tr').count(),1);
  await page.locator('.row-open').click();assert.match(await page.locator('#detail-body').textContent(),/File changed/);
  assert.match(await page.locator('#detail-body').textContent(),/Records that use this evidence/);
  await page.locator('#detail-body .references button').first().click();assert.match(await page.locator('#detail-body').textContent(),/Needs review/);
  await page.keyboard.press('Escape');
  await page.locator('[data-view=corrections]').click();await page.locator('.row-open').click();
  const correction=await page.locator('#detail-body').textContent();
  assert(correction.indexOf('Original wording')<correction.indexOf('Corrected wording'));
  assert.match(correction,/The feature improves accuracy\./);
  assert.match(correction,/This correction applies to claims about features that have not been evaluated\./);
  fs.mkdirSync(browserOutput,{recursive:true});
  await page.screenshot({path:path.join(browserOutput,'correction-detail.png')});
  await page.keyboard.press('Escape');await page.locator('#subject').selectOption('code');
  await page.locator('[data-view=episodes]').click();await page.locator('.row-open').click();
  await page.getByRole('button',{name:'Show episode events'}).click();
  assert.equal(await page.locator('#rows tr').count(),25);
  await page.locator('#order').selectOption('title');
  await page.locator('#page-size').selectOption('10');assert.equal(await page.locator('#rows tr').count(),10);
  const demo=process.env.MEMORY_VIEWER||path.join(root,'results/host-example/memory-viewer.html');
  const optionalChecks=[];
  fs.mkdirSync(browserOutput,{recursive:true});
  if(fs.existsSync(demo)){
   await page.goto(pathToFileURL(demo).href);await page.screenshot({path:path.join(browserOutput,'viewer-desktop.png'),fullPage:true});
   await page.locator('[data-view=pending]').click();assert.equal(await page.locator('#rows tr').count(),1);
   await page.locator('.row-open').click();assert.match(await page.locator('#detail-body').textContent(),/execution_unconfirmed|Execution not confirmed/);
   await page.keyboard.press('Escape');
   await page.setViewportSize({width:390,height:844});
   await page.screenshot({path:path.join(browserOutput,'viewer-mobile.png'),fullPage:true});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
   optionalChecks.push('unresolved action display','mobile width');
  }
  const projectViewer=path.join(root,'memory-viewer.html');
  if(fs.existsSync(projectViewer)){
   await page.setViewportSize({width:1440,height:1000});await page.goto(pathToFileURL(projectViewer).href);
   for(const view of ['decisions','research','corrections','patterns','drift','captures']){
    await page.locator(`[data-view=${view}]`).click();assert((await page.locator('#rows tr').count())>0,view+' must contain project evidence');
   }
   await page.locator('[data-view=decisions]').click();await page.locator('.row-open').first().click();
   assert.match(await page.locator('#detail-body').textContent(),/Decision basis/);
   assert.match(await page.locator('#detail-body').textContent(),/Actions, outcomes and revisions/);
   await page.keyboard.press('Escape');
   await page.screenshot({path:path.join(browserOutput,'project-desktop.png'),fullPage:true});
   await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(browserOutput,'project-mobile.png'),fullPage:true});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
   optionalChecks.push('project decision, research, writing, pattern, drift and capture views','decision lineage navigation','project mobile width');
  }
  const planning=path.join(temp,'planning');
  execFileSync(process.env.MEMORY_PYTHON||'python',['-m','examples.planning_case','--project',planning,'--extra','26'],{cwd:root});
  await page.setViewportSize({width:1440,height:1000});
  await page.goto(pathToFileURL(path.join(planning,'board.html')).href);
  await page.locator('[data-view=board]').click();
  assert.equal(await page.locator('.work-card').count(),25);
  assert.equal(await page.locator('#from').isVisible(),false);
  await page.locator('#next').click();assert.equal(await page.locator('.work-card').count(),4);
  await page.locator('#query').fill('legacy exception');assert.equal(await page.locator('.work-card').count(),1);
  await page.locator('.work-card').click();
  assert.match(await page.locator('#detail-body').textContent(),/Work scope/);
  assert.match(await page.locator('#detail-body').textContent(),/Latin-1/);
  assert.match(await page.locator('#detail-body').textContent(),/Decision history/);
  await page.keyboard.press('Escape');await page.locator('#clear').click();
  const sprint=JSON.parse(fs.readFileSync(path.join(planning,'fixture.json'),'utf8')).sprint.episode_id;
  await page.locator('#sprint').selectOption(sprint);
  assert.equal(await page.locator('.work-card').count(),2);
  assert.match(await page.locator('#sprint-summary').textContent(),/Reliable continuation/);
  await page.screenshot({path:path.join(browserOutput,'work-board-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.screenshot({path:path.join(browserOutput,'work-board-mobile.png'),fullPage:true});
  optionalChecks.push('work board pagination','board search','sprint selection','intent and scope detail','board hides irrelevant filters','board mobile width');
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
  const report={passed:true,browser:await browser.version(),checks:['local file opening','pagination','search','subject and date filters','detail panel','source text','document change status','reverse evidence navigation','episode navigation','sort and page size','escaped script input','original-before-corrected wording','complete correction scope',...optionalChecks,'no network requests','no JavaScript errors'],network_requests:requests.length,javascript_errors:errors};
  fs.writeFileSync(path.join(browserOutput,'browser-validation.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report));
 }finally{if(browser)await browser.close();fs.rmSync(temp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
