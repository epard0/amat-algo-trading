// Read-only real sealed evidence; no tasks, orders or broker mutations.
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
    await p.getByLabel("Research comparison").selectOption("interaction");
    await p
      .getByRole("heading", {
        name: "Do the two improvements work better together?",
        exact: true,
      })
      .waitFor();
    await p
      .getByRole("heading", {
        name: "Keep the improvements that earn their place",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await p.getByLabel("Research comparison").inputValue(),
      "interaction",
    );
    assert.equal(
      await p.getByLabel("Strategy", { exact: true }).inputValue(),
      "all_volatility",
    );
    const data = await p.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=interaction&strategy=all_volatility&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(data.status, "available");
    assert.equal(data.efficiency.reference_count, 3);
    assert.equal(data.efficiency.candidate_count, 5);
    assert.equal(data.efficiency.rejected_count, 2);
    assert.equal(data.comparison.length, 6);
    assert.equal(data.efficiency.all_results.length, 8);
    assert.equal(data.efficiency.uncertainty_reference, "all-groups parent");
    assert.equal(
      data.efficiency.decisions.all_combined_double.preserves_2023,
      false,
    );
    assert.equal(
      await p.getByLabel("Strategy", { exact: true }).locator("option").count(),
      6,
    );
    const bad = await p.evaluate(async () =>
      (
        await fetch(
          "/api/v1/analysis/performance?study=interaction&strategy=all_combined_double&period=full&cost=6",
        )
      ).json(),
    );
    assert.equal(bad.status, "unavailable");
    await p
      .getByLabel("Strategy", { exact: true })
      .selectOption("all_volatility");
    await p.getByLabel("Time period", { exact: true }).selectOption("full");
    await p.getByText("+264.58%", { exact: true }).first().waitFor();
    await p
      .getByText("Comparison settings and source", { exact: true })
      .click();
    await p.getByLabel("Assumed trading cost per side").selectOption("2");
    await p.getByText("+290.52%", { exact: true }).first().waitFor();
    await p.getByLabel("Assumed trading cost per side").selectOption("10");
    await p.getByText("+226.81%", { exact: true }).first().waitFor();
    await p.getByLabel("Assumed trading cost per side").selectOption("6");
    await p.getByText("+264.58%", { exact: true }).first().waitFor();
    await p
      .getByText("Rejected experiments · all-groups switching combinations", {
        exact: true,
      })
      .click();
    await p
      .getByRole("heading", {
        name: "Did we preserve the 2023 improvement?",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await p.locator(".interaction-year-guard tbody tr").count(),
      8,
    );
    assert.equal(await p.locator(".conditional-gate-row").count(), 4);
    for (const period of [
      "2023",
      "stress_2018",
      "stress_covid",
      "stress_2022",
      "walk_forward",
    ]) {
      await p.getByLabel("Time period", { exact: true }).selectOption(period);
      await p
        .getByRole("heading", {
          name: "Keep the improvements that earn their place",
          exact: true,
        })
        .waitFor();
    }
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
    await p.getByLabel("Time period", { exact: true }).selectOption("full");
    await p.getByText("+264.58%", { exact: true }).first().waitFor();
    const dir =
      "/home/supasuge/.codex/visualizations/2026/10/19/01a151b1-467e-7561-aba1-c8d5f097afae/";
    await p
      .locator(".analysis-primary-plot")
      .screenshot({ path: dir + "amat-all-groups-switching-growth.png" });
    await p
      .getByText("Rejected experiments · all-groups switching combinations", {
        exact: true,
      })
      .click();
    await p
      .locator(".interaction-year-guard")
      .screenshot({ path: dir + "amat-all-groups-switching-2023.png" });
    assert.deepEqual(errors, []);
    console.log(
      "Interaction study: 24 native trials, three retained/two removed, three parents, exact cost views, 2023 guard, gate frequency, stress/year/month views and mobile passed.",
    );
  } finally {
    await b.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
