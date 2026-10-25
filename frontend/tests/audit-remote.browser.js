// Manual paper-only lifecycle integration test. Stops and restarts both VPS containers.
// Run only during an authorized operations audit; verify final healthy states.
async (page) => {
  await page.goto("http://127.0.0.1:8765/");
  await page.setViewportSize({ width: 1440, height: 1000 });
  const capabilities = (
    await (
      await page.request.get("http://127.0.0.1:8765/api/v1/capabilities")
    ).json()
  ).items;
  const steps = [
    "perps.stop",
    "perps.start",
    "perps.restart",
    "perps.status",
    "vps.executor.stop",
    "vps.executor.start",
    "vps.executor.restart",
    "vps.executor.status",
  ];
  const results = [];
  for (const id of steps) {
    await page.getByRole("button", { name: "Remote", exact: true }).click();
    const cap = capabilities.find((c) => c.id === id);
    await page.getByRole("button", { name: cap.title, exact: true }).click();
    if ((await page.getByRole("combobox", {name:"Choose a task",exact:true}).inputValue()) !== cap.title)
      throw new Error("Remote button selected wrong capability");
    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/v1/actions") && r.status() === 202,
    );
    await page
      .getByRole("button", { name: "Run action", exact: true })
      .click();
    const a = await (await response).json();
    let job;
    for (let n = 0; n < 90; n++) {
      job = await (
        await page.request.get("http://127.0.0.1:8765/api/v1/jobs/" + a.job_id)
      ).json();
      if (
        ["succeeded", "failed", "refused", "outcome_unknown"].includes(
          job.status,
        )
      )
        break;
      await page.waitForTimeout(1000);
    }
    results.push({
      capability: id,
      id: a.job_id,
      status: job.status,
      reason: job.reason,
      output: job.output?.slice(-400),
    });
    const close = page.getByRole("button", {
      name: "Close details",
      exact: true,
    });
    if (await close.count()) await close.click();
    if (job.status !== "succeeded") throw new Error(JSON.stringify(results));
  }
  await page.getByRole("button", { name: "Remote", exact: true }).click();
  let engines;
  for (let n = 0; n < 50; n++) {
    engines = await (
      await page.request.get("http://127.0.0.1:8765/api/v1/remote/engines")
    ).json();
    if (
      engines.perps?.deployment?.health === "healthy" &&
      engines.executor?.deployment?.health === "healthy"
    )
      break;
    await page.waitForTimeout(1000);
  }
  return {
    results,
    final: {
      transport: engines.transport,
      perps: engines.perps?.deployment,
      executor: engines.executor?.deployment,
      executorReadiness: engines.executor?.readiness?.ready_for_orders,
    },
  };
}
