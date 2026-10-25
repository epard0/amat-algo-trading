// Isolated guided-terminal regressions. All action requests are in-memory fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { chromium } = require('playwright');
const web = path.resolve(__dirname, '../web');
const { build } = createRequire(path.join(web, 'package.json'))('esbuild');
(async () => {
  const compiled = await build({absWorkingDir:web,bundle:true,write:false,format:'iife',jsx:'automatic',stdin:{resolveDir:web,loader:'tsx',contents:`
    import {createRoot} from 'react-dom/client';
    import {useState} from 'react';
    import TaskConsole from './src/TaskConsole';
    import {SnapshotList} from './src/OperationsViews';
    const capabilities=[{id:'config.validate',title:'Validate configuration',target:'local',summary:'Read configuration',effects:['No trading effects'],inputs:{name:{type:'string',default:'initial'},enabled:{type:'boolean',default:false}}},{id:'not.supported',title:'Unavailable task',supported:false}];
    const stats={previews:[],dispatches:[],jobs:[],responses:[],failOnce:false};window.fixtureStats=stats;
    const request=async(path,body)=>{
      if(path==='/actions/preview'){
        stats.previews.push(body);const name=body.inputs.name;
        await new Promise(r=>setTimeout(r,name==='slow'?900:40));
        if(name==='bad')throw new Error('Invalid fixture input');
        return {preview_id:'preview-'+name,inputs:body.inputs,expires_at:new Date(Date.now()+120000).toISOString()};
      }
      stats.dispatches.push(body);
      let job=stats.jobs.find(j=>j.key===body.idempotency_key);
      if(!job){job={key:body.idempotency_key,job_id:'fixture-'+stats.jobs.length};stats.jobs.push(job);}
      if(stats.failOnce){stats.failOnce=false;throw new Error('Response lost; retry reconnects to the same job');}
      return job;
    };
    function Fixture(){const [cap,setCap]=useState(''),[inputs,setInputs]=useState('{}'),[result,setResult]=useState(null);
      return <main><TaskConsole capabilities={capabilities} capability={cap} inputs={inputs} select={id=>{setCap(id);setInputs(JSON.stringify({name:'initial',enabled:false}));}} edit={setInputs} request={request} accepted={setResult}/>
      {result&&<p id='accepted'>Accepted {result.job_id}</p>}
      <button id='uncertain' onClick={()=>stats.failOnce=true}>Simulate lost response</button>
      <SnapshotList inspect={()=>{}} snapshots={[{id:'new',provider:'fixture',feed:'daily',first_session:'2020-01-01',last_session:'2026-10-23',symbols:['AAA','BBB'],row_count:200},{id:'old',provider:'fixture',feed:'daily',first_session:'2020-01-01',last_session:'2026-10-22',symbols:['AAA','CCC'],row_count:190}]}/>
      </main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture/>);
  `}});
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1024,height:1000}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('https://terminal.test/',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><div id="root"></div>'}));
    await page.goto('https://terminal.test/');
    await page.addStyleTag({content:fs.readFileSync(path.join(web,'src/styles.css'),'utf8')});
    await page.addScriptTag({content:compiled.outputFiles[0].text});
    const run=page.getByRole('button',{name:'Run action',exact:true});
    await page.getByText('No action selected',{exact:true}).waitFor();assert(!(await run.isEnabled()));
    const picker=page.getByRole('combobox',{name:'Choose a task'});await picker.fill('validate');await picker.press('Enter');
    await page.getByText('Ready to run · inputs validated',{exact:true}).waitFor();
    assert.equal((await page.evaluate(()=>window.fixtureStats.dispatches)).length,0,'Automatic preview dispatched a task');
    const name=page.getByRole('textbox',{name:'Name',exact:true});
    await name.fill('slow');await page.waitForFunction(()=>window.fixtureStats.previews.some(p=>p.inputs.name==='slow'));
    await name.fill('fast');await page.getByText('Ready to run · inputs validated',{exact:true}).waitFor();
    await page.waitForTimeout(1000);await run.click();await page.locator('#accepted').waitFor();
    let stats=await page.evaluate(()=>window.fixtureStats);assert.equal(stats.dispatches.length,1);assert.equal(stats.dispatches[0].inputs.name,'fast');assert.equal(stats.dispatches[0].preview_id,'preview-fast');
    assert.equal(await page.getByRole('dialog').count(),0,'Preview opened an old modal');
    await page.getByRole('button',{name:'Prepare another run'}).click();
    await name.fill('bad');await page.getByRole('alert').filter({hasText:'Invalid fixture input'}).waitFor();assert(!(await run.isEnabled()));
    await name.fill('retry');await page.getByText('Ready to run · inputs validated',{exact:true}).waitFor();
    await page.locator('#uncertain').click();await run.click();
    await page.getByRole('alert').filter({hasText:'Response lost'}).waitFor();assert(await picker.isDisabled(),'Uncertain task settings were not locked');
    await page.getByText('Ready to run · inputs validated',{exact:true}).waitFor();await run.dblclick();
    await page.locator('#accepted').filter({hasText:'fixture-1'}).waitFor();stats=await page.evaluate(()=>window.fixtureStats);
    assert.equal(stats.jobs.length,2,'Retry duplicated an accepted job');assert.equal(stats.dispatches.at(-1).idempotency_key,stats.dispatches.at(-2).idempotency_key);
    await page.locator('.snapshot-record').first().locator('summary').first().click();
    await page.getByText('Symbols added: BBB.',{exact:true}).waitFor();await page.getByText('Symbols removed: CCC.',{exact:true}).waitFor();
    await page.getByText('Coverage ends: 2026-10-22 → 2026-10-23.',{exact:true}).waitFor();
    const widths=[];for(const width of [1440,1024,768,390]){await page.setViewportSize({width,height:1000});const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);assert(fits,'Terminal overflow at '+width);widths.push(width);}
    assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:['no-action','preview-without-dispatch','out-of-order-preview','inline-run','validation','uncertain-response-idempotency','snapshot-metadata-differences','responsive-widths'],widths,jobs:stats.jobs.length},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
