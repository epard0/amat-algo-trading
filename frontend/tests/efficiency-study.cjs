// Actual sealed study and read-only defaults; never dispatches an engine/broker action.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(process.env.AMAT_TERMINAL_URL || "http://127.0.0.1:8765");
    await page.getByLabel("Research comparison").selectOption("efficiency");
    await page
      .getByRole("heading", {
        name: "Can we trade less and protect the right groups?",
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
      "efficiency",
    );
    assert.equal(
      await page.getByLabel("Strategy", { exact: true }).inputValue(),
      "core_daily",
    );
    assert.equal(
      await page
        .getByLabel("Strategy", { exact: true })
        .locator("option")
        .count(),
      1,
    );
    assert.equal(await page.locator(".analysis-comparison-row").count(), 1);
    await page
      .getByText(
        "None of the eight changes passed every return and drawdown check. Daily core groups remains the default.",
        { exact: true },
      )
      .waitFor();
    await page
      .getByText(
        "Rejected experiments · trade reduction and group-risk audit",
        { exact: true },
      )
      .click();
    const audit = page.locator("details").filter({
      has: page.getByText(
        "Rejected experiments · trade reduction and group-risk audit",
        { exact: true },
      ),
    });
    assert.equal(
      await audit.locator("table").nth(0).locator("tbody tr").count(),
      9,
    );
    assert.equal(
      await audit.locator("table").nth(2).locator("tbody tr").count(),
      8,
    );
    const response = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=efficiency&strategy=core_daily&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(response.status, "available");
    assert.deepEqual(response.efficiency.approved, ["core_daily"]);
    assert.equal(response.efficiency.rejected_count, 8);
    assert(
      response.efficiency.all_results.find((r) => r.id === "band3").fills <
        response.metrics.fills,
    );
    assert.equal(response.efficiency.yearly.length, 7);
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+262.75%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Assumed trading cost per side").selectOption("6");
    await page.getByText("+220.32%", { exact: true }).first().waitFor();
    await page.getByLabel("Assumed trading cost per side").selectOption("10");
    await page.getByText("+187.59%", { exact: true }).first().waitFor();
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
    await page.getByLabel("Assumed trading cost per side").selectOption("2");
    await page.getByText("+197.87%", { exact: true }).first().waitFor();
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `Overflow ${width}`,
      );
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page.getByRole("button", { name: /^Find assets/ }).click();
    const setup = page.getByRole("region", { name: "Task setup" });
    await setup.getByText("Change settings", { exact: false }).click();
    assert.equal(
      await setup.getByLabel("Strategy", { exact: true }).inputValue(),
      "amat_group_selection_daily.toml",
    );
    assert.equal(await setup.getByLabel("Past trading days").count(), 0);
    await page
      .getByRole("button", { name: "Close task", exact: false })
      .click();
    const root =
      "/home/supasuge/.codex/visualizations/2026/10/19/01a151b1-467e-7561-aba1-c8d5f097afae/";
    await page
      .locator(".analysis-primary-plot")
      .screenshot({ path: root + "amat-efficiency-growth.png" });
    await page
      .locator(".validation-details")
      .screenshot({ path: root + "amat-efficiency-results.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Efficiency study: 27 trials, eight rejected, current best only, cost/year/stress/month views, guided default and mobile passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
