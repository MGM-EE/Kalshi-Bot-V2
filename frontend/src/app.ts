type Health = {status: string; detail: string; age_seconds: number; exchange_age_seconds: number | null};
type Book = {valid?: boolean; yes_bids?: string[][]; yes_asks?: string[][]; no_bids?: string[][];
  no_asks?: string[][]; bids?: string[][]; asks?: string[][]};
type Market = {ticker: string; title: string; close_time: number; opening_reference: string | number | null;
  outcome_mapping: Record<string, string> | null; current_index: string | null; rules: string;
  rule_checks: string[]; reference_status: string; [key: string]: unknown};
type Venue = {book?: Book; ticker?: {last: string; bid: string; ask: string; spread: string};
  trades?: Record<string, unknown>[]};
type Asset = {market: Market | null; upcoming: Market[]; kalshi: Venue; kraken: Venue};
type Snapshot = {assets: Record<string, Asset>; sources: Record<string, Health>;
  events: {time: number; source: string; status: string; detail: string}[]};

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text = "", cls = "") {
  const node = document.createElement(tag); node.textContent = text; node.className = cls; return node;
}
function text(node: HTMLElement, value: string) {if (node.textContent !== value) node.textContent = value;}
const money = (v: unknown) => v === null || v === undefined ? "Unavailable" : `$${v}`;
const cache = new Map<string, string>();
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
  document.body.classList.remove("offline");
  text(document.getElementById("connection")!, "DISCONNECTED / PAUSED — Phase 1 read-only data monitor");
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
let failures = 0;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/stream`);
  let lastFrame = Date.now();
  const timer = setInterval(() => {if (Date.now() - lastFrame > 5000) ws.close();}, 1000);
  ws.onmessage = event => {try {render(JSON.parse(event.data)); lastFrame = Date.now(); failures = 0;}
    catch {ws.close();}};
  ws.onclose = () => {
    clearInterval(timer); document.body.classList.add("offline");
    text(document.getElementById("connection")!, "DISCONNECTED / PAUSED — displayed values are stale");
    setTimeout(connect, Math.min(30000, 1000 * 2 ** Math.min(failures++, 5)));
  };
  ws.onerror = () => ws.close();
}
connect();
