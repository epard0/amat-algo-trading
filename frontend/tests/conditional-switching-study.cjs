// Read-only UI checks against sealed measured evidence; never submit a task/order.
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
    await page.getByLabel("Research comparison").selectOption("conditional");
    await page
      .getByRole("heading", {
        name: "When does smarter switching help?",
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
      "conditional",
    );
    assert.equal(
      await page.getByLabel("Strategy", { exact: true }).inputValue(),
      "core_daily",
    );
    const data = await page.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=conditional&strategy=core_daily&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(data.status, "available");
    assert.equal(data.efficiency.all_results.length, 9);
    assert.equal(data.efficiency.condition_summary.length, 8);
    assert.equal(
      data.efficiency.approved.length + data.efficiency.rejected_count,
      9,
    );
    assert.equal(
      await page
        .getByLabel("Strategy", { exact: true })
        .locator("option")
        .count(),
      data.efficiency.approved.length,
    );
    assert.equal(data.efficiency.yearly.length, 7);
    const rejected = data.efficiency.all_results.find(
      (r) => r.status === "rejected",
    );
    if (rejected) {
      const bad = await page.evaluate(
        async (id) =>
          (
            await fetch(
              `/api/v1/analysis/performance?study=conditional&strategy=${id}&period=full&cost=6`,
            )
          ).json(),
        rejected.id,
      );
      assert.equal(bad.status, "unavailable");
    }
    await page
      .getByText("Rejected experiments · conditional switching audit", {
        exact: true,
      })
      .click();
    assert.equal(await page.locator(".conditional-gate-row").count(), 8);
    await page
      .getByRole("heading", {
        name: "How often did market conditions call for caution?",
        exact: true,
      })
      .waitFor();
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+262.75%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Assumed trading cost per side").selectOption("6");
    await page.getByText("+220.32%", { exact: true }).first().waitFor();
    await page.getByLabel("Assumed trading cost per side").selectOption("10");
    await page.getByText("+187.59%", { exact: true }).first().waitFor();
    const retained = data.efficiency.approved.find((id) => id !== "core_daily");
    if (retained) {
      await page.getByLabel("Strategy", { exact: true }).selectOption(retained);
      await page
        .getByRole("heading", {
          name: "Keep the improvements that earn their place",
          exact: true,
        })
        .waitFor();
      await page.waitForFunction(
        () => document.querySelectorAll(".analysis-comparison-row").length > 1,
      );
    }
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
      .screenshot({ path: root + "amat-conditional-switching-growth.png" });
    await page
      .locator(".validation-details")
      .screenshot({ path: root + "amat-conditional-switching-results.png" });
    assert.deepEqual(errors, []);
    console.log(
      `Conditional study: 27 trials, ${data.efficiency.approved.length - 1} retained, ${data.efficiency.rejected_count} removed; default, costs, yearly/stress/monthly, activation chart and mobile passed.`,
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
