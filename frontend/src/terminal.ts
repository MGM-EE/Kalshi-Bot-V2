// Wire values remain descriptive; all order decisions and accounting live on the server.
import {settingsForm} from "./settings.js";
type Wire = Record<string, any>;
function el<K extends keyof HTMLElementTagNameMap>(tag: K, value = "", cls = "") {const n = document.createElement(tag); n.textContent = value; n.className = cls; return n;}
function show(id: string, value: string) {const n=document.getElementById(id)!; if(n.textContent!==value)n.textContent=value;}
const fmt = (v: unknown, digits = 2): string => v === null || v === undefined || !Number.isFinite(Number(v)) ? "Unavailable" : Number(v).toLocaleString(undefined,{maximumFractionDigits:digits});
function table(headers: string[], rows: unknown[][]) {const t=el("table"), h=el("tr");headers.forEach(v=>h.append(el("th",v)));t.append(h);rows.forEach(row=>{const tr=el("tr");row.forEach(v=>tr.append(el("td",String(v??"Unavailable"))));t.append(tr);});return t;}
const assetSelect=document.getElementById("selected-asset") as HTMLSelectElement;
const outcomeSelect=document.getElementById("selected-outcome") as HTMLSelectElement;
const alertToggle=document.getElementById("alerts-enabled") as HTMLInputElement;
try {alertToggle.checked=localStorage.getItem("fill-alerts")!=="off";}catch{}
alertToggle.onchange=()=>{try{localStorage.setItem("fill-alerts",alertToggle.checked?"on":"off");}catch{}};
let latest: Wire|null=null, initialized=false, lastFill=0, alertTimer=0;
const recentEvents: string[]=[];
const trends: Record<string, {ts:number;spot:number|null;contract:number|null}[]>={};
let drawAt=0;
function conditions(entry: Wire, exit: Wire) {
  return {buy:[...(entry.execution_conditions??[]),...(entry.conditions??[])],sell:exit.conditions??[]};
}
function tiles(id:string, rows:Wire[]) {
  const parent=document.getElementById(id)!;
  parent.replaceChildren(...(rows.length?rows:[{metric:"No evaluable conditions",state:"gray",reason:"Unavailable / no open position"}]).map(r=>{
    const n=el("span",`${r.metric.replaceAll("_"," ")} ${r.state==="green"?"✓":r.state==="yellow"?"~":r.state==="red"?"×":"—"}`,`tile ${r.state}`);
    n.title=`${r.metric}: ${r.value??"Unavailable"} ${r.unit??""}\nRequired: ${r.operator??""} ${r.threshold??"—"}${r.upper!=null?` … ${r.upper}`:""}\n${r.reason}\nSource: ${r.source??"Unknown"}`;parent.append(n);return n;
  }));
}
function spark(values:(number|null)[], label:string) {
  const box=el("div");box.append(el("h3",label));const points=values.filter((v):v is number=>v!==null&&Number.isFinite(v));
  if(points.length<2){box.append(el("p","Collecting observed data…","muted"));return box;}
  const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox","0 0 500 72");svg.setAttribute("class","spark");svg.setAttribute("role","img");svg.setAttribute("aria-label",`${label} · ${points.length} observed dashboard samples`);
  const low=Math.min(...points), high=Math.max(...points);
  let segment:string[]=[];
  const flush=()=>{if(segment.length){const path=document.createElementNS(svg.namespaceURI,"polyline");path.setAttribute("points",segment.join(" "));path.setAttribute("fill","none");path.setAttribute("stroke","#6bdeb1");path.setAttribute("stroke-width","2");svg.append(path);segment=[];}};
  values.forEach((p,i)=>{if(p===null||!Number.isFinite(p)){flush();return;}segment.push(`${i/(values.length-1)*500},${66-(p-low)/(high-low||1)*60}`);});flush();box.append(svg);return box;
}
function notify(data:Wire) {
  const fills:Wire[]=data.paper?.fills??[];
  if(!initialized){lastFill=Math.max(0,...fills.map(f=>f.id));initialized=true;return;}
  const added=fills.filter(f=>f.id>lastFill).sort((a,b)=>a.id-b.id);
  for(const fill of added){
    lastFill=Math.max(lastFill,fill.id);
    const direction=data.assets[fill.asset]?.market?.ticker===fill.ticker?data.assets[fill.asset]?.market?.outcome_mapping?.[fill.outcome]??fill.outcome:fill.outcome;
    const message=`PAPER ${fill.action.toUpperCase()} — ${direction.toUpperCase()} · ${fill.asset} · ${fill.quantity} @ $${fill.price} · ${fill.action==="buy"?`cost incl. fees $${fmt(Number(fill.quantity)*Number(fill.price)+Number(fill.net_fee),6)}`:`realized net $${fmt(fill.realized_pnl,6)}`} · ${fill.reason}`;
    recentEvents.unshift(message);if(recentEvents.length>20)recentEvents.pop();show("fill-alert",message);
    const strip=document.getElementById("decision-strip")!;strip.classList.remove("flash-buy","flash-sell");
    if(alertToggle.checked){void strip.offsetWidth;strip.classList.add(fill.action==="buy"?"flash-buy":"flash-sell");}
    clearTimeout(alertTimer);alertTimer=window.setTimeout(()=>{strip.classList.remove("flash-buy","flash-sell");show("fill-alert","");},2500);
  }
}
export function renderTerminal(data:Wire) {
  latest=data;const asset=assetSelect.value,outcome=outcomeSelect.value,item=data.assets[asset],market=item?.market;
  const indicators=data.indicators?.[asset]??{},strategy=data.strategy?.decisions?.[asset]??{};
  const entry=strategy.entries?.[outcome]??strategy.entry??{},exit=strategy.exits?.[outcome]??{};
  const flow=indicators.kraken_whale_flow,kalshi=indicators.kalshi_whale_flow;
  const seconds=60, sf=flow?.windows?.[seconds]??{},cf=kalshi?.windows?.[seconds]??{};
  const remaining=market?Math.max(0,Math.ceil(market.close_time-data.timestamp)):null;
  show("strip-clock",remaining===null?"No round":`${Math.floor(remaining/60)}:${String(remaining%60).padStart(2,"0")} LEFT`);
  const book=item?.kalshi.book, direction=market?.outcome_mapping?.[outcome]??"Unverified";
  const sourceNames=[`kalshi:${asset}:book`,`kraken:${asset}:trade`,`kalshi:${asset}:reference`];
  const status=sourceNames.map(k=>`${k.split(":")[0]} ${k.endsWith("reference")?"INDEX":""} ${data.sources[k]?.status??"missing"}`).join(" · ");
  const values:Record<string,string>={"DIRECTION":`${direction} / ${outcome.toUpperCase()}`,"CF / LINE":`${fmt(market?.current_index,4)} / ${fmt(market?.opening_reference,4)}`,"BID / ASK":book?.valid?`${fmt(book[outcome+"_bids"]?.[0]?.[0],4)} / ${fmt(book[outcome+"_asks"]?.[0]?.[0],4)}`:"Unavailable","KRAKEN LARGE NET 60s":flow?.fresh?`$${fmt(sf.net_large_usd)}${sf.complete?"":" (partial)"}`:"Unavailable","SWING / CHOP":`${fmt(indicators.swing_risk?.[outcome]?.value?.score,1)} / ${fmt(indicators.choppiness?.score,1)}`,"NET P&L":`$${fmt(data.paper?.all_time_pnl_conservative)}`,"SOURCES":status};
  const valueNode=document.getElementById("strip-values")!;valueNode.replaceChildren(...Object.entries(values).map(([k,v])=>{const n=el("span");n.append(el("b",k),document.createTextNode(v));return n;}));
  const rows=conditions(entry,exit);tiles("buy-tiles",rows.buy);tiles("sell-tiles",rows.sell);
  show("current-decision",`${exit.action&&exit.action!=="HOLD"?`${exit.action} — ${exit.reason}`:entry.operational_state??(entry.eligible?"ENTRY SIGNAL — NOT FILLED":"WAIT")} · ${entry.reason??"Market / rules unavailable"}`);
  const age=sourceNames.map(k=>`${k}: ${fmt(data.sources[k]?.exchange_age_seconds??data.sources[k]?.age_seconds,1)}s`).join(" · ");
  show("selected-market",`${asset} · ${market?.ticker??"No current market"} · CF distance $${market?.current_index!=null&&market?.opening_reference!=null?fmt(Number(market.current_index)-Number(market.opening_reference),4):"Unavailable"} · ${age}`);
  const detailRows=[...rows.buy.map((r:Wire)=>["BUY",r]),...rows.sell.map((r:Wire)=>["SELL",r])];
  document.getElementById("condition-details")!.replaceChildren(table(["Side","Metric","State","Actual","Threshold","Source / age","Reason"],detailRows.map(([side,r])=>[side,r.metric,r.state,`${r.value??"Unavailable"} ${r.unit??""}`,`${r.operator??""} ${r.threshold??"—"}${r.upper!=null?` … ${r.upper}`:""}`,`${r.source??"Unknown"} · ${age}`,r.reason])));
  document.getElementById("whales")!.replaceChildren(table(["Venue · 60s","Large buy USD","Large sell USD","Net USD","Net qty","Pressure","Coverage"],[["Kraken",fmt(sf.large_buy_usd),fmt(sf.large_sell_usd),fmt(sf.net_large_usd),`${fmt(sf.net_large_quantity)} coins`,flow?.pressure??"Unavailable",sf.complete?"Complete": "Partial / warming up"],["Kalshi",fmt(cf.large_buy_usd),fmt(cf.large_sell_usd),fmt(cf.net_large_usd),`${fmt(cf.net_large_quantity)} contracts`,kalshi?.pressure??"Unavailable",cf.complete?"Complete":`${kalshi?.source_mode??"missing"} / partial`]]),el("p",indicators.holdings_visibility??"Holdings unknown","muted"));
  const walls=indicators.large_order_persistence??{};
  document.getElementById("risks")!.replaceChildren(table(["Metric","Observed value / status"],[["Chop score / state",`${fmt(indicators.choppiness?.score,1)} / ${indicators.choppiness_state??"Unavailable"}`],["Swing score / state",`${fmt(indicators.swing_risk?.[outcome]?.value?.score,1)} / ${indicators.swing_risk?.[outcome]?.state??"Unavailable"}`],["Volatility / efficiency",`${fmt(indicators.spot_statistics?.realized_volatility_percent,4)}% / ${fmt(indicators.spot_statistics?.trend_efficiency,3)}`],["Line crossings 60s",fmt(indicators.line?.crossings_60s,0)],["Kraken large bid / ask persistence",walls.kraken?.fresh?`${fmt(walls.kraken.bid_persistence_seconds,1)}s / ${fmt(walls.kraken.ask_persistence_seconds,1)}s`:"Unavailable"],["Kalshi large bid / ask persistence",walls.kalshi?.fresh?`${fmt(walls.kalshi.bid_persistence_seconds,1)}s / ${fmt(walls.kalshi.ask_persistence_seconds,1)}s`:"Unavailable"],["Pressure setup score (not probability)",fmt(indicators.buy_sell_pressure_setup?.score,1)],["Potential absorption",indicators.buy_sell_pressure_setup?.potential_absorption?"Observed replenishment against flow":"No observed evidence"]]),el("p","Scores are transparent rule scores, not probabilities. Disappearing L2 levels have unknown fill/cancel cause.","muted"));
  const points=trends[asset]??(trends[asset]=[]);
  if(!points.length||data.timestamp-points[points.length-1].ts>=1){points.push({ts:data.timestamp,spot:data.sources[`kraken:${asset}:ticker`]?.status==="live"?Number(item?.kraken.ticker?.last)||null:null,contract:book?.valid&&["live","polling"].includes(data.sources[`kalshi:${asset}:book`]?.status)?Number(book[outcome+"_asks"]?.[0]?.[0])||null:null});if(points.length>120)points.shift();}
  if(data.timestamp-drawAt>=1){drawAt=data.timestamp;document.getElementById("sparklines")!.replaceChildren(spark(points.map(p=>p.spot),`${asset} spot · observed dashboard samples`),spark(points.map(p=>p.contract),`${asset} ${outcome.toUpperCase()} ask · observed dashboard samples`));}
  const p=data.paper;
  if(p)document.getElementById("positions")!.replaceChildren(table(["Position","Qty","Basis USD","Pinned strategy","Round entries / trips"],(p.positions??[]).filter((v:Wire)=>Number(v.quantity)>0).map((v:Wire)=>[`${v.asset} ${v.outcome} · ${v.ticker}`,v.quantity,fmt(v.cost_basis),v.strategy_context?.profile?`${v.strategy_context.profile.name} v${v.strategy_context.profile.version}`:"Manual",`${p.rounds?.find((r:Wire)=>r.ticker===v.ticker)?.entries??0} / ${p.rounds?.find((r:Wire)=>r.ticker===v.ticker)?.round_trips??0}`])));
  notify(data);
  const log=[...recentEvents,...(p?.fills??[]).slice(0,10).map((f:Wire)=>`${new Date(f.time*1000).toLocaleTimeString()} · confirmed ${f.action} ${f.asset} ${f.quantity} @ $${f.price} · fee $${f.net_fee} · net realized $${f.realized_pnl} · ${f.reason}`),...Object.entries(data.strategy?.decisions??{}).flatMap(([a,d]:[string,any])=>Object.entries(d.entries??{}).map(([o,e]:[string,any])=>`${a} ${o} · ${e.eligible?"ENTRY SIGNAL — NOT FILLED":"WAIT"} · ${e.reason}`)),...(p?.orders??[]).filter((o:Wire)=>o.status==="rejected").slice(0,3).map((o:Wire)=>`REJECTED · ${o.reason}`)];
  document.getElementById("research-events")!.replaceChildren(...log.slice(0,25).map(v=>el("p",v)));
}
assetSelect.onchange=()=>{drawAt=0;if(latest)renderTerminal(latest);};
outcomeSelect.onchange=()=>{trends[assetSelect.value]=[];drawAt=0;if(latest)renderTerminal(latest);};

