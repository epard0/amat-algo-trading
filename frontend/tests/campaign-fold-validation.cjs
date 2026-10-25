const assert = require('node:assert/strict');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765/');
    const table = page.getByRole('table', { name: 'Final strategy comparison', exact: true });
    await table.waitFor();
    const response = await page.request.get('http://127.0.0.1:8765/api/v1/analysis/performance?study=campaign&strategy=core_daily&period=full&cost=6');
    const data = await response.json();
    const campaign = data.efficiency.campaign;
    assert.equal(campaign.all_archived_gp_fits_reproduced, true);
    assert.equal(campaign.coverage_complete, false);
    assert.equal(campaign.raw_checkpoint_identity_equal, false);
    assert.equal(campaign.checkpoint_difference_explained, true);
    assert.equal(await table.locator('tbody tr').count(), 4);
    assert.equal(await page.getByRole('table', { name: 'Training fold validation results' }).locator('tbody tr').count(), 7);
    assert.equal(await page.getByLabel('Final return comparison chart', { exact: true }).getByRole('img').count(), 4);
    for (const row of campaign.final_comparison) {
      const rendered = table.getByRole('row').filter({ hasText: row.label });
      assert((await rendered.textContent()).includes((row.full_return * 100).toFixed(2)));
      assert((await rendered.textContent()).includes((row.forward_return * 100).toFixed(2)));
    }
    assert(Math.abs(campaign.final_comparison[0].full_return - data.metrics.net_return) < 1e-12);
    for (const width of [1024, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    }
    assert.deepEqual(errors, []);
    console.log('Seven fold results, four source-backed strategy comparisons and charts, explicit limits and mobile passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
