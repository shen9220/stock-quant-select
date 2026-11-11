/* Local review only: no maintenance jobs or publication. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1760, height: 1100 } });
    page.setDefaultTimeout(120000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const ready = () => page.waitForFunction(() => document.getElementById('v3Status')?.textContent === '数据已加载');
    const score = async (code, expected) => {
      const row = page.locator('#v3App .tablewrap tbody tr').filter({ hasText: code }).first();
      assert.equal((await row.locator('td').nth(1).innerText()).trim(), expected);
    };
    const select = async (model, rank = 1, mode = 'D1') => {
      await page.evaluate(async ({model, rank, mode}) => {
        V3.state.mode = mode; V3.state.month = '2026-11'; V3.state.from = ''; V3.state.to = '';
        await V3.chooseRank(model, rank);
      }, {model, rank, mode});
      await ready();
      console.log('Checking', model, rank, mode);
    };
    await page.goto('http://127.0.0.1:5051/'); await ready();
    assert.equal(await page.locator('.workspace-status,.preview-label').count(), 0);
    assert.equal(await page.locator('#v3Status').isVisible(), false);
    assert(!/交易数据记录 · 首板战法|本机真实数据 · 待验收/.test(await page.locator('body').innerText()));
    await select('original'); await score('002865', '14.12');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出 CSV', exact: true }).click();
    const download = await downloadPromise;
    console.log('Checking CSV');
    const csv = await fs.readFile(await download.path(), 'utf8');
    assert(csv.includes('"14.12","002865"'));
    await select('original', 2); await score('603893', '13.66');
    await select('cyb'); await score('300556', '13.26');
    await select('cyb', 2); await score('300426', '12.11');
    await select('under'); await score('300556', '21.2');
    await select('under', 2); await score('300426', '19.76');
    await select('original', 1, 'D2'); await score('301718', '15.15');
    await select('stable');
    assert.match(await page.locator('#v3App .tablewrap tbody tr').filter({hasText:'605258'}).first().locator('td').nth(1).innerText(), /65.*未过门槛/);
    await select('original');
    const out = process.env.V3_REVIEW_DIR || '/Users/admin/stock_quant_select/workspace/reviews/交易看板V3';
    await fs.mkdir(out, { recursive: true });
    await page.screenshot({ path: out + '/第十四版-说明行移除与分值补齐.png' });
    const layout = [];
    for (const width of [1760, 1280, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: 900});
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      assert.equal(await page.locator('#v3Status').isVisible(), false);
      layout.push(width);
    }
    await page.setViewportSize({width:390,height:844});
    const mobileRow = page.locator('.mobiletrade').first();
    assert.equal(await mobileRow.isVisible(), true);
    assert((await mobileRow.innerText()).includes('14.12分'));
    await page.evaluate(() => window.scrollTo({top:document.querySelector('.mobiletrade').getBoundingClientRect().top + scrollY - document.getElementById('v3Header').offsetHeight - 16}));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({ path: out + '/第十四版-手机分值展示.png' });
    for (const view of ['history', 'compare', 'pool', 'trade']) {
      await page.evaluate(view => V3.view(view), view); await ready();
      console.log('Checking view', view);
      assert.equal(await page.locator('.workspace-status').count(), 0);
      if (view === 'compare') assert.equal(await page.locator('.compare-row').count(), 18);
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ scoreExamples: 8, csvScore: true, d2SignalScore: true, thresholdLabel: true, removedStatusRow: true, layout, views: 4, pageErrors: errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
