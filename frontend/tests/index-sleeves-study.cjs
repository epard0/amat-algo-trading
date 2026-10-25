// Real read-only sealed research. No jobs, account mutations or orders.
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
    await page.goto(process.env.AMAT_TERMINAL_URL || "http://127.0.0.1:8765");
    await page.getByLabel("Research comparison").selectOption("indices");
    await page
      .getByRole("heading", {
        name: "Do tailored index strategies add value?",
        exact: true,
      })
      .waitFor();
    await page
      .getByText(
        "No index sleeve passed all return, drawdown and borrowing-cost checks. The existing strategies remain in place.",
        { exact: true },
      )
      .waitFor();
    assert.equal(
      await page.getByLabel("Research comparison").inputValue(),
      "indices",
    );
    assert.equal(
      await page
        .getByLabel("Strategy", { exact: true })
        .locator("option")
        .count(),
      2,
    );
    assert.equal(
      await page.getByLabel("Strategy", { exact: true }).inputValue(),
      "core_daily",
    );
    const data = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=indices&strategy=core_daily&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(data.status, "available");
    assert.equal(data.efficiency.rejected_count, 8);
    assert.equal(data.efficiency.reference_count, 2);
    assert.equal(data.efficiency.candidate_count, 8);
    assert.equal(data.efficiency.index_sleeves.borrow_rows.length, 8);
    assert.equal(data.efficiency.all_results.length, 10);
    assert.deepEqual(data.efficiency.approved, [
      "conditional_reference",
      "core_daily",
    ]);
    assert(
      data.efficiency.index_sleeves.borrow_rows.find(
        (r) => r.id === "qqq_trend",
      ).borrow_cost > 0,
    );
    const bad = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=indices&strategy=qqq_trend&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(bad.status, "unavailable");
    await page
      .getByText("Rejected experiments · index long/short sleeve audit", {
        exact: true,
      })
      .click();
    await page
      .getByRole("heading", {
        name: "Does shorting still help after borrowing costs?",
        exact: true,
      })
      .waitFor();
    assert.equal(await page.locator(".index-sleeve-audit tbody tr").count(), 8);
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+262.75%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Assumed trading cost per side").selectOption("6");
    await page.getByText("+220.32%", { exact: true }).first().waitFor();
    await page.getByLabel("Assumed trading cost per side").selectOption("10");
    await page.getByText("+187.59%", { exact: true }).first().waitFor();
    await page
      .getByLabel("Strategy", { exact: true })
      .selectOption("conditional_reference");
    await page.getByText("+200.71%", { exact: true }).first().waitFor();
    for (const period of [
      "stress_2018",
      "stress_covid",
      "stress_2022",
      "2023",
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
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `overflow ${width}`,
      );
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
    const root =
      "/home/supasuge/.codex/visualizations/2026/10/19/01a151b1-467e-7561-aba1-c8d5f097afae/";
    await page
      .locator(".analysis-primary-plot")
      .screenshot({ path: root + "amat-index-sleeves-growth.png" });
    await page
      .getByText("Rejected experiments · index long/short sleeve audit", {
        exact: true,
      })
      .click();
    await page
      .locator(".index-sleeve-audit")
      .screenshot({ path: root + "amat-index-sleeves-borrowing.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Index sleeves: 40 candidate scenarios, eight rejected, two unchanged references, borrow audit, costs/stress/year/month views and mobile passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
