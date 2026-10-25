// Read-only UI coverage. Uses an installed Playwright module and the running local terminal.
// Engine lifecycle and task execution audits live in separate, explicitly invoked scripts.
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const source = fs.readFileSync(
      path.join(__dirname, "audit-ui.browser.js"),
      "utf8",
    );
    const audit = new Function("return (" + source + ")")();
    const result = await audit(page);
    if (errors.length) throw new Error(errors.join("\n"));
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
