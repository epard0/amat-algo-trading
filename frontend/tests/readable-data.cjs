// Isolated presentation regressions: no API, task dispatch, provider or remote access.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { chromium } = require("playwright");
const web = path.resolve(__dirname, "../web");
const { build } = createRequire(path.join(web, "package.json"))("esbuild");

(async () => {
  const fixture = await build({
    absWorkingDir: web, bundle: true, write: false, format: "iife", jsx: "automatic",
    stdin: { resolveDir: web, loader: "tsx", contents: `
      import { createRoot } from 'react-dom/client';
      import { useState, useEffect } from 'react';
      import ReadableTable from './src/ReadableTable';
      import { StructuredData } from './src/StructuredData';
      import { TaskPicker } from './src/TaskPicker';
      import { eventMessage } from './src/eventPresentation';
      import PerpsWorkspace from './src/PerpsWorkspace';
      const perpsReport = () => Promise.resolve({observed_at:'2026-10-23T17:00:00Z',accounts:[{strategy:'A perpetual strategy with a long descriptive name',state:'Awaiting completed hour',equity:12345.67,net_pnl:2345.67,net_return:.234567,max_drawdown:-.154,fees:12.34,effective_leverage:1.5}]});
      function Fixture() {
        const [clock, tick] = useState(0); useEffect(() => { const timer=setInterval(()=>tick(n=>n+1),100); return ()=>clearInterval(timer); }, []);

        const [chosen, setChosen] = useState('');
        const headers = ['Strategy', 'State', 'Net return', 'Largest decline', 'Trading costs', 'Coverage', 'Decision', 'Source'];
        return <main style={{maxWidth: '1180px', margin: 'auto', padding: '16px'}}>
          <TaskPicker label="Choose fixture task" value={chosen} onChange={setChosen} options={[
            {value: 'disabled', label: 'Unavailable fixture', disabled: true},
            {value: 'validate', label: 'Validate configuration', group: 'Local', description: 'Read the existing configuration'},
            {value: 'status', label: 'Inspect remote status', group: 'Remote'}
          ]}/><p role="status">{chosen || 'No action selected'}</p>
          <div className="table-scroll"><ReadableTable aria-label="Wide fixture comparison"><thead><tr>{headers.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
          <tr>{['A very long strategy name with meaningful qualifiers that must wrap', 'Awaiting source observation', '+12.3456%', '−15.4%', '$123,456.78', 'January 2017–October 2026', 'Retain for review with no automatic promotion', 'a'.repeat(180)].map((value,i) => <td key={i}>{value}</td>)}</tr>
          <tr>{headers.map((h,i)=><td key={h}>{i === 0 ? 'Second strategy' : 'Unavailable'}</td>)}</tr>
          </tbody></ReadableTable></div>
          <StructuredData value={[{name:'Recorded diagnostic', status:'Unavailable', observed_at:'2026-10-23T10:00:00Z', path:'/very/'.repeat(40), metadata:{long_field:'x'.repeat(180)}, id:'hidden-primary-id'}]}/>
          <p id="event-message">{eventMessage({kind:'action.succeeded',action_id:'opaque-action-id'})}</p>
          <PerpsWorkspace request={perpsReport} capabilities={[]} manage={()=>{}} />
        </main>;
      }
      createRoot(document.getElementById('root')).render(<Fixture/>);
    ` },
  });
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setContent('<!doctype html><html lang="en"><body><div id="root"></div></body></html>');
    await page.addStyleTag({ content: fs.readFileSync(path.join(web, "src/styles.css"), "utf8") });
    await page.addScriptTag({ content: fixture.outputFiles[0].text });
    await page.getByRole("combobox", { name: "Choose fixture task" }).waitFor();
    await page.getByText('A perpetual strategy with a long descriptive name', {exact:true}).waitFor();
    const measurements = [];
    for (const width of [1440, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      const measured = await page.evaluate(() => ({
        width: innerWidth,
        document: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
        wrappers: [...document.querySelectorAll('.table-scroll,.analysis-table-wrap,.structured-record')].map(el => [el.scrollWidth, el.clientWidth]),
        cellFont: getComputedStyle(document.querySelector('td')).fontSize,
        cellFamily: getComputedStyle(document.querySelector('td')).fontFamily,
        headingLabels: [...document.querySelectorAll('tbody tr:first-child td')].map(el => el.dataset.label),
      }));
      assert(measured.document[0] <= measured.document[1], `Document overflow at ${width}px`);
      assert(measured.wrappers.every(([scroll, client]) => scroll <= client + 1), `Record/table overflow at ${width}px`);
      assert.equal(measured.cellFont, '14px');
      assert(!measured.cellFamily.includes('Mono'), 'Table retained tiny monospace font');
      assert.equal(measured.headingLabels[7], 'Source');
      measurements.push(measured);
    }
    const picker = page.getByRole('combobox', {name:'Choose fixture task'});
    await picker.focus();
    await picker.press('Enter');
    await page.getByRole('status').filter({hasText:'validate'}).waitFor();
    await picker.click();
    await picker.press('ArrowDown');
    await page.waitForTimeout(350);
    await picker.press('Enter');
    assert.equal(await page.getByRole('status').innerText(), 'status', 'Parent refresh reset keyboard selection');
    await picker.click(); await picker.press('Home'); await picker.press('Enter');
    assert.equal(await page.getByRole('status').innerText(), 'validate', 'Home did not skip an unavailable option');
    await picker.fill('remote');
    await page.getByRole('option', {name:'Remote Inspect remote status'}).waitFor();
    await picker.press('Enter');
    assert.equal(await page.getByRole('status').innerText(), 'status');
    await picker.press('ArrowDown');
    await picker.press('Escape');
    assert.equal(await picker.getAttribute('aria-expanded'), 'false');
    assert.equal(await picker.inputValue(), 'Inspect remote status');
    const record = page.locator('.structured-record');
    assert(!(await record.getAttribute('open')), 'Detailed fields should start collapsed');
    await record.locator('summary').first().click();
    await record.getByText('hidden-primary-id', {exact:true}).waitFor();
    assert(!(await page.locator('#event-message').innerText()).includes('opaque-action-id'));
    const expandedOverflow = await record.evaluate(el => el.scrollWidth <= el.clientWidth + 1);
    assert(expandedOverflow, 'Expanded structured record overflow');
    await page.evaluate(() => { document.body.style.zoom = '1.25'; });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && [...document.querySelectorAll('.table-scroll,.structured-record')].every(el => el.scrollWidth <= el.clientWidth + 1)), 'Increased text/zoom caused horizontal overflow');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({passed:true, widths:measurements.map(m=>m.width), checks:['wide tables preserve all labeled values without horizontal scroll','14px sans-serif table text','keyboard picker skips unavailable choices','search and Escape preserve selection','all structured fields accessible through disclosure','event summary omits action ID'], measurements}, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
