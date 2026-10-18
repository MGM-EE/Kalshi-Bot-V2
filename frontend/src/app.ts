import {renderTerminal, loadSettings} from "./terminal.js";
import {settingsForm} from "./settings.js";
type Health = {status: string; detail: string; age_seconds: number; exchange_age_seconds: number | null};
type Book = {valid?: boolean; yes_bids?: string[][]; yes_asks?: string[][]; no_bids?: string[][];
  no_asks?: string[][]; bids?: string[][]; asks?: string[][]};
type Market = {ticker: string; title: string; close_time: number; opening_reference: string | number | null;
  outcome_mapping: Record<string, string> | null; current_index: string | null; rules: string;
  rule_checks: string[]; reference_status: string; [key: string]: unknown};
type Venue = {book?: Book; ticker?: {last: string; bid: string; ask: string; spread: string};
  trades?: Record<string, unknown>[]};
type Asset = {market: Market | null; upcoming: Market[]; kalshi: Venue; kraken: Venue};
type PaperRecord = {id: string; status: string; filled_quantity: string; fees: string; reason: string};
type Portfolio = {cash: string; available_cash: string; reserved_cash: string; capital_committed: string;
  realized_pnl: string; unrealized_pnl_conservative: string; equity_conservative: string; fees: string;
  open_positions: number; orders: PaperRecord[]};
type Snapshot = {assets: Record<string, Asset>; sources: Record<string, Health>; paper?: Portfolio | null;
  strategy?: {control: {enabled: boolean; emergency: boolean; profile_name: string; profile_version: number}; decisions: Record<string, unknown>};
  events: {time: number; source: string; status: string; detail: string}[]};

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text = "", cls = "") {
  const node = document.createElement(tag); node.textContent = text; node.className = cls; return node;
}
function text(node: HTMLElement, value: string) {if (node.textContent !== value) node.textContent = value;}
const money = (v: unknown) => v === null || v === undefined ? "Unavailable" : `$${v}`;
const cache = new Map<string, string>();
let latest: Snapshot | null = null;
function update(key: string, node: HTMLElement, value: unknown, render: () => Node[]) {
  const signature = JSON.stringify(value);
  if (cache.get(key) !== signature) {cache.set(key, signature); node.replaceChildren(...render());}
}
function table(headers: string[], rows: unknown[][]) {
  const t = el("table"), head = el("tr"); headers.forEach(h => head.append(el("th", h))); t.append(head);
  rows.forEach(row => {const tr = el("tr"); row.forEach(c => tr.append(el("td", String(c ?? "—")))); t.append(tr);});
  return t;
}
const cards = new Map<string, {fields: Record<string, HTMLElement>; book: HTMLElement;
  rules: HTMLElement; trades: HTMLElement; upcoming: HTMLElement}>();
