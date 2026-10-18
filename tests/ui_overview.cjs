// Plain-language status/credential/recording acceptance with synthetic wire fixtures.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
 const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
 try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = process.env.TERMINAL_URL || 'http://127.0.0.1:8765';
  let runtime = {credentials: {configured: false, source: 'none'}, account: {persistent: true, file: 'paper.sqlite3'}, recorder: {state: 'off'}};
  await page.route('**/api/runtime', route => route.fulfill({json: runtime}));
  let socket; await page.routeWebSocket('**/api/stream', ws => {socket = ws;});
  await page.addInitScript(() => {Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copiedSummary = text;}}});});
  await page.goto(base);
  const snapshot = await (await page.request.get(base + '/api/state')).json();
  snapshot.mode = 'DISCONNECTED / PAUSED'; socket.send(JSON.stringify(snapshot));
  await page.waitForFunction(() => document.getElementById('connection').textContent.includes('APP CONNECTED'));
  assert((await page.locator('#api-status').textContent()).includes('needs connecting'));
  assert((await page.locator('#bot-status').textContent()).includes('Watching only'));
  assert((await page.locator('#storage-status').textContent()).includes('saved locally'));
  assert((await page.locator('#recording-overview').textContent()).includes('recording is off'));
  runtime = {credentials: {configured: true, source: 'windows_user_store', key_id: 'TEST_SECRET_NOT_FOR_EXPORT'}, account: {persistent: true, file: 'paper.sqlite3'}, recorder: {state: 'recording', events: 1234, file: 'example.jsonl.gz'}};
  await page.locator('#refresh-status').click();
  await page.waitForFunction(() => document.getElementById('recording-overview').textContent.includes('Recording market data'));
  assert((await page.locator('#api-status').textContent()).includes('waiting for Kalshi'));
  for (const name of ['kalshi:socket', 'kalshi:BTC:reference', 'kalshi:BTC:book', 'kraken:BTC:book', 'kraken:BTC:trade']) snapshot.sources[name] = {status: 'live', age_seconds: 0, exchange_age_seconds: 0};
  snapshot.strategy.control.enabled = true;
  socket.send(JSON.stringify(snapshot));
  await page.waitForFunction(() => document.getElementById('api-status').textContent.includes('connection is active'));
  assert((await page.locator('#feed-status').textContent()).includes('feeds are arriving'));
  assert((await page.locator('#bot-status').textContent()).includes('Looking for an open market'));
  await page.locator('#selected-asset').selectOption('ETH');
  assert((await page.locator('#feed-status').textContent()).includes('Waiting for required market data'));
  assert((await page.locator('#connection').textContent()).includes('APP CONNECTED'), 'asset selection must not conflate missing market data with a disconnected app');
  snapshot.strategy.control.emergency = true; socket.send(JSON.stringify(snapshot));
  await page.waitForFunction(() => document.getElementById('bot-status').textContent.includes('Emergency paper exit'));
  runtime.recorder.state = 'failed'; await page.locator('#refresh-status').click();
  await page.waitForFunction(() => document.getElementById('recording-overview').textContent.includes('recording has failed'));
  await page.locator('#copy-diagnostics').click();
  const copied = await page.evaluate(() => window.copiedSummary);
  assert(copied.includes('windows_user_store')); assert(!copied.includes('TEST_SECRET')); assert(!copied.includes('cash'));
  await page.screenshot({path: '/tmp/kalshi-recovery-desktop.png'});
  await page.setViewportSize({width: 390, height: 844});
  assert(!await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), 'mobile overflow');
  await page.screenshot({path: '/tmp/kalshi-recovery-mobile.png'});
  assert.equal(errors.length, 0, errors.join('\n'));
  console.log('PASS: setup guidance, feed vs app status, controls, recording failures, private diagnostic export, mobile layout');
 } finally {await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});
