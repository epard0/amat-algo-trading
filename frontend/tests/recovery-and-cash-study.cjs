// Read-only real evidence, no task/order submission.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  try {
    const p = await b.newPage({ viewport: { width: 1440, height: 1100 } });
    p.setDefaultTimeout(60000);
    const errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto("http://127.0.0.1:8765");
    await p.getByLabel("Research comparison").selectOption("recovery");
    await p.getByLabel("Time period", { exact: true }).selectOption("2023");
    await p
      .getByRole("heading", { name: "Why did 2023 lose money?", exact: true })
      .waitFor();
    assert.equal(
      await p.getByLabel("Research comparison").inputValue(),
      "recovery",
    );
    assert.equal(
      await p.getByLabel("Time period", { exact: true }).inputValue(),
      "2023",
    );
    const data = await p.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=recovery&strategy=all_groups&period=2023&cost=6",
        )
      ).json(),
    );
    assert.equal(data.status, "available");
    assert.deepEqual(data.efficiency.approved, ["all_groups", "core_daily"]);
    assert.equal(data.efficiency.decisions.all_groups_spy.status, "duplicate");
    assert.equal(data.diagnostic_2023.cost_bps, 6);
    assert(
      Math.abs(
        data.diagnostic_2023.assets.reduce((n, a) => n + a.contribution, 0) -
          data.diagnostic_2023.net_return,
      ) < 1e-9,
    );
    assert.equal(await p.locator(".diagnosis-bar").count(), 11);
    assert.equal(
      await p.getByLabel("Strategy", { exact: true }).locator("option").count(),
      2,
    );
    await p.getByLabel("Strategy", { exact: true }).selectOption("all_groups");
    await p.getByText("+5.56%", { exact: true }).first().waitFor();
    await p.getByLabel("Time period", { exact: true }).selectOption("full");
    await p.getByText("+243.91%", { exact: true }).first().waitFor();
    await p
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await p.getByLabel("Assumed trading cost per side").selectOption("2");
    await p.getByText("+281.13%", { exact: true }).first().waitFor();
    await p.getByLabel("Assumed trading cost per side").selectOption("10");
    await p.getByText("+215.36%", { exact: true }).first().waitFor();
    await p.getByLabel("Research comparison").selectOption("quality_recovery");
    await p
      .getByRole("heading", {
        name: "Keep the improvements that earn their place",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await p.getByLabel("Strategy", { exact: true }).locator("option").count(),
      1,
    );
    await p.getByLabel("Research comparison").selectOption("opportunistic");
    await p
      .getByRole("heading", {
        name: "Can unused cash work harder?",
        exact: true,
      })
      .waitFor();
    await p
      .getByRole("heading", {
        name: "Keep the improvements that earn their place",
        exact: true,
      })
      .waitFor();
    const cash = await p.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=opportunistic&strategy=core_daily&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(cash.status, "available");
    assert.equal(cash.efficiency.rejected_count, 8);
    assert.equal(cash.efficiency.reference_count, 3);
    assert.equal(cash.efficiency.index_sleeves.borrow_rows.length, 8);
    assert.equal(
      await p.getByLabel("Strategy", { exact: true }).locator("option").count(),
      3,
    );
    await p
      .getByText("Rejected experiments · unused-cash netted sleeve audit", {
        exact: true,
      })
      .click();
    await p
      .getByRole("heading", {
        name: "Does shorting still help after borrowing costs?",
        exact: true,
      })
      .waitFor();
    for (const width of [1440, 1024, 390]) {
      await p.setViewportSize({ width, height: 1100 });
      assert(
        await p.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `overflow ${width}`,
      );
    }
    await p.setViewportSize({ width: 1440, height: 1100 });
    await p.getByLabel("Research comparison").selectOption("recovery");
    await p
      .getByRole("heading", { name: "Why did 2023 lose money?", exact: true })
      .waitFor();
    const dir =
      "/home/supasuge/.codex/visualizations/2026/10/19/01a151b1-467e-7561-aba1-c8d5f097afae/";
    await p
      .locator(".loss-year-diagnosis")
      .screenshot({ path: dir + "amat-2023-loss-diagnosis.png" });
    await p.getByLabel("Strategy", { exact: true }).selectOption("all_groups");
    await p.getByLabel("Time period", { exact: true }).selectOption("2023");
    await p.getByText("+5.56%", { exact: true }).first().waitFor();
    await p
      .locator(".analysis-primary-plot")
      .screenshot({ path: dir + "amat-2023-recovery-comparison.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Recovery: 2023 native attribution reconciles, one canonical improvement, duplicate excluded, failed quality/cash sleeves hidden, cost controls and mobile passed.",
    );
  } finally {
    await b.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
