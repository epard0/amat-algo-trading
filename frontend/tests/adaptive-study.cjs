// Actual registered adaptive study; this check makes no action requests.
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
    await page.getByLabel("Research comparison").selectOption("adaptive");
    await page
      .getByRole("heading", {
        name: "Does adaptive rotation improve the results?",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", { name: "Growth over time", exact: true })
      .waitFor();
    assert.equal(
      await page.getByLabel("Research comparison").inputValue(),
      "adaptive",
    );
    const chart = page.getByRole("img", { name: /Cumulative strategy growth/ });
    assert.equal(
      await chart.locator("path").count(),
      5,
      "Combined, v13, daily selection and two benchmarks",
    );
    assert.equal(await page.locator(".analysis-comparison-row").count(), 10);
    assert.equal(
      await page.locator(".analysis-cost-table tbody tr").count(),
      10,
    );
    await page.getByLabel("Strategy", { exact: true }).selectOption("groups");
    await page.getByText("+27.92%", { exact: true }).first().waitFor();
    await page.getByLabel("Time period", { exact: true }).selectOption("2025");
    await page
      .getByRole("heading", { name: "Growth over time", exact: true })
      .waitFor();
    await page
      .getByLabel("Time period", { exact: true })
      .selectOption("2026_ytd");
    await page.getByText("+27.92%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await page.getByLabel("Assumed trading cost per side").selectOption("10");
    await page
      .getByRole("heading", { name: "Growth over time", exact: true })
      .waitFor();
    const selected = await page
      .getByLabel("Assumed trading cost per side")
      .inputValue();
    assert.equal(selected, "10");
    await page.getByLabel("Assumed trading cost per side").selectOption("2");
    await page.getByText("+27.92%", { exact: true }).first().waitFor();
    await page
      .getByText("Comparison settings and source", { exact: true })
      .click();
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
      .screenshot({ path: "/tmp/amat-adaptive-study.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Adaptive study: all arms/costs, reference curves, controls and responsive layout passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
