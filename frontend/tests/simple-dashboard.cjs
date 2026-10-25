// UI checks use the real read-only API. Action responses below are intercepted;
// no engine command, broker submission or notification is dispatched.
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
    await page.goto(process.env.AMAT_TERMINAL_URL || "http://127.0.0.1:5173");
    await page.getByRole("button", { name: "Simple view", exact: true }).click();
    await page
      .getByRole("heading", { name: "Your overview", exact: true })
      .waitFor();
    await page
      .getByText("Showing a saved account record", { exact: true })
      .waitFor();
    assert.equal(
      await page.locator('nav[aria-label="Main navigation"] button').count(),
      3,
    );
    assert.equal(
      await page.getByText("LIVE ACTIVITY", { exact: true }).count(),
      0,
    );
    await page
      .getByRole("button", { name: "Investments", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "What you hold", exact: true })
      .waitFor();
    await page
      .getByText("No investments were listed in this account record.", {
        exact: true,
      })
      .waitFor();
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page.getByRole("button", { name: /^Find assets/ }).click();
    assert.equal(
      await page.getByLabel("Saved market dataset").isVisible(),
      false,
    );
    await page.getByText("Change settings", { exact: false }).click();
    await page.getByLabel("Number of assets to show").fill("5");
    const selectedConfig = await page
      .getByLabel("Strategy", { exact: true })
      .inputValue();
    const calls = [];
    const replies = async (route) => {
      const body = route.request().postDataJSON();
      calls.push({ url: route.request().url(), body });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          route.request().url().endsWith("/preview")
            ? { preview_id: "ui-preview", effects: ["Read saved market data"] }
            : { job_id: "ui-only-test", status: "queued" },
        ),
      });
    };
    await page.route("**/api/v1/actions/preview", replies);
    await page.route("**/api/v1/actions", replies);
    await page.route("**/api/v1/jobs/ui-only-test", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "ui-only-test",
          capability: "amat.scan",
          status: "succeeded",
          output: JSON.stringify({
            session: "2026-10-12",
            assets: [{ symbol: "SPY", rank: 1, selected: true }],
          }),
        }),
      }),
    );
    await page
      .getByRole("button", { name: "Review task", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Ready to start", exact: true })
      .waitFor();
    assert.equal(calls.length, 1, "Review must never dispatch");
    assert.equal(calls[0].body.inputs.top, 5);
    assert.equal(calls[0].body.inputs.config, selectedConfig);
    await page.getByRole("button", { name: "Start task", exact: true }).click();
    await page
      .getByRole("heading", { name: "Completed", exact: true })
      .waitFor();
    assert.equal(calls.length, 2);
    assert.equal(calls[1].body.preview_id, "ui-preview");
    assert.ok(calls[1].body.idempotency_key);
    assert.equal(calls[1].body.capability, "amat.scan");
    await page.getByRole("cell", { name: "SPY", exact: true }).waitFor();
    assert.equal(
      await page.locator(".simple-result pre").isVisible(),
      false,
      "Raw output is collapsed",
    );
    // An uncertain network outcome must reuse the original request identity.
    await page.getByRole("button", { name: "Close task", exact: true }).click();
    await page.getByRole("button", { name: /^Find assets/ }).click();
    let attempts = [];
    await page.route("**/api/v1/actions", async (route) => {
      attempts.push(route.request().postDataJSON());
      await route.fulfill({
        status: attempts.length === 1 ? 503 : 200,
        contentType: "application/json",
        body: JSON.stringify(
          attempts.length === 1
            ? { detail: "Test network interruption" }
            : { job_id: "ui-only-test", status: "queued" },
        ),
      });
    });
    await page
      .getByRole("button", { name: "Review task", exact: true })
      .click();
    await page.getByRole("button", { name: "Start task", exact: true }).click();
    await page
      .getByRole("alert")
      .filter({ hasText: "We couldn’t confirm" })
      .waitFor();
    await page.getByRole("button", { name: "Start task", exact: true }).click();
    await page
      .getByRole("heading", { name: "Completed", exact: true })
      .waitFor();
    assert.equal(attempts.length, 2);
    assert.equal(
      attempts[0].idempotency_key,
      attempts[1].idempotency_key,
      "Uncertain request retry must not duplicate a task",
    );
    await page.getByRole("button", { name: "Activity", exact: true }).click();
    await page
      .getByRole("heading", { name: "Your tasks", exact: true })
      .waitFor();
    await page.getByRole("button", { name: "Overview", exact: true }).click();
    await page.getByRole("button", { name: "Close task", exact: true }).click();
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(
        await page.evaluate(() => document.body.scrollWidth > innerWidth),
        false,
        `No overflow at ${width}px`,
      );
    }
    await page.screenshot({
      path: process.env.AMAT_MOBILE_SCREENSHOT || "/tmp/amat-simple-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1100 });
    await page
      .getByRole("button", { name: "Advanced view", exact: true })
      .click();
    await page.getByRole("button", { name: "Research", exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Simple view", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Your overview", exact: true })
      .waitFor();
    await page.screenshot({
      path: process.env.AMAT_SCREENSHOT || "/tmp/amat-simple-desktop.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "Simple dashboard: navigation, saved-data labeling, collapsed inputs, reviewed action, readable results, mobile layout and advanced-view switch passed. No real actions dispatched.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
