const assert = require('node:assert/strict');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror', e=>errors.push(e.message));
  try {
    await page.goto('http://127.0.0.1:8765/');
    if (await page.getByRole('button',{name:'Performance',exact:true}).count()) await page.getByRole('button',{name:'Performance',exact:true}).click();
    else if (await page.getByRole('link',{name:'Performance',exact:true}).count()) await page.getByRole('link',{name:'Performance',exact:true}).click();
    const section=page.getByRole('region',{name:'Native annual strategy research'});
    await section.getByRole('heading',{name:'Can yearly updates improve the current strategy?'}).waitFor();
    const publication=await page.evaluate(async ()=>(await fetch('/api/v1/analysis/performance?study=campaign&strategy=core_daily&period=full&cost=6')).json());
    const data=publication.efficiency.native_annual;
    assert.equal(data.status,'available');assert.equal(data.candidate_count,16);assert.equal(data.promoted_count,0);assert.equal(data.control_replays,15);assert.equal(data.source_parity_replays,78);assert.equal(data.coverage_complete,false);
    assert.equal(data.benchmark_details.status,'available');assert.equal(data.benchmark_details.cost_replay_count,717);assert.equal(data.benchmark_details.decision_input_count,239);
    assert.equal(await section.getByRole('table',{name:'Native benchmark review'}).locator('tbody tr').count(),7);
    assert.equal(await section.getByRole('table',{name:'Expanded historical return frontier'}).locator('tbody tr').count(),3);
    await section.evaluate(el=>el.scrollIntoView({block:'start'}));
    await page.screenshot({path:'/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-20-native-annual-scheduling/dashboard-desktop.png'});
    const table=section.getByRole('table',{name:'Annual strategy comparison'});
    assert.equal(await table.locator('tbody tr').count(),12);
    assert.equal(await section.getByRole('table',{name:'Native annual forward and stress results'}).locator('tbody tr').count(),10);
    assert.equal(await section.getByRole('table',{name:'Native annual uncertainty'}).locator('tbody tr').count(),2);
    for(const cost of ['2','10','6']) {
      await section.getByLabel('Annual research trading cost').selectOption(cost);
      const baseline=data.scenarios[cost].rows.find(r=>r.id==='core_daily');
      assert((await table.locator('tbody tr').first().innerText()).includes((baseline.full_return*100).toFixed(2)));
    }
    await section.getByLabel('Annual research starting strategy').selectOption('original');
    await section.getByLabel('Annual recipe details').selectOption('annual_gp');
    assert.equal(await table.locator('tbody tr').count(),12);
    assert(await section.locator('.analysis-month-grid .analysis-month').count()>=100);
    assert(await section.locator('svg').count()>=1);
    for(const width of [1024,390]) {
      await page.setViewportSize({width,height:1100});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Page overflows horizontally');
    }
    await section.screenshot({path:'/home/supasuge/Documents/amat-workspace/amat/outputs/analysis/2026-10-20-native-annual-scheduling/dashboard-mobile.png'});
    assert.deepEqual(errors,[]);
    console.log('Native annual dashboard: all costs, 16 rejected variants, original/current parents, growth/monthly/annual/stress/uncertainty visuals and responsive layout passed.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
