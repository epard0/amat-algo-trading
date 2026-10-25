async (page) => {
  await page.goto("http://127.0.0.1:8765/");
  await page.getByRole("button", { name: "Performance", exact: true }).click();
  const studies = await page
    .getByLabel("Research comparison", { exact: true })
    .locator("option")
    .evaluateAll((es) =>
      es.map((e) => ({ value: e.value, title: e.textContent })),
    );
  const results = [];
  for (const study of studies) {
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/analysis/performance?") &&
        new URL(r.url()).searchParams.get("study") === study.value,
    );
    if (
      (await page
        .getByLabel("Research comparison", { exact: true })
        .inputValue()) === study.value
    )
      await page
        .getByRole("button", { name: "Refresh data", exact: true })
        .click();
    else
      await page
        .getByLabel("Research comparison", { exact: true })
        .selectOption(study.value);
    const r = await response;
    const d = await r.json();
    await page.getByLabel("Time period", { exact: true }).waitFor();
    await page.locator(".analysis-loading").waitFor({ state: "hidden" });
    const buttons = await page.locator("main button").count();
    for (let i = 0; i < buttons; i++) {
      const b = page.locator("main button").nth(i);
      if ((await b.isVisible()) && (await b.isEnabled())) await b.click();
    }
    results.push({
      study: study.value,
      status: r.status(),
      sourceStatus: d.status,
      buttons,
    });
  }
  await page
    .getByLabel("Research comparison", { exact: true })
    .selectOption("validation");
  const periods = await page
    .getByLabel("Time period", { exact: true })
    .locator("option")
    .evaluateAll((es) => es.map((e) => e.value));
  for (const value of periods) {
    await page.getByLabel("Time period", { exact: true }).selectOption(value);
    await page.locator(".analysis-loading").waitFor({ state: "hidden" });
  }
  const strategy = page.getByLabel("Strategy", { exact: true });
  const strategies = await strategy
    .locator("option")
    .evaluateAll((es) => es.map((e) => e.value));
  for (const value of strategies) await strategy.selectOption(value);
  const benchmark = page.getByRole("checkbox");
  if (await benchmark.count()) {
    await benchmark.first().uncheck();
    await benchmark.first().check();
  }
  await page
    .getByText("Comparison settings and source", { exact: true })
    .click();
  const cost = page.getByLabel("Assumed trading cost per side");
  if (await cost.count())
    for (const value of ["2", "6", "10"]) await cost.selectOption(value);
  return { results, periods: periods.length, strategies: strategies.length };
}
