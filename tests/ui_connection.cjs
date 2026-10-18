// Focused browser regressions. All wire fixtures/transport failures are test-only.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.TERMINAL_URL || 'http://127.0.0.1:8765';
async function until(page, check) {
 for (let i = 0; i < 150; i++) {if (await page.evaluate(check)) return; await new Promise(r => setTimeout(r, 20));}
 throw Error('Browser condition did not become true: ' + check);
}
(async () => {
 const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless: true, args: ['--no-sandbox']});
 try {
  const seed = await browser.newPage();
  const snapshot = await (await seed.request.get(base + '/api/state')).json();
  await seed.close();
  async function scenario() {
   const page = await browser.newPage(), errors = [];
   page.on('console', m => {if (m.type() === 'error') errors.push(m.text());});
   await page.clock.install(); await page.clock.pauseAt(new Date(Date.now() + 1000));
   await page.addInitScript(() => {
    window.testSockets = [];
    class TestSocket {
     static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
     readyState = 0; closeCount = 0;
     constructor() {window.testSockets.push(this);}
     close() {this.closeCount++; this.readyState = 3; this.onclose?.();}
     frame(data) {this.readyState = 1; this.onmessage?.({data});}
    }
    window.WebSocket = TestSocket;
   });
   let pollCount = 0;
   await page.route('**/api/state', async route => {pollCount++; await route.fulfill({json: snapshot});});
   await page.goto(base); await until(page, () => window.testSockets.length === 1);
   const send = data => page.evaluate(data => window.testSockets.at(-1).frame(data), JSON.stringify(data));
   const offline = () => page.locator('body').evaluate(n => n.classList.contains('offline'));
   return {page, errors, send, offline, polls: () => pollCount};
  }
  // Brief stream gaps use HTTP without tearing down the original socket.
  {
   const {page, send, offline, polls} = await scenario(); await send(snapshot);
   assert((await page.locator('#connection').textContent()).includes('APP CONNECTED'));
   assert((await page.locator('#feed-status').textContent()).includes('Waiting for required market data'));
   await page.clock.runFor(7000);
   await until(page, () => document.getElementById('app-status').textContent.includes('backup updates'));
   assert(polls() >= 1); assert(!await offline());
   assert.equal(await page.evaluate(() => window.testSockets[0].closeCount), 0);
   await send(snapshot);
   assert(!(await page.locator('#connection').textContent()).includes('BACKUP'));
   assert.equal(await page.evaluate(() => window.testSockets.length), 1);
   await page.close();
  }
  // Slow initial handshakes get time; backup data can still be shown.
  {
   const {page} = await scenario(); await page.clock.runFor(10000);
   await until(page, () => document.getElementById('connection').textContent.includes('BACKUP'));
   assert.equal(await page.evaluate(() => window.testSockets[0].closeCount), 0);
   await page.clock.runFor(23000);
   assert.equal(await page.evaluate(() => window.testSockets[0].closeCount), 1);
   assert.equal(await page.evaluate(() => window.testSockets.length), 2);
   await page.close();
  }
  // A late HTTP response cannot rewind a newer streamed snapshot.
  {
   const {page, send} = await scenario(); let delayed;
   await page.unroute('**/api/state');
   await page.route('**/api/state', route => {delayed = route;});
   await send(snapshot); await page.clock.runFor(6000);
   await until(page, () => document.getElementById('app-status').textContent.includes('No fresh update'));
   assert(delayed);
   const newer = structuredClone(snapshot); newer.paper.cash = '321.09'; await send(newer);
   await delayed.fulfill({json: snapshot});
   // Flush the response handler without advancing virtual watchdog timers.
   await new Promise(resolve => setTimeout(resolve, 50));
   assert((await page.locator('#paper-values').textContent()).includes('$321.09'));
   assert(!(await page.locator('#connection').textContent()).includes('BACKUP'));
   await page.close();
  }
  // Rendering errors remain visible and recover on the same socket.
  {
   const {page, send, errors, offline} = await scenario(); await send(snapshot);
   await page.evaluate(() => window.testSockets[0].frame('{bad json'));
   assert((await page.locator('#current-decision').textContent()).includes('DISPLAY ERROR'));
   await page.clock.runFor(1000); await send({});
   assert.equal(await page.evaluate(() => window.testSockets[0].closeCount), 0);
   assert.equal(errors.filter(e => e.includes('Dashboard could not display backend state:')).length, 1);
   assert(await offline()); await send(snapshot); assert(!await offline()); await page.close();
  }
  // Complete outages stay visibly stale without claiming the bot has paused.
  {
   const {page, send, offline} = await scenario();
   await page.unroute('**/api/state');
   await page.route('**/api/state', route => route.fulfill({status: 503, body: 'unavailable'}));
   await send(snapshot); await page.clock.runFor(7000);
   await until(page, () => document.body.classList.contains('offline'));
   assert(await offline());
   assert((await page.locator('#bot-status').textContent()).includes('unconfirmed'));
   assert((await page.locator('#connection-note').textContent()).includes('does not stop the backend'));
   await page.close();
  }
  console.log('PASS: brief gaps, startup grace, polling recovery, response ordering, render recovery, complete outage');
 } finally {await browser.close();}
})().catch(error => {console.error(error); process.exit(1);});
