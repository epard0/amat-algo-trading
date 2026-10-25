// Durable/SSE task output and selected-run isolation with in-memory fixtures.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {createRequire}=require('node:module'),{chromium}=require('playwright');
const web=path.resolve(__dirname,'../web'),{build}=createRequire(path.join(web,'package.json'))('esbuild');
(async()=>{
 const fixture=await build({absWorkingDir:web,bundle:true,write:false,format:'iife',jsx:'automatic',stdin:{resolveDir:web,loader:'tsx',contents:`
 import {createRoot} from 'react-dom/client';import {useState} from 'react';import ResearchWorkspace from './src/ResearchWorkspace';
 const jobs=[{id:'first',capability:'first-task',status:'started',started_at:new Date().toISOString()},{id:'second',capability:'second-task',status:'started',started_at:new Date().toISOString()}];
 const request=async path=>{const id=path.split('/').pop();await new Promise(r=>setTimeout(r,id==='second'?900:40));return {...jobs.find(j=>j.id===id),live_output:id==='first'?'FIRST PRIVATE OUTPUT':'SECOND DURABLE OUTPUT\\n',live_output_cursor:10};};
 function Fixture(){const [events,setEvents]=useState([]);return <main><ResearchWorkspace latestJobId='' catalog={[]} jobs={jobs} events={events} artifacts={[]} runtime={{}} stream='Live' updated='' request={request} inspect={()=>{}} openArtifact={()=>{}} cancel={()=>{}} refresh={()=>{}}/><button id='stream' onClick={()=>setEvents([{event_id:'11',cursor:'11',action_id:'second',kind:'action.output',payload:{text:'SECOND STREAMING CHUNK'}}])}>Stream next chunk</button></main>};
 createRoot(document.getElementById('root')).render(<Fixture/>);
 `}});
 const browser=await chromium.launch({headless:true});try{const page=await browser.newPage();await page.setContent('<div id="root"></div>');await page.addStyleTag({content:fs.readFileSync(path.join(web,'src/styles.css'),'utf8')});await page.addScriptTag({content:fixture.outputFiles[0].text});
 await page.getByText('FIRST PRIVATE OUTPUT',{exact:true}).waitFor();const picker=page.getByRole('combobox',{name:'Monitor run'});await picker.fill('second');await picker.press('Enter');assert(!(await page.locator('.research-measured').innerText()).includes('FIRST PRIVATE OUTPUT'),'Prior selected-run output leaked');
 await page.locator('#stream').click();await page.getByText('SECOND STREAMING CHUNK',{exact:true}).waitFor();await page.getByText('SECOND DURABLE OUTPUT',{exact:false}).waitFor();const output=await page.locator('.research-measured').innerText();assert(output.includes('SECOND STREAMING CHUNK'));assert(!output.includes('FIRST PRIVATE OUTPUT'));assert.equal((output.match(/SECOND STREAMING CHUNK/g)||[]).length,1);console.log(JSON.stringify({passed:true,checks:['stream-before-completion','durable-prefix-and-sse-cursor','selected-run-isolation']}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
