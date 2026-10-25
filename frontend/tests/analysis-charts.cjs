// Read-only chart checks against actual registered research and market data.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(process.env.AMAT_TERMINAL_URL || "http://127.0.0.1:8765");
    await page.getByLabel("Research comparison").selectOption("reference");
    await page
      .getByRole("heading", { name: "Growth over time", exact: true })
      .waitFor();
    await page
      .getByRole("heading", { name: "Strongest matching trends", exact: true })
      .waitFor();
    const chart = page.getByRole("img", { name: /Cumulative strategy growth/ });
    assert.equal(await chart.locator("path").count(), 3);
    await chart.focus();
    await page.keyboard.press("ArrowLeft");
    assert.equal(await chart.locator("circle").count(), 3);
    await page.getByLabel("Time period", { exact: true }).selectOption("2025");
    await page.getByText("+26.84%", { exact: true }).first().waitFor();
    await page.getByLabel("Strategy", { exact: true }).selectOption("B1");
    await page.getByRole("button", { name: /Daily asset selection/ }).waitFor();
    await page
      .getByLabel("Time period", { exact: true })
      .selectOption("2026_ytd");
    await page.getByText("+17.12%", { exact: true }).first().waitFor();
    await page.getByLabel("Compare with SPY and QQQ").uncheck();
    assert.equal(await chart.locator("path").count(), 2);
    await page.getByLabel("Strategy", { exact: true }).selectOption("baseline");
    await page.getByLabel("Compare with SPY and QQQ").check();
    await page.getByText("+11.39%", { exact: true }).first().waitFor();
    const rank = page.locator(".analysis-ranking button").nth(1);
    const symbol = await rank.locator("strong").textContent();
    await rank.click();
    await page
      .getByRole("heading", { name: symbol + " price history", exact: true })
      .waitFor();
    await page
      .getByRole("img", {
        name: new RegExp(symbol + " historical closing price"),
      })
      .waitFor();
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
    await page.screenshot({
      path: "/tmp/amat-visual-overview.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "Verified real-data performance, period/strategy controls, keyboard chart inspection, asset price selection, and responsive layouts.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
