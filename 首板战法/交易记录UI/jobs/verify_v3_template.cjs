/* Review the template example and the live UI without writing trading inputs. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises'),assert=require('node:assert/strict');
const out=process.env.V3_REVIEW_DIR||'/Users/admin/stock_quant_select/workspace/reviews/交易看板取值上线';
const url=process.env.V3_URL||'http://127.0.0.1:5052';
(async()=>{
  const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
  try{
    await fs.mkdir(out,{recursive:true});
    const errors=[];
    const page=await browser.newPage({viewport:{width:1760,height:1050}});
    page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(60000);
    const fixture=JSON.parse(await fs.readFile(out+'/模板UI核验数据.json','utf8'));
    await page.route('**/api/v3/ledger?**',async route=>route.fulfill({json:{success:true,data:fixture}}));
    const ready=()=>page.waitForFunction(()=>document.getElementById('v3Status')?.textContent==='数据已加载');
    await page.goto(url);await ready();
    const wb=page.locator('#v3App .tablewrap tbody tr').filter({hasText:'万邦医药'}).first();
    assert.match(await wb.locator('td').first().innerText(),/2026-11-02/);
    for(const [i,want] of [[5,'40,000.00'],[7,'20,000.00'],[8,'18,516.00'],[9,'1,484.00'],[11,'-424.02'],[12,'-142.71'],[13,'-82.84'],[15,'-848.03'],[16,'-570.84'],[17,'-451.10'],[18,'-713.55'],[20,'17,982.06'],[21,'40,179.61']])assert.equal((await wb.locator('td').nth(i).innerText()).trim(),want);
    assert.match(await wb.locator('td').nth(19).innerText(),/-533.94[\s\S]*已实现/);
    assert.equal(await page.locator('#v3App thead tr').first().locator('th').count(),23);
    assert((await page.locator('.card').filter({hasText:'期末总资产'}).innerText()).includes('39,461.45'));
    assert((await page.locator('.card').filter({hasText:'本期资产盈亏'}).innerText()).includes('-538.55'));
    const download=page.waitForEvent('download');await page.getByRole('button',{name:'导出 CSV',exact:true}).click();
    const csv=await fs.readFile(await (await download).path(),'utf8');
    assert(csv.includes('9:35整批盈亏(元)'));assert(csv.includes('"-142.7098"'));assert(csv.includes('"doc-delta-v1"'));
    assert(csv.includes('"217.87065"'));assert(csv.includes('利润状态'));
    await page.screenshot({path:out+'/模板口径-桌面.png'});
    await page.evaluate(()=>V3.view('history'));await ready();
    const month=page.locator('.month-row').filter({hasText:'2026-11'}).first();
    assert((await month.innerText()).includes('39,461.45'));assert((await month.innerText()).includes('-464.26'));
    assert.equal(await page.locator('#v3App thead tr').first().locator('th').count(),13);
    await page.screenshot({path:out+'/模板口径-月表.png'});
    await page.evaluate(()=>V3.view('trade'));await ready();
    for(const width of [1760,1280,768,390,320]){
      await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    }
    await page.setViewportSize({width:390,height:844});await page.screenshot({path:out+'/模板口径-手机.png'});
    assert((await page.locator('.mobiletrade').filter({hasText:'万邦医药'}).innerText()).includes('已实现'));
    await page.unroute('**/api/v3/ledger?**');await page.setViewportSize({width:1760,height:1050});
    await page.evaluate(()=>V3.load());await ready();
    const live=await page.evaluate(async()=>await api('/api/v3/ledger?model=original&rank=1&mode=D1'));
    assert.equal(live.version,'doc-delta-v1');assert.equal(live.source_mode,'market');
    const liveWb=live.rows.find(r=>r.date==='2026-11-02');assert.equal(liveWb.aProfit,-82.8414);assert.equal(liveWb.pProfit,-451.1023);
    await page.evaluate(()=>V3.view('compare'));await ready();
    assert.equal(await page.locator('.compare-row').count(),18);assert.equal(await page.locator('button').filter({hasText:'重试'}).count(),0);
    const compare=page.locator('.compare-row').filter({hasText:'V1 原始'}).filter({hasText:'第一名'}).first();
    assert((await compare.innerText()).includes(Number(live.monthly_stats['2026-11'].profit).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})));
    await page.screenshot({path:out+'/正式行情-模型对比.png'});
    await page.evaluate(async()=>{V3.state.mode='D2';await V3.chooseRank('original',1)});await ready();
    assert.equal(await page.locator('.error').count(),0);assert.match(await page.locator('#v3Title').innerText(),/D2/);
    assert.deepEqual(errors,[]);
    const report={templateCells:true,cards:true,csv:true,monthTable:true,mobile:true,widths:[1760,1280,768,390,320],liveSourceIsolated:true,comparisonLines:18,d2:true,pageErrors:errors};
    await fs.writeFile(out+'/UI验收结果.json',JSON.stringify(report,null,2));console.log(report);
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