export async function loadSettings() {
  const status=document.getElementById("settings-result")!;
  try {
    const request=async(path:string)=>{const r=await fetch(path);if(!r.ok)throw Error(path);return r.json();};
    const [schema,indicators,execution,credentials]=await Promise.all([request("/api/settings/schema"),request("/api/indicators/config"),request("/api/settings/execution"),request("/api/credentials/status")]);
    const getIndicators=settingsForm(schema.indicators,indicators,document.getElementById("indicator-fields")!);
    const getExecution=settingsForm(schema.execution,execution,document.getElementById("execution-fields")!);
    const save=async(path:string,value:unknown)=>{const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});const v=await r.json();status.textContent=r.ok?"New settings version saved. Definitions and warmup behavior apply as described.":JSON.stringify(v);if(r.ok)await loadSettings();};
    (document.getElementById("indicator-settings") as HTMLFormElement).onsubmit=e=>{e.preventDefault();void save("/api/indicators/config",{config:getIndicators(),acknowledge_open_positions:(document.getElementById("indicator-ack") as HTMLInputElement).checked});};
    (document.getElementById("execution-settings") as HTMLFormElement).onsubmit=e=>{e.preventDefault();void save("/api/settings/execution",getExecution());};
    show("credential-status",`Kalshi credential source: ${credentials.source} · ${credentials.configured?"configured":"not configured"}. ${credentials.scope??""}`);
  }catch{status.textContent="Settings service unavailable. Reload after reconnecting.";}
}