for (const asset of ["BTC", "ETH", "SOL"]) {
  const article = el("article"), dl = el("dl"), fields: Record<string, HTMLElement> = {};
  article.append(el("h2", `${asset} / USD · 15 MIN`));
  for (const key of ["Market", "Cutoff remaining", "Kraken spot", "Kraken bid / ask", "Opening line",
    "CF reference", "YES bid / ask", "NO bid / ask", "Outcome mapping", "Rule validation"]) {
    fields[key] = el("dd", "Unavailable"); dl.append(el("dt", key), fields[key]);
  }
  article.append(dl);
  const book = el("div"), trades = el("div"), rules = el("pre"), upcoming = el("pre");
  for (const [title, content] of [["Order-book depth · top 5", book], ["Recent public executions", trades],
    ["Contract rules and reference diagnostics", rules], ["Upcoming markets", upcoming]] as const) {
    const details = el("details"); details.append(el("summary", title), content); article.append(details);
  }
  document.getElementById("markets")!.append(article); cards.set(asset, {fields, book, rules, trades, upcoming});
}
function quote(rows: string[][] | undefined, valid: boolean | undefined) {
  return valid && rows?.length ? money(rows[0][0]) : "Unavailable";
}
function render(data: Snapshot) {
  latest = data;
  document.body.classList.remove("offline");
  text(document.getElementById("connection")!, `PAPER ONLY — ${data.strategy?.control.enabled && !data.strategy.control.emergency ? "automatic entries enabled; rules and data still gate fills" : "automatic entries paused"} · see source health`);
  renderPaper(data.paper);
  renderTerminal(data);
  if (data.strategy) text(document.getElementById("strategy-control")!, `${data.strategy.control.enabled ? "ENTRIES ENABLED" : "ENTRIES PAUSED"} · ${data.strategy.control.profile_name} v${data.strategy.control.profile_version} · ${data.strategy.control.emergency ? "EMERGENCY EXIT ACTIVE" : "Pinned exits continue for existing automatic positions"}`);
  for (const [asset, value] of Object.entries(data.assets)) {
    const card = cards.get(asset); if (!card) continue;
    const m = value.market, kb = value.kalshi.book, spot = value.kraken.ticker;
    const remaining = m ? Math.max(0, Math.ceil(m.close_time - Date.now() / 1000)) : null;
    const entries: Record<string, string> = {
      "Market": m?.ticker ?? "No current market", "Cutoff remaining": remaining === null ? "Unavailable" :
        `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`,
      "Kraken spot": money(spot?.last), "Kraken bid / ask": `${money(spot?.bid)} / ${money(spot?.ask)}`,
      "Opening line": money(m?.opening_reference), "CF reference": money(m?.current_index),
      "YES bid / ask": `${quote(kb?.yes_bids, kb?.valid)} / ${quote(kb?.yes_asks, kb?.valid)}`,
      "NO bid / ask": `${quote(kb?.no_bids, kb?.valid)} / ${quote(kb?.no_asks, kb?.valid)}`,
      "Outcome mapping": m?.outcome_mapping ? `YES=${m.outcome_mapping.yes} · NO=${m.outcome_mapping.no}` : "Unverified",
      "Rule validation": m ? (m.rule_checks.length ? `${m.rule_checks.length} unresolved checks` : "Outcome checks passed; reference pending") : "Unavailable",
    };
    Object.entries(entries).forEach(([k, v]) => text(card.fields[k], v));
    update(`${asset}:books`, card.book, [kb, value.kraken.book], () => {
      const nodes: Node[] = [];
      for (const [name, bids, asks, valid] of [
        ["Kalshi YES · USD / contracts", kb?.yes_bids, kb?.yes_asks, kb?.valid],
        ["Kalshi NO · USD / contracts", kb?.no_bids, kb?.no_asks, kb?.valid],
        ["Kraken spot · USD / coins", value.kraken.book?.bids, value.kraken.book?.asks, value.kraken.book?.valid],
      ] as const) {
        nodes.push(el("h3", name));
        nodes.push(valid ? table(["Bid", "Qty", "Ask", "Qty"], Array.from({length: 5}, (_, i) =>
          [bids?.[i]?.[0], bids?.[i]?.[1], asks?.[i]?.[0], asks?.[i]?.[1]])) : el("p", "Snapshot unavailable", "warning"));
      }
      return nodes;
    });
    text(card.rules, JSON.stringify(m, null, 2) ?? "No contract loaded");
    text(card.upcoming, value.upcoming.map(m => `${m.ticker} · opens ${new Date(Number(m.open_time) * 1000).toISOString()}`).join("\n") || "None discovered");
    update(`${asset}:trades`, card.trades, [value.kraken.trades, value.kalshi.trades], () => [
      el("h3", "Kraken · aggressor side / USD / coin quantity"),
      table(["Side", "Price", "Qty"], (value.kraken.trades ?? []).slice(-5).reverse().map(t =>
        [t.aggressor_side, t.price, t.quantity])),
      el("h3", "Kalshi · taker outcome / YES USD / contracts"),
      table(["Outcome", "Price", "Qty"], (value.kalshi.trades ?? []).slice(-5).reverse().map(t =>
        [t.taker_outcome_side ?? t.taker_side ?? "Unknown", t.yes_price_dollars, t.count_fp])),
    ]);
  }
  const summary = document.getElementById("health")!;
  update("health", summary, data.sources, () => ["kalshi", "kraken"].map(venue => {
    const sources = Object.entries(data.sources).filter(([k]) => k.startsWith(venue));
    const live = sources.filter(([, v]) => v.status === "live").length;
    return el("span", `${venue.toUpperCase()} · ${live}/${sources.length} channels live`, "badge");
  }));
  const sourceTable = document.getElementById("sources")!;
  update("sources", sourceTable, data.sources, () => [table(["Source", "State", "Age (s)", "Detail"],
    Object.entries(data.sources).map(([k, v]) => [k, v.status, v.age_seconds, v.detail]))]);
  update("events", document.getElementById("events")!, data.events, () => data.events.slice(-10).reverse()
    .map(e => el("div", `${new Date(e.time * 1000).toLocaleTimeString()} · ${e.source} · ${e.status} · ${e.detail}`)));
}

