const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch();try{
 const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8765/');const table=page.getByRole('table',{name:'Selection-adjusted growth intervals'});await table.waitFor();
 const response=await page.request.get('http://127.0.0.1:8765/api/v1/analysis/performance?study=campaign&strategy=core_daily&period=full&cost=6');const data=await response.json(), c=data.efficiency.campaign;
 assert.equal(c.uncertainty_is_diagnostic,true);assert.equal(c.uncertainty_family_histories,293);assert.equal(c.uncertainty_months,118);assert.equal(c.promotion,false);assert.equal(c.current_native_execution_parity,false);
 assert.equal(await table.locator('tbody tr').count(),3);assert.equal(await page.getByLabel('Adjusted uncertainty interval chart',{exact:true}).getByRole('img').count(),3);
 for(const row of c.uncertainty_rows){const rendered=await table.getByRole('row').filter({hasText:row.label}).textContent();for(const block of ['1','3']){const ci=row.intervals[block];assert(ci.simultaneous_lower<0&&ci.simultaneous_upper>0);assert(rendered.includes((ci.simultaneous_lower*100).toFixed(2)));assert(rendered.includes((ci.simultaneous_upper*100).toFixed(2)));}}
 assert.equal(await page.getByRole('table',{name:'Learned strategy validation gates'}).locator('tbody tr').count(),6);
 for(const width of [1024,390]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
 assert.deepEqual(errors,[]);console.log('Selection-adjusted interval values/chart, diagnostic limits, pipeline blockers and mobile passed');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1});
