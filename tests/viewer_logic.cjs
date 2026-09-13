// Runs the actual HTML filter/pagination functions. Does not render a browser.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),template=fs.readFileSync(path.join(root,'memory_module/viewer.html'),'utf8');
const logic=template.split('// Pure view logic.')[1].split('// End pure view logic.')[0];
const sandbox={};vm.createContext(sandbox);vm.runInContext(logic,sandbox);
new vm.Script(template.split('<script>')[1].split('</script>')[0]);
const records=Array.from({length:32},(_,i)=>({id:String(i),kind:'note',subject:i%2?'code':'writing',status:'recorded',date:'2026-09-12T12:00:00Z',title:'Record '+String(i).padStart(2,'0'),episode_id:'ep',detail:{text:i===0?'</script><script>bad()</script>':'text'}}));
const pending=new Map([['1',{state:'execution_unconfirmed'}]]);
const filter={view:'events',query:'',subject:'',status:'',episode:'',from:'',to:'',order:'title'};
const select=extra=>sandbox.selectRecords(records,pending,{...filter,...extra});
assert.equal(select({}).length,32);
assert.equal(select({subject:'code'}).length,16);
assert.equal(select({query:'Record 31'})[0].id,'31');
assert.equal(select({query:'bad()'}).length,1);
assert.equal(select({from:'2026-09-13'}).length,0);
assert.equal(select({to:'2026-09-11'}).length,0);
assert.equal(select({episode:'other'}).length,0);
assert.equal(select({view:'pending',status:'execution_unconfirmed'})[0].id,'1');
assert.equal(select({view:'episodes'}).length,0);
assert.equal(select({view:'sources'}).length,0);
const first=sandbox.pageRecords(select({}),1,25),last=sandbox.pageRecords(select({}),2,25);
assert.equal(first.records.length,25);assert.equal(last.records.length,7);
assert.equal(first.start,1);assert.equal(first.end,25);assert.equal(last.start,26);assert.equal(last.end,32);
assert.equal(sandbox.pageRecords(select({query:'Record 31'}),2,25).page,1);
assert.equal(sandbox.pageRecords([],1,25).start,0);
assert.equal(sandbox.pageRecords(select({}),99,25).page,2);
const snapshot=fs.readFileSync(path.join(root,'results/host-example/memory-viewer.html'),'utf8');
for(const tag of ['style','script']){
 const content=snapshot.split('<'+tag+'>')[1].split('</'+tag+'>')[0];
 const hash=crypto.createHash('sha256').update(content).digest('base64');
 assert.ok(snapshot.includes("'sha256-"+hash+"'"));
}
const data=JSON.parse(snapshot.split('<script id="memory-data" type="application/json">')[1].split('</script>')[0]);
assert.ok(data.records.length>0);assert.equal(data.pending.length,1);
const report={passed:true,node:process.version,checks:['script syntax','subject filter','search','date range','episode filter','pending status','record-type views','pagination bounds and reset','content security hashes','snapshot JSON'],limitation:'Tests execute actual filtering and pagination functions, but do not render HTML or validate browser interactions.'};
fs.mkdirSync(path.join(root,'results/viewer-v3'),{recursive:true});
fs.writeFileSync(path.join(root,'results/viewer-v3/javascript-validation.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
