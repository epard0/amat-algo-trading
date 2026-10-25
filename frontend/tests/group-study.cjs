// Uses all completed registered runs; never submits a research or broker action.
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
    await page.getByLabel("Research comparison").selectOption("groups");
    await page
      .getByRole("heading", {
        name: "What improves group selection?",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", { name: "Which combination helps?", exact: true })
      .waitFor();
    assert.equal(
      await page.getByLabel("Research comparison").inputValue(),
      "groups",
    );
    assert.equal(
      await page.getByLabel("Time period", { exact: true }).inputValue(),
      "full",
    );
    assert.equal(await page.locator(".analysis-factor-row button").count(), 8);
    assert.equal(await page.locator(".analysis-comparison-row").count(), 12);
    assert.equal(
      await page.locator(".analysis-cost-table tbody tr").count(),
      12,
    );
    const chart = page.getByRole("img", { name: /Cumulative strategy growth/ });
    assert.equal(await chart.locator("path").count(), 6);
    await page
      .locator(".analysis-factor-row button")
      .filter({ hasText: "+100.15%" })
      .click();
    await page
      .getByLabel("Strategy", { exact: true })
      .filter({ hasText: "Groups + daily · core ETFs" })
      .waitFor();
    assert.equal(
      await page.getByLabel("Strategy", { exact: true }).inputValue(),
      "core_daily",
    );
    await page
      .getByLabel("Time period", { exact: true })
      .selectOption("2026_ytd");
    await page.getByText("+25.36%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Assumed trading cost per side").selectOption("10");
    await page
      .getByRole("heading", { name: "Growth over time", exact: true })
      .waitFor();
    assert(await page.getByLabel("Assumed trading cost per side").isVisible());
    await page.getByLabel("Assumed trading cost per side").selectOption("2");
    await page.getByText("+25.36%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Time period", { exact: true }).selectOption("full");
    await page.getByText("+100.15%", { exact: true }).first().waitFor();
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
      .getByRole("region", { name: "Strategy analysis" })
      .screenshot({ path: "/tmp/amat-group-study.png" });
    await page
      .locator(".analysis-primary-plot")
      .screenshot({ path: "/tmp/amat-group-growth.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Group study: eight combinations, twelve approaches, three costs, common period, reference curves, and responsive layout passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
