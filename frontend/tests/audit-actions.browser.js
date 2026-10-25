// Manual integration run: executes bounded analysis jobs, never broker orders.
async (page) => {
  await page.goto("http://127.0.0.1:8765/");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Tasks", exact: true }).click();
  const actions = [
    "amat.config.validate",
    "amat.config.show",
    "amat.config.universe",
    "amat.status",
    "amat.active",
    "amat.backtest.synthetic",
    "amat.scan",
    "amat.backtest",
    "amat.walkforward",
    "amat.montecarlo",
    "amat.research.launch",
  ];
  const results = [];
  for (const capability of actions) {
    await page.getByRole("combobox", {name:"Choose a task",exact:true}).fill(capability);
    await page.getByRole("combobox", {name:"Choose a task",exact:true}).press("Enter");
    if (capability === "amat.backtest.synthetic")
      await page.getByLabel("Sessions", { exact: true }).fill("300");
    if (capability === "amat.montecarlo") {
      await page.getByLabel("Paths", { exact: true }).fill("100");
      await page.getByLabel("Start", { exact: true }).fill("2023-01-01");
      await page.getByLabel("End", { exact: true }).fill("2023-12-31");
    }
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/v1/actions") && r.status() === 202,
    );
    await page
      .getByRole("button", { name: "Run action", exact: true })
      .click();
    const accepted = await (await response).json();
    let job;
    for (let attempt = 0; attempt < 180; attempt++) {
      const response = await page.request.get(
        "http://127.0.0.1:8765/api/v1/jobs/" + accepted.job_id,
      );
      job = await response.json();
      if (
        job.id === accepted.job_id &&
        [
          "succeeded",
          "failed",
          "refused",
          "cancelled",
          "outcome_unknown",
        ].includes(job.status)
      )
        break;
      await page.waitForTimeout(1000);
    }
    if (job.status !== "succeeded")
      throw new Error(
        `${capability}: ${job.status} · ${job.reason || job.output?.slice(-500)}`,
      );
    results.push({
      capability,
      id: accepted.job_id,
      status: job.status,
      reason: job.reason,
      output:
        job.status === "succeeded"
          ? job.output?.slice(-160)
          : job.output?.slice(-1300),
    });
    const close = page.getByRole("button", {
      name: "Close details",
      exact: true,
    });
    if (await close.count()) await close.click();
  }
  return results;
}
