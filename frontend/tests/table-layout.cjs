// Read-only coverage of the actual conditional comparison tables. No action is dispatched.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const url = process.env.AMAT_TERMINAL_URL || 'http://127.0.0.1:5173/';
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.getByRole('button',{name:'Performance',exact:true}).click();
    const picker = page.getByLabel('Research comparison',{exact:true});
    await picker.waitFor();
    const studies = await picker.locator('option').evaluateAll(options => options.map(option => option.value));
    const coverage = [];
    for (const study of process.env.AMAT_SKIP_STUDIES ? [] : studies) {
      await page.setViewportSize({width:1440,height:1000});
      if (await picker.inputValue() !== study) {
        const response = page.waitForResponse(response => response.url().includes('/analysis/performance?') && new URL(response.url()).searchParams.get('study') === study);
        await picker.selectOption(study);
        await response;
      }
      await page.locator('.analysis-loading').waitFor({state:'hidden'});
      // Open observational disclosures, including nested comparison records.
      await page.locator('main details').evaluateAll(details => details.forEach(detail => detail.open = true));
      const tables = await page.locator('main table').count();
      for (const width of [1440,1024,768,390]) {
        await page.setViewportSize({width,height:1000});
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const state = await page.evaluate(() => ({
          overflow:[...document.querySelectorAll('main .table-scroll,main .analysis-table-wrap,main table')].filter(el => el.getClientRects().length && el.scrollWidth > el.clientWidth + 1).map(el => ({tag:el.tagName, label:el.getAttribute('aria-label'),className:el.className, scroll:el.scrollWidth,width:el.clientWidth})),
          document:[document.documentElement.scrollWidth,innerWidth],
          unmigrated:[...document.querySelectorAll('main table')].filter(el => !el.classList.contains('readable-table')).length,
          missingLabels:document.querySelectorAll('main table td:not([data-label])').length,
        }));
        assert.equal(state.unmigrated,0,`${study}: unmigrated table`);
        assert.equal(state.missingLabels,0,`${study}: unlabeled responsive values`);
        assert.deepEqual(state.overflow,[],`${study} at ${width}: ${JSON.stringify(state.overflow)}`);
        if (state.document[0] > state.document[1]) { await page.screenshot({path:'/tmp/amat-layout-failure.png'}); console.log(await page.evaluate(() => ({now:[document.documentElement.scrollWidth,innerWidth], elements:[...document.querySelectorAll('body *')].filter(el => el.scrollWidth > el.clientWidth + 1).map(el => ({tag:el.tagName, cls:el.className, width:el.clientWidth,scroll:el.scrollWidth,right:el.getBoundingClientRect().right,text:(el.innerText||'').slice(0,50)})).slice(0,25)}))); }
        assert(state.document[0] <= state.document[1],`${study} document overflow at ${width}: ${state.document}`);
      }
      coverage.push({study,tables,widths:[1440,1024,768,390]});
    }
    const surfaces = [];
    for (const desk of ['Markets','Kalshi perps','Operations','Tasks','Remote']) {
      await page.getByRole('button',{name:desk,exact:true}).click();
      await page.locator('main details').evaluateAll(details => details.forEach(detail => detail.open = true));
      const views = desk === 'Markets' ? ['Positions','Orders','Runs'] : ['default'];
      for (const view of views) {
        if (view !== 'default') await page.locator('.holdings .tabs button').filter({hasText:view}).click();
        for (const width of [1440,1024,768,390]) {
          await page.setViewportSize({width,height:1000});
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const state = await page.evaluate(() => ({
            overflow:[...document.querySelectorAll('main .table-scroll,main .analysis-table-wrap,main table')].filter(el => el.getClientRects().length && el.scrollWidth > el.clientWidth + 1).map(el => ({label:el.getAttribute('aria-label'),className:el.className})),
            document:[document.documentElement.scrollWidth,innerWidth],
            missingLabels:document.querySelectorAll('main table td:not([data-label])').length,
          }));
          assert.deepEqual(state.overflow,[],`${desk}/${view} at ${width}: ${JSON.stringify(state.overflow)}`);
          assert.equal(state.missingLabels,0,`${desk}/${view}: unlabeled values`);
          assert(state.document[0] <= state.document[1],`${desk}/${view} document overflow at ${width}: ${state.document}`);
        }
        surfaces.push({desk,view,tables:await page.locator('main table').count()});
      }
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,coverage,surfaces},null,2));
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
