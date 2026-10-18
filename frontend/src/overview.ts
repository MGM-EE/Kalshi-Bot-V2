import type {ConnectionStatus} from "./connection.js";
type Data = Record<string, any>;
let snapshot: Data | null = null, runtime: Data | null = null;
let runtimeFresh = false;
let link: ConnectionStatus = {state: "connecting", reconnects: 0, ageSeconds: null};
const selected = () => (document.getElementById("selected-asset") as HTMLSelectElement).value;
const direction = () => (document.getElementById("selected-outcome") as HTMLSelectElement).value;

function set(id: string, title: string, detail: string, tone = "warning") {
  const card = document.getElementById(id)!;
  card.querySelector("strong")!.textContent = title;
  card.querySelector("p")!.textContent = detail;
  card.dataset.tone = tone;
}
function draw() {
  const age = link.ageSeconds === null ? "No update received yet" : `Last displayed update ${Math.floor(link.ageSeconds)}s ago`;
  const state = link.state;
  const usable = state === "stream" || state === "polling";
  const note = document.getElementById("connection-note")!;
  note.textContent = `${age}. Reconnect attempts: ${link.reconnects}. A browser interruption does not stop the backend or pause paper entries.`;
  if (state === "connecting") set("app-status", "Opening the local app", "Waiting for the first update. Keep the app's terminal window open.");
  else if (state === "stream") set("app-status", "Dashboard connected", "The app is sending regular updates. Price-feed readiness is shown separately below.", "live");
  else if (state === "polling") set("app-status", "Connected through backup updates", "The live connection was interrupted. Checking the app about every 2 seconds while reconnecting.");
  else if (state === "display-error") set("app-status", "An update could not be displayed", "The dashboard will retry the next update. Copy the troubleshooting summary below if this continues.", "bad");
  else set("app-status", "No fresh update from the app", "These values may be out of date. Keep the terminal open; restart the app if updates do not return.", "bad");

  const asset = selected(), market = snapshot?.assets?.[asset]?.market;
  const sources = snapshot?.sources ?? {}, control = snapshot?.strategy?.control;
  const isLive = (name: string) => sources[name]?.status === "live";
  const needed = [`kalshi:${asset}:book`, `kalshi:${asset}:reference`, `kraken:${asset}:book`, `kraken:${asset}:trade`];
  const missing = needed.filter(name => !isLive(name));
  const readable = (name: string) => name.startsWith("kraken") ? `Kraken ${name.endsWith("book") ? "prices" : "trades"}` : `Kalshi ${name.endsWith("reference") ? "settlement reference" : "prices"}`;
  if (!usable) set("feed-status", "Price-feed state is unconfirmed", "Reconnect the dashboard before relying on the displayed prices.");
  else if (missing.length) set("feed-status", "Waiting for required market data", `For ${asset}: ${missing.map(readable).join(", ")}. Paper buys wait for fresh data; other checks also apply.`);
  else set("feed-status", "Required price feeds are arriving", `Receiving ${asset} prices, trades and the official reference. Rules, fees and risk checks still decide whether a paper buy is allowed.`, "live");

  if (!runtimeFresh) set("api-status", "Checking local API setup", "API status is unavailable until the app responds. Your private key stays on this PC.");
  else if (!runtime?.credentials?.configured) set("api-status", "Kalshi API key needs connecting", "Open CONNECT_KALSHI.bat in the project folder, enter the API key ID, choose your private-key file, then restart the app.");
  else if (!usable) set("api-status", "Local key found; connection unconfirmed", "A saved key does not prove access. Reconnect the dashboard to check the Kalshi feed.");
  else if (isLive("kalshi:socket")) set("api-status", "Kalshi data connection is active", "The backend reports an authenticated data connection. Individual feeds can still be unavailable. This app cannot place real-money orders.", "live");
  else set("api-status", "Local key found; waiting for Kalshi", "The key is present, but an active Kalshi stream is not confirmed. This can also mean no current market was found. Check Source health for details.");

  const entry = snapshot?.strategy?.decisions?.[asset]?.entries?.[direction()];
  const positions = snapshot?.paper?.open_positions ?? 0;
  const holdings = positions ? ` Watching ${positions} open paper position${positions === 1 ? "" : "s"}; exits still need fresh market data.` : " No open paper positions.";
  if (!usable || !control) set("bot-status", "Paper-bot state is unconfirmed", "The backend may still be running. Reconnect before relying on the controls or displayed decisions.");
  else if (control.emergency) set("bot-status", "Emergency paper exit is active", `New automatic buys are off. The bot is trying to exit paper holdings when valid prices allow.${holdings}`, "bad");
  else if (!control.enabled) set("bot-status", "Watching only; new buys are paused", `This is normal for a new account. Choose a profile and use Enable paper entries when you are ready.${holdings}`);
  else if (!market) set("bot-status", "Looking for an open market", `Automatic paper entries are enabled, but no current ${asset} contract has been found.${holdings}`);
  else if (entry?.eligible) set("bot-status", "A paper-buy signal is being checked", `A signal is not a confirmed purchase. Execution and risk checks may still block it.${holdings}`, "live");
  else set("bot-status", "Watching; waiting for the buy rules", `Automatic paper entries are enabled. The bot waits for every required condition, valid prices and risk limits.${holdings}`, "live");

  const recorder = runtime?.recorder;
  if (!runtimeFresh) set("recording-overview", "Recording status is unconfirmed", "Reconnect to check whether full market-data recording is active.");
  else if (recorder?.state === "recording") set("recording-overview", "Recording market data for replay", `${recorder.events.toLocaleString()} events written or queued for ${recorder.file}. Stop with Ctrl+C so the file finishes properly.`, "live");
  else if (recorder?.state === "failed") set("recording-overview", "Market-data recording has failed", "This recording cannot be trusted for replay. Paper trading may continue. Stop cleanly and check free disk space before starting a new recorded session.", "bad");
  else set("recording-overview", "Full market recording is off", "Paper trades and settings can still be saved. For a new separate recorded paper account, stop the app, then open RECORD_PAPER_SESSION.bat.");
  if (!runtimeFresh) set("storage-status", "Checking saved data", "Keep your project's data folder; it contains saved paper accounts and recordings.");
  else if (runtime?.account?.persistent) set("storage-status", "Paper account is saved locally", `Account file: ${runtime.account.file}. Trades, balances and settings survive restarts. Keep the data folder; deleting it removes this history.`, "live");
  else set("storage-status", "Temporary paper account", "This account is in memory and will not survive a backend restart.");
}