function renderPaper(paper: Portfolio | null | undefined) {
  if (!paper) return;
  const node = document.getElementById("paper-values")!;
  const fields = {"Paper cash": money(paper.cash), "Available cash": money(paper.available_cash),
    "Reserved cash": money(paper.reserved_cash), "Capital committed": money(paper.capital_committed),
    "Realized net P&L": money(paper.realized_pnl), "Conservative unrealized P&L": money(paper.unrealized_pnl_conservative),
    "Conservative equity": money(paper.equity_conservative), "Total fees": money(paper.fees),
    "Open positions": String(paper.open_positions),
    ...Object.fromEntries(Object.entries(paper as unknown as Record<string,unknown>).filter(([k])=>["starting_balance","daily_realized_pnl","all_time_pnl_conservative","return_after_fees_percent","daily_drawdown"].includes(k)).map(([k,v])=>[k.replaceAll("_"," "), String(v)]))};
  update("paper-values", node, fields, () => Object.entries(fields).flatMap(([k, v]) => [el("dt", k), el("dd", v)]));
  const orders = document.getElementById("paper-orders")!;
  update("paper-orders", orders, paper.orders, () => paper.orders.slice(0, 10).map(order => {
    const row = el("p", `${order.id.slice(0, 8)} · ${order.status.toUpperCase()} · confirmed qty ${order.filled_quantity} · fees $${order.fees} · ${order.reason} `);
    if (["pending", "resting"].includes(order.status)) {
      const button = el("button", "Cancel remainder");
      button.onclick = async () => {
        button.disabled = true;
        try {
          const response = await fetch(`/api/paper/orders/${encodeURIComponent(order.id)}`, {method: "DELETE"});
          const value = await response.json();
          text(document.getElementById("paper-result")!, response.ok ? `${value.status}: ${value.reason}` : JSON.stringify(value));
        } catch {text(document.getElementById("paper-result")!, "Cancellation response unavailable; check order state before retrying.");}
        finally {button.disabled = false;}
      };
      row.append(button);
    }
    return row;
  }));
}

