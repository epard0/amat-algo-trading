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
    await page.getByLabel("Research comparison").selectOption("volatility_next");
    await page
      .getByRole("heading", {
        name: "Five ways to improve the current best",
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
      "volatility_next",
    );
    assert.equal(
      await page.getByLabel("Strategy", { exact: true }).inputValue(),
      "core_daily",
    );
    const data = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=volatility_next&strategy=core_daily&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(data.status, "available");
    assert.equal(data.efficiency.candidate_count, 5);
    assert.equal(data.efficiency.all_results.length, 6);
    assert.match(data.efficiency.current_default, /volatility/);
    assert(Math.abs(data.metrics.net_return - 2.64581947) < 1e-6);
    assert.equal(data.efficiency.rejected_count, 5);
    assert.equal(
      await page
        .getByLabel("Strategy", { exact: true })
        .locator("option")
        .count(),
      1,
    );
    const rejected = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=volatility_next&strategy=wider_band&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(rejected.status, "unavailable");
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+264.58%", { exact: true }).first().waitFor();
    await page
      .getByText(
        "Rejected experiments · five improvements to the current best",
        { exact: true },
      )
      .click();
    for (const label of [
      "Wider rebalance band · 3%",
      "Two-day replacement confirmation",
      "Lower single-asset cap · 40%",
      "Inverse-volatility allocation",
    ])
      await page.getByText(label, { exact: true }).first().waitFor();
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
          name: "Keep the improvements that earn their place",
          exact: true,
        })
        .waitFor();
    }
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+264.58%", { exact: true }).first().waitFor();
    await page.screenshot({
      path: "/home/supasuge/.codex/visualizations/2026/10/19/01a151b1-467e-7561-aba1-c8d5f097afae/amat-volatility-next.png",
      fullPage: true,
    });
    for (const width of [1024, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      );
    }
    assert.deepEqual(errors, []);
    console.log("Volatility default and five-candidate dashboard passed");
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
