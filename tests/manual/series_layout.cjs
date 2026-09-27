// Run with Node and Playwright installed, or set JMSHELF_PLAYWRIGHT_PATH.
// Uses synthetic data only; no personal library or JM requests are involved.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.JMSHELF_PLAYWRIGHT_PATH || 'playwright');

const publicRoot = path.resolve(__dirname, '../../public');
const members = Array.from({ length: 120 }, (_, index) => ({
  id: `chapter-${index + 1}`, sourceId: String(100000 + index), seriesId: '100000',
  chapterIndex: index + 1, title: `测试连载 · 第 ${index + 1} 话`,
  displayName: `测试连载 · 第 ${index + 1} 话`, nickname: '', note: '', authors: [], tags: [],
  collections: [], coverPath: 'synthetic-cover', pageCount: 3, progressPage: 0,
  rootPath: 'synthetic-pages', lastReadAt: null,
}));
const series = {
  id: 'layout-series', displayName: '长篇系列窗口测试', memberIds: members.map(item => item.id),
  members, resumeComicId: members[0].id, updateSupported: true, autoUpdateEnabled: true,
  updateAvailableCount: 0, updateItems: [], lastCheckedAt: null, updateError: null,
};
let refreshCount = 0;
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const json = (data) => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)); };
  if (url.pathname === '/api/comics') return json(members);
  if (/^\/api\/comics\/[^/]+\/pages$/.test(url.pathname)) return json([0, 1, 2].map(index => ({ index, name: `${index}.svg` })));
  if (url.pathname === '/api/library-series') return json([series]);
  if (url.pathname.endsWith('/refresh')) {
    refreshCount++;
    series.lastCheckedAt = new Date().toISOString();
    return json({ newCount: 0, queuedCount: 0, tasks: [], series });
  }
  if (url.pathname === '/api/collections') return json([]);
  if (url.pathname === '/api/provider/status') return json({ available: true, authenticated: false, version: 'test' });
  if (url.pathname === '/api/downloads') return json({ items: [] });
  if (url.pathname.startsWith('/api/')) return json({ ok: true });
  if (url.pathname.startsWith('/media/')) {
    res.setHeader('Content-Type', 'image/svg+xml');
    return res.end('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="415"><rect width="300" height="415" fill="#324154"/><text x="35" y="205" fill="#d5dbe5" font-size="28">TEST CHAPTER</text></svg>');
  }
  const target = path.join(publicRoot, url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
  if (!target.startsWith(publicRoot + path.sep)) { res.writeHead(403).end(); return; }
  try {
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' })[path.extname(target)] || 'application/octet-stream');
    res.end(await fs.readFile(target));
  } catch (_) { res.writeHead(404).end(); }
});

async function assertUsable(page, selectors) {
  for (const selector of selectors) {
    const result = await page.locator(selector).evaluate(el => {
      const r = el.getBoundingClientRect();
      const target = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      return { visible: r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth, hit: el === target || el.contains(target) };
    });
    assert.equal(result.visible, true, `${selector} must stay inside the viewport`);
    assert.equal(result.hit, true, `${selector} must not be covered by chapter content`);
  }
}

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const controls = ['#series-refresh', '#series-continue-reading', '#series-dialog [data-action="dissolve-series"]', '#series-dialog .modal-close', '#series-dialog .modal-footer [data-close-modal]'];
    for (const viewport of [{ width: 1360, height: 860 }, { width: 920, height: 620 }, { width: 768, height: 540 }, { width: 640, height: 480 }]) {
      await page.setViewportSize(viewport);
      series.displayName = '长篇标题测试：'.repeat(18);
      series.updateError = '网络暂时不可用，请稍后重试。'.repeat(40);
      await page.goto(origin);
      await page.locator('.series-card').click();
      await page.waitForTimeout(800);
      const scroll = page.locator('#series-chapter-scroll');
      assert.equal(await page.locator('.series-book-entry').count(), 120);
      assert.ok(await scroll.evaluate(el => el.scrollHeight > el.clientHeight && el.clientHeight > 70));
      await assertUsable(page, controls);
      const footerBefore = await page.locator('#series-dialog .modal-footer').boundingBox();
      await scroll.evaluate(el => { el.scrollTop = el.scrollHeight; });
      await assertUsable(page, controls);
      const footerAfter = await page.locator('#series-dialog .modal-footer').boundingBox();
      assert.equal(footerBefore.y, footerAfter.y, 'Scrolling chapters must not move actions');
      const previousScroll = await scroll.evaluate(el => el.scrollTop);
      await page.locator('#series-refresh').click();
      await page.getByText('检查完成，这个系列已是最新', { exact: true }).waitFor();
      await page.waitForTimeout(450);
      assert.ok(Math.abs(await scroll.evaluate(el => el.scrollTop) - previousScroll) <= 1, 'Refreshing must preserve chapter position');
      await assertUsable(page, controls);
      await page.locator('.series-book-entry').last().click();
      await page.locator('#detail-drawer.open').waitFor();
      assert.equal(await page.locator('#detail-display-name').innerText(), members[119].displayName);
      await page.locator('#detail-drawer [data-action="close-detail"]').click();
      await page.locator('#detail-drawer.open').waitFor({ state: 'hidden' });
      await page.locator('.series-card').click();
      assert.equal(await scroll.evaluate(el => el.scrollTop), 0, 'Reopening starts at the first chapter');
      await page.waitForTimeout(450);
      await page.locator('#series-dialog .modal-footer [data-close-modal]').click();
      await page.locator('#series-dialog').waitFor({ state: 'hidden' });
      console.log(`PASS ${viewport.width}x${viewport.height}: 120 chapters, fixed controls, refresh position, details, close`);
    }
    // A small collection remains compact; updating must never reopen another series.
    series.members = members.slice(0, 3);
    series.memberIds = series.members.map(item => item.id);
    series.displayName = '三话系列';
    series.updateError = null;
    await page.setViewportSize({ width: 1360, height: 860 });
    await page.goto(origin);
    await page.locator('.series-card').click();
    await page.waitForTimeout(600);
    await assertUsable(page, controls);
    assert.ok((await page.locator('.series-view-card').boundingBox()).height < 700);
    assert.equal(refreshCount, 4);
    assert.deepEqual(errors, []);
    console.log('PASS compact series and no browser JavaScript errors');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