const form = document.getElementById("paper-form") as HTMLFormElement;
let retryPayload: Record<string, unknown> | null = null;
form.onsubmit = async event => {
  event.preventDefault();
  const result = document.getElementById("paper-result")!, submit = form.querySelector("button")!;
  if (!retryPayload) {
    const fields = new FormData(form), asset = String(fields.get("asset")), market = latest?.assets[asset]?.market;
    if (!market) {text(result, "No current market available."); return;}
    retryPayload = {client_order_id: crypto.randomUUID(), asset, ticker: market.ticker,
      action: fields.get("action"), outcome: fields.get("outcome"), quantity: fields.get("quantity"),
      limit_price: fields.get("limit_price"), mode: fields.get("mode"),
      max_cost: fields.get("action") === "buy" ? fields.get("max_cost") : null};
  }
  submit.disabled = true;
  try {
    const response = await fetch("/api/paper/orders", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(retryPayload)});
    const value = await response.json();
    text(result, response.ok ? `${value.id.slice(0, 8)} · ${value.status.toUpperCase()} · ${value.reason}. Confirmed quantity: ${value.filled_quantity}.` : JSON.stringify(value));
    retryPayload = null;
  } catch {
    text(result, "Response unavailable. Submit again to retry the SAME order; form edits wait until that request resolves.");
  } finally {submit.disabled = false;}
};
let failures = 0;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/stream`);
  const started = performance.now();
  let lastFrame: number | null = null;
  let renderingFailed = false;
  const timer = setInterval(() => {
    // Opening a socket is not evidence of fresh data. Allow startup time, but
    // keep the existing five-second stale-data limit after frames arrive.
    const expired = lastFrame === null ? performance.now() - started > 30000 : performance.now() - lastFrame > 5000;
    if (expired) {
      offline("DISCONNECTED / PAUSED — awaiting fresh backend state");
      if (ws.readyState !== WebSocket.CLOSING && ws.readyState !== WebSocket.CLOSED) ws.close();
    }
  }, 1000);
  ws.onmessage = event => {
    lastFrame = performance.now();
    try {
      render(JSON.parse(event.data));
      renderingFailed = false;
      failures = 0;
    } catch (error) {
      // A UI exception must not masquerade as a transport failure or create a
      // reconnect loop. Retry rendering the next frame and expose the cause.
      if (!renderingFailed) console.error("Dashboard could not display backend state:", error);
      renderingFailed = true;
      offline("DISPLAY ERROR — values are stale; see browser Console; awaiting a valid update");
    }
  };
  ws.onclose = () => {
    clearInterval(timer);
    offline("DISCONNECTED / PAUSED — awaiting fresh backend state");
    setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)));
  };
  ws.onerror = () => ws.close();
}
function offline(reason: string) {
  document.body.classList.add("offline");
  text(document.getElementById("connection")!, "DISCONNECTED / PAUSED — displayed values are stale");
  text(document.getElementById("current-decision")!, reason);
}
connect();

type StrategyProfile = {name: string; version: number; [key: string]: unknown};
let strategyProfiles: StrategyProfile[] = [];
let profileSchema: Parameters<typeof settingsForm>[0] | null = null;
let getProfileFields: (()=>unknown) | null = null;
function drawProfileFields(profile: StrategyProfile) {
  if(profileSchema) {
    getProfileFields=settingsForm(profileSchema,profile,document.getElementById("profile-fields")!);
    document.querySelectorAll<HTMLInputElement|HTMLSelectElement>("#profile-fields input,#profile-fields select").forEach(n=>n.disabled=profile.name==="BASELINE V1");
  }
}
const selectProfile = document.getElementById("strategy-profile") as HTMLSelectElement;
const profileEditor = document.getElementById("profile-json") as HTMLTextAreaElement;
function selectedProfile() {return strategyProfiles[Number(selectProfile.value)];}
function editProfile() {
  const profile = selectedProfile(); if (!profile) return;
  profileEditor.value = JSON.stringify(profile, null, 2);
  profileEditor.readOnly = profile.name === "BASELINE V1";
  drawProfileFields(profile);
}
async function loadProfiles() {
  try {
    const response = await fetch("/api/strategy/profiles");
    if (!response.ok) throw new Error();
    const data = await response.json(); strategyProfiles = data.profiles;
    profileSchema=(await (await fetch("/api/settings/schema")).json()).profile;
    selectProfile.replaceChildren(...strategyProfiles.map((p, i) => {const o = el("option", `${p.name} v${p.version}`); o.value = String(i); return o;}));
    const active = strategyProfiles.findIndex(p => p.name === data.control.profile_name && p.version === data.control.profile_version);
    selectProfile.value = String(Math.max(0, active)); editProfile();
    document.getElementById("metric-catalog")!.replaceChildren(table(["Metric", "Units", "Definition"], Object.entries(data.metrics).map(([k, v]) => [k, ...(v as string[])])));
  } catch {text(document.getElementById("strategy-result")!, "Profile service unavailable; reconnect and reload to edit.");}
}
selectProfile.onchange = editProfile;
async function controlStrategy(value: Record<string, unknown>) {
  try {
    const response = await fetch("/api/strategy/control", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
    const result = await response.json();
    text(document.getElementById("strategy-result")!, response.ok ? "Paper strategy control saved; see live conditions and source health." : JSON.stringify(result));
  } catch {text(document.getElementById("strategy-result")!, "Control response unavailable; check live state before retrying.");}
}
document.getElementById("strategy-enable")!.onclick = () => {const p = selectedProfile(); if (p) void controlStrategy({enabled: true, profile_name: p.name, profile_version: p.version});};
document.getElementById("strategy-pause")!.onclick = () => {void controlStrategy({enabled: false});};
document.getElementById("strategy-emergency")!.onclick = () => {void controlStrategy({emergency: true, enabled: false});};
document.getElementById("strategy-clear-emergency")!.onclick = () => {void controlStrategy({emergency: false});};
document.getElementById("profile-copy")!.onclick = () => {
  try {const p = JSON.parse(profileEditor.value); p.name = "CUSTOM V2"; p.version = Math.max(0, ...strategyProfiles.filter(v => v.name === p.name).map(v => v.version))+1;
    profileEditor.readOnly = false; profileEditor.value = JSON.stringify(p, null, 2);
  } catch {text(document.getElementById("strategy-result")!, "Invalid profile JSON.");}
};
document.getElementById("profile-save")!.onclick = async () => {
  try {
    const profile = JSON.parse(profileEditor.value);
    const response = await fetch("/api/strategy/profiles", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(profile)});
    const result = await response.json(); text(document.getElementById("strategy-result")!, response.ok ? `Saved ${result.identifier}. Select and enable it explicitly to use it.` : JSON.stringify(result));
    if (response.ok) await loadProfiles();
  } catch {text(document.getElementById("strategy-result")!, "Invalid JSON or profile service unavailable.");}
};
document.getElementById("profile-export")!.onclick = () => {
  try {const value = JSON.stringify(JSON.parse(profileEditor.value), null, 2); const url = URL.createObjectURL(new Blob([value], {type: "application/json"}));
    const link = el("a"); link.href = url; link.download = "strategy-profile.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch {text(document.getElementById("strategy-result")!, "Invalid profile JSON.");}
};
(document.getElementById("profile-import") as HTMLInputElement).onchange = async event => {
  const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return;
  if (file.size > 128000) {text(document.getElementById("strategy-result")!, "Profile exceeds 128 KB."); return;}
  try {profileEditor.value = JSON.stringify(JSON.parse(await file.text()), null, 2); profileEditor.readOnly = false;}
  catch {text(document.getElementById("strategy-result")!, "Invalid profile JSON.");}
};
void loadProfiles();

document.getElementById("fields-copy")!.onclick=()=>{
  const p=(getProfileFields?.()??selectedProfile()) as StrategyProfile;
  p.name="CUSTOM V2";p.version=Math.max(0,...strategyProfiles.filter(v=>v.name===p.name).map(v=>v.version))+1;
  drawProfileFields(p);
};
document.getElementById("fields-save")!.onclick=()=>{
  if(!getProfileFields)return;
  profileEditor.value=JSON.stringify(getProfileFields());
  document.getElementById("profile-save")!.click();
};
void loadSettings();

import "./research.js";
