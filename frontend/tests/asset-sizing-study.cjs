const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    page.setDefaultTimeout(60000);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("http://127.0.0.1:8765/");
    await page.getByLabel("Research comparison").selectOption("asset_sizing");
    await page
      .getByRole("heading", {
        name: "Can each asset signal when to size defensively?",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", {
        name: "Keep the improvements that earn their place",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await page.getByLabel("Research comparison").inputValue(),
      "asset_sizing",
    );
    const data = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=asset_sizing&strategy=core_daily&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(data.status, "available");
    assert.equal(data.efficiency.candidate_count, 6);
    assert.equal(data.efficiency.rejected_count, 6);
    assert.equal(data.efficiency.all_results.length, 7);
    assert.equal(data.efficiency.asset_activation.length, 11);
    assert(Math.abs(data.metrics.net_return - 2.64581947) < 1e-6);
    assert.equal(
      await page
        .getByLabel("Strategy", { exact: true })
        .locator("option")
        .count(),
      1,
    );
    assert.equal(
      await page
        .locator(
          '[aria-label="Conditional sizing results"] .analysis-comparison-row',
        )
        .count(),
      7,
    );
    assert.equal(
      await page
        .getByRole("table", { name: "Asset condition activation" })
        .locator("tbody tr")
        .count(),
      11,
    );
    const bad = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=asset_sizing&strategy=either_reduce&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(bad.status, "unavailable");
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+264.58%", { exact: true }).first().waitFor();
    await page
      .getByText(
        "Rejected experiments · asset-specific inverse-volatility sizing",
        { exact: true },
      )
      .click();
    await page.getByText("How asset sizing works", { exact: true }).click();
    await page.screenshot({
      path: "/home/supasuge/.codex/visualizations/2026/10/19/01a151b1-467e-7561-aba1-c8d5f097afae/amat-asset-sizing.png",
      fullPage: true,
    });
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    for (const cost of ["2", "10", "6"]) {
      await page.getByLabel("Assumed trading cost per side").selectOption(cost);
      await page
        .getByRole("heading", {
          name: "Did conditional sizing improve returns?",
          exact: true,
        })
        .waitFor();
    }
    for (const period of [
      "2023",
      "stress_2018",
      "stress_covid",
      "stress_2022",
      "walk_forward",
    ]) {
      await page
        .getByLabel("Time period", { exact: true })
        .selectOption(period);
      await page
        .getByRole("heading", {
          name: "Did conditional sizing improve returns?",
          exact: true,
        })
        .waitFor();
    }
    for (const width of [1024, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
    }
    assert.deepEqual(errors, []);
    console.log(
      "Asset sizing: six rejected candidates blocked, seven-way visual audit, eleven-asset activation heatmap, cost/stress views and mobile passed",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
