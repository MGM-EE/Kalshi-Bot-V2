// Schema-driven local settings: all writes are validated again by the backend.
type Schema = {type?: string; title?: string; description?: string; $ref?: string;
  $defs?: Record<string, Schema>; properties?: Record<string, Schema>; items?: Schema;
  prefixItems?: Schema[]; anyOf?: Schema[]; enum?: unknown[]; const?: unknown;
  additionalProperties?: Schema;
  minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number};
function node<K extends keyof HTMLElementTagNameMap>(tag: K, value = "") {const n = document.createElement(tag); n.textContent = value; return n;}
export function settingsForm(root: Schema, value: unknown, parent: HTMLElement): () => unknown {
  const resolve = (s: Schema): Schema => s.$ref ? root.$defs![s.$ref.split("/").pop()!] : s;
  function field(raw: Schema, initial: unknown, key: string, container: HTMLElement): () => unknown {
    let s = resolve(raw);
    const nullable = s.anyOf?.some(v => v.type === "null") ?? false;
    if (s.anyOf) {
      const decimal = s.anyOf.some(v=>v.type==="number") && s.anyOf.some(v=>v.type==="string");
      s = {...s, ...resolve(s.anyOf.find(v => v.type !== "null")!), ...(decimal?{type:"string"}:{})};
    }
    if (s.additionalProperties && typeof initial === "object" && initial !== null) s = {...s,properties:Object.fromEntries(Object.keys(initial).map(k=>[k,s.additionalProperties!]))};
    if (s.properties) {
      const group = node("details"); group.open = ["entry", "exit", "sizing", "risk"].includes(key);
      group.append(node("summary", ({entry: "Buy conditions", exit: "Sell conditions", sizing: "Position sizing", risk: "Risk limits", execution: "Paper execution", assets: "Markets / per-asset thresholds"} as Record<string,string>)[key] ?? key.replaceAll("_", " ")));
      const grid = node("div"); grid.className = "settings-grid"; group.append(grid); container.append(group);
      const source = (initial ?? {}) as Record<string, unknown>;
      const getters = Object.entries(s.properties).map(([k,v]) => [k, field(v, source[k], k, grid)] as const);
      return () => Object.fromEntries(getters.map(([k,get]) => [k,get()]));
    }
    if (s.type === "array") {
      const group = node("details"); group.append(node("summary", key.replaceAll("_", " ")));
      const rows = node("div"); group.append(rows); container.append(group);
      let getters: (() => unknown)[] = [];
      let values = Array.isArray(initial) ? initial : [];
      function draw() {
        rows.replaceChildren(); getters = values.map((v,i) => {
          const row = node("div"); row.className = "setting-row"; rows.append(row);
          const get = field(s.prefixItems?.[i] ?? s.items ?? {}, v, String(i+1), row);
          if (!s.prefixItems) {const remove = node("button", "Remove"); remove.type = "button"; row.append(remove);
            remove.onclick = () => {values = getters.map(g => g()).filter((_,j) => j !== i); draw();};}
          return get;
        });
      }
      draw();
      if (!s.prefixItems) {const add = node("button", "Add"); add.type = "button"; group.append(add);
        add.onclick = () => {values = getters.map(g => g()); values.push(key === "rules" ? {metric:"momentum",operator:"min",threshold:"0",upper:null,enabled:true,required:true,weight:"1",warning_band:"0",confirmation_seconds:0} : "UP"); draw();};}
      return () => getters.map(g => g());
    }
    const label = node("label", key.replaceAll("_", " "));
    label.title = s.description ?? `${key.replaceAll("_", " ")}${key.includes("seconds") ? " · seconds" : key.includes("price") || key.includes("cost") || key.includes("usd") || key.includes("dollars") ? " · USD" : ""}${nullable ? " · leave blank for no override" : ""}`;
    container.append(label);
    if (s.enum || s.const !== undefined || key === "metric") {
      const select = node("select");
      const choices = key === "metric" ? ["ask","all_in_price","spread","depth","distance","distance_percent","normalized_distance","losing_side","approach_velocity","approach_positive","momentum","consistency","contract_acceleration","divergence","observations","data_age","whale_flow","kalshi_whale_flow","flow_imbalance","persistent_liquidity","whale_duration","choppiness","swing_risk","volatility","liquidity","cross_market"] : s.enum ?? [s.const];
      if (nullable) choices.unshift("");
      choices.forEach(v => {const o = node("option", String(v)); o.value = String(v); select.append(o);});
      select.value = String(initial ?? ""); label.append(select);
      return () => select.value === "" && nullable ? null : typeof choices[0] === "number" ? Number(select.value) : select.value;
    }
    const input = node("input"); label.append(input);
    if (s.type === "boolean") {input.type = "checkbox"; input.checked = Boolean(initial); return () => input.checked;}
    const numeric = s.type === "number" || s.type === "integer";
    input.type = numeric ? "number" : "text"; input.value = initial === null || initial === undefined ? "" : String(initial);
    if (numeric) {input.step = s.type === "integer" ? "1" : "any"; if (s.minimum !== undefined) input.min = String(s.minimum); if (s.maximum !== undefined) input.max = String(s.maximum);}
    if (!nullable) input.required = true;
    return () => nullable && !input.value.trim() ? null : numeric ? Number(input.value) : input.value;
  }
  parent.replaceChildren(); return field(root, value, "Settings", parent);
}
