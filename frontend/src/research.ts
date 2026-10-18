type Data=Record<string,any>;
function el<K extends keyof HTMLElementTagNameMap>(tag:K,text=""){const n=document.createElement(tag);n.textContent=text;return n;}
const num=(v:unknown):string=>v===null||v===undefined?"Unavailable":typeof v==="object"?JSON.stringify(v):Number.isFinite(Number(v))?Number(v).toLocaleString(undefined,{maximumFractionDigits:6}):String(v);
function table(headers:string[],rows:unknown[][]){const t=el("table"),h=el("tr");headers.forEach(v=>h.append(el("th",v)));t.append(h);rows.forEach(row=>{const r=el("tr");row.forEach(v=>r.append(el("td",num(v))));t.append(r);});return t;}
async function request(path:string,body?:unknown){const r=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw Error(typeof value.detail==="string"?value.detail:JSON.stringify(value));return value;}
let current:Data|null=null,result:Data|null=null;
let profiles:Data[]=[];
const group=document.getElementById("analytics-group") as HTMLSelectElement;
function breakdown(){const rows=current?.[group.value]??{};document.getElementById("analytics-breakdown")!.replaceChildren(table(["Group","Closed trades","Wins","Net USD"],Object.entries(rows).map(([k,v]:[string,any])=>[k,v.trades,v.wins,v.net_pnl])));}
group.onchange=breakdown;
function summary(a:Data){return table(["Metric","Value"],[['Closed trades',a.total_closed_trades],['Open cycles',a.open_cycles],['Win rate (fraction)',a.win_rate],['Average win / loss USD',`${num(a.average_win)} / ${num(a.average_loss)}`],['Net expectancy USD / trade',a.net_expectancy],['Profit factor (unavailable when no losses)',a.profit_factor],['Closed net P&L USD',a.net_closed_pnl],['Maximum observed conservative drawdown USD',a.maximum_observed_drawdown],['Realized-path drawdown USD',a.maximum_realized_drawdown],['All fill fees USD',a.fees],['Estimated adverse slippage USD',a.estimated_slippage_usd],['Rejected / unfilled orders',`${a.rejected_orders} / ${a.unfilled_orders}`]]);}
async function refresh(){
 const status=document.getElementById("recording-status")!;
 try{const [a,recordings,p]=await Promise.all([request('/api/research/analytics'),request('/api/research/recordings'),request('/api/strategy/profiles')]);current=a;profiles=p.profiles;
  document.getElementById("analytics-summary")!.replaceChildren(summary(a),el("p",`${a.observed_drawdown_scope} ${a.realized_drawdown_scope}`));breakdown();
  status.textContent=`Recorder: ${recordings.recorder.state} · events ${recordings.recorder.events??0} ${recordings.recorder.error??""}`;
  const file=document.getElementById("replay-file") as HTMLSelectElement,old=file.value;file.replaceChildren(...recordings.files.map((name:string)=>{const o=el("option",name);o.value=name;return o;}));if(recordings.files.includes(old))file.value=old;
  const options=document.getElementById("replay-profiles") as HTMLSelectElement;options.replaceChildren(...profiles.map((p,i)=>{const o=el("option",`${p.name} v${p.version}`);o.value=String(i);return o;}));
 }catch(e){status.textContent=String(e);}
}
document.getElementById("analytics-refresh")!.onclick=()=>void refresh();
(document.getElementById("research-panel") as HTMLDetailsElement).ontoggle=e=>{if((e.target as HTMLDetailsElement).open)void refresh();};
(document.getElementById("replay-form") as HTMLFormElement).onsubmit=async e=>{
 e.preventDefault();const button=(e.target as HTMLFormElement).querySelector('button')!;
 const status=document.getElementById("replay-status")!,selected=Array.from((document.getElementById("replay-profiles") as HTMLSelectElement).selectedOptions).map(o=>profiles[Number(o.value)]);
 if(selected.length>3){status.textContent='Select at most three comparison profiles.';return;}
 button.disabled=true;status.textContent='REPLAY — running in isolated accounts; live account continues separately.';
 try{result=await request('/api/research/replay',{file:(document.getElementById("replay-file") as HTMLSelectElement).value,period:(document.getElementById("replay-period") as HTMLSelectElement).value,profiles:selected});
   const nodes:Node[]=[];for(const [i,r] of result!.results.entries()){
    nodes.push(el('h3',`REPLAY ${i===0?'RECORDED CONFIGURATION':`${selected[i-1].name} v${selected[i-1].version}`} · ${r.period}`),el('p',`Dataset ${r.dataset_sha256} · ${r.events} events · ${new Date(r.start_time*1000).toISOString()} to ${new Date(r.end_time*1000).toISOString()} · exact recorded ledger: ${r.exact_reproduction===true?'VERIFIED':r.exact_reproduction===null?'comparison run':'FAILED'}`),summary(r.analytics),el('p',`Cycle time p50 / p95 / max: ${num(r.performance.cycle_p50_ms)} / ${num(r.performance.cycle_p95_ms)} / ${num(r.performance.cycle_max_ms)} ms · ${r.performance.cycles} cycles · ${num(r.wall_seconds)} seconds replay time.`));
   }
   document.getElementById('replay-results')!.replaceChildren(...nodes);status.textContent='REPLAY complete. Results are paper research observations; no live account was changed.';(document.getElementById('replay-export') as HTMLButtonElement).disabled=false;
 }catch(error){status.textContent=String(error);}finally{button.disabled=false;}
};
document.getElementById('replay-export')!.onclick=()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=el('a');a.href=url;a.download='paper-replay-audit.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
