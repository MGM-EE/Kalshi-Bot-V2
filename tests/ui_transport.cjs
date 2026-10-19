// Deterministic transport regressions: no browser, backend, network, or credentials.
// Run with `node tests/ui_transport.cjs` after installing the existing npm dev deps.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../frontend/src/connection.ts'), 'utf8'), {
 compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}
}).outputText;
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
 let resolve, reject;
 const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
 return {promise, resolve, reject};
}
function response(value) {return {ok: true, json: async () => value};}
function harness(options = {}) {
 let mono = 0, wall = 0, timerID = 0, constructions = 0;
 const timers = new Map(), sockets = [], polls = [], renders = [], statuses = [], errors = [];
 const documentListeners = new Map(), windowListeners = new Map();
 function target(listeners) {
  return {
   addEventListener(name, listener) {if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(listener);},
   removeEventListener(name, listener) {listeners.get(name)?.delete(listener);},
   emit(name, event = {}) {for (const listener of listeners.get(name) || []) listener(event);}
  };
 }
 const document = {...target(documentListeners), hidden: false}, window = target(windowListeners);
 const addTimer = (fn, delay, interval) => {const id = ++timerID; timers.set(id, {fn, at: mono + delay, interval}); return id;};
 class Socket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  readyState = 0; closeCount = 0;
  constructor() {
   constructions++;
   if (constructions <= (options.constructorFailures || 0)) throw Error('Socket unavailable');
   sockets.push(this);
  }
  close() {
   this.closeCount++;
   if (options.closeBehavior === 'open') return;
   this.readyState = Socket.CLOSING; // Deliberately never emit onclose.
  }
  frame(value) {this.readyState = Socket.OPEN; this.onmessage?.({data: JSON.stringify(value)});}
 }
 const exports = {};
 const fixedMath = Object.create(Math); fixedMath.random = () => 0.5;
 vm.runInNewContext(source, {
  exports, WebSocket: Socket, AbortController, document, window, location: {protocol: 'http:', host: 'test.local'},
  performance: {now: () => mono}, Date: {now: () => wall}, Math: fixedMath,
  console: {error: (...args) => errors.push(args)},
  setTimeout: (fn, delay) => addTimer(fn, delay, null), clearTimeout: id => timers.delete(id),
  setInterval: (fn, delay) => addTimer(fn, delay, delay), clearInterval: id => timers.delete(id),
  fetch: (url, init) => {
   const pending = deferred(); polls.push({url, init, ...pending});
   if (!options.hangPolls) pending.resolve(response({source: 'polling', sequence: polls.length}));
   return pending.promise;
  }
 });
 const stop = exports.startConnection(value => {
  if (value.bad) throw Error('Cannot display this snapshot');
  renders.push(value);
 }, value => statuses.push(value));
 async function advance(ms) {
  const end = mono + ms;
  while (true) {
   let next;
   for (const [id, timer] of timers) if (timer.at <= end && (!next || timer.at < next[1].at)) next = [id, timer];
   if (!next) break;
   const [id, timer] = next;
   wall += timer.at - mono; mono = timer.at;
   if (timer.interval === null) timers.delete(id); else timer.at += timer.interval;
   timer.fn(); await flush();
  }
  wall += end - mono; mono = end; await flush();
 }
 return {
  sockets, polls, renders, statuses, errors, Socket, stop, advance, document, window, timers,
  sleep: ms => {wall += ms;}, constructions: () => constructions,
  listenerCount: () => [...documentListeners.values(), ...windowListeners.values()].reduce((n, set) => n + set.size, 0)
 };
}
(async () => {
 // A stale OPEN socket is detached before close; even a missing close event or
 // late events cannot prevent replacement or roll the dashboard backward.
 {
  const h = harness({closeBehavior: 'open'}), old = h.sockets[0];
  old.frame({sequence: 1}); const lateMessage = old.onmessage, lateClose = old.onclose;
  await h.advance(14000); assert.equal(h.sockets.length, 1); assert(h.polls.length > 0);
  await h.advance(1000); assert.equal(old.closeCount, 1); assert.equal(old.readyState, h.Socket.OPEN);
  await h.advance(1000); assert.equal(h.sockets.length, 2);
  h.sockets[1].frame({sequence: 10});
  lateMessage({data: '{"sequence":0}'}); lateClose();
  assert.equal(h.renders.at(-1).sequence, 10); assert.equal(h.sockets[1].closeCount, 0);
  h.stop();
 }
 // Handshake deadlines do not depend on readyState or onclose delivery.
 {
  const h = harness();
  await h.advance(29000); assert.equal(h.sockets[0].closeCount, 0);
  await h.advance(1000); assert.equal(h.sockets[0].closeCount, 1);
  await h.advance(1000); assert.equal(h.sockets.length, 2); h.stop();
 }
 // A socket already stuck CLOSING/CLOSED, or reporting only onerror, also recovers.
 for (const state of [2, 3, 'error']) {
  const h = harness(), old = h.sockets[0];
  if (state === 'error') old.onerror(); else old.readyState = state;
  await h.advance(2000); assert.equal(h.sockets.length, 2); h.stop();
 }
 // Retry delays remain bounded, and one successful frame resets the backoff.
 {
  const h = harness({constructorFailures: 8});
  await h.advance(90000); assert(h.constructions() >= 7 && h.constructions() <= 9);
  await h.advance(31000); assert.equal(h.sockets.length, 1);
  const socket = h.sockets[0]; socket.frame({sequence: 1}); socket.onerror();
  await h.advance(1000); assert.equal(h.sockets.length, 2); h.stop();
 }
 // An HTTP operation that ignores abort (including a hung response body) cannot
 // hold the polling slot or deliver an expired snapshot over a later response.
 {
  const h = harness({hangPolls: true});
  await h.advance(5000); assert.equal(h.polls.length, 1);
  const body = deferred(); h.polls[0].resolve({ok: true, json: () => body.promise}); await flush();
  await h.advance(4000); assert(h.polls[0].init.signal.aborted); assert.equal(h.polls.length, 2);
  h.polls[1].resolve(response({sequence: 20})); await flush();
  body.resolve({sequence: 1}); await flush(); assert.equal(h.renders.at(-1).sequence, 20);
  h.stop();
 }
 // A newer streamed frame wins even if the aborted HTTP request later resolves.
 {
  const h = harness({hangPolls: true});
  h.sockets[0].frame({sequence: 1}); await h.advance(6000); assert.equal(h.polls.length, 1);
  h.sockets[0].frame({sequence: 50}); assert(h.polls[0].init.signal.aborted);
  h.polls[0].resolve(response({sequence: 2})); await flush();
  assert.equal(h.renders.at(-1).sequence, 50); assert.equal(h.statuses.at(-1).state, 'stream'); h.stop();
 }
 // Backend snapshot versions prevent an already stale HTTP/cache or stream
 // frame from rewinding state; a new backend runtime can restart its sequence.
 {
  const h = harness({hangPolls: true});
  const snapshot = (runtime, sequence) => ({sequence, stream_version: {runtime_id: runtime, sequence}});
  h.sockets[0].frame(snapshot('first', 7)); await h.advance(6000);
  h.polls[0].resolve(response(snapshot('first', 6))); await flush();
  assert.equal(h.renders.at(-1).sequence, 7);
  await h.advance(2000); h.polls[1].resolve(response(snapshot('first', 8))); await flush();
  h.sockets[0].frame(snapshot('first', 5)); assert.equal(h.renders.at(-1).sequence, 8);
  h.sockets[0].frame(snapshot('second', 1)); assert.equal(h.renders.at(-1).sequence, 1); h.stop();
 }
 // Wall-clock sleep expires connections even if the browser's monotonic clock
 // and timers pause. Returning to the tab reconnects immediately and releases HTTP.
 {
  const h = harness({hangPolls: true}); h.sockets[0].frame({sequence: 1});
  await h.advance(6000); const pending = h.polls[0];
  h.sleep(60000); h.document.emit('visibilitychange');
  assert.equal(h.sockets.length, 2); assert(pending.init.signal.aborted); assert.equal(h.polls.length, 2);
  assert(h.statuses.at(-1).ageSeconds >= 66);
  h.sockets[1].frame({sequence: 2}); h.window.emit('online'); assert.equal(h.sockets.length, 3);
  h.sockets[2].frame({sequence: 3}); h.document.emit('visibilitychange'); assert.equal(h.sockets.length, 3);
  h.sleep(60000); h.window.emit('pageshow', {persisted: true}); assert.equal(h.sockets.length, 4); h.stop();
 }
 // Invalid payloads remain display errors until a valid update, and emit one
 // diagnostic per error episode without restarting an otherwise healthy stream.
 {
  const h = harness({hangPolls: true}), socket = h.sockets[0];
  socket.onmessage({data: '{bad json'}); socket.frame({bad: true});
  assert.equal(h.statuses.at(-1).state, 'display-error'); assert.equal(h.errors.length, 1);
  await h.advance(6000); h.polls[0].reject(Error('Backend unavailable')); await flush();
  assert.equal(h.statuses.at(-1).state, 'display-error'); assert.equal(h.sockets.length, 1);
  socket.frame({sequence: 10}); assert.equal(h.statuses.at(-1).state, 'stream'); h.stop();
 }
 // Disposal removes timers/listeners, aborts HTTP, and fences all late callbacks.
 {
  const h = harness({hangPolls: true}), lateMessage = h.sockets[0].onmessage;
  await h.advance(5000); h.stop(); assert(h.polls[0].init.signal.aborted);
  assert.equal(h.listenerCount(), 0); assert.equal(h.timers.size, 0);
  lateMessage({data: '{"sequence":100}'}); h.polls[0].resolve(response({sequence: 200}));
  h.window.emit('online'); await h.advance(60000);
  assert.equal(h.renders.length, 0); assert.equal(h.sockets.length, 1);
 }
 console.log('PASS: browser transport deadlines, stuck sockets, generations, bounded retries, HTTP timeouts/order, sleep/network recovery, display errors, disposal');
})().catch(error => {console.error(error); process.exit(1);});
