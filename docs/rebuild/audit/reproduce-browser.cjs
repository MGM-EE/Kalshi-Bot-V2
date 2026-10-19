// A socket that stays CLOSING must not prevent creation of a replacement forever.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const projectRequire = createRequire(process.cwd() + '/package.json');
const ts = projectRequire('typescript');
const source = fs.readFileSync('frontend/src/connection.ts', 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
let now = 0, tick, sockets = [], statuses = [];
class Socket {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  constructor() {this.readyState = 0; sockets.push(this);}
  close() {this.readyState = 2;} // Network blackhole: no close event arrives.
}
const sandbox = {
  exports: {}, WebSocket: Socket, console, AbortController,
  location: {protocol: 'http:', host: 'localhost'},
  performance: {now: () => now},
  document: {hidden: false, addEventListener() {}, removeEventListener() {}},
  window: {addEventListener() {}, removeEventListener() {}},
  setInterval: fn => {tick = fn; return 1;}, clearInterval() {},
  setTimeout: () => 1, clearTimeout() {},
  fetch: async () => ({ok: true, json: async () => ({})}),
};
vm.runInNewContext(compiled, sandbox);
const stop = sandbox.exports.startConnection(() => {}, value => statuses.push(value));
now = 31000; tick();
assert.equal(sockets[0].readyState, Socket.CLOSING);
for (now = 32000; now < 3600000; now += 1000) tick();
assert.equal(sockets.length, 1);
console.log(JSON.stringify({simulated_seconds: now / 1000, sockets_created: sockets.length,
  socket_state: 'CLOSING', replacement_created: false}, null, 2));
stop();
