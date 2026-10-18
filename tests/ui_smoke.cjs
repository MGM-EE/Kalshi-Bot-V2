// Optional Chromium acceptance check. Uses deterministic wire fixtures only in this test.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let socket;
 await page.routeWebSocket('**/api/stream',ws=>{socket=ws;});
 const base=process.env.TERMINAL_URL||'http://127.0.0.1:8765';
 await page.goto(base);await page.waitForSelector('#strategy-profile option',{state:'attached'});
 await page.waitForFunction(()=>document.querySelectorAll('#indicator-fields input').length>20);
 const snapshot=await (await page.request.get(base+'/api/state')).json();
 snapshot.mode='LIVE DATA — PAPER TRADING';snapshot.timestamp=Date.now()/1000;
 snapshot.paper.fills=[];socket.send(JSON.stringify(snapshot));
 await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('APP CONNECTED'));
 const height=await page.locator('#decision-strip').evaluate(n=>n.getBoundingClientRect().height);
 assert(height>=160&&height<=220,`desktop strip height ${height}`);
 // A qualifying signal cannot produce a flash. Historical fills on initial load cannot flash.
 assert(!await page.locator('#decision-strip').evaluate(n=>n.classList.contains('flash-buy')));
 const fill={id:1,time:snapshot.timestamp,action:'buy',outcome:'yes',asset:'BTC',ticker:'TEST',quantity:'2',price:'.4',net_fee:'.02',reason:'Test confirmed paper fill',realized_pnl:'0'};
 snapshot.paper.fills=[fill];socket.send(JSON.stringify(snapshot));
 await page.waitForFunction(()=>document.getElementById('fill-alert').textContent.includes('PAPER BUY'));
 assert(await page.locator('#decision-strip').evaluate(n=>n.classList.contains('flash-buy')));
 await page.waitForTimeout(2700);socket.send(JSON.stringify(snapshot));await page.waitForTimeout(150);
 assert.equal(await page.locator('#fill-alert').textContent(),'');
 assert(!await page.locator('#decision-strip').evaluate(n=>n.classList.contains('flash-buy')));
 await page.locator('#alerts-enabled').evaluate(n=>n.closest('details').open=true);
 await page.locator('#alerts-enabled').uncheck();
 snapshot.paper.fills=[{...fill,id:2,action:'sell',realized_pnl:'.15'},fill];socket.send(JSON.stringify(snapshot));
 await page.waitForFunction(()=>document.getElementById('fill-alert').textContent.includes('PAPER SELL'));
 assert(!await page.locator('#decision-strip').evaluate(n=>n.classList.contains('flash-sell')));
 // Read-only baseline and full nested per-asset settings are available without JSON editing.
 await page.locator('#strategy-profile').selectOption({label:'BASELINE V1 v1'});
 assert(await page.locator('#profile-fields input').first().isDisabled());
 await page.locator('#fields-copy').evaluate(n=>n.click());
 assert(!await page.locator('#profile-fields input').first().isDisabled());
 assert((await page.locator('#indicator-fields input').count())>40);
 await page.locator('#research-panel').evaluate(n=>n.open=true);
 await page.waitForFunction(()=>document.getElementById('analytics-summary').textContent.includes('Closed trades'));
 await page.waitForSelector('#replay-file option',{state:'attached'});
 const recording=await page.locator('#replay-file option').evaluateAll(options=>options.filter(o=>o.value.startsWith('ui_acceptance_')).at(-1)?.value);
 if(recording){
   const before=await (await page.request.get(base+'/api/paper/portfolio')).json();
   await page.locator('#replay-file').selectOption(recording);
   await page.getByRole('button',{name:'Run isolated replay'}).click();
   await page.waitForFunction(()=>document.getElementById('replay-status').textContent.startsWith('REPLAY complete'));
   assert((await page.locator('#replay-results').textContent()).includes('VERIFIED'));
   const after=await (await page.request.get(base+'/api/paper/portfolio')).json();
   assert.equal(after.cash,before.cash);assert.equal(after.orders.length,before.orders.length);
 }
 assert.equal(errors.length,0,errors.join('\n'));
 await page.screenshot({path:'/tmp/kalshi-dashboard-desktop.png',fullPage:false});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'/tmp/kalshi-dashboard-mobile.png'});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'mobile viewport overflow');
 console.log(JSON.stringify({desktopStripHeight:height,duplicateFillSuppressed:true,alertsToggle:true,settingsFields:true,replayVerified:Boolean(recording),pageErrors:errors}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