export function renderOverview(value: Data) {snapshot = value; draw();}
export function connectionChanged(value: ConnectionStatus) {
  link = value;
  const stale = !["stream", "polling"].includes(value.state);
  document.body.classList.toggle("offline", stale);
  const connection = document.getElementById("connection")!;
  connection.className = value.state === "stream" ? "live" : value.state === "polling" || value.state === "connecting" ? "warning" : "bad";
  connection.textContent = value.state === "stream" ? "APP CONNECTED · PAPER ONLY" : value.state === "polling" ? "APP CONNECTED · BACKUP UPDATES · PAPER ONLY" : value.state === "display-error" ? "DISPLAY ERROR · VALUES MAY BE STALE" : value.state === "connecting" ? "OPENING THE LOCAL APP · PAPER ONLY" : "APP DISCONNECTED · VALUES MAY BE STALE";
  if (stale) document.getElementById("current-decision")!.textContent = value.state === "display-error" ? "DISPLAY ERROR — waiting for an update that can be displayed" : "Waiting for fresh app updates; the backend may still be running.";
  draw();
}

async function refreshRuntime() {
  try {
    const response = await fetch("/api/runtime", {cache: "no-store", signal: AbortSignal.timeout(4000)});
    if (!response.ok) throw Error();
    runtime = await response.json(); runtimeFresh = true;
  } catch {runtimeFresh = false;}
  draw();
}
document.getElementById("selected-asset")!.addEventListener("change", draw);
document.getElementById("selected-outcome")!.addEventListener("change", draw);
document.getElementById("refresh-status")!.onclick = () => {void refreshRuntime();};
document.getElementById("copy-diagnostics")!.onclick = async () => {
  // Deliberately omit credential IDs, paths, headers, holdings and account values.
  const summary = JSON.stringify({
    app: "Kalshi Terminal V2 (paper only)", time: new Date().toISOString(),
    connection: link, asset: selected(),
    credentials: runtimeFresh ? {configured: runtime?.credentials?.configured, source: runtime?.credentials?.source} : "unavailable",
    recorder: runtimeFresh ? runtime?.recorder?.state : "unavailable",
    sources: Object.fromEntries(Object.entries(snapshot?.sources ?? {}).map(([name, raw]: [string, any]) => [name, {status: raw.status, age_seconds: raw.age_seconds}])),
  }, null, 2);
  const result = document.getElementById("diagnostic-result")!;
  try {await navigator.clipboard.writeText(summary); result.textContent = "Troubleshooting summary copied. It does not include your key or account balances.";}
  catch {result.textContent = summary;}
};
void refreshRuntime();
setInterval(() => {void refreshRuntime();}, 10000);
