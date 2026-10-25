const assert = require('node:assert/strict');
const {chromium} = require('playwright');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1100}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8765/');
    const table=page.getByRole('table',{name:'Learned strategy validation gates'});
    await table.waitFor();
    const response=await page.request.get('http://127.0.0.1:8765/api/v1/analysis/performance?study=campaign&strategy=core_daily&period=full&cost=6');
    const data=await response.json(), campaign=data.efficiency.campaign;
    assert.equal(campaign.inference_boundary_combinations,756);
    assert.equal(campaign.recipe_selection_cases,48);
    assert.equal(campaign.archived_execution_logical_cases,54);
    assert(campaign.archived_execution_unique_cases<=54);
    assert.equal(campaign.current_native_execution_parity,false);
    assert.equal(campaign.promotion,false);
    assert.equal(campaign.coverage_complete,false);
    assert.equal(await table.locator('tbody tr').count(),6);
    assert((await table.getByRole('row').filter({hasText:'Current whole-share execution parity'}).textContent()).includes('Blocked'));
    assert((await table.getByRole('row').filter({hasText:'Overall promotion'}).textContent()).includes('Blocked'));
    assert.equal(await page.getByRole('table',{name:'Final strategy comparison',exact:true}).locator('tbody tr').count(),4);
    assert.equal(await page.getByRole('table',{name:'Training fold validation results'}).locator('tbody tr').count(),7);
    for(const width of [1024,390]) {
      await page.setViewportSize({width,height:1100});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    }
    assert.deepEqual(errors,[]);
    console.log('Inference/selection/execution evidence, explicit parity and promotion blockers, preserved final comparison and mobile passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
