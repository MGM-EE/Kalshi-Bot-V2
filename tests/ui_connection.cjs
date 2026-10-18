// Focused browser regression: startup grace, stale frames, render failures/recovery.
// Run against an isolated app: TERMINAL_URL=http://127.0.0.1:8765 node tests/ui_connection.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true, args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    await page.clock.install();
    await page.addInitScript(() => {
      window.testSockets = [];
      class TestSocket {
        static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        readyState = 0;
        closeCount = 0;
        constructor() {window.testSockets.push(this);}
        close() {this.closeCount++; this.readyState = 3; this.onclose?.();}
        frame(data) {this.readyState = 1; this.onmessage?.({data});}
      }
      window.WebSocket = TestSocket;
    });
    const base = process.env.TERMINAL_URL || 'http://127.0.0.1:8765';
    await page.goto(base);
    await page.waitForFunction(() => window.testSockets.length === 1);
    await page.clock.runFor(10000);
    assert.equal(await page.evaluate(() => window.testSockets[0].closeCount), 0, 'slow handshake stays open');
    assert(await page.locator('body').evaluate(n => n.classList.contains('offline')), 'startup remains stale');
    await page.clock.runFor(23000);
    assert.equal(await page.evaluate(() => window.testSockets[0].closeCount), 1, 'stalled startup closes once');
    assert.equal(await page.evaluate(() => window.testSockets.length), 2, 'one reconnect');
    const snapshot = await (await page.request.get(base + '/api/state')).json();
    const send = data => page.evaluate(data => window.testSockets.at(-1).frame(data), JSON.stringify(data));
    await send(snapshot);
    assert(!await page.locator('body').evaluate(n => n.classList.contains('offline')), 'fresh frame restores display');
    await page.evaluate(() => window.testSockets.at(-1).frame('{bad json'));
    assert((await page.locator('#current-decision').textContent()).includes('DISPLAY ERROR'));
    assert(await page.locator('body').evaluate(n => n.classList.contains('offline')));
    await page.clock.runFor(3000);
    await send({}); // A rendering exception is also visible, without reconnecting.
    assert.equal(await page.evaluate(() => window.testSockets.length), 2);
    assert.equal(await page.evaluate(() => window.testSockets.at(-1).closeCount), 0);
    assert.equal(errors.filter(e => e.includes('Dashboard could not display backend state:')).length, 1, 'errors do not flood console');
    await send(snapshot);
    assert(!await page.locator('body').evaluate(n => n.classList.contains('offline')), 'valid frame recovers');
    await page.clock.runFor(6000);
    assert(await page.locator('body').evaluate(n => n.classList.contains('offline')), 'missing frames mark data stale');
    assert.equal(await page.evaluate(() => window.testSockets[1].closeCount), 1);
    console.log('PASS: startup grace, bounded reconnects, render diagnostics/recovery, five-second stale limit');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exit(1);});
