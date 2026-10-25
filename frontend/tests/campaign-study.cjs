const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch();try{
 const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(60000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765/');
 await page.getByRole('heading',{name:'What is the research campaign learning?',exact:true}).waitFor();
 await page.getByRole('heading',{name:/Did this campaign candidate improve performance\?|How do the historical strategies compare\?/}).waitFor();
 assert.equal(await page.getByLabel('Research comparison').inputValue(),'campaign');
 const data=await page.evaluate(async()=> (await fetch('/api/v1/analysis/performance?study=campaign&strategy=core_daily&period=full&cost=6')).json());
 assert.equal(data.status,'available');const detail=data.efficiency;assert(detail.campaign.cycle_id);
 assert.equal(await page.getByRole('table',{name:'Historical return frontier'}).locator('tbody tr').count(),3);
 if(detail.campaign.benchmark_replay){const rows=detail.campaign.benchmark_replay.rows;assert.equal(await page.getByRole('table',{name:'Historical strategy replays'}).locator('tbody tr').count(),rows.length);assert(rows.some(r=>r.arm==='core_daily'&&r.cost_bps===6&&Math.abs(r.full_return-data.metrics.net_return)<1e-10));}
 assert.equal(await page.locator('[aria-label="Conditional sizing results"] .analysis-comparison-row').count(),detail.all_results.length);
 assert(detail.all_results.every(r=>!r.id.startsWith('legacy_')),'Fractional semantic probes must stay out of whole-share comparisons');
 const auditOnly=detail.all_results.find(r=>['historical_comparator','diagnostic'].includes(r.status));if(auditOnly){const response=await page.evaluate(async id=>(await fetch(`/api/v1/analysis/performance?study=campaign&strategy=${id}&period=full&cost=6`)).json(),auditOnly.id);assert.equal(response.status,'unavailable');}
 const rejected=detail.all_results.find(r=>r.status==='rejected');if(rejected){const response=await page.evaluate(async id=>(await fetch(`/api/v1/analysis/performance?study=campaign&strategy=${id}&period=full&cost=6`)).json(),rejected.id);assert.equal(response.status,'unavailable');}
 if(detail.campaign.cycle_id==='001-six-positions'){assert.equal(detail.candidate_count,1);assert.equal(detail.rejected_count,1);assert.equal(detail.campaign.coverage_complete,false);assert(Math.abs(data.metrics.net_return-2.6458194748)<1e-8);}
 await page.getByLabel('Time period',{exact:true}).selectOption('full');
 await page.getByText('Comparison settings and source',{exact:true}).click();
 for(const cost of ['2','10','6']){const done=page.waitForResponse(r=>r.url().includes('/analysis/performance?')&&r.url().includes('study=campaign')&&r.url().includes(`cost=${cost}`));await page.getByLabel('Assumed trading cost per side').selectOption(cost);await done;await page.getByRole('heading',{name:/Did this campaign candidate improve performance\?|How do the historical strategies compare\?/}).waitFor();}
 for(const period of ['2023','stress_2018','stress_covid','stress_2022','walk_forward']){const done=page.waitForResponse(r=>r.url().includes('/analysis/performance?')&&r.url().includes('study=campaign')&&r.url().includes(`period=${period}`));await page.getByLabel('Time period',{exact:true}).selectOption(period);await done;await page.getByRole('heading',{name:/Did this campaign candidate improve performance\?|How do the historical strategies compare\?/}).waitFor();}
 for(const width of [1024,390]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
 assert.deepEqual(errors,[]);console.log('Campaign latest-cycle publication, historical frontier, rejected selection, cost/stress views and mobile passed');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
