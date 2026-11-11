/* Exercise static masking, actual password decryption, 36 ledgers and logout. */
const {chromium}=require('/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {execFileSync}=require('node:child_process'),assert=require('node:assert/strict'),fs=require('node:fs/promises');
const base=process.env.PAGES_TEST_URL||'http://127.0.0.1:5080/?pages=1';
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:950}});page.setDefaultTimeout(90000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>document.getElementById('v3Status')?.textContent==='数据已加载');
  assert.equal(await page.getByRole('button',{name:'新增',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'导出 CSV',exact:true}).count(),0);
  assert(!(await page.locator('#v3App').innerText()).includes('万邦医药'));
  const pub=await page.evaluate(()=>api('/api/v3/ledger?model=original&rank=1&mode=D1'));
  assert.equal(pub.version,'doc-delta-v1');assert.equal(pub.rows.length,0);
  for(const s of Object.values(pub.monthly_stats))assert(Object.values(s).every(v=>v===null));
  // Password remains in process memory and is never printed or saved by this test.
  const pass=execFileSync('/usr/bin/python3',['-c',"import sys;sys.path.insert(0,'/Users/admin/stock_quant_select/首板战法/交易记录UI/lib');from pages_export import _pages_password;sys.stdout.write(_pages_password())"],{stdio:['ignore','pipe','pipe']}).toString();
  await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('#authPass').fill(pass);await page.locator('#authOk').click();
  await page.waitForFunction(()=>!!window.__PAGES_FULL__&&!window.__pagesLoginLock);
  assert.equal(await page.locator('#authErr').innerText(),'');
  const result=await page.evaluate(async()=>{const keys=Object.keys(window.__PAGES_FULL__.ledgers);const list=[];for(const key of keys){const [model,rank,mode]=key.split(':');const a=await api(`/api/v3/ledger?model=${model}&rank=${rank}&mode=${mode}`);list.push({version:a.version,mode:a.mode,rows:a.rows.length})}return list});
  assert.equal(result.length,36);assert(result.every(r=>r.version==='doc-delta-v1'));
  assert((await page.locator('#v3App').innerText()).includes('万邦医药'));
  await page.evaluate(()=>V3.view('compare'));await page.waitForFunction(()=>document.getElementById('v3Status')?.textContent==='数据已加载');
  assert.equal(await page.locator('.compare-row').count(),18);
  await page.screenshot({path:'/Users/admin/stock_quant_select/workspace/reviews/交易看板取值上线/Pages登录后-模型对比.png'});
  await page.getByRole('button',{name:'退出',exact:true}).click();await page.waitForFunction(()=>!window.__PAGES_FULL__);
  await page.waitForFunction(()=>document.getElementById('v3Status')?.textContent==='数据已加载');
  assert(!(await page.locator('#v3App').innerText()).includes('万邦医药'));
  assert.deepEqual(errors,[]);
  const report={url:base,publicMasked:true,passwordDecrypt:true,ledgers:36,comparisonLines:18,logoutMasked:true,pageErrors:errors};
  await fs.writeFile('/Users/admin/stock_quant_select/workspace/reviews/交易看板取值上线/Pages验收结果.json',JSON.stringify(report,null,2));console.log(report);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
