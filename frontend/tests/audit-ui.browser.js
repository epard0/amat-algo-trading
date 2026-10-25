async (page) => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  await page.goto(process.env.AMAT_TERMINAL_URL || "http://127.0.0.1:8765/");
  await page.setViewportSize({ width: 1440, height: 1000 });
  const tabs = [
    "Markets",
    "Performance",
    "Kalshi perps",
    "Tasks",
    "Operations",
    "Remote",
  ];
  assert(
    JSON.stringify(await page.locator("nav button").allTextContents()) ===
      JSON.stringify(tabs),
    "Unexpected navigation",
  );
  await page.getByRole("button", { name: "Markets", exact: true }).click();
  await page.locator(".chart svg").waitFor();
  const bars = [];
  for (const limit of ["60", "120", "250"]) {
    await page
      .getByRole("button", { name: limit + " bars", exact: true })
      .click();
    await page.getByText(limit + " historical bars", { exact: true }).waitFor();
    bars.push(limit);
  }
  const refreshedBars = page.waitForResponse(r => r.url().includes('/api/v1/market/bars?'));
  await page.getByRole('button',{name:'Refresh data',exact:true}).click();
  assert((await refreshedBars).status() === 200, 'Market refresh did not reload candles');
  const volume = page.getByRole("button", { name: /Volume/ });
  await volume.click();
  assert(
    (await volume.getAttribute("aria-pressed")) === "false",
    "Volume did not hide",
  );
  await volume.click();
  const resolutions = [];
  for (const resolution of ["1d", "1w", "5m"]) {
    const response = page.waitForResponse(
      (r) =>
        r.url().includes("/api/v1/market/bars?") &&
        new URL(r.url()).searchParams.get("resolution") === resolution,
    );
    await page.getByLabel("Bar resolution").selectOption(resolution);
    const d = await (await response).json();
    assert(d.items?.length > 1, "Missing " + resolution + " bars");
    resolutions.push(resolution);
  }
  await page.getByLabel("Chart symbol").fill("QQQ");
  await page
    .locator(".price-panel .panel-title")
    .getByText("QQQ", { exact: false })
    .waitFor();
  for (const name of ["Orders", "Runs", "Positions"]) {
    await page
      .locator(".holdings .tabs button")
      .filter({ hasText: name })
      .click();
  }
  await page
    .locator(".holdings .tabs button")
    .filter({ hasText: "Runs" })
    .click();
  const row = page.locator(".holdings tbody tr").first();
  await row.click();
  await page
    .getByRole("button", { name: "Close details", exact: true })
    .click();
  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByLabel("Search task activity").fill("does-not-match-anything");
  assert(
    (await page.locator(".research-feed").innerText()).includes(
      "No matching events",
    ),
    "Activity search failed",
  );
  await page.getByLabel("Search task activity").fill("");
  await page
    .getByRole("checkbox", { name: "Errors only", exact: true })
    .check();
  await page
    .getByRole("checkbox", { name: "Errors only", exact: true })
    .uncheck();
  await page.getByRole("button", { name: "Run details", exact: true }).click();
  await page
    .getByRole("button", { name: "Close details", exact: true })
    .click();
  assert(!(await page.locator('main').innerText()).includes('REGISTERED EVIDENCE'),'Workspace evidence clutter remains');
  const evidence = [];
  await page.getByRole("button", { name: "Operations", exact: true }).click();
  await page
    .getByText("Historical data sources & snapshots", { exact: true })
    .click();
  const snapshots = page.getByLabel("Market snapshot");
  const options = await snapshots
    .locator("option")
    .evaluateAll((es) => es.map((e) => e.value));
  for (const value of options) await snapshots.selectOption(value);
  const snapshotRecord = page.locator('.snapshot-record').first();
  await snapshotRecord.locator('summary').first().click();
  assert((await snapshotRecord.innerText()).includes('Historical data'),'Snapshot summary missing');
  await page.getByText("Workspace preferences", { exact: true }).click();
  const consoleToggle = page.getByRole("checkbox", {
    name: "Show event console",
    exact: true,
  });
  await consoleToggle.check();
  assert(
    !(await page.locator(".event-console").getAttribute("class")).includes(
      "collapsed",
    ),
    "Console did not expand",
  );
  await consoleToggle.uncheck();
  await page.getByRole("button", { name: "Reset layout", exact: true }).click();
  assert(
    (await page.getByLabel("Chart symbol").inputValue()) === "SPY",
    "Reset symbol failed",
  );
  assert(
    (await page.getByLabel("Bar resolution").inputValue()) === "5m",
    "Reset resolution failed",
  );
  await page
    .getByRole("button", { name: "Operator guide", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Close details", exact: true })
    .click();
  for (let n = 1; n <= 6; n++) {
    await page.keyboard.press("Alt+" + n);
    assert(
      (await page.locator("h1").innerText()) ===
        (tabs[n - 1] === "Markets" ? "Market overview" : tabs[n - 1]),
      "Keyboard shortcut failed",
    );
  }
  await page.keyboard.press("Alt+7");
  assert(
    (await page.locator("h1").innerText()) === "Remote",
    "Unsupported shortcut changed tab",
  );
  await page.getByRole("link", { name: "AMAT terminal", exact: true }).click();
  assert(
    (await page.locator("h1").innerText()) === "Market overview",
    "Brand home link failed",
  );
  await page.getByRole("button", { name: "Kalshi perps", exact: true }).click();
  await page
    .getByRole("button", { name: "Manage remote engine", exact: true })
    .click();
  const diagnostics = [];
  await page.getByText('Automatic monitoring', {exact:true}).waitFor();
  const mobile = [];
  await page.setViewportSize({ width: 390, height: 844 });
  for (const name of tabs) {
    await page.getByRole("button", { name, exact: true }).click();
    await page.waitForTimeout(150);
    mobile.push({
      name,
      ...(await page.evaluate(() => ({
        width: innerWidth,
        scroll: document.documentElement.scrollWidth,
      }))),
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  return {
    tabs,
    bars,
    resolutions,
    evidence,
    snapshots: options.length,
    diagnostics,
    mobile,
  };
}
