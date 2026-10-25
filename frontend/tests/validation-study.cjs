// Read-only checks against completed, sealed long-history research evidence.
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
    await page.getByLabel("Research comparison").selectOption("validation");
    await page
      .getByRole("heading", {
        name: "How resilient are daily groups?",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", { name: "What the tests tell us", exact: true })
      .waitFor();
    assert.equal(
      await page.getByLabel("Research comparison").inputValue(),
      "validation",
    );
    assert.equal(
      await page.getByLabel("Time period", { exact: true }).inputValue(),
      "walk_forward",
    );
    assert.equal(await page.locator(".analysis-comparison-row").count(), 3);
    assert.equal(
      await page.locator(".validation-month-buttons button").count(),
      82,
    );
    assert.equal(
      await page.locator(".validation-stress-grid article").count(),
      3,
    );
    const chart = page.getByRole("img", { name: /Cumulative strategy growth/ });
    assert.equal(await chart.locator("path").count(), 5);
    await page.getByText("+197.87%", { exact: true }).first().waitFor();
    await page
      .getByRole("button", { name: "2020-09 -6.6%", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: "2020-09 · detailed comparison",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await page.locator(".validation-market-context > div").count(),
      11,
    );
    await page
      .getByText("Rejected experiments · recovery and ML audit", {
        exact: true,
      })
      .click();
    await page
      .getByText("Exit indicator accuracy and recovery history", {
        exact: true,
      })
      .click();
    await page
      .getByRole("img", {
        name: /Walk-forward ML adverse-condition probability/,
      })
      .waitFor();
    const api = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=validation&strategy=core_daily&period=walk_forward&cost=2",
        )
      ).json(),
    );
    assert.equal(api.status, "available");
    assert.equal(api.validation.folds.length, 7);
    assert(
      api.validation.folds.every((f) => f.last_label_close < f.test_start),
    );
    const all = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=validation&strategy=core_daily&period=full&cost=2",
        )
      ).json(),
    );
    assert.equal(all.validation.months.length, 118);
    for (const period of [
      "stress_2018",
      "stress_covid",
      "stress_2022",
      "2017",
      "2023",
    ]) {
      await page
        .getByLabel("Time period", { exact: true })
        .selectOption(period);
      await page
        .getByRole("heading", { name: "What the tests tell us", exact: true })
        .waitFor();
      assert((await chart.locator("path").count()) >= 4);
    }
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+262.75%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Assumed trading cost per side").selectOption("10");
    await page.getByText("+187.59%", { exact: true }).first().waitFor();
    await page.getByLabel("Assumed trading cost per side").selectOption("2");
    await page.getByText("+262.75%", { exact: true }).first().waitFor();
    assert.deepEqual(
      await page
        .getByLabel("Strategy", { exact: true })
        .locator("option")
        .evaluateAll((es) => es.map((e) => e.value)),
      ["baseline", "B1", "core_daily"],
    );
    const capabilities = await page.evaluate(async () =>
      (await fetch("/api/v1/capabilities")).json(),
    );
    const serialized = JSON.stringify(capabilities);
    assert(!serialized.includes("amat_v14_adaptive_rotation.toml"));
    assert(!serialized.includes("amat_group_selection_daily_quality.toml"));
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({ width, height: 1100 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `Overflow at ${width}`,
      );
    }
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page
      .locator(".analysis-primary-plot")
      .screenshot({ path: "/tmp/amat-validation-growth.png" });
    await page
      .locator(".validation-details")
      .screenshot({ path: "/tmp/amat-validation-details.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Long-history validation: 27 records, seven purged folds, 118 months, stress periods, cost changes and responsive charts passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
