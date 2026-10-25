const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.goto('http://127.0.0.1:8765/');
  const button=page.getByRole('button',{name:'Performance',exact:true});
  if(await button.count())await button.click();else await page.getByRole('link',{name:'Performance',exact:true}).click();
  const section=page.getByRole('region',{name:'Cost-aware annual allocation research'});
  await section.getByRole('heading',{name:'Can cost-aware yearly updates improve the quality strategy?'}).waitFor();
  const data=await page.evaluate(async()=>(await(await fetch('/api/v1/analysis/performance?study=campaign&strategy=core_daily&period=full&cost=6')).json()).efficiency.native_annual.cost_aware_comparison);
  assert.equal(data.candidate_count,2);assert.equal(data.promoted_count,0);assert.equal(data.control_replays,9);
  const table=section.getByRole('table',{name:'Annual strategy comparison'});
  for(const parent of ['cost_annual_252','cost_annual_756']) {
   await section.getByLabel('Annual research starting strategy').selectOption(parent);
   for(const cost of ['2','6','10']){
    await section.getByLabel('Annual research trading cost').selectOption(cost);
    assert.equal(await table.locator('tbody tr').count(),4);assert.equal(await section.getByRole('table',{name:'Cost-aware yearly decisions'}).locator('tbody tr').count(),3);
    const candidate=data.scenarios[cost].rows.find(r=>r.parent===parent);
    assert((await table.innerText()).includes((candidate.full_return*100).toFixed(2)));
    assert.equal(await section.getByRole('table',{name:'Native annual forward and stress results'}).locator('tbody tr').count(),10);
    assert.equal(await section.getByRole('table',{name:'Native annual uncertainty'}).locator('tbody tr').count(),1);
   }
  }
  assert(await section.locator('svg').count()>0);assert(await section.locator('.analysis-month').count()>100);
  await section.getByLabel("Annual research starting strategy").selectOption("cost_annual_252");await section.getByLabel("Annual research trading cost").selectOption("6");await section.getByRole("heading",{name:"Can cost-aware yearly updates improve the quality strategy?"}).evaluate(el=>el.scrollIntoView({block:"start"}));await page.screenshot({path:'/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-20-cost-aware-annual-allocation/dashboard-desktop.png'});
  for(const width of [1024,390]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
  await section.screenshot({path:'/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-20-cost-aware-annual-allocation/dashboard-mobile.png'});
  assert.deepEqual(errors,[]);console.log('Cost-aware annual dashboard: both lookbacks, all costs, growth, monthly, annual, stress and uncertainty views; responsive layout passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
