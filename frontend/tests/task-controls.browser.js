// Explicit integration check: dispatches only inert local configuration validation.
async (page) => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  await page.goto(process.env.AMAT_TERMINAL_URL || 'http://127.0.0.1:8765/');
  await page.setViewportSize({width:1440,height:1000});
  await page.getByRole('button',{name:'Tasks',exact:true}).click();
  await page.getByText('No action selected',{exact:true}).waitFor();
  assert(await page.getByRole('button',{name:'Run action',exact:true}).isDisabled(),'Empty task can run');
  const picker=page.getByRole('combobox',{name:'Choose a task',exact:true});
  await picker.fill('amat.research.launch');await picker.press('Enter');
  await page.getByLabel('Iterations',{exact:true}).fill('0');
  await page.locator('#action-feedback [role=alert]').waitFor();
  let attempts=0;
  await page.route('**/api/v1/actions/preview',async route=>{
    if(++attempts===1)await route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({detail:'CSRF token required'})});
    else await route.continue();
  });
  await picker.fill('amat.config.validate');await picker.press('Enter');
  await page.getByText('Ready to run · inputs validated',{exact:true}).waitFor();
  await page.unroute('**/api/v1/actions/preview');assert(attempts===2,'Preview did not renew CSRF/session');
  assert(await page.getByRole('dialog').count()===0,'Old preview modal remains');
  const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/actions')&&r.status()===202);
  await page.getByRole('button',{name:'Run action',exact:true}).click();
  const accepted=await(await response).json();
  await page.getByText('Action accepted. Follow progress and output below.',{exact:true}).waitFor();
  await page.locator('.research-measured').getByText('Configuration valid:',{exact:false}).waitFor({timeout:30000});
  for(const width of [1440,1024,768,390]){
    await page.setViewportSize({width,height:1000});
    await page.waitForTimeout(100);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Task overflow at '+width);
  }
  return {passed:true,job:accepted.job_id,checks:['empty-disabled','automatic-validation','CSRF-renewal','inline-preview','inert-config-run','live-output','responsive-widths']};
}
